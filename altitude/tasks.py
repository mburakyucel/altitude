"""Task lifecycle — five states, one writer. Every transition goes through here."""
from __future__ import annotations
import json
import os
import re
import uuid

from . import config, state as S

def short_reason(reason: str, limit: int = 200) -> str:
    """The first sentence of a block reason, for the card; the whole reason stays in detail."""
    first = re.split(r"(?<=[.!?])\s|\s[—–-]\s|:\s`", reason.strip(), maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"

TRANSITIONS = {
    "approved": {"running", "parked", "rejected"},
    "running": {"reported", "blocked", "parked"},
    "blocked": {"running", "parked", "rejected", "reported"},
    "reported": {"done", "running", "blocked"},                    # running: verifier says not done → resume
    "parked": {"approved", "rejected"},
    "done": set(),
    "rejected": set(),
}

class TransitionError(Exception):
    pass


TASK_MESSAGE_ROLES = ("burak", "l2")


def append_task_message(project: str, slug: str, role: str, text: str, *,
                        expected_dispatch_id: str, expected_session_id: str | None = None,
                        expected_state: str | None = None, actor: str | None = None) -> dict:
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
    with S.project_lock(project):
        task = S.load_task(project, slug)
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
    if to in ("parked", "rejected") and frm in ("running", "blocked") and task.get("agent_id"):
        from . import engines
        note = engines.claude_rm(task["agent_id"])
        S.append_event(project, task["slug"], "session-stopped", agent_id=task["agent_id"], note=note[:200])
    S.regen_state_md(project)
    return task


def new(project: str, title: str, request: str, actor: str = "l3", source: str = "chat", model: str | None = None,
        paths: list[str] | None = None, engine: str | None = None, hold_merge: str | None = None) -> dict:
    if model and model not in config.MODEL_ALIASES:
        raise TransitionError(f"model must be one of {config.MODEL_ALIASES}")
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
        task = {"slug": slug, "title": title, "state": "approved", "created": S.now(),
                "attempt": 0, "dispatch_id": None, "session_id": None, "agent_id": None,
                "worktree": None,
                "branch": None, "prs": [], "spend": {}, "blocked_reason": None, "source": source,
                "verified": None, "model": model, "paths": [p.strip() for p in (paths or []) if p.strip()],
                "engine": engine,  # decision 45: a forced engine for every L1 of this task (None = by quota)
                "hold_merge": (hold_merge or "").strip() or None}  # decision 48: why Burak merges this one himself (None = the L2 merges)
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
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["blocked_reason"] = None
        return _move(project, task, "rejected", actor, reason=reason)


def park(project: str, slug: str, reason: str, actor: str = "l3") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        return _move(project, task, "parked", actor, reason=reason)


def unpark(project: str, slug: str, actor: str = "l3") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        return _move(project, task, "approved", actor)


def brief(project: str, slug: str, brief_md: str, actor: str = "l3") -> Path:
    with S.project_lock(project):
        d = S.task_dir(project, slug)
        S.atomic_write(d / "brief.md", brief_md.rstrip() + "\n")
        S.append_event(project, slug, "brief", by=actor, bytes=len(brief_md))
        return d / "brief.md"


def dispatch(project: str, slug: str, *, dispatch_id: str, session_id: str | None, agent_id: str | None,
             worktree: str | None, branch: str | None, actor: str = "altd") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.update({"dispatch_id": dispatch_id, "session_id": session_id, "agent_id": agent_id,
                     "worktree": worktree, "branch": branch, "blocked_reason": None,
                     "dispatched": S.now()})
        task["attempt"] = int(dispatch_id.rsplit("-", 1)[-1]) if dispatch_id.rsplit("-", 1)[-1].isdigit() else task["attempt"] + 1
        return _move(project, task, "running", actor, dispatch_id=dispatch_id, session_id=session_id)


def report(project: str, slug: str, verified: dict, actor: str = "altd", *,
           expected_state: str | None = None, expected_attempt: int | None = None,
           expected_block_from: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
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


def block(project: str, slug: str, reason: str, actor: str = "altd") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["blocked_reason"] = reason
        return _move(project, task, "blocked", actor, reason=reason)


def resume(project: str, slug: str, actor: str = "altd", **ev) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["blocked_reason"] = None
        return _move(project, task, "running", actor, **ev)


def done(project: str, slug: str, actor: str = "l3", digest: str = "") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        d = S.task_dir(project, slug)
        task = _move(project, task, "done", actor)
        if digest:
            S.atomic_write(d / "digest.md", digest.rstrip() + "\n")
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def _archive(project: str, slug: str) -> None:
    src = S.tasks_dir(project) / slug
    if src.is_dir():
        dst = S.archive_dir(project) / slug
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def set_spend(project: str, slug: str, **spend) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.setdefault("spend", {}).update(spend)
        S.save_task(project, task)
        return task


# ---- Decisions and FYIs (the two outbound channels, decision 9) --------------

def fyi(project: str, slug: str | None, text: str, actor: str = "l3") -> dict:
    """An FYI is a line in the project's inbox.jsonl; the page shows the tail."""
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
        if t["state"] == "blocked" and not t.get("resume_after"):  # an operational hold is not a user decision
            out.append({"project": project, "slug": t["slug"], "title": t["title"],
                        "question": f"Stopped mid-task: {short_reason(t.get('blocked_reason') or 'no reason recorded')}",
                        "options": ["Resume", "Park", "Reject"], "asked": t.get("updated"), "kind": "blocked",
                        "detail": t.get("blocked_reason")})
    return out


def set_hold_merge(project: str, slug: str, why: str | None, actor: str = "l3") -> dict:
    """PRs merge by default; a hold is an explicit, reasoned exception."""
    why = (why or "").strip() or None
    with S.project_lock(project):
        t = S.load_task(project, slug)
        t["hold_merge"] = why
        S.save_task(project, t)
    S.append_event(project, slug, "hold-merge" if why else "release-merge", why=why, actor=actor)
    return t
