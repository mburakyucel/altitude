"""Durable, fail-closed circuit breaker for automatic recovery launches.

This module is deliberately policy-only: callers authenticate manual reset requests and
place ``reserve``/``launch_gate`` at every automatic launch boundary.  In particular,
``ALTITUDE_ACTOR`` is not an authorization mechanism and is never consulted here.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import errno
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping

from . import config, state as S

VERSION = 1
CLOSED = "closed"
OPEN = "open"
MAX_EVENTS = 512
MAX_FAILURES = 512
DEFAULT_ACTION_DEADLINE_SECONDS = 60 * 60
_RESET_FD = None
_RESET_PID = None
_RESET_PID_START = None
_RESET_PATH = None


class BreakerOpen(RuntimeError):
    """The durable breaker refused an automatic action."""

    def __init__(self, message: str, state: Mapping | None = None):
        super().__init__(message)
        self.state = dict(state or {})


class BreakerPermitError(BreakerOpen):
    """A permit is missing, stale, mismatched, or already consumed."""


class BreakerStateError(BreakerOpen):
    """The durable breaker state could not be trusted or persisted."""


def state_path() -> Path:
    return config.MONITOR_DIR / "recovery-breaker.json"


def history_path() -> Path:
    return config.MONITOR_DIR / "recovery-breaker-history.jsonl"


def lock_path() -> Path:
    return config.MONITOR_DIR / "recovery-breaker.lock"


def reset_authority_path() -> Path:
    return config.MONITOR_DIR / "recovery-breaker-reset-authority.lock"


def _proc_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, ValueError):
        return None


def _exact_process_live(pid, expected_start) -> bool | None:
    """Return True/False only when exact liveness is provable; None is fail-closed unknown."""
    if not isinstance(pid, int) or pid <= 0 or not isinstance(expected_start, str) or not expected_start:
        return None
    try:
        current = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return False
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ESRCH):
            return False
        return None
    except (IndexError, ValueError):
        return None
    return current == expected_start


def acquire_reset_authority() -> None:
    """Hold reset authority for this exact daemon process lifetime.

    A fork inherits the descriptor but not the owner PID identity; a separate process cannot
    acquire the flock while the daemon lives. This is the authority boundary below HTTP auth.
    """
    global _RESET_FD, _RESET_PID, _RESET_PID_START, _RESET_PATH
    path = reset_authority_path()
    pid, start = os.getpid(), _proc_start(os.getpid())
    if not start:
        raise BreakerStateError("cannot establish daemon process-start identity for breaker reset")
    if _RESET_FD is not None and _RESET_PID == pid and _RESET_PID_START == start and _RESET_PATH == path:
        try:
            os.fstat(_RESET_FD)
            reconcile()
            return
        except OSError:
            _RESET_FD = None
    if _RESET_FD is not None:
        try:
            os.close(_RESET_FD)
        except OSError:
            pass
        _RESET_FD = None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise BreakerStateError("another daemon process owns recovery-breaker reset authority") from exc
    _RESET_FD, _RESET_PID, _RESET_PID_START, _RESET_PATH = fd, pid, start, path
    reconcile()


def _has_reset_authority() -> bool:
    if (_RESET_FD is None or _RESET_PID != os.getpid()
            or _RESET_PID_START != _proc_start(os.getpid()) or _RESET_PATH != reset_authority_path()):
        return False
    try:
        os.fstat(_RESET_FD)
        return True
    except OSError:
        return False


def _instant(value=None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        out = value
    elif isinstance(value, (int, float)):
        out = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError("at must be a datetime, timestamp, ISO string, or None")
    return out.replace(tzinfo=timezone.utc) if out.tzinfo is None else out.astimezone(timezone.utc)


def _iso(value=None) -> str:
    return _instant(value).isoformat()


def _parse_at(value) -> datetime | None:
    try:
        return _instant(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _bucket(at: datetime) -> int:
    seconds = max(1, int(config.AGENT_POLL_SECONDS))
    return int(at.timestamp()) // seconds


def _closed_state(generation: int = 1, *, at=None, last_reset=None) -> dict:
    stamp = _iso(at)
    return {
        "version": VERSION,
        "mode": CLOSED,
        "generation": generation,
        "opened_at": None,
        "reason": None,
        "events": [],
        "failures": [],
        "updated_at": stamp,
        "last_reset": last_reset,
    }


def _corrupt_state(message: str, raw: str | None = None, generation: int = 0) -> dict:
    fingerprint = hashlib.sha256((raw or "").encode("utf-8", "replace")).hexdigest()
    return {
        "version": VERSION,
        "mode": OPEN,
        "generation": max(0, generation),
        "opened_at": None,
        "reason": {"code": "state-corrupt", "message": message},
        "events": [],
        "failures": [],
        "updated_at": None,
        "corrupt": True,
        "corrupt_error": message,
        "corrupt_sha256": fingerprint,
    }


def _validate(value, raw: str) -> dict:
    generation = value.get("generation", 0) if isinstance(value, dict) else 0
    generation = generation if isinstance(generation, int) and generation >= 0 else 0
    if not isinstance(value, dict):
        return _corrupt_state("breaker state is not a JSON object", raw, generation)
    if value.get("version") != VERSION:
        return _corrupt_state(f"unsupported breaker state version: {value.get('version')!r}", raw, generation)
    if value.get("mode") not in (CLOSED, OPEN):
        return _corrupt_state(f"invalid breaker mode: {value.get('mode')!r}", raw, generation)
    if not isinstance(value.get("generation"), int) or value["generation"] < 1:
        return _corrupt_state("invalid breaker generation", raw, generation)
    if not isinstance(value.get("events", []), list) or not isinstance(value.get("failures", []), list):
        return _corrupt_state("breaker events/failures are not lists", raw, generation)
    out = dict(value)
    out.setdefault("events", [])
    out.setdefault("failures", [])
    out.setdefault("opened_at", None)
    out.setdefault("reason", None)
    out.setdefault("last_reset", None)
    return out


def _read_locked() -> tuple[dict, str | None]:
    try:
        raw = state_path().read_text()
    except FileNotFoundError:
        return _closed_state(), None
    except OSError as exc:
        message = f"cannot read {state_path()}: {exc}"
        return _corrupt_state(message), message
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        message = f"corrupt JSON in {state_path()}: {exc}"
        return _corrupt_state(message, raw), message
    state = _validate(value, raw)
    return state, state.get("corrupt_error")


def _save_locked(state: dict, *, at=None) -> None:
    state["updated_at"] = _iso(at)
    try:
        S.atomic_write(state_path(), json.dumps(state, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        raise BreakerStateError(f"cannot persist recovery breaker state: {exc}") from exc


def _append_history_locked(record: dict) -> None:
    try:
        with open(history_path(), "a") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise BreakerStateError(f"cannot persist recovery breaker history: {exc}") from exc


@contextmanager
def _lock() -> Iterator[None]:
    try:
        config.MONITOR_DIR.mkdir(parents=True, exist_ok=True)
        stream = open(lock_path(), "a+")
        fcntl.flock(stream, fcntl.LOCK_EX)
    except OSError as exc:
        try:
            stream.close()
        except (NameError, OSError):
            pass
        raise BreakerStateError(f"cannot acquire recovery breaker lock: {exc}") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(stream, fcntl.LOCK_UN)
        finally:
            stream.close()


def _event_at(event: Mapping) -> datetime | None:
    return _parse_at(event.get("reserved_at"))


def _within(value, now: datetime, seconds: int) -> bool:
    stamp = _parse_at(value)
    return stamp is not None and (now - stamp).total_seconds() <= seconds


def _counts(state: Mapping, now: datetime) -> tuple[int, int]:
    bucket = _bucket(now)
    events = [e for e in state.get("events", []) if isinstance(e, dict)]
    tick = sum(1 for event in events if event.get("bucket") == bucket)
    window = sum(1 for event in events if _within(event.get("reserved_at"), now, config.RECOVERY_BREAKER_WINDOW_SECONDS))
    return tick, window


def _failure_counts(state: Mapping, now: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for failure in state.get("failures", []):
        if not isinstance(failure, dict) or not _within(failure.get("at"), now, config.RECOVERY_BREAKER_WINDOW_SECONDS):
            continue
        family = str(failure.get("family") or "").strip()
        if family:
            counts[family] = counts.get(family, 0) + 1
    return counts


def _compact(state: dict, now: datetime) -> None:
    events = [e for e in state.get("events", []) if isinstance(e, dict)]
    active = [e for e in events if e.get("state") in ("reserved", "launching", "started")]
    terminal = [e for e in events if e not in active][-MAX_EVENTS:]
    room = max(0, MAX_EVENTS - len(active))
    state["events"] = active + (terminal[-room:] if room else [])
    failures = [f for f in state.get("failures", []) if isinstance(f, dict)]
    recent = [f for f in failures if _within(f.get("at"), now, config.RECOVERY_BREAKER_WINDOW_SECONDS * 2)]
    state["failures"] = recent[-MAX_FAILURES:]


def _action_identity(event: Mapping) -> str:
    payload = [event.get(key) for key in
               ("token", "generation", "kind", "project", "slug", "evidence_generation",
                "owner_pid", "owner_start", "started_at", "deadline_at")]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _reconcile_started_locked(state: dict, now: datetime) -> list[dict]:
    """Archive started actions only when their exact owning process is provably gone."""
    if state.get("corrupt"):
        return []
    reconciled = []
    for event in state.get("events", []):
        if not isinstance(event, dict) or event.get("state") != "started":
            continue
        if _exact_process_live(event.get("owner_pid"), event.get("owner_start")) is not False:
            continue
        identity = event.get("action_identity")
        if not isinstance(identity, str) or identity != _action_identity(event):
            continue
        event.update({
            "state": "settled",
            "settled_at": now.isoformat(),
            "outcome": "owner-dead",
            "orphaned_at": now.isoformat(),
            "historical": event.get("generation") != state.get("generation"),
        })
        reconciled.append({
            "event": "logical-action-owner-dead",
            "at": now.isoformat(),
            "generation": event.get("generation"),
            "action_identity": identity,
            "kind": event.get("kind"),
            "project": event.get("project"),
            "slug": event.get("slug"),
            "deadline_at": event.get("deadline_at"),
        })
    if reconciled:
        _compact(state, now)
        _save_locked(state, at=now)
        for record in reconciled:
            _append_history_locked(record)
    return reconciled


def _public(state: Mapping, now: datetime) -> dict:
    tick, window = _counts(state, now)
    families = _failure_counts(state, now)
    actions = []
    for event in state.get("events", [])[-12:]:
        if not isinstance(event, dict):
            continue
        action = {key: event.get(key) for key in (
            "kind", "project", "slug", "evidence_generation", "state", "reserved_at",
            "started_at", "deadline_at", "settled_at", "orphaned_at", "outcome", "error_family",
            "action_identity", "owner_pid",
        ) if event.get(key) is not None}
        if event.get("state") == "started":
            action["owner_live"] = _exact_process_live(event.get("owner_pid"), event.get("owner_start"))
            deadline = _parse_at(event.get("deadline_at"))
            action["deadline_expired"] = deadline is not None and now >= deadline
        actions.append(action)
    return {
        "version": VERSION,
        "mode": state.get("mode", OPEN),
        "generation": state.get("generation", 0),
        "opened_at": state.get("opened_at"),
        "reason": state.get("reason"),
        "updated_at": state.get("updated_at"),
        "last_reset": state.get("last_reset"),
        "reset_required": state.get("mode") == OPEN,
        "corrupt": bool(state.get("corrupt")),
        "corrupt_error": state.get("corrupt_error"),
        "tick": {"bucket": _bucket(now), "used": tick, "limit": config.RECOVERY_BREAKER_TICK_LIMIT},
        "window": {"used": window, "limit": config.RECOVERY_BREAKER_WINDOW_LIMIT,
                   "seconds": config.RECOVERY_BREAKER_WINDOW_SECONDS},
        "failure_families": {"counts": families, "limit": config.RECOVERY_BREAKER_FAILURE_LIMIT},
        "recent_actions": actions,
    }


def public_state(*, at=None) -> dict:
    """Reconcile proven-dead owners and return a UI-safe snapshot."""
    now = _instant(at)
    with _lock():
        state, _ = _read_locked()
        _reconcile_started_locked(state, now)
        return _public(state, now)


def reconcile(*, at=None) -> dict:
    """Settle crash-orphaned logical starts without ever clearing a live or unknown owner."""
    now = _instant(at)
    with _lock():
        state, _ = _read_locked()
        reconciled = _reconcile_started_locked(state, now)
        return {"reconciled": len(reconciled), "actions": reconciled, "state": _public(state, now)}


def _raise_open(state: Mapping) -> None:
    reason = state.get("reason")
    message = reason.get("message") if isinstance(reason, dict) else str(reason or "recovery breaker is open")
    cls = BreakerStateError if state.get("corrupt") else BreakerOpen
    raise cls(message, _public(state, _instant()))


def _trip(state: dict, *, code: str, message: str, trigger: Mapping | None, now: datetime) -> dict:
    if state.get("mode") == OPEN:
        return state
    old_generation = int(state["generation"])
    for event in state.get("events", []):
        if (isinstance(event, dict) and event.get("generation") == old_generation
                and event.get("state") in ("reserved", "launching")):
            event["state"] = "invalidated"
            event["invalidated_at"] = now.isoformat()
    state["mode"] = OPEN
    state["generation"] = old_generation + 1
    state["opened_at"] = now.isoformat()
    state["reason"] = {"code": code, "message": message, "trigger": dict(trigger or {})}
    return state


def _permit_identity(permit: Mapping) -> tuple:
    if not isinstance(permit, Mapping):
        raise BreakerPermitError("breaker permit must be a mapping")
    return tuple(permit.get(key) for key in
                 ("token", "generation", "kind", "project", "slug", "evidence_generation"))


def _find_event(state: Mapping, permit: Mapping) -> dict | None:
    identity = _permit_identity(permit)
    for event in state.get("events", []):
        if not isinstance(event, dict):
            continue
        if tuple(event.get(key) for key in
                 ("token", "generation", "kind", "project", "slug", "evidence_generation")) == identity:
            return event
    return None


def reserve(kind: str, *, project: str | None = None, slug: str | None = None,
            evidence_generation: str, source: str = "automatic", at=None) -> dict:
    """Atomically reserve one automatic action or open before the N+1 action.

    ``evidence_generation`` must be stable evidence chosen by the state machine (dispatch
    generation, report fingerprint, etc.), never an inferred model label.
    """
    kind = str(kind or "").strip()
    evidence_generation = str(evidence_generation or "").strip()
    if not kind or not evidence_generation:
        raise ValueError("kind and evidence_generation are required")
    now = _instant(at)
    with _lock():
        state, _ = _read_locked()
        _reconcile_started_locked(state, now)
        if state.get("mode") != CLOSED or state.get("corrupt"):
            _raise_open(state)
        duplicate = next((event for event in state.get("events", [])
                          if isinstance(event, dict)
                          and event.get("state") in ("reserved", "launching", "started")
                          and event.get("kind") == kind and event.get("project") == project
                          and event.get("slug") == slug
                          and event.get("evidence_generation") == evidence_generation), None)
        if duplicate:
            raise BreakerPermitError("this recovery evidence already has an active permit", _public(state, now))
        tick, window = _counts(state, now)
        trigger = {"kind": kind, "project": project, "slug": slug,
                   "evidence_generation": evidence_generation}
        code = message = None
        if tick >= config.RECOVERY_BREAKER_TICK_LIMIT:
            code, message = "tick-limit", f"automatic recovery tick limit reached ({tick}/{config.RECOVERY_BREAKER_TICK_LIMIT})"
        elif window >= config.RECOVERY_BREAKER_WINDOW_LIMIT:
            code, message = "window-limit", f"automatic recovery rolling limit reached ({window}/{config.RECOVERY_BREAKER_WINDOW_LIMIT})"
        if code:
            _trip(state, code=code, message=message, trigger=trigger, now=now)
            _save_locked(state, at=now)
            _append_history_locked({"event": "opened", "at": now.isoformat(),
                                    "generation": state["generation"], "reason": state["reason"]})
            _raise_open(state)
        permit = {
            "token": uuid.uuid4().hex,
            "generation": state["generation"],
            "bucket": _bucket(now),
            "kind": kind,
            "project": project,
            "slug": slug,
            "evidence_generation": evidence_generation,
            "source": str(source or "automatic"),
            "state": "reserved",
            "reserved_at": now.isoformat(),
        }
        state["events"].append(dict(permit))
        _compact(state, now)
        _save_locked(state, at=now)
        return dict(permit)


@contextmanager
def launch_gate(permit: Mapping, *, at=None,
                deadline_seconds: int | float = DEFAULT_ACTION_DEADLINE_SECONDS) -> Iterator[dict]:
    """Atomically consume a permit as a logical automatic action.

    The breaker bounds durable control-plane actions, not operating-system process trees; engine/L3
    lifecycle code remains the authority for PIDs, groups, cancellation, and restart adoption. The
    lock is released before the caller performs work, and ``settle`` closes the exact generation.
    """
    now = _instant(at)
    try:
        deadline_seconds = float(deadline_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("deadline_seconds must be a positive finite number") from exc
    if not 0 < deadline_seconds < 365 * 24 * 60 * 60:
        raise ValueError("deadline_seconds must be a positive finite number")
    owner_pid = os.getpid()
    owner_start = _proc_start(owner_pid)
    if not owner_start:
        raise BreakerStateError("cannot establish logical action owner process identity")
    with _lock():
        state, _ = _read_locked()
        _reconcile_started_locked(state, now)
        if state.get("mode") != CLOSED or state.get("corrupt"):
            _raise_open(state)
        if permit.get("generation") != state.get("generation"):
            raise BreakerPermitError("stale breaker generation", _public(state, now))
        event = _find_event(state, permit)
        if event is None or event.get("state") != "reserved":
            raise BreakerPermitError("breaker permit is missing, mismatched, or consumed", _public(state, now))
        event["state"] = "started"
        event["started_at"] = now.isoformat()
        event["deadline_at"] = (now + timedelta(seconds=deadline_seconds)).isoformat()
        event["owner_pid"] = owner_pid
        event["owner_start"] = owner_start
        event["action_identity"] = _action_identity(event)
        _save_locked(state, at=now)
    yield dict(event)


def settle(permit: Mapping, *, outcome: str, error_family: str | None = None,
           detail: str | None = None, at=None) -> dict:
    """Settle only the exact current-generation permit; repeated settlement is idempotent."""
    outcome = str(outcome or "").strip()
    if not outcome:
        raise ValueError("outcome is required")
    family = str(error_family or "").strip() or None
    now = _instant(at)
    with _lock():
        state, _ = _read_locked()
        if state.get("corrupt"):
            _raise_open(state)
        _reconcile_started_locked(state, now)
        if permit.get("generation") != state.get("generation"):
            historical = _find_event(state, permit)
            if historical is not None and historical.get("state") == "settled":
                return {"settled": True, "stale": True, "historical": True, "idempotent": True,
                        "generation": state.get("generation"), "mode": state.get("mode")}
            if state.get("mode") == OPEN and historical is not None and historical.get("state") == "started":
                historical.update({"state": "settled", "settled_at": now.isoformat(),
                                   "outcome": outcome, "historical": True})
                if detail:
                    historical["detail"] = str(detail)[-600:]
                if family:
                    historical["error_family"] = family
                _save_locked(state, at=now)
                return {"settled": True, "stale": True, "historical": True, "idempotent": False,
                        "generation": state.get("generation"), "mode": state.get("mode")}
            return {"settled": False, "stale": True, "generation": state.get("generation"),
                    "mode": state.get("mode")}
        event = _find_event(state, permit)
        if event is None:
            return {"settled": False, "stale": False, "missing": True,
                    "generation": state.get("generation"), "mode": state.get("mode")}
        if event.get("state") == "settled":
            return {"settled": True, "idempotent": True, "generation": state["generation"],
                    "mode": state["mode"]}
        if event.get("state") not in ("reserved", "started", "launch-failed"):
            raise BreakerPermitError(f"permit cannot settle from state {event.get('state')!r}", _public(state, now))
        event.update({"state": "settled", "settled_at": now.isoformat(), "outcome": outcome})
        if detail:
            event["detail"] = str(detail)[-600:]
        if family:
            event["error_family"] = family
            state["failures"].append({"family": family, "at": now.isoformat(),
                                      "token": permit.get("token"), "generation": state["generation"],
                                      "kind": event.get("kind"), "project": event.get("project"),
                                      "slug": event.get("slug")})
        _compact(state, now)
        opened = False
        if family and _failure_counts(state, now).get(family, 0) >= config.RECOVERY_BREAKER_FAILURE_LIMIT:
            _trip(state, code="failure-family-limit",
                  message=f"automatic recovery failure family {family!r} reached the limit ({config.RECOVERY_BREAKER_FAILURE_LIMIT})",
                  trigger={"family": family, "kind": event.get("kind"), "project": event.get("project"),
                           "slug": event.get("slug")}, now=now)
            opened = True
        _save_locked(state, at=now)
        if opened:
            _append_history_locked({"event": "opened", "at": now.isoformat(),
                                    "generation": state["generation"], "reason": state["reason"]})
        return {"settled": True, "idempotent": False, "opened": opened,
                "generation": state["generation"], "mode": state["mode"]}


def reset(expected_generation: int, reason: str, *, at=None) -> dict:
    """Manually reset one exact OPEN generation.

    Authorization belongs to the local authenticated control-plane caller.  This function
    intentionally does not trust environment variables, including ``ALTITUDE_ACTOR``.
    """
    if not _has_reset_authority():
        raise BreakerPermitError("breaker reset requires the live daemon process authority")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("a manual reset reason is required")
    now = _instant(at)
    with _lock():
        state, _ = _read_locked()
        _reconcile_started_locked(state, now)
        if state.get("mode") != OPEN:
            raise BreakerPermitError("recovery breaker is not open", _public(state, now))
        if state.get("generation") != expected_generation:
            raise BreakerPermitError("recovery breaker generation changed; reload before reset", _public(state, now))
        started = [event for event in state.get("events", [])
                   if isinstance(event, dict) and event.get("state") == "started"]
        if started:
            raise BreakerPermitError(
                "recovery breaker reset refused while a logical action is still started",
                _public(state, now),
            )
        _append_history_locked({
            "event": "reset", "at": now.isoformat(), "generation": expected_generation,
            "reason": reason, "opened_at": state.get("opened_at"), "open_reason": state.get("reason"),
            "corrupt": bool(state.get("corrupt")), "corrupt_sha256": state.get("corrupt_sha256"),
            "event_count": len(state.get("events", [])), "failure_count": len(state.get("failures", [])),
        })
        fresh = _closed_state(expected_generation + 1, at=now, last_reset={
            "at": now.isoformat(), "reason": reason, "from_generation": expected_generation,
        })
        _save_locked(fresh, at=now)
        return _public(fresh, now)
