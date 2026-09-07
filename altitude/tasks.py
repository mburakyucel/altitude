"""Task lifecycle — queued, running, blocked, reported, then archived. Every transition goes through here."""
from __future__ import annotations
import json
import os
import re
import subprocess
import uuid
from datetime import datetime

from . import config, github_intake, state as S, usage

def short_reason(reason: str, limit: int = 200) -> str:
    """The first sentence of a block reason, for the card; the whole reason stays in detail."""
    first = re.split(r"(?<=[.!?])\s|\s[—–-]\s|:\s`", reason.strip(), maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"

TRANSITIONS = {
    "queued": {"running", "blocked", "rejected"},               # blocked: a dispatch-time fault
    "running": {"reported", "blocked", "rejected", "done"},
    "blocked": {"running", "queued", "rejected", "reported"},   # queued: resumed before any launch
    "reported": {"done", "running", "blocked", "rejected"},      # running: verifier says not done → resume
    "done": set(),
    "rejected": set(),
}

class TransitionError(Exception):
    pass


TASK_MESSAGE_ROLES = ("burak", "l2", "l3")
OPERATOR_MESSAGE_ROLE = TASK_MESSAGE_ROLES[0]
_UNSET = object()


def _require_daemon_fence(task: dict, slug: str, *, expected_daemon_request: str | None = None,
                          expected_agent_id: object = _UNSET, expected_session_id: object = _UNSET) -> None:
    """Fence a task transition against one pending/executing operator request.

    I-20260904-062512: the check runs under the project lock held by every caller below. Only the daemon runner
    naming the executing request may advance it, and a stop/reject must still name the worker the caller observed.
    """
    request = task.get("daemon_request") or {}
    active = request.get("status") in ("pending", "executing")
    if expected_daemon_request is None:
        if active:
            raise TransitionError(f"{slug}: daemon request {request.get('id')} owns this task")
        return
    if (request.get("id") != expected_daemon_request or request.get("status") != "executing"):
        raise TransitionError(f"{slug}: daemon request {expected_daemon_request} is no longer executing")
    if expected_agent_id is not _UNSET and task.get("agent_id") != expected_agent_id:
        raise TransitionError(f"{slug}: worker identity changed")
    if expected_session_id is not _UNSET and task.get("session_id") != expected_session_id:
        raise TransitionError(f"{slug}: worker identity changed")


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _save_claim_task(project: str, task: dict) -> None:
    """Persist resume-transaction bookkeeping as one direct atomic status write."""
    task["updated"] = S.now()
    S.write_json(S.status_path(project, task["slug"]), task)


def _rows(path: Path, what: str) -> list[dict]:
    """Read one JSONL file of messages, failing loudly on a corrupt record."""
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"corrupt {what} in {path} at line {number}: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row.get("id"):
            raise ValueError(f"corrupt {what} in {path} at line {number}: invalid message")
        rows.append(row)
    return rows


def message(project: str, slug: str, role: str, text: str, *, by: str | None = None,
            expected_attempt: int | None = None, wake_blocked: bool = True) -> dict:
    """Append one message to the task conversation. Burak's and L3's messages also wait in the task's inbox until
    the worker reads them at its next checkpoint. An L2 names its attempt, so a worker of an earlier attempt cannot speak for
    the current one."""
    if role not in TASK_MESSAGE_ROLES:
        raise TransitionError(f"task message role must be one of {TASK_MESSAGE_ROLES}")
    text = str(text or "").strip()
    if not text:
        raise TransitionError("task message is empty")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        allowed = ("running", "blocked", "reported") if role == "l2" else ("running", "blocked")
        if task.get("state") not in allowed:
            raise TransitionError(f"{slug}: cannot message the L2 in {task.get('state')} state")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        row = {"id": uuid.uuid4().hex, "at": S.now(), "role": role, "text": text, "by": by or role}
        d = S.task_dir(project, slug)
        _append_jsonl(d / "conversation.jsonl", row)
        if role in ("burak", "l3"):  # an answer waits in the inbox until the worker reads it
            _append_jsonl(d / "inbox.jsonl", row)
            # I-20260904-062512: the durable inbox is also altd's handoff. The coordinator must not run the
            # provenance gate itself because its deployment-checkout Git metadata is deliberately read-only.
            if task.get("state") == "blocked" and wake_blocked:
                task["resume_request"] = row["id"]
                task["resume_after"] = task.get("resume_after") or S.now()
                task.pop("resume_failed", None)
                S.save_task(project, task)
                S.append_event(project, slug, "resume-requested", by=row["by"], reason="task message",
                               message_id=row["id"])
                S.regen_state_md(project)
        S.append_event(project, slug, "task-message", message_id=row["id"], role=role, by=row["by"])
        return row


def enqueue(project: str, slug: str, text: str, *, by: str = "altitude") -> dict:
    """Leave control-plane text for the worker's next checkpoint without a conversation entry."""
    row = {"id": uuid.uuid4().hex, "at": S.now(), "text": text, "by": by}
    with S.project_lock(project):
        _append_jsonl(S.task_dir(project, slug) / "inbox.jsonl", row)
    return row


def task_messages(project: str, slug: str, limit: int | None = None) -> list[dict]:
    """The durable task conversation."""
    rows = _rows(S.task_dir(project, slug) / "conversation.jsonl", "task conversation")
    if any(row.get("role") not in TASK_MESSAGE_ROLES for row in rows):
        raise ValueError(f"corrupt task conversation of {project}/{slug}: invalid role")
    if limit is not None:
        count = max(0, int(limit))
        rows = rows[-count:] if count else []
    return rows


def pending(project: str, slug: str) -> list[dict]:
    """What waits for the worker's next checkpoint."""
    return _rows(S.task_dir(project, slug) / "inbox.jsonl", "task inbox")


def claim_resume(project: str, slug: str, *, expected_daemon_request: str | None = None,
                 expected_agent_id: object = _UNSET,
                 expected_session_id: object = _UNSET) -> dict | None:
    """Persist one daemon-owned resume and remove its exact inbox batch before the provider can see hooks.

    The rows live in the claim until the task binds or the claim is released, so a failure can put them back
    without racing messages appended after this snapshot. ``dispatching`` makes the same claim visible to the
    independent restart guard as well as altd's in-process keyed runner.
    """
    path = S.task_dir(project, slug) / "inbox.jsonl"
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id)
        if task.get("state") != "blocked" or task.get("resume_claim"):
            return None
        rows = _rows(path, "task inbox")
        claim = {"id": uuid.uuid4().hex, "at": S.now(), "owner_pid": os.getpid(), "phase": "claimed",
                 "request": task.get("resume_request"), "resume_after": task.get("resume_after"), "messages": rows}
        task.update({"resume_claim": claim, "dispatching": claim["at"]})
        _save_claim_task(project, task)
        path.unlink(missing_ok=True)
        return claim


