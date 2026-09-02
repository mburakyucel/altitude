"""Task lifecycle — queued, running, blocked, reported, then archived. Every transition goes through here."""
from __future__ import annotations
import copy
import json
import os
import re
import subprocess
import uuid

from . import config, state as S

def short_reason(reason: str, limit: int = 200) -> str:
    """The first sentence of a block reason, for the card; the whole reason stays in detail."""
    first = re.split(r"(?<=[.!?])\s|\s[—–-]\s|:\s`", reason.strip(), maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"

TRANSITIONS = {
    "queued": {"running", "rejected"},
    "running": {"reported", "blocked", "rejected", "done"},
    "blocked": {"running", "rejected", "reported"},
    "reported": {"done", "running", "blocked", "rejected"},      # running: verifier says not done → resume
    "done": set(),
    "rejected": set(),
}

class TransitionError(Exception):
    pass


def owner_provider_capability_hold(task: dict) -> str | None:
    """Return the one closed hold for mutations on behalf of a task's L2 owner."""
    from . import engines
    engine = task.get("l2_engine") or "claude"
    try:
        engines.require_autonomous_engine(engine)
    except engines.EngineCapabilityError as exc:
        return f"engine hold: {exc}"
    return None


def require_owner_provider_capability(task: dict) -> None:
    """Fence every trusted mutation performed by or for the current L2 owner."""
    if task.get("state") == "queued" and not task.get("dispatch_id") and not task.get("l2_engine"):
        return
    if hold := owner_provider_capability_hold(task):
        raise TransitionError(hold)


def owner_command(project: str, slug: str, decide, validate) -> tuple[dict, object]:
    """The sole short-lock write seam for the embedded L2 owner operation.

    ``decide`` receives a detached active TaskRecord and returns
    ``(candidate, result, event)``. Effects never belong in either callback. Legacy lifecycle
    writers remain frozen until Phase 3; this seam owns only B3 planning/CAS/receipts/projections.
    """
    with S.project_lock(project):
        path = S.tasks_dir(project) / slug / "status.json"
        task = S.read_json(path)
        if not task:
            raise TransitionError(f"{slug}: active task no longer exists")
        candidate, result, event = decide(copy.deepcopy(task))
        if not isinstance(candidate, dict) or candidate.get("slug") != slug:
            raise TransitionError(f"{slug}: invalid owner command result")
        validate(candidate)
        if candidate != task:
            S.save_task(project, candidate)
            if event:
                kind, fields = event
                S.append_event(project, slug, kind, **fields)
        return candidate, result


TASK_MESSAGE_ROLES = ("burak", "l2")


def append_task_message(project: str, slug: str, role: str, text: str, *,
                        expected_dispatch_id: str, expected_session_id: str | None = None,
                        expected_state: str | None = None, expected_l2_token: str | None = None,
                        actor: str | None = None) -> dict:
    """Append one human-facing task message for the exact current L2 dispatch.

    The conversation is an append-only JSONL artifact separate from operational events and
    engine output.  Every writer must name the dispatch it believes it owns; stale pages and
    stale L2 processes therefore fail before they can speak into a replacement task.  The
    project lock serializes the append with lifecycle changes, and fsync makes a successful
    return a durable message rather than a buffered best effort.
    """
    if role not in TASK_MESSAGE_ROLES:
        raise TransitionError(f"task message role must be one of {TASK_MESSAGE_ROLES}")
    text = str(text or "").strip()
    if not text:
        raise TransitionError("task message is empty")
    if not expected_dispatch_id:
        raise TransitionError("task message has no dispatch owner")
    if role == "l2" and not expected_l2_token:
        raise TransitionError("L2 message has no ownership capability")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        allowed_states = ("running", "blocked", "reported") if role == "l2" else ("running", "blocked")
        if task.get("state") not in allowed_states:
            raise TransitionError(f"{slug}: cannot message L2 in {task.get('state')} state")
        if task.get("dispatch_id") != expected_dispatch_id:
            raise TransitionError(
                f"{slug}: L2 dispatch changed from {expected_dispatch_id!r} "
                f"to {task.get('dispatch_id')!r}"
            )
        if expected_session_id is not None and task.get("session_id") != expected_session_id:
            raise TransitionError(f"{slug}: L2 session changed before the message was recorded")
        if expected_l2_token is not None and task.get("l2_token") != expected_l2_token:
            raise TransitionError(f"{slug}: L2 ownership capability changed before the message was recorded")
        if expected_state is not None and task.get("state") != expected_state:
            raise TransitionError(
                f"{slug}: task changed from {expected_state} to {task.get('state')} "
                "before the message was recorded"
            )
        message = {
            "id": uuid.uuid4().hex,
            "at": S.now(),
            "role": role,
            "text": text,
            "dispatch_id": task["dispatch_id"],
            "session_id": task.get("session_id"),
            "by": actor or role,
        }
        path = S.task_dir(project, slug) / "conversation.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as stream:
            stream.write(json.dumps(message, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        S.append_event(project, slug, "task-message", message_id=message["id"], role=role,
                       dispatch_id=task["dispatch_id"], by=message["by"])
        return message


def task_messages(project: str, slug: str, limit: int | None = None) -> list[dict]:
    """Read the durable task conversation, failing loudly on a corrupt record."""
    path = S.task_dir(project, slug) / "conversation.jsonl"
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    if limit is not None:
        count = max(0, int(limit))
        lines = lines[-count:] if count else []
    messages = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"corrupt task conversation in {path} at line {line_number}: {exc}") from exc
        if (not isinstance(message, dict) or message.get("role") not in TASK_MESSAGE_ROLES
                or not isinstance(message.get("text"), str)):
            raise ValueError(f"corrupt task conversation in {path} at line {line_number}: invalid message")
        messages.append(message)
    return messages


def _move(project: str, task: dict, to: str, actor: str, **ev) -> dict:
    frm = task["state"]
    if to not in TRANSITIONS.get(frm, set()):
        raise TransitionError(f"{task['slug']}: {frm} → {to} is not allowed")
    task["state"] = to
    if to == "running":
        task["dispatching"] = None
    S.save_task(project, task)
    S.append_event(project, task["slug"], "state", frm=frm, to=to, by=actor, **ev)
    S.regen_state_md(project)
    return task


def new(project: str, title: str, request: str, actor: str = "l3", source: str = "chat", model: str | None = None,
        paths: list[str] | None = None, hold_merge: str | None = None, engine: str | None = None) -> dict:
    if source not in ("chat", "recovery"):
        raise TransitionError("task source must be chat or recovery")
    if engine and engine not in config.ENGINES:
        raise TransitionError(f"engine must be one of {config.ENGINES}")
    if model and engine != "codex" and model not in config.MODEL_ALIASES:
        raise TransitionError(f"model must be one of {config.MODEL_ALIASES}")
    if model in config.MODEL_ALIASES and engine is None:
        engine = "claude"  # a provider-specific model name is itself an explicit provider pin
    if engine and engine not in config.AUTONOMOUS_ENGINES:
        raise TransitionError(
            f"engine {engine!r} has no autonomous/mutating launch capability; choose one of "
            f"{config.AUTONOMOUS_ENGINES}"
        )
    config.project(project)
    with S.project_lock(project):
        base = S.slugify(title)
        slug, n = base, 1
        while S.task_dir(project, slug).exists():
            n += 1
            slug = f"{base}-{n}"
        recovery_claimed = False
        if source == "recovery":
            from . import recovery
            try:
                recovery.claim_repair(project, slug, actor=actor)
            except ValueError as exc:
                raise TransitionError(str(exc)) from exc
            recovery_claimed = True
        d = S.tasks_dir(project) / slug
        try:
            d.mkdir(parents=True)
            S.atomic_write(d / "request.md", request.rstrip() + "\n")
        except Exception:
            if recovery_claimed:
                recovery.release_failed_claim(project, slug)
            raise
        task = {"slug": slug, "title": title, "state": "queued", "created": S.now(),
                "attempt": 0, "dispatch_id": None, "session_id": None, "agent_id": None,
                "worktree": None,
                "branch": None, "prs": [], "spend": {}, "blocked_reason": None, "source": source,
                "verified": None, "model": model, "engine": engine, "l2_engine": None,
                "engine_model": None, "routing": None, "l2_token": None,
                "paths": [p.strip() for p in (paths or []) if p.strip()],
                "hold_merge": (hold_merge or "").strip() or None}
        try:
            S.save_task(project, task)
        except Exception:
            if recovery_claimed:
                recovery.release_failed_claim(project, slug)
            raise
        S.append_event(project, slug, "new", by=actor, title=title, source=source, queued=True,
                       recovery_delegated=source == "recovery")
        S.regen_state_md(project)
        return task


def reject(project: str, slug: str, reason: str, actor: str = "burak") -> dict:
    snapshot = S.load_task(project, slug)
    require_owner_provider_capability(snapshot)
    expected_owner = None
    if snapshot.get("l2_engine") == "codex":
        if snapshot.get("active_operation") is None:
            raise TransitionError(f"{slug}: unknown legacy Codex ownership cannot be rejected safely")
        from . import dispatch
        stopped = dispatch.cancel_owner(project, slug, f"task rejected: {reason}")
        expected_owner = stopped.get("active_operation")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        if task.get("l2_engine") == "codex":
            from . import dispatch
            if task.get("active_operation") != expected_owner or not dispatch.owner_archive_ready(project, task):
                raise TransitionError(f"{slug}: Codex owner is not terminal and empty")
        task["blocked_reason"] = None
        task = _move(project, task, "rejected", actor, reason=reason)
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def brief(project: str, slug: str, brief_md: str, actor: str = "l3") -> Path:
    with S.project_lock(project):
        require_owner_provider_capability(S.load_task(project, slug))
        d = S.task_dir(project, slug)
        S.atomic_write(d / "brief.md", brief_md.rstrip() + "\n")
        S.append_event(project, slug, "brief", by=actor, bytes=len(brief_md))
        return d / "brief.md"


def dispatch(project: str, slug: str, *, dispatch_id: str, session_id: str | None, agent_id: str | None,
             worktree: str | None, branch: str | None, l2_token: str, l2_engine: str = "claude",
             engine_model: str | None = None, routing: dict | None = None, actor: str = "altd") -> dict:
    if not session_id or not agent_id or not l2_token:
        raise TransitionError(f"{slug}: dispatch requires a concrete worker, session, and L2 capability")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("dispatch_id"):
            require_owner_provider_capability(task)
        previous_dispatch_id = task.get("dispatch_id")
        if previous_dispatch_id and previous_dispatch_id != dispatch_id:
            from . import transcript
            transcript.sync(project, slug)
        task.update({"dispatch_id": dispatch_id, "session_id": session_id, "agent_id": agent_id,
                     "l2_token": l2_token, "worktree": worktree, "branch": branch, "blocked_reason": None,
                     "l2_engine": l2_engine, "engine_model": engine_model, "routing": routing,
                     "dispatched": S.now()})
        if previous_dispatch_id and previous_dispatch_id != dispatch_id:
            task["previous_dispatch_id"] = previous_dispatch_id
        task["attempt"] = int(dispatch_id.rsplit("-", 1)[-1]) if dispatch_id.rsplit("-", 1)[-1].isdigit() else task["attempt"] + 1
        return _move(project, task, "running", actor, dispatch_id=dispatch_id, session_id=session_id)


def report(project: str, slug: str, verified: dict, actor: str = "altd", *,
           expected_state: str | None = None, expected_attempt: int | None = None,
           expected_block_from: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        if expected_state is not None and task["state"] != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task['state']}")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: expected attempt {expected_attempt}, found {task.get('attempt')}")
        if expected_block_from is not None:
            last_block = next((ev for ev in reversed(S.read_events(project, slug))
                               if ev.get("kind") == "state" and ev.get("to") == "blocked"), None)
            if not last_block or last_block.get("frm") != expected_block_from:
                raise TransitionError(f"{slug}: latest block did not come from {expected_block_from}")
        verified = {**verified, "attempt": task["attempt"]}
        task["verified"] = verified
        task["blocked_reason"] = None
        if verified.get("prs"):
            task["prs"] = sorted(set(task.get("prs", []) + list(verified["prs"])))
        return _move(project, task, "reported", actor, verdict=verified.get("verdict"))


def block(project: str, slug: str, reason: str, actor: str = "altd", *,
          expected_state: str | None = None, expected_dispatch_id: str | None = None,
          expected_session_id: str | None = None, expected_agent_id: str | None = None,
          expected_pending_identity: dict | None = None, updates: dict | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        for label, expected, actual in (
            ("state", expected_state, task.get("state")),
            ("dispatch", expected_dispatch_id, task.get("dispatch_id")),
            ("session", expected_session_id, task.get("session_id")),
            ("agent", expected_agent_id, task.get("agent_id")),
        ):
            if expected is not None and expected != actual:
                raise TransitionError(f"{slug}: {label} changed before block ({expected!r} → {actual!r})")
        if expected_pending_identity is not None:
            pending = task.get("pending_action") or {}
            if pending.get("identity") != expected_pending_identity:
                raise TransitionError(f"{slug}: pending action changed before block")
        task.update(updates or {})
        task["blocked_reason"] = reason
        return _move(project, task, "blocked", actor, reason=reason)


def resume(project: str, slug: str, actor: str = "altd", *,
           expected_state: str | None = None, expected_dispatch_id: str | None = None,
           expected_session_id: str | None = None, expected_agent_id: str | None = None,
           expected_pending_identity: dict | None = None, **ev) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        for label, expected, actual in (
            ("state", expected_state, task.get("state")),
            ("dispatch", expected_dispatch_id, task.get("dispatch_id")),
            ("session", expected_session_id, task.get("session_id")),
            ("agent", expected_agent_id, task.get("agent_id")),
        ):
            if expected is not None and expected != actual:
                raise TransitionError(f"{slug}: {label} changed before resume ({expected!r} → {actual!r})")
        if expected_pending_identity is not None:
            pending = task.get("pending_action") or {}
            if pending.get("identity") != expected_pending_identity:
                raise TransitionError(f"{slug}: pending action changed before resume")
        task["blocked_reason"] = None
        return _move(project, task, "running", actor, **ev)


def _require_no_code_change(task: dict) -> None:
    """A proposal/research task may close directly; code delivery must use the verified report path."""
    worktree = task.get("worktree")
    if not worktree:
        raise TransitionError("L2 direct completion requires its task worktree")
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree,
                            capture_output=True, text=True, timeout=30)
    if status.returncode != 0 or (status.stdout or "").strip():
        raise TransitionError("task worktree has uncommitted changes; code work must be landed and reported")
    diff = subprocess.run(["git", "diff", "--quiet", "origin/main...HEAD"], cwd=worktree,
                          capture_output=True, text=True, timeout=30)
    if diff.returncode != 0:
        if diff.returncode == 1:
            raise TransitionError("task branch contains code changes; use alt land and the verified report path")
        raise TransitionError(f"cannot prove the task branch is unchanged: {(diff.stderr or '').strip()[:200]}")


def done(project: str, slug: str, actor: str = "l3", digest: str = "", *,
         expected_state: str | None = None, expected_dispatch_id: str | None = None,
         expected_session_id: str | None = None, expected_agent_id: str | None = None,
         expected_l2_token: str | None = None) -> dict:
    snapshot = S.load_task(project, slug)
    expected_owner = None
    if actor != "l2" and snapshot.get("l2_engine") == "codex" and snapshot.get("active_operation"):
        from . import dispatch
        exact = dispatch.owner_result_snapshot(project, snapshot)
        expected_owner = exact["task"].get("active_operation")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        if expected_owner is not None:
            from . import dispatch
            if (task.get("active_operation") != expected_owner
                    or not dispatch.owner_archive_ready(project, task)):
                raise TransitionError(f"{slug}: Codex owner changed before terminalization")
        if actor == "l2":
            if (not expected_dispatch_id or not expected_l2_token
                    or task.get("dispatch_id") != expected_dispatch_id
                    or task.get("l2_token") != expected_l2_token):
                raise TransitionError(f"{slug}: L2 ownership changed before completion")
            if task.get("state") != "running":
                raise TransitionError(f"{slug}: L2 can complete only its running task")
            _require_no_code_change(task)
            task["completion_requested"] = {"at": S.now(), "digest": digest,
                                            "dispatch_id": expected_dispatch_id,
                                            "agent_id": task.get("agent_id"),
                                            "session_id": task.get("session_id")}
            S.save_task(project, task)
            S.append_event(project, slug, "completion-requested", by=actor,
                           dispatch_id=expected_dispatch_id)
            return task
        for label, expected, actual in (
            ("state", expected_state, task.get("state")),
            ("dispatch", expected_dispatch_id, task.get("dispatch_id")),
            ("session", expected_session_id, task.get("session_id")),
            ("agent", expected_agent_id, task.get("agent_id")),
        ):
            if expected is not None and expected != actual:
                raise TransitionError(f"{slug}: {label} changed before completion ({expected!r} → {actual!r})")
        d = S.task_dir(project, slug)
        task = _move(project, task, "done", actor)
        if digest:
            S.atomic_write(d / "digest.md", digest.rstrip() + "\n")
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def finalize_completion(project: str, slug: str, *, expected_dispatch_id: str,
                        expected_agent_id: str | None, expected_session_id: str | None,
                        actor: str = "altd") -> dict:
    """Archive a no-code L2 completion only after its physical worker has exited."""
    snapshot = S.load_task(project, slug)
    expected_owner = None
    if snapshot.get("l2_engine") == "codex" and snapshot.get("active_operation"):
        from . import dispatch
        exact = dispatch.owner_result_snapshot(project, snapshot)
        expected_owner = exact["task"].get("active_operation")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        if expected_owner is not None:
            from . import dispatch
            if (task.get("active_operation") != expected_owner
                    or not dispatch.owner_archive_ready(project, task)):
                raise TransitionError(f"{slug}: Codex owner changed before terminalization")
        request = task.get("completion_requested") or {}
        expected = (expected_dispatch_id, expected_agent_id, expected_session_id)
        current = (task.get("dispatch_id"), task.get("agent_id"), task.get("session_id"))
        requested = (request.get("dispatch_id"), request.get("agent_id"), request.get("session_id"))
        if task.get("state") != "running" or current != expected or requested != expected:
            raise TransitionError(f"{slug}: completion ownership changed before worker exit")
        _require_no_code_change(task)
        digest = str(request.get("digest") or "")
        task.pop("completion_requested", None)
        task.pop("pending_action", None)
        d = S.task_dir(project, slug)
        task = _move(project, task, "done", actor, requested_by="l2")
        if digest:
            S.atomic_write(d / "digest.md", digest.rstrip() + "\n")
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def _archive(project: str, slug: str) -> None:
    src = S.tasks_dir(project) / slug
    if src.is_dir():
        task = S.load_task(project, slug)
        if task.get("l2_engine") == "codex" and task.get("active_operation") is not None:
            from . import dispatch
            if not dispatch.owner_archive_ready(project, task):
                raise TransitionError(f"{slug}: refusing to archive a live Codex owner")
        # Capture the final state/outcome after digest/report creation and before the task moves.
        from . import transcript
        transcript.sync(project, slug)
        dst = S.archive_dir(project) / slug
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def set_spend(project: str, slug: str, **spend) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        require_owner_provider_capability(task)
        task.setdefault("spend", {}).update(spend)
        S.save_task(project, task)
        return task


# ---- User decisions and FYIs -------------------------------------------------

def fyi(project: str, slug: str | None, text: str, actor: str = "l3") -> dict:
    """An FYI is a line in the project's inbox.jsonl; the page shows the tail."""
    if slug:
        try:
            target = S.load_task(project, slug)
        except KeyError:
            target = None
        if target is not None:
            require_owner_provider_capability(target)
    item = {"at": S.now(), "kind": "fyi", "project": project, "slug": slug, "text": text.strip(), "by": actor, "seen": False}
    p = config.project_dir(project) / "inbox.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        import json
        f.write(json.dumps(item, sort_keys=True) + "\n")
    if slug:
        S.append_event(project, slug, "fyi", text=text.strip(), by=actor)
    return item


def inbox(project: str, limit: int = 50) -> list[dict]:
    import json
    p = config.project_dir(project) / "inbox.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def decisions(project: str) -> list[dict]:
    """Tasks blocked on user input. Ordinary task steering happens directly with the L2."""
    out = []
    for t in S.list_tasks(project):
        if (t["state"] == "blocked" and not t.get("resume_after")
                and not t.get("pending_action")):  # operational waits are not user decisions
            out.append({"project": project, "slug": t["slug"], "title": t["title"],
                        "question": f"Stopped mid-task: {short_reason(t.get('blocked_reason') or 'no reason recorded')}",
                        "options": ["Resume", "Reject"], "asked": t.get("updated"), "kind": "blocked",
                        "detail": t.get("blocked_reason")})
    return out


def set_hold_merge(project: str, slug: str, why: str | None, actor: str = "l3") -> dict:
    """PRs merge by default; a hold is an explicit, reasoned exception."""
    why = (why or "").strip() or None
    with S.project_lock(project):
        t = S.load_task(project, slug)
        require_owner_provider_capability(t)
        t["hold_merge"] = why
        S.save_task(project, t)
    S.append_event(project, slug, "hold-merge" if why else "release-merge", why=why, actor=actor)
    return t
