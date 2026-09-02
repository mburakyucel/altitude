"""Small recovery safety fuse: faults pause ordinary dispatch until explicit clearance."""
from __future__ import annotations

import fcntl
import hashlib
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


def _record() -> dict | None:
    """Read the one recovery authority, including its inactive high-water form."""
    value = S.read_json(hold_path(), None)
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("active"), bool):
        raise ValueError("unknown recovery record; migrate it before activation")
    epoch = value.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ValueError("recovery record has no canonical monotonic epoch; migrate it before activation")
    if value["active"]:
        if (not isinstance(value.get("episode"), str) or not value["episode"]
                or isinstance(value.get("revision"), bool)
                or not isinstance(value.get("revision"), int) or value["revision"] < 1):
            raise ValueError("active recovery hold has no canonical episode revision; clear it before activation")
    else:
        receipt = value.get("clearance")
        keys = {"id", "at", "by", "reason", "since", "episode", "revision", "repair", "faults",
                "l3_attention", "prior_epoch"}
        if (set(value) != {"active", "epoch", "clearance"} or not isinstance(receipt, dict)
                or set(receipt) != keys or receipt.get("prior_epoch") != epoch - 1
                or not isinstance(receipt.get("episode"), str) or not receipt["episode"]
                or isinstance(receipt.get("revision"), bool)
                or not isinstance(receipt.get("revision"), int) or receipt["revision"] < 1
                or not isinstance(receipt.get("id"), str) or not receipt["id"]
                or receipt["id"] != hashlib.sha256(S._canonical_json({  # noqa: SLF001
                    "kind": "recovery-clearance-v2", **{k: v for k, v in receipt.items() if k != "id"}})).hexdigest()):
            raise ValueError("unknown inactive recovery record; migrate it before activation")
    return value


def _clearance_epoch() -> int:
    value = _record()
    return int((value or {}).get("epoch") or 0)


def clearance_epoch() -> int:
    """Public read-only projection of the canonical inactive recovery generation."""
    return _clearance_epoch()


def _reconcile_clearance_audit(value: dict | None) -> None:
    """Project the latest canonical inactive receipt into audit history, idempotently."""
    if not value or value.get("active") is not False:
        return
    receipt = value["clearance"]
    if not isinstance(receipt.get("id"), str) or not receipt["id"]:
        raise ValueError("inactive recovery record has no stable clearance receipt")
    S.append_jsonl(clearance_history_path(), receipt, key_field="id")


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
    value = _record()
    return value if value and value["active"] else None


def _advance(current: dict) -> None:
    """Invalidate every consumer of the prior episode snapshot after material fault evidence changes."""
    current["revision"] += 1
    attention = current.get("l3_attention")
    if isinstance(attention, dict):
        attention.update({"revision": current["revision"], "updated": S.now(), "next_attempt": None})
        attention.pop("claim", None)
        attention.pop("claimed", None)


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
        record = _record()
        _reconcile_clearance_audit(record)
        current = record if record and record["active"] else None
        if current is None:
            current = {"active": True, "since": S.now(), "episode": secrets.token_urlsafe(12),
                       "revision": 1, "epoch": int((record or {}).get("epoch") or 0),
                       "faults": [], "repair": None}
        else:
            _advance(current)
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
        attention = current.get("l3_attention")
        if isinstance(attention, dict):
            attention["faults"] = [{"kind": row.get("kind"), "incident": row.get("incident")}
                                   for row in current["faults"][-20:]]
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
        current = _record()
        _reconcile_clearance_audit(current)
        current = current if current and current["active"] else None
        if not current:
            return None
        faults = current.get("faults") or []
        for row in reversed(faults):
            if row.get("kind") == kind:
                row["incident"] = incident
                _advance(current)
                attention = current.get("l3_attention")
                if isinstance(attention, dict):
                    attention["faults"] = [{"kind": item.get("kind"), "incident": item.get("incident")}
                                           for item in faults[-20:]]
                current["updated"] = S.now()
                S.write_json(hold_path(), current)
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
        current = _record()
        _reconcile_clearance_audit(current)
        current = current if current and current["active"] else None
        if not current:
            return None
        episode = current["episode"]
        revision = current["revision"]
        attention = current.get("l3_attention")
        if not isinstance(attention, dict) or attention.get("episode") != episode:
            attention = {
                "episode": episode,
                "project": project,
                "requested": S.now(),
                "updated": S.now(),
                "revision": revision,
                "handled_revision": 0,
                "attempts": 0,
                "next_attempt": None,
                "faults": [],
            }
        else:
            attention["revision"] = revision
        if attention.get("project") is None and project:
            attention["project"] = project
        faults = list(attention.get("faults") or [])
        existing = next((row for row in faults if row.get("kind") == kind), None)
        changed = existing is None or bool(incident and not existing.get("incident"))
        if changed and isinstance(current.get("l3_attention"), dict):
            _advance(current)
            revision = current["revision"]
            attention["revision"] = revision
        if existing is None:
            faults.append({"kind": kind, "incident": incident})
        elif incident and not existing.get("incident"):
            existing["incident"] = incident
        attention["faults"] = faults[-20:]
        attention["updated"] = S.now()
        current["l3_attention"] = attention
        current["updated"] = S.now()
        S.write_json(hold_path(), current)
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
    if (not isinstance(attention, dict) or attention.get("project") != project
            or attention.get("revision") != (current or {}).get("revision")):
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
        current = _record()
        current = current if current and current["active"] else None
        attention = (current or {}).get("l3_attention")
        if (not isinstance(attention, dict) or attention.get("project") != project
                or attention.get("revision") != (current or {}).get("revision")):
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
        S.write_json(hold_path(), current)
        return _attention_snapshot(project, attention)