def mark_resume_held(project: str, slug: str, hold: str, *,
                     retry_at: str | None = None,
                     expected_daemon_request: str | None = None,
                     expected_agent_id: object = _UNSET,
                     expected_session_id: object = _UNSET) -> dict:
    """Keep a blocked resume due without letting an old request annotate a replacement worker."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id)
        if task.get("state") != "blocked":
            raise TransitionError(f"{slug}: expected blocked, found {task.get('state')}")
        after = retry_at or task.get("resume_after") or S.now()
        if task.get("resume_after") == after and task.get("blocked_reason") == f"waiting: {hold}":
            return task
        task["resume_after"] = after
        task["blocked_reason"] = f"waiting: {hold}"
        S.save_task(project, task)
        S.append_event(project, slug, "resume-held", hold=hold)
        return task


def update_resume_claim(project: str, slug: str, claim_id: str, **updates) -> dict:
    """Advance only the claim this daemon owns; a lifecycle race fails closed before provider binding."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        claim = task.get("resume_claim") or {}
        if task.get("state") != "blocked" or claim.get("id") != claim_id:
            raise TransitionError(f"{slug}: resume claim {claim_id} is no longer current")
        claim.update(updates)
        task["resume_claim"] = claim
        _save_claim_task(project, task)
        return claim


