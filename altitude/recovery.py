"""Small recovery safety fuse: faults pause ordinary dispatch until explicit clearance."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager

from . import config, state as S


def hold_path():
    return config.MONITOR_DIR / "recovery-hold.json"


def lock_path():
    return config.MONITOR_DIR / "recovery-hold.lock"


@contextmanager
def _lock():
    config.ensure_root()
    with open(lock_path(), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def status() -> dict | None:
    value = S.read_json(hold_path(), None)
    return value if isinstance(value, dict) and value.get("active") else None


def hold(reason: str, *, kind: str = "operator", incident: str | None = None,
         actor: str = "altd") -> dict:
    """Trip the fuse without dispatching recovery work."""
    reason = (reason or "system health fault").strip()[:500]
    with _lock():
        current = status() or {"active": True, "since": S.now(), "faults": [], "repair": None}
        fault = {"at": S.now(), "kind": kind, "reason": reason, "incident": incident, "by": actor}
        current.update({"active": True, "updated": fault["at"]})
        current["faults"] = [*(current.get("faults") or [])[-19:], fault]
        S.write_json(hold_path(), current)
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


def clear(reason: str, *, actor: str) -> dict:
    """Clear the fuse explicitly; never restart or unmask the service."""
    if actor not in ("l3", "burak"):
        raise ValueError("only L3 or Burak may clear a recovery hold")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("recovery clearance requires a reason")
    with _lock():
        current = status()
        if not current:
            raise ValueError("no active recovery hold")
        hold_path().unlink()
    for project in config.load_projects():
        project_hold = config.project_dir(project) / "hold.json"
        value = S.read_json(project_hold, None)
        if isinstance(value, dict) and str(value.get("reason") or "").startswith("recovery hold"):
            project_hold.unlink(missing_ok=True)
    return {"cleared": True, "at": S.now(), "by": actor, "reason": reason,
            "repair": current.get("repair")}
