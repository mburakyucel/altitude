"""Small recovery safety fuse: faults pause ordinary dispatch until explicit clearance."""
from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config, state as S


RECOVERY_TURN_RETRY_SECONDS = 300
RECOVERY_TURN_MAX_RETRY_SECONDS = 1800
RECOVERY_TURN_CLAIM_SECONDS = max(config.L3_TURN_TIMEOUT, config.L3_CODEX_TURN_TIMEOUT) + 60


def hold_path():
    return config.MONITOR_DIR / "recovery-hold.json"


def lock_path():
    return config.MONITOR_DIR / "recovery-hold.lock"


def launch_lock_path():
    return config.MONITOR_DIR / "recovery-launch.lock"


def clearance_history_path():
    return config.MONITOR_DIR / "recovery-clearances.jsonl"


def _clean(value: object, limit: int = 300) -> str:
    """Keep private recovery state bounded, one-line, and safe to render in operator views."""
    return " ".join(str(value or "").split())[:limit]


def _fault_digest(current: dict) -> str:
    evidence = {"episode": current.get("episode"), "faults": current.get("faults") or []}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _commit(current: dict) -> None:
    """Persist one hold RMW without discarding an unreplayed state-first transition."""
    S.reconcile_transition(current)
    current["state_revision"] = int(current.get("state_revision") or 0) + 1
    S.write_json(hold_path(), current)


@contextmanager
def _lock():
    config.ensure_root()
    with S.ordered_file_lock(lock_path(), S.LockLevel.RECOVERY):
        yield


@contextmanager
def _launch_lock():
    """Serialize the hold linearization point with the short worker-launch operation."""
    config.ensure_root()
    with S.ordered_file_lock(launch_lock_path(), S.LockLevel.RECOVERY):
        yield


class LaunchHeld(RuntimeError):
    pass


def status() -> dict | None:
    value = S.read_json(hold_path(), None)
    return value if isinstance(value, dict) and value.get("active") else None


def hold(reason: str, *, kind: str = "operator", incident: str | None = None,
         actor: str = "altd") -> dict:
    """Publish the fuse first, then settle any worker already past launch permission."""
    reason = _clean(reason or "system health fault", 500)
    kind = _clean(kind or "system-health", 100)
    actor = _clean(actor, 40)
    incident = _clean(incident, 80) or None
    # Do not wait for the launch barrier before publishing. flock waiters are not FIFO: if an ordinary launch
    # queued ahead of this fault, it could otherwise acquire the barrier and spawn before the hold became visible.
    with _lock():
        current = status() or {"active": True, "since": S.now(), "episode": secrets.token_urlsafe(12),
                               "faults": [], "repair": None}
        now = S.now()
        matches = [row for row in current.get("faults") or [] if row.get("kind") == kind]
        other = [row for row in current.get("faults") or [] if row.get("kind") != kind]
        count = sum(max(1, int(row.get("count", 1))) for row in matches) + 1
        first = min((row.get("first") or row.get("at") or now for row in matches), default=now)
        previous_incident = next((row.get("incident") for row in reversed(matches) if row.get("incident")), None)
        fault = {"first": first, "last": now, "count": count, "kind": kind, "reason": reason,
                 "incident": incident or previous_incident, "by": actor}
        current.update({"active": True, "updated": now})
        current["faults"] = [*other[-19:], fault]
        _commit(current)
    # A launcher that yielded from launch_permission before publication is already committed to spawning. Wait
    # only for that short spawn boundary to settle. Every queued launcher rechecks the now-published hold first.
    # State and launch locks are deliberately never nested, avoiding an inversion with launch_permission.
    with _launch_lock():
        pass
    return current


def attach_incident(kind: str, incident: str) -> dict | None:
    """Link evidence to the matching held fault without recording the fault a second time."""
    kind, incident = _clean(kind, 100), _clean(incident, 80)
    if not kind or not incident:
        return status()
    with _lock():
        current = status()
        if not current:
            return None
        faults = current.get("faults") or []
        for row in reversed(faults):
            if row.get("kind") == kind:
                row["incident"] = incident
                current["updated"] = S.now()
                _commit(current)
                break
        return current