def release_resume_claim(project: str, slug: str, claim_id: str, *, consume_request: bool,
                         suppress_retry: bool = False) -> bool:
    """Release one failed claim, restoring its batch ahead of messages that arrived while it ran."""
    path = S.task_dir(project, slug) / "inbox.jsonl"
    with S.project_lock(project):
        task = S.load_task(project, slug)
        claim = task.get("resume_claim") or {}
        if claim.get("id") != claim_id:
            return False
        claimed = claim.get("messages") or []
        current = _rows(path, "task inbox")
        seen = {row["id"] for row in claimed}
        rows = claimed + [row for row in current if row["id"] not in seen]
        if rows:
            S.atomic_write(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
        else:
            path.unlink(missing_ok=True)
        task.pop("resume_claim", None)
        task["dispatching"] = None
        if suppress_retry:
            task.pop("resume_after", None)
            task.pop("resume_request", None)
            task["resume_failed"] = claim_id
        elif consume_request:
            same_request = (claim.get("request") is not None
                            and task.get("resume_request") == claim.get("request"))
            same_timer = (claim.get("request") is None and not task.get("resume_request")
                          and task.get("resume_after") == claim.get("resume_after"))
            if same_request or same_timer:
                task.pop("resume_after", None)
                task.pop("resume_request", None)
                task["resume_failed"] = claim_id
        _save_claim_task(project, task)
        S.regen_state_md(project)
        return True


def take_inbox(project: str, slug: str, ids: set[str] | None = None) -> list[dict]:
    """Remove delivered messages from the inbox (all of them, or only `ids`) and return them."""
    path = S.task_dir(project, slug) / "inbox.jsonl"
    with S.project_lock(project):
        rows = _rows(path, "task inbox")
        taken = [row for row in rows if ids is None or row["id"] in ids]
        left = [row for row in rows if row not in taken]
        if left:
            S.atomic_write(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in left))
        else:
            path.unlink(missing_ok=True)
    return taken


def render_inbox(rows: list[dict]) -> str:
    """The messages as the worker reads them."""
    return "\n\n".join(f"Message from {str(row.get('by') or 'burak').capitalize()} ({row.get('at') or ''}):\n{row['text']}"
                       for row in rows)


def _clear_block(task: dict) -> None:
    task["blocked_reason"] = None
    for key in ("resume_after", "resume_request", "resume_claim", "resume_failed", "waiting_on", "fault", "escalated"):
        task.pop(key, None)


def _move(project: str, task: dict, to: str, actor: str, **ev) -> dict:
    frm = task["state"]
    if to not in TRANSITIONS.get(frm, set()):
        raise TransitionError(f"{task['slug']}: {frm} → {to} is not allowed")
    usage.remember(task)
    if to == "rejected":
        usage.capture(project, task)
    stopped = None
    if to == "rejected" and frm in ("running", "blocked") and task.get("agent_id"):
        from . import engines
        engine = task.get("l2_engine") or "claude"
        try:
            note = engines.remove_l2_worker(
                engine, task["agent_id"], job_root=S.task_dir(project, task["slug"]) / "l2-engine")
        except Exception as exc:
            raise TransitionError(
                f"{task['slug']}: cannot reject while its {engine} worker may still be live: {exc}"
            ) from exc
        stopped = (engine, note)
    task["state"] = to
    if to in ("blocked", "reported", "done", "rejected"):
        usage.capture(project, task, final=to in ("done", "rejected"))
    if to == "running":
        task["dispatching"] = None
    S.save_task(project, task)
    S.append_event(project, task["slug"], "state", frm=frm, to=to, by=actor, **ev)
    if stopped:
        engine, note = stopped
        S.append_event(project, task["slug"], "session-stopped", agent_id=task["agent_id"],
                       engine=engine, note=note[:200])
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
    config.project(project)
    try:
        issue = github_intake.inline(project, title, request)
    except github_intake.IssueIntakeError as exc:
        raise TransitionError(f"GitHub issue intake failed: {exc}") from exc
    if issue:
        request = request.rstrip() + "\n\n" + issue
    with S.project_lock(project):
        config.project(project)
        base = S.slugify(title)
        slug, n = base, 1
        while S.task_dir(project, slug).exists():
            n += 1
            slug = f"{base}-{n}"
        d = S.tasks_dir(project) / slug
        d.mkdir(parents=True)
        S.atomic_write(d / "request.md", request.rstrip() + "\n")
        task = {"slug": slug, "title": title, "state": "queued", "created": S.now(),
                "attempt": 0, "session_id": None, "agent_id": None,
                "worktree": None,
                "branch": None, "prs": [], "spend": {}, "blocked_reason": None, "source": source,
                "verified": None, "model": model, "engine": engine, "l2_engine": None,
                "engine_model": None, "routing": None,
                "paths": [p.strip() for p in (paths or []) if p.strip()],
                "hold_merge": (hold_merge or "").strip() or None}
        S.save_task(project, task)
        S.append_event(project, slug, "new", by=actor, title=title, source=source, queued=True)
        S.regen_state_md(project)
        return task


