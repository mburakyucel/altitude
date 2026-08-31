"""Small recovery safety fuse: faults pause ordinary dispatch until explicit clearance."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager

from . import config, state as S


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


@contextmanager
def _lock():
    config.ensure_root()
    with open(lock_path(), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def _launch_lock():
    """Serialize the hold linearization point with the short worker-launch operation."""
    config.ensure_root()
    with open(launch_lock_path(), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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
        current = status() or {"active": True, "since": S.now(), "faults": [], "repair": None}
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
        S.write_json(hold_path(), current)
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
                S.write_json(hold_path(), current)
                break
        return current


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
            and task.get("state") in ("requested", "proposed", "approved", "running", "blocked", "reported")
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
        S.write_json(hold_path(), current)
        return current


def release_failed_claim(project: str, slug: str) -> None:
    """Roll back only the matching claim when its task could not be persisted."""
    with _lock():
        current = status()
        repair = (current or {}).get("repair") or {}
        if repair.get("project") == project and repair.get("slug") == slug:
            current["repair"] = None
            current["updated"] = S.now()
            S.write_json(hold_path(), current)


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
        record = {
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
        }
        history = clearance_history_path()
        history.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(history, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        hold_path().unlink()
    for project in config.load_projects():
        project_hold = config.project_dir(project) / "hold.json"
        value = S.read_json(project_hold, None)
        if isinstance(value, dict) and str(value.get("reason") or "").startswith("recovery hold"):
            project_hold.unlink(missing_ok=True)
    return {"cleared": True, "at": S.now(), "by": actor, "reason": reason,
            "repair": current.get("repair")}