def l3_attention_is_current(project: str, episode: str, revision: int, claim: str) -> bool:
    """Fence a delayed wake and renew its lease at the post-L3-lock model-start boundary."""
    with _lock():
        current = _record()
        current = current if current and current["active"] else None
        attention = (current or {}).get("l3_attention")
        valid = bool(isinstance(attention, dict) and current.get("revision") == revision
                     and attention.get("project") == project
                     and attention.get("episode") == episode and int(attention.get("revision") or 0) == revision
                     and int(attention.get("handled_revision") or 0) < revision and attention.get("claim") == claim)
        if not valid:
            return False
        attention["claimed"] = S.now()
        current["l3_attention"] = attention
        current["updated"] = S.now()
        S.write_json(hold_path(), current)
        return True


def observe_l3_state(project: str, identity: dict | None = None) -> dict:
    """Return the one closed recovery snapshot every L3 physical operation must bind."""
    with _lock():
        current = status()
        if identity is None:
            if current is not None:
                raise ValueError("active recovery requires the exact claimed L3 attention snapshot")
            return {"state": "none", "episode_id": None, "permit_revision": None,
                    "claim": None, "epoch": _clearance_epoch()}
        if not isinstance(identity, dict) or set(identity) != {"episode_id", "permit_revision", "claim"}:
            raise ValueError("recovery identity must contain only episode_id, permit_revision, and claim")
        episode, revision, claim = (identity["episode_id"], identity["permit_revision"], identity["claim"])
        attention = (current or {}).get("l3_attention")
        if (not isinstance(episode, str) or not episode or isinstance(revision, bool)
                or not isinstance(revision, int) or revision < 1 or not isinstance(claim, str) or not claim
                or current is None or current.get("revision") != revision
                or not isinstance(attention, dict) or attention.get("project") != project
                or attention.get("episode") != episode or attention.get("revision") != revision
                or attention.get("claim") != claim or int(attention.get("handled_revision") or 0) >= revision):
            raise ValueError("recovery episode or launch permit changed during L3 turn")
        return {"state": "active", "episode_id": episode, "permit_revision": revision,
                "claim": claim, "epoch": _clearance_epoch()}


def l3_state_is_current(project: str, observation: dict) -> bool:
    """Recheck an installed none/active snapshot; active checks also renew the exact wake lease."""
    if not isinstance(observation, dict) or set(observation) != {
            "state", "episode_id", "permit_revision", "claim", "epoch"}:
        return False
    if observation.get("state") == "none":
        if any(observation.get(key) is not None for key in ("episode_id", "permit_revision", "claim")):
            return False
        with _lock():
            return status() is None and observation.get("epoch") == _clearance_epoch()
    if observation.get("state") != "active":
        return False
    return l3_attention_is_current(project, observation.get("episode_id"),
                                   observation.get("permit_revision"), observation.get("claim"))


def fail_l3_attention(project: str, episode: str, revision: int, claim: str, error: str) -> bool:
    """Leave the same wake pending with bounded exponential backoff."""
    with _lock():
        current = status()
        attention = (current or {}).get("l3_attention")
        if (not isinstance(attention, dict) or attention.get("project") != project
                or attention.get("episode") != episode or attention.get("claim") != claim
                or (current or {}).get("revision") != revision):
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
        S.write_json(hold_path(), current)
        return True


def complete_l3_attention(project: str, episode: str, revision: int, claim: str) -> bool:
    """Acknowledge only the exact wake snapshot that a successful L3 turn handled."""
    with _lock():
        current = status()
        attention = (current or {}).get("l3_attention")
        if (not isinstance(attention, dict) or attention.get("project") != project
                or attention.get("episode") != episode or attention.get("claim") != claim
                or (current or {}).get("revision") != revision):
            return False
        if int(attention.get("revision") or 0) != revision:
            return False
        attention.update({"handled_revision": revision, "handled": S.now(), "next_attempt": None})
        attention.pop("last_error", None)
        attention.pop("claim", None)
        attention.pop("claimed", None)
        current["l3_attention"] = attention
        current["updated"] = S.now()
        S.write_json(hold_path(), current)
    try:
        S.project_log(project, "recovery-turn-handled", episode=episode, revision=revision,
                      faults=list(attention.get("faults") or [])[-20:])
    except OSError:
        # The hold record and eventual clearance record still durably carry the handled revision. Do not replay a
        # successful model turn merely because this supplementary audit append failed.
        pass
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