def reject(project: str, slug: str, reason: str, actor: str = "burak", *,
           expected_state: str | None = None, expected_agent_id: object = _UNSET,
           expected_session_id: object = _UNSET, expected_daemon_request: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id, expected_session_id=expected_session_id)
        if expected_state is not None and task.get("state") != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task.get('state')}")
        _clear_block(task)
        task = _move(project, task, "rejected", actor, reason=reason)
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def brief(project: str, slug: str, brief_md: str, actor: str = "l3") -> Path:
    with S.project_lock(project):
        d = S.task_dir(project, slug)
        S.atomic_write(d / "brief.md", brief_md.rstrip() + "\n")
        S.append_event(project, slug, "brief", by=actor, bytes=len(brief_md))
        return d / "brief.md"


def dispatch(project: str, slug: str, *, attempt: int, session_id: str | None, agent_id: str | None,
             worktree: str | None, branch: str | None, l2_engine: str = "claude",
             engine_model: str | None = None, routing: str | None = None, actor: str = "altd") -> dict:
    if not session_id or not agent_id:
        raise TransitionError(f"{slug}: dispatch requires a concrete worker and session")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug)
        usage.remember(task)
        task.update({"attempt": attempt, "session_id": session_id, "agent_id": agent_id, "worktree": worktree,
                     "branch": branch, "blocked_reason": None, "l2_engine": l2_engine, "engine_model": engine_model,
                     "routing": routing, "dispatched": S.now()})
        return _move(project, task, "running", actor, attempt=attempt, session_id=session_id)


def report(project: str, slug: str, verified: dict, actor: str = "altd", *,
           expected_state: str | None = None, expected_attempt: int | None = None,
           expected_block_from: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug)
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
        _clear_block(task)
        if verified.get("prs"):
            task["prs"] = sorted(set(task.get("prs", []) + list(verified["prs"])))
        return _move(project, task, "reported", actor, verdict=verified.get("verdict"))


def block(project: str, slug: str, reason: str, actor: str = "altd", *,
          expected_state: str | None = None, expected_attempt: int | None = None, updates: dict | None = None,
          expected_agent_id: object = _UNSET, expected_session_id: object = _UNSET,
          expected_daemon_request: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id, expected_session_id=expected_session_id)
        if expected_state is not None and task.get("state") != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task.get('state')}")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        task.update(updates or {})
        task["blocked_reason"] = reason
        return _move(project, task, "blocked", actor, reason=reason)