def request_l3_attention(project: str | None, *, kind: str, incident: str | None = None) -> dict | None:
    """Attach one durable L3 wake request to the active recovery episode.

    Fault kinds coalesce into one record. Repeated evidence for a kind already represented in the episode updates
    the fuse but does not fan out another model turn. A target-less request remains durable evidence and is not
    dispatched to an arbitrary project.
    """
    kind, incident = _clean(kind, 100), _clean(incident, 80) or None
    project = _clean(project, 100) or None
    with _lock():
        current = status()
        if not current:
            return None
        episode = _clean(current.get("episode") or current.get("since"), 40)
        attention = current.get("l3_attention")
        if not isinstance(attention, dict) or attention.get("episode") != episode:
            attention = {
                "episode": episode,
                "project": project,
                "requested": S.now(),
                "updated": S.now(),
                "revision": 1,
                "handled_revision": 0,
                "attempts": 0,
                "next_attempt": None,
                "faults": [],
            }
        if attention.get("project") is None and project:
            attention["project"] = project
        faults = list(attention.get("faults") or [])
        existing = next((row for row in faults if row.get("kind") == kind), None)
        if existing is None:
            faults.append({"kind": kind, "incident": incident})
        elif incident and not existing.get("incident"):
            existing["incident"] = incident
        attention["faults"] = faults[-20:]
        attention["updated"] = S.now()
        current["l3_attention"] = attention
        current["updated"] = S.now()
        _commit(current)
        return dict(attention)


def _due(when: object, now: datetime) -> bool:
    if not when:
        return True
    try:
        return datetime.fromisoformat(str(when)) <= now
    except ValueError:
        return True


def _claim_active(attention: dict, now: datetime) -> bool:
    claimed = attention.get("claimed")
    if not attention.get("claim") or not claimed:
        return False
    try:
        return datetime.fromisoformat(str(claimed)) + timedelta(seconds=RECOVERY_TURN_CLAIM_SECONDS) > now
    except ValueError:
        return False


def _attention_snapshot(project: str, attention: dict) -> dict:
    return {
        "episode": attention.get("episode"),
        "project": project,
        "revision": int(attention.get("revision") or 0),
        "attempts": int(attention.get("attempts") or 0),
        "claim": attention.get("claim"),
        "faults": [
            {"kind": _clean(row.get("kind"), 100), "incident": _clean(row.get("incident"), 80) or None}
            for row in (attention.get("faults") or [])[-20:]
        ],
    }


def l3_attention_due(project: str, *, now: datetime | None = None) -> dict | None:
    """Return a bounded wake snapshot when this project's active episode is due."""
    current = status()
    attention = (current or {}).get("l3_attention")
    if not isinstance(attention, dict) or attention.get("project") != project:
        return None
    revision = int(attention.get("revision") or 0)
    if revision <= int(attention.get("handled_revision") or 0):
        return None
    now = now or datetime.now(timezone.utc)
    if _claim_active(attention, now) or not _due(attention.get("next_attempt"), now):
        return None
    return _attention_snapshot(project, attention)


def claim_l3_attention(project: str) -> dict | None:
    """Atomically claim a due wake across server processes; an abandoned claim expires after the turn timeout."""
    now = datetime.now(timezone.utc)
    with _lock():
        current = status()
        attention = (current or {}).get("l3_attention")
        if not isinstance(attention, dict) or attention.get("project") != project:
            return None
        revision = int(attention.get("revision") or 0)
        if revision <= int(attention.get("handled_revision") or 0):
            return None
        if _claim_active(attention, now) or not _due(attention.get("next_attempt"), now):
            return None
        attention["claim"] = secrets.token_urlsafe(12)
        attention["claimed"] = now.replace(microsecond=0).isoformat()
        current["l3_attention"] = attention
        current["updated"] = S.now()
        _commit(current)
        return _attention_snapshot(project, attention)