def observe_owner_state(project: str, task: dict) -> dict:
    """Bind an L2 generation to the canonical active episode/revision or inactive epoch."""
    current = status()
    if current is None:
        return {"state": "none", "episode_id": None, "permit_revision": None,
                "epoch": _clearance_epoch()}
    repair = current.get("repair") or {}
    if (task.get("source") != "recovery" or repair.get("project") != project
            or repair.get("slug") != task.get("slug")):
        raise ValueError(dispatch_hold(project, task) or "active recovery blocks ordinary owner launch")
    return {"state": "active", "episode_id": current["episode"],
            "permit_revision": current["revision"], "epoch": _clearance_epoch()}


def owner_state_is_current(project: str, task: dict, physical: dict, epoch: int) -> bool:
    """Recheck one installed owner generation against the canonical recovery record."""
    try:
        observation = observe_owner_state(project, task)
    except ValueError:
        return False
    return (physical.get("recovery_episode_id"), physical.get("recovery_permit_revision"), epoch) == (
        observation["episode_id"], observation["permit_revision"], observation["epoch"])


@contextmanager
def launch_permission(project: str, task: dict):
    """Keep the final hold check serialized through creation of the background worker."""
    with _launch_lock():
        reason = dispatch_hold(project, task)
        if reason:
            raise LaunchHeld(reason)
        yield


@contextmanager
def owner_launch_permission(project: str, task: dict, physical: dict, epoch: int):
    """Linearize one owner spawn with canonical recovery episode publication."""
    with _launch_lock():
        if not owner_state_is_current(project, task, physical, epoch):
            raise LaunchHeld("recovery episode or owner launch permit changed")
        yield


@contextmanager
def l3_launch_permission(project: str, observation: dict):
    """Linearize one L3 spawn with hold publication while honoring its exact installed snapshot."""
    with _launch_lock():
        if not l3_state_is_current(project, observation):
            raise LaunchHeld("recovery episode or launch permit changed before L3 provider launch")
        yield


@contextmanager
def l3_effect_permission(project: str, observation: dict):
    """Linearize one short local mutation with recovery-hold publication."""
    with _launch_lock():
        if not l3_state_is_current(project, observation):
            raise LaunchHeld("recovery episode or launch permit changed before L3 mutation")
        yield


def clear(reason: str, *, actor: str, project: str | None = None,
          expected_l3: dict | None = None) -> dict:
    """Clear the fuse explicitly; never restart or unmask the service."""
    if actor not in ("l3", "burak"):
        raise ValueError("only L3 or Burak may clear a recovery hold")
    reason = _clean(reason, 300)
    if not reason:
        raise ValueError("recovery clearance requires a reason")
    with _lock():
        current = _record()
        _reconcile_clearance_audit(current)
        current = current if current and current["active"] else None
        if not current:
            raise ValueError("no active recovery hold")
        if actor == "l3":
            attention = current.get("l3_attention")
            expected = expected_l3 or {}
            if (not isinstance(expected, dict) or set(expected) != {
                    "state", "episode_id", "permit_revision", "claim", "epoch"}
                    or expected.get("state") != "active" or not isinstance(attention, dict)
                    or isinstance(expected.get("permit_revision"), bool)
                    or not isinstance(expected.get("permit_revision"), int)
                    or attention.get("project") != project
                    or (current.get("episode"), current.get("revision"), attention.get("claim")) !=
                    (expected.get("episode_id"), expected.get("permit_revision"), expected.get("claim"))
                    or attention.get("revision") != expected.get("permit_revision")
                    or int(attention.get("handled_revision") or 0) >= expected.get("permit_revision", 0)):
                raise ValueError("L3 recovery clearance does not match the exact active episode revision and claim")
        repair = current.get("repair") or None
        record = {
            "at": S.now(),
            "by": actor,
            "reason": reason,
            "since": _clean(current.get("since"), 40),
            "episode": current["episode"],
            "revision": current["revision"],
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
            "prior_epoch": current["epoch"],
        }
        record["id"] = hashlib.sha256(S._canonical_json(  # noqa: SLF001
            {"kind": "recovery-clearance-v2", **record})).hexdigest()
        inactive = {"active": False, "epoch": current["epoch"] + 1, "clearance": record}
        S.write_json(hold_path(), inactive)
        _reconcile_clearance_audit(inactive)
    for project in config.load_projects():
        project_hold = config.project_dir(project) / "hold.json"
        value = S.read_json(project_hold, None)
        if isinstance(value, dict) and str(value.get("reason") or "").startswith("recovery hold"):
            project_hold.unlink(missing_ok=True)
    return {"cleared": True, "at": S.now(), "by": actor, "reason": reason,
            "episode": current["episode"], "revision": current["revision"],
            "epoch": inactive["epoch"],
            "repair": current.get("repair")}