def resume(project: str, slug: str, actor: str = "altd", *, agent_id: str | None = None,
           session_id: str | None = None, expected_claim: str | None = None,
           expected_daemon_request: str | None = None, expected_agent_id: object = _UNSET,
           expected_session_id: object = _UNSET, **ev) -> dict:
    """blocked → running. With a worker, the task is bound to it; a resumed Claude session may carry a new id."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id)
        if expected_claim is not None and (task.get("resume_claim") or {}).get("id") != expected_claim:
            raise TransitionError(f"{slug}: resume claim {expected_claim} is no longer current")
        if agent_id:
            usage.remember(task)
            task.update({"agent_id": agent_id, "session_id": session_id or task.get("session_id")})
        _clear_block(task)
        return _move(project, task, "running", actor, **ev)


def requeue(project: str, slug: str, actor: str = "altd", *, engine: str | None = None,
            clear_worker: bool = False, expected_daemon_request: str | None = None,
            expected_agent_id: object = _UNSET, expected_session_id: object = _UNSET, **ev) -> dict:
    """blocked → queued: a task blocked before any launch, or a fresh attempt after a worker's window ran out.

    The next dispatch routes by quota again unless ``engine`` names the one to use; ``clear_worker`` drops the
    exhausted worker's identity so the fresh attempt starts from the task's saved progress, not its transcript."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id)
        if task.get("agent_id") and not clear_worker:
            raise TransitionError(f"{slug}: has an L2 worker; resume it instead")
        usage.capture(project, task)
        task.update({"agent_id": None, "session_id": None, "l2_engine": engine, "engine_model": None, "routing": None})
        _clear_block(task)
        return _move(project, task, "queued", actor, **ev)


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
         expected_state: str | None = None, expected_attempt: int | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug)
        if expected_state is not None and task.get("state") != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task.get('state')}")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        if actor == "l2":  # archived by the server once this worker has exited
            if task.get("state") != "running":
                raise TransitionError(f"{slug}: L2 can complete only its running task")
            _require_no_code_change(task)
            task["completion_requested"] = {"at": S.now(), "digest": digest}
            S.save_task(project, task)
            S.append_event(project, slug, "completion-requested", by=actor)
            return task
        if task.get("state") == "running":
            raise TransitionError(f"{slug}: cannot complete a running worker; wait for its report or stop/reject it")
        d = S.task_dir(project, slug)
        task = _move(project, task, "done", actor)
        if digest:
            S.atomic_write(d / "digest.md", digest.rstrip() + "\n")
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def finalize_completion(project: str, slug: str, actor: str = "altd") -> dict:
    """Archive a no-code L2 completion once its worker has exited."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        request = task.pop("completion_requested", None)
        if task.get("state") != "running" or not request:
            raise TransitionError(f"{slug}: no completion to finalize")
        _require_no_code_change(task)
        digest = str(request.get("digest") or "")
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
        dst = S.archive_dir(project) / slug
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def set_spend(project: str, slug: str, **spend) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.setdefault("spend", {}).update(spend)
        S.save_task(project, task)
        return task


# ---- User decisions and FYIs -------------------------------------------------

def fyi(project: str, slug: str | None, text: str, actor: str = "l3") -> dict:
    """An FYI is a line in the project's inbox.jsonl; the page shows the tail."""
    if slug is not None:
        S.require_task_slug(slug)
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
    """Tasks blocked on Burak: an L2's block flagged for him, L3's escalation, or a block from before L3 saw
    blocks first. A block waiting on L3, or on a timed hold, is Altitude's wait, not a decision."""
    out = []
    for t in S.list_tasks(project):
        if t["state"] == "blocked" and not t.get("resume_after") and t.get("waiting_on", "burak") == "burak":
            who = "L3 asks" if t.get("escalated") else "Stopped mid-task"
            out.append({"project": project, "slug": t["slug"], "title": t["title"],
                        "question": f"{who}: {short_reason(t.get('blocked_reason') or 'no reason recorded')}",
                        "options": ["Resume", "Reject"], "asked": t.get("updated"), "kind": "blocked",
                        "detail": t.get("blocked_reason")})
    return out


def block_question(slug: str, reason: str) -> str:
    """The message L3 receives when an L2 blocks: answer from the record, or hand Burak one plain dilemma."""
    return (f"Task `{slug}` blocked and asks: {reason[:800]}\n\n"
            f"Read `alt task messages {slug}` and `alt task show {slug}`. When the brief, the docs, or a recorded "
            f"decision settles it, answer with `alt task message {slug} \"<answer>\"`; that resumes the task. When the "
            "call is Burak's (taste, priorities, spend, a paradigm decision, anything the brief marked as his), or the "
            f"L2 is insisting on a point you already answered, run `alt task escalate {slug} --question \"<one plain "
            "dilemma with your recommendation>\"`. Reply in one or two plain sentences.")


def escalate(project: str, slug: str, question: str, actor: str = "l3") -> dict:
    """L3 hands a blocked task's question to Burak as one plain dilemma; the L2's own words stay in the events."""
    question = (question or "").strip()
    if not question:
        raise TransitionError("escalation needs the question")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") != "blocked":
            raise TransitionError(f"{slug} is {task.get('state')}, not blocked")
        task.update({"waiting_on": "burak", "escalated": True, "blocked_reason": question})
        S.save_task(project, task)
    S.append_event(project, slug, "escalated", by=actor, question=question)
    return task