def l3_attention_is_current(project: str, episode: str, revision: int, claim: str) -> bool:
    """Fence a delayed wake and renew its lease at the post-L3-lock model-start boundary."""
    with _lock():
        current = status()
        attention = (current or {}).get("l3_attention")
        valid = bool(isinstance(attention, dict) and attention.get("project") == project
                     and attention.get("episode") == episode and int(attention.get("revision") or 0) == revision
                     and int(attention.get("handled_revision") or 0) < revision and attention.get("claim") == claim)
        if not valid:
            return False
        attention["claimed"] = S.now()
        current["l3_attention"] = attention
        current["updated"] = S.now()
        _commit(current)
        return True


def fail_l3_attention(project: str, episode: str, revision: int, claim: str, error: str) -> bool:
    """Leave the same wake pending with bounded exponential backoff."""
    with _lock():
        current = status()
        attention = (current or {}).get("l3_attention")
        if (not isinstance(attention, dict) or attention.get("project") != project
                or attention.get("episode") != episode or attention.get("claim") != claim):
            return False
        if int(attention.get("revision") or 0) != revision:
            return False
        attempts = int(attention.get("attempts") or 0) + 1
        delay = min(RECOVERY_TURN_RETRY_SECONDS * (2 ** min(attempts - 1, 3)),
                    RECOVERY_TURN_MAX_RETRY_SECONDS)
        attention.update({
            "attempts": attempts,
            "last_error": _clean(error or "L3 recovery turn failed", 200),
            "last_attempt": S.now(),
            "next_attempt": (datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat(),
        })
        attention.pop("claim", None)
        attention.pop("claimed", None)
        current["l3_attention"] = attention
        current["updated"] = S.now()
        _commit(current)
        return True


def complete_l3_attention(project: str, episode: str, revision: int, claim: str) -> bool:
    """Acknowledge only the exact wake snapshot that a successful L3 turn handled."""
    with _lock():
        current = status()
        attention = (current or {}).get("l3_attention")
        if (not isinstance(attention, dict) or attention.get("project") != project
                or attention.get("episode") != episode or attention.get("claim") != claim):
            return False
        if int(attention.get("revision") or 0) != revision:
            return False
        attention.update({"handled_revision": revision, "handled": S.now(), "next_attempt": None})
        attention.pop("last_error", None)
        attention.pop("claim", None)
        attention.pop("claimed", None)
        current["l3_attention"] = attention
        current["updated"] = S.now()
        current["state_revision"] = int(current.get("state_revision") or 0) + 1
        S.write_json_transition(
            hold_path(), current, "recovery-turn-handled",
            subject=f"recovery/{episode}", actor="altd",
            projection=S.project_projection(project), episode=episode, revision=revision,
            faults=list(attention.get("faults") or [])[-20:],
        )
    return True


def claim_repair(project: str, slug: str, *, actor: str) -> dict:
    """Claim the episode's single repair L2 before its task is created."""
    if actor not in ("l3", "burak"):
        raise ValueError("a recovery task must be explicitly delegated by L3 or Burak")
    with _lock():
        current = status()
        if not current:
            raise ValueError("a recovery task requires an active recovery hold")
        active = [
            (name, task.get("slug"))
            for name in config.load_projects()
            for task in S.list_tasks(name)
            if task.get("source") == "recovery"
            and task.get("state") in ("queued", "running", "blocked", "reported")
        ]
        if active:
            name, existing = active[0]
            raise ValueError(
                f"recovery already has active repair task {name}/{existing}; "
                "resume or finish that task instead of creating another"
            )
        claimed = current.get("repair")
        if claimed:
            raise ValueError(
                f"recovery already has repair task {claimed.get('project')}/{claimed.get('slug')}; "
                "resume or finish that task instead of creating another"
            )
        current["repair"] = {"project": project, "slug": slug, "claimed": S.now(), "by": actor}
        current["updated"] = S.now()
        _commit(current)
        return current


def release_failed_claim(project: str, slug: str) -> None:
    """Roll back only the matching claim when its task could not be persisted."""
    with _lock():
        current = status()
        repair = (current or {}).get("repair") or {}
        if repair.get("project") == project and repair.get("slug") == slug:
            current["repair"] = None
            current["updated"] = S.now()
            _commit(current)


def dispatch_hold(project: str, task: dict | None = None) -> str | None:
    """Return the hold reason, except for the one explicitly claimed recovery task."""
    current = status()
    if not current:
        return None
    repair = current.get("repair") or {}
    if (task and task.get("source") == "recovery"
            and project == repair.get("project")
            and task.get("slug") == repair.get("slug")):
        return None
    fault = (current.get("faults") or [{}])[-1]
    return f"recovery hold: {fault.get('kind') or 'system health fault'}; explicit L3 clearance required"


@contextmanager
def launch_permission(project: str, task: dict):
    """Keep the final hold check serialized through creation of the background worker."""
    with _launch_lock():
        reason = dispatch_hold(project, task)
        if reason:
            raise LaunchHeld(reason)
        yield


def clear(reason: str, *, actor: str) -> dict:
    """Clear the fuse explicitly; never restart or unmask the service."""
    if actor not in ("l3", "burak"):
        raise ValueError("only L3 or Burak may clear a recovery hold")
    reason = _clean(reason, 300)
    if not reason:
        raise ValueError("recovery clearance requires a reason")
    with _lock():
        current = status()
        if not current:
            raise ValueError("no active recovery hold")
        repair = current.get("repair") or None
        pending = current.get("clearance_pending")
        revision = int(current.get("state_revision") or 0)
        digest = _fault_digest(current)
        if isinstance(pending, dict) and (
                pending.get("hold_revision") != revision or pending.get("fault_digest") != digest):
            current.pop("clearance_pending", None)
            current["updated"] = S.now()
            _commit(current)
            raise ValueError("recovery evidence changed after clearance was prepared; inspect and clear again")
        if isinstance(pending, dict) and (pending.get("by") != actor or pending.get("reason") != reason):
            raise ValueError("clearance retry must use the same actor and reason")
        record = pending if isinstance(pending, dict) else {
            "clearance_id": str(current.get("episode") or current.get("since")),
            "at": S.now(),
            "by": actor,
            "reason": reason,
            "since": _clean(current.get("since"), 40),
            "repair": ({"project": _clean(repair.get("project"), 100),
                        "slug": _clean(repair.get("slug"), 160)} if repair else None),
            "faults": [
                {"kind": _clean(row.get("kind"), 100),
                 "count": max(1, int(row.get("count", 1))),
                 "incident": _clean(row.get("incident"), 80) or None}
                for row in (current.get("faults") or [])[-20:]
            ],
            "l3_attention": ({
                "project": _clean(current["l3_attention"].get("project"), 100) or None,
                "revision": int(current["l3_attention"].get("revision") or 0),
                "handled_revision": int(current["l3_attention"].get("handled_revision") or 0),
            } if isinstance(current.get("l3_attention"), dict) else None),
            "hold_revision": revision + 1,
            "fault_digest": digest,
        }
        if pending is None:
            current["clearance_pending"] = record
            current["updated"] = S.now()
            _commit(current)
        history = clearance_history_path()
        S.append_jsonl(history, record, key_field="clearance_id")
        hold_path().unlink()
        S._fsync_directory(hold_path().parent)
    for project in config.load_projects():
        project_hold = config.project_dir(project) / "hold.json"
        value = S.read_json(project_hold, None)
        if isinstance(value, dict) and str(value.get("reason") or "").startswith("recovery hold"):
            project_hold.unlink(missing_ok=True)
    return {"cleared": True, "at": S.now(), "by": actor, "reason": reason,
            "repair": current.get("repair")}