def set_hold_merge(project: str, slug: str, why: str | None, actor: str = "l3") -> dict:
    """PRs merge by default; a hold is an explicit, reasoned exception."""
    why = (why or "").strip() or None
    if why is None and actor != "burak":
        raise TransitionError("only Burak may release a merge hold")
    with S.project_lock(project):
        t = S.load_task(project, slug)
        t["hold_merge"] = why
        t["hold_merge_id"] = uuid.uuid4().hex
        S.save_task(project, t)
        S.append_event(project, slug, "hold-merge" if why else "release-merge", why=why, actor=actor,
                       hold_id=t["hold_merge_id"])
    return t


def apply_merge_approval(project: str, slug: str, approval: str, pull: dict, *, head: str,
                         reason: str, actor: str) -> dict:
    """Apply recorded operator authority; altd supplies the origin-bound GitHub observation.

    I-20260907-205556: no caller prose grants approval. The exact operator reply must follow the
    current hold and an unambiguous PR presentation, with no later operator message or PR update.
    """
    if actor != "l3" or not reason.strip():
        raise TransitionError("recorded approval requires the coordinator daemon and a reason")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        try:
            if not task.get("hold_merge") or task["state"] not in ("running", "blocked", "reported"):
                raise ValueError("task has no active merge hold")
            rows = task_messages(project, slug)
            operator = next((r for r in reversed(rows) if r["role"] == OPERATOR_MESSAGE_ROLE), {})
            if (operator.get("id") != approval or operator.get("by") != OPERATOR_MESSAGE_ROLE
                    or operator.get("text") != "Good to merge"):
                raise ValueError("approval must name the latest operator message, exactly 'Good to merge'")
            previous = rows[:rows.index(operator)]
            presentation = previous[-1] if previous else {}
            urls = re.findall(r"https://github\.com/[\w.-]+/[\w.-]+/pull/[1-9][0-9]*\b",
                              presentation.get("text", ""))
            if presentation.get("role") != "l2" or set(urls) != {pull["url"]}:
                raise ValueError("approval must directly follow the owner's presentation of this PR alone")
            events = [json.loads(line) for line in (S.task_dir(project, slug) / "events.log").read_text().splitlines()
                      if line.strip()]  # a corrupt later hold must not disappear from authorization evidence
            generation = next((i for i in range(len(events) - 1, -1, -1)
                               if events[i]["kind"] in ("new", "hold-merge", "release-merge")), None)
            hold = events[generation] if generation is not None else {}
            if (hold.get("kind") not in ("new", "hold-merge")
                    or task.get("hold_merge_id") != hold.get("hold_id")
                    or hold["kind"] == "hold-merge" and hold.get("why") != task["hold_merge"]):
                raise ValueError("current hold has no matching recorded generation")
            def timestamp(value):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("approval evidence needs timezone-aware timestamps")
                return parsed
            if not (timestamp(hold["at"]) < timestamp(presentation["at"]) < timestamp(operator["at"])
                    and timestamp(pull["updatedAt"]) < timestamp(presentation["at"])):
                raise ValueError("approval is stale: hold or PR changed since its presentation")
            if (pull.get("state") != "OPEN" or pull.get("isDraft") is not False
                    or pull.get("isCrossRepository") is not False or pull.get("baseRefName") != "main"
                    or pull.get("headRefName") != task.get("branch") or pull.get("headRefOid") != head):
                raise ValueError("approval PR must be open, ready, and match the task branch and observed head")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            S.append_event(project, slug, "merge-approval-refused", actor=actor, approval=approval,
                           reason=reason, error=str(exc))
            raise TransitionError(f"recorded merge approval refused: {exc}") from exc
        receipt = {"actor": actor, "authorized_by": operator["role"], "reason": reason,
                   "approval": approval, "approved_at": operator["at"],
                   "hold": task["hold_merge"], "hold_event": generation, "hold_at": hold["at"],
                   "hold_id": task.get("hold_merge_id"),
                   "presentation": presentation["id"], "pr": pull["number"], "url": pull["url"],
                   "head": head, "pr_updated_at": pull["updatedAt"], "at": S.now()}
        task.update(hold_merge=None, merge_approval=receipt)
        S.save_task(project, task)
        S.append_event(project, slug, "release-merge", **receipt)
        return receipt
