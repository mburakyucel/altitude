"""Task lifecycle — queued, running, blocked, reported, then archived. Every transition goes through here."""
from __future__ import annotations
import json
import hashlib
import os
import re
import stat
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

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

# Pending designs are selected raster captures, never executable worktree documents.
DESIGN_IMAGE_LIMIT = 8 << 20
DESIGN_TOTAL_LIMIT = 32 << 20
DESIGN_TEXT_LIMIT = 64 << 10
DESIGN_IMAGE_COUNT = 12


@contextmanager
def _design_directory(root: Path, parts: list[str], *, create: bool = False):
    """Walk relative to an open root without following any symlink, including racing replacements."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if not part or part in (".", "..") or "/" in part or "\\" in part:
                raise ValueError("invalid design path")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _design_bytes(root: Path, relative: str, limit: int) -> bytes:
    parts = relative.split("/")
    if any(not p or p in (".", "..") or "\\" in p for p in parts):
        raise ValueError("invalid design path")
    with _design_directory(root, parts[:-1]) as directory:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("design file is not a bounded regular file")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
    if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("design file changed during capture")
    return data


def _design_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _capture_design(project: str, task: dict, selection: dict) -> tuple[dict, dict[str, bytes]]:
    """Capture exactly the owner's named screens and explanation; no recursive directory publication."""
    if not isinstance(selection, dict) or set(selection) != {"title", "proposal", "images"}:
        raise TransitionError("design JSON requires title, proposal and images")
    title, images = selection["title"], selection["images"]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        raise TransitionError("design title needs 1–160 characters")
    if not isinstance(images, list) or not 1 <= len(images) <= DESIGN_IMAGE_COUNT:
        raise TransitionError(f"select 1–{DESIGN_IMAGE_COUNT} design screenshots")
    root = config.project_path(project).resolve()
    worktree = Path(task.get("worktree") or root).absolute()
    if not worktree.is_relative_to(root) or worktree == root or worktree.name != task["slug"]:
        raise TransitionError("design capture requires this task's registered worktree")
    relative_worktree = worktree.relative_to(root)

    def read(path, extensions, limit):
        if (not isinstance(path, str) or not path.startswith("design/wireframes/")
                or Path(path).suffix.lower() not in extensions):
            raise ValueError("select supported files under design/wireframes")
        return _design_bytes(root, f"{relative_worktree}/{path}", limit)

    try:
        text = read(selection["proposal"], (".md", ".txt"), DESIGN_TEXT_LIMIT).decode("utf-8")
        if not text.strip():
            raise ValueError("the proposal text is empty")
        captured, files, total = [], {}, 0
        for item in images:
            if (not isinstance(item, dict) or set(item) != {"title", "path"}
                    or not isinstance(item["title"], str) or not 1 <= len(item["title"].strip()) <= 160):
                raise ValueError("each screenshot needs a title and path")
            data = read(item["path"], (".png", ".jpg", ".jpeg"), DESIGN_IMAGE_LIMIT)
            suffix = Path(item["path"]).suffix.lower()
            if suffix == ".png":
                valid = data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR") and data.endswith(b"IEND\xaeB`\x82")
            else:
                valid = data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
                suffix = ".jpg"
            if not valid:
                raise ValueError("screenshots must contain PNG or JPEG image data")
            total += len(data)
            if total > DESIGN_TOTAL_LIMIT:
                raise ValueError("selected screenshots exceed 32 MiB")
            digest = hashlib.sha256(data).hexdigest()
            name = digest + suffix
            files[name] = data
            captured.append({"title": item["title"].strip(), "name": name, "size": len(data)})
    except (OSError, ValueError, UnicodeError) as exc:
        raise TransitionError(f"design capture unavailable: {exc}") from exc
    design = {"title": title.strip(), "text": text, "images": captured}
    return {**design, "id": _design_hash(design)}, files


def _save_design(project: str, slug: str, files: dict[str, bytes]) -> None:
    relative = (S.task_dir(project, slug) / "designs").relative_to(config.ROOT)
    with _design_directory(config.ROOT, list(relative.parts), create=True) as directory:
        for name, data in files.items():
            try:
                saved = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(saved.st_mode):
                    raise ValueError("saved design path is not a regular file")
                if saved.st_size <= DESIGN_IMAGE_LIMIT and _design_bytes(config.ROOT, str(relative / name), DESIGN_IMAGE_LIMIT) == data:
                    continue
            except FileNotFoundError:
                pass
            temporary = f".{uuid.uuid4().hex}.tmp"
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Restoring the same digest's selected bytes preserves the published proposal identity.
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        os.fsync(directory)


def design_image(project: str, slug: str, design: dict, name: str) -> bytes:
    image = next((item for item in design["images"] if item["name"] == name), None)
    if image is None or not re.fullmatch(r"[a-f0-9]{64}\.(png|jpg)", name):
        raise ValueError("design image unavailable")
    relative = (S.task_dir(project, slug) / "designs" / name).relative_to(config.ROOT)
    data = _design_bytes(config.ROOT, str(relative), DESIGN_IMAGE_LIMIT)
    if len(data) != image["size"] or hashlib.sha256(data).hexdigest() != name.split(".")[0]:
        raise ValueError("saved design image was altered")
    return data


def task_design(project: str, slug: str, identity: str, revision: int) -> tuple[dict, dict]:
    """Resolve a fixed proposal through its existing question; missing evidence never selects another version."""
    config.project(project)
    task = S.load_task(project, slug)
    question = _question_target(task, identity, revision)
    design = question.get("design")
    if not design or design["id"] != _design_hash({k: v for k, v in design.items() if k != "id"}):
        raise ValueError("saved design unavailable")
    return task, question


def require_design(project: str, slug: str, question: dict) -> None:
    """A decision cannot accept missing or changed captured evidence."""
    if not question.get("design"):
        return
    try:
        _, saved = task_design(project, slug, question["id"], question["revision"])
        for image in saved["design"]["images"]:
            design_image(project, slug, saved["design"], image["name"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TransitionError("Design unavailable. Return to the question and ask the L2 to restore the saved preview.") from exc


def design_url(project: str, slug: str, question: dict) -> str:
    return f"/projects/{quote(project, safe='')}/tasks/{slug}/design/{question['id']}/{question['revision']}"


def ci_recheck_identity(task: dict) -> dict:
    return {**{key: task.get(key) for key in (
        "state", "block_id", "attempt", "agent_id", "session_id", "l2_engine", "fault",
        "blocked_reason", "resume_request", "resume_after", "dispatching")},
        "daemon_request_id": (task.get("daemon_request") or {}).get("id"),
        "resume_claim_id": (task.get("resume_claim") or {}).get("id")}


def ci_recheck_current(task: dict, record: dict) -> bool:
    return (task.get("state") == "blocked" and bool(task.get("fault"))
            and record.get("identity") == ci_recheck_identity(task))


def recheck_ci(project: str, slug: str, run: int, at: str, reason: str, *, actor: str) -> dict:
    """The stalled CI recovery owner (2026-09-09): one finite coordinator probe, never a resume."""
    if actor not in ("l3", OPERATOR_MESSAGE_ROLE) or not str(reason or "").strip():
        raise TransitionError("CI recheck requires L3 or the operator and a reason")
    try:
        due = datetime.fromisoformat(at)
        if due.tzinfo is None or isinstance(run, bool) or int(run) <= 0:
            raise ValueError()
        due = due.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise TransitionError("CI recheck requires a positive run id and an ISO time with timezone") from None
    with S.project_lock(project):
        task = S.load_task(project, slug)
        previous = task.get("ci_recheck") or {}
        identity = ci_recheck_identity(task)
        same = (previous.get("identity"), previous.get("run"), previous.get("at"), previous.get("reason")) == (
            identity, int(run), due.isoformat(), reason.strip())
        if same:
            return previous
        if task["state"] != "blocked" or not task.get("fault"):
            raise TransitionError("CI recheck requires an existing fault-blocked task")
        if (task.get("resume_after") or task.get("resume_claim") or task.get("dispatching")
                or (task.get("daemon_request") or {}).get("status") in ("pending", "executing")):
            raise TransitionError("CI recheck cannot target a pending task lifecycle change")
        if previous.get("status") in ("pending", "probing", "notifying") and ci_recheck_current(task, previous):
            raise TransitionError("this task already has a CI recheck; inspect task status")
        now = datetime.fromisoformat(S.now())
        if not now <= due <= now + timedelta(days=7):
            raise TransitionError("CI recheck time must be within the next seven days")
        record = {"id": uuid.uuid4().hex, "actor": actor, "requested_at": S.now(), "identity": identity,
                  "run": int(run), "at": due.isoformat(), "due_at": due.isoformat(), "reason": reason.strip(),
                  "status": "pending", "reads": 0, "deadline": (due + timedelta(hours=2)).isoformat()}
        if ci_recheck_current(task, previous) and previous.get("observation"):
            record["previous_observation"] = previous["observation"]
        task["ci_recheck"] = record
        S.save_task(project, task)
        S.append_event(project, slug, "ci-recheck", request_id=record["id"], by=actor,
                       reason=record["reason"], run=run, due_at=record["due_at"])
        return record


def _conversation_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_daemon_fence(task: dict, slug: str, *, expected_daemon_request: str | None = None,
                          expected_agent_id: object = _UNSET, expected_session_id: object = _UNSET,
                          expected_block_id: object = _UNSET) -> None:
    """Fence a task transition against one pending/executing operator request.

    I-20260904-062512: the check runs under the project lock held by every caller below. Only the daemon runner
    naming the executing request may advance it, and a stop/reject must still name the worker the caller observed.
    """
    if expected_block_id is not _UNSET and task.get("block_id") != expected_block_id:
        raise TransitionError(f"{slug}: block changed before resume")
    if expected_agent_id is not _UNSET and task.get("agent_id") != expected_agent_id:
        raise TransitionError(f"{slug}: worker identity changed")
    if expected_session_id is not _UNSET and task.get("session_id") != expected_session_id:
        raise TransitionError(f"{slug}: worker identity changed")
    request = task.get("daemon_request") or {}
    active = request.get("status") in ("pending", "executing")
    if expected_daemon_request is None:
        if active:
            raise TransitionError(f"{slug}: daemon request {request.get('id')} owns this task")
        return
    if (request.get("id") != expected_daemon_request or request.get("status") != "executing"):
        raise TransitionError(f"{slug}: daemon request {expected_daemon_request} is no longer executing")
    if request.get("operation") == "handoff" and request.get("attempt") != task.get("attempt"):
        raise TransitionError(f"{slug}: attempt changed after handoff request")
    if request.get("block_id") != task.get("block_id"):
        raise TransitionError(f"{slug}: block changed after daemon request")


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
    try:
        contents = path.read_text()
    except FileNotFoundError:
        return []
    rows = []
    for number, line in enumerate(contents.splitlines(), 1):
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


def report_owner(task: dict) -> dict:
    """Identity of the work a report may conclude, including same-attempt continuation."""
    return {**{key: task.get(key) for key in ("attempt", "agent_id", "session_id", "worker_started_at",
                                            "block_id", "report_after")},
            "resume_claim": (task.get("resume_claim") or {}).get("id")}


def report_current(task: dict, path: Path) -> bool:
    """An earlier delivery or owner turn cannot report completion of follow-up work."""
    after = max(task.get("report_after") or "", task.get("worker_started_at") or "",
                (task.get("delivery") or {}).get("at", ""))
    return path.is_file() and (not after or path.stat().st_mtime >= datetime.fromisoformat(after).timestamp())


def reported_continuable(task: dict, report: dict | None) -> bool:
    """Local report evidence offers continuation; admission checks the PR's current state."""
    return bool(task.get("state") == "reported" and task.get("agent_id") and task.get("session_id")
                and task.get("worktree") and any(
                    pr.get("number") in task.get("prs", []) and pr.get("merged") is False
                    for pr in ((report or {}).get("landed") or {}).get("prs", [])))


def continue_report(project: str, task: dict, *, actor: str, reason: str, check_pr: bool = True) -> dict:
    """Under the project lock, retire completion evidence and enter the ordinary resume path."""
    from . import verify
    slug = task["slug"]
    _require_daemon_fence(task, slug)
    report = S.read_json(S.task_dir(project, slug) / "report.json", {})
    if check_pr:
        if not reported_continuable(task, report):
            raise TransitionError(f"{slug}: continuation requires a reported owner with an open PR")
        try:
            open_pr = any((verify.gh(["pr", "view", str(pr["number"]), "--json", "state"],
                                    config.project_path(project)) or {}).get("state") == "OPEN"
                          for pr in (report.get("landed") or {}).get("prs", [])
                          if pr.get("number") in task.get("prs", []) and pr.get("merged") is False)
        except verify.VerifierFault as exc:
            raise TransitionError(f"{slug}: cannot confirm open PR; message not sent: {exc}") from exc
        if not open_pr:
            raise TransitionError(f"{slug}: the reported PR is no longer open")
    S.append_event(project, slug, "report-superseded", report=report, verified=task.get("verified"), by=actor)
    task.pop("verified", None)
    task["report_after"] = datetime.now(timezone.utc).isoformat()
    task["l3_handled"] = None
    _supersede_resume(task)
    task["blocked_reason"] = reason
    if task["state"] == "blocked":
        S.save_task(project, task)
        S.regen_state_md(project)
        return task
    return _move(project, task, "blocked", actor, reason=reason)


def message(project: str, slug: str, role: str, text: str, *, by: str | None = None,
            expected_attempt: int | None = None, wake_blocked: bool = True,
            question_id: str | None = None, revision: int | None = None,
            group_id: str | None = None, group_revision: int | None = None,
            stop_id: str | None = None) -> dict:
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
        if stop_id is not None and stop_id != task.get("stop_id"):
            raise TransitionError("The stopped session changed. Refresh before sending this correction.")
        allowed = ("running", "blocked", "reported")
        if role != "l2" and task.get("questions"):
            allowed += ("queued",)  # a known dilemma remains discussable while its next attempt waits
        if task.get("state") not in allowed:
            raise TransitionError(f"{slug}: cannot message the L2 in {task.get('state')} state")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        if _ensure_question(project, task):
            S.save_task(project, task)
        row = {"id": uuid.uuid4().hex, "at": _conversation_time(), "role": role, "text": text, "by": by or role}
        # #277: a coordinator's waiting update is discussion, not evidence that the fault is repaired.
        wake_blocked = wake_blocked and not (role == "l3" and task.get("fault"))
        if not wake_blocked:
            row["wake"] = False
        groups = _groups(task)
        explicit_question = question_id is not None or revision is not None
        if explicit_question and (group_id is not None or group_revision is not None):
            raise TransitionError("message context names a question or a group, not both")
        if explicit_question:
            target = _question_target(task, question_id, revision)
            row.update(question_id=target["id"], question_revision=target["revision"],
                       question_refs=[{"id": target["id"], "revision": target["revision"]}],
                       question_context=question_context(target))
            if groups and any((q["id"], q["revision"]) != (target["id"], target["revision"])
                              for q in _group_members(task, groups[-1])):
                row["question_context"] += "\n\nCurrent task context:\n" + group_context(task)
        elif groups or group_id is not None or group_revision is not None:
            group = (_group_target(task, group_id, group_revision)
                     if group_id is not None or group_revision is not None else groups[-1])
            members = _group_members(task, group)
            row.update(group_id=group["id"], group_revision=group["revision"],
                       question_refs=[{"id": q["id"], "revision": q["revision"]} for q in members],
                       question_context=group_context(task, group))
            if len(members) == 1:
                row.update(question_id=members[0]["id"], question_revision=members[0]["revision"])
            if groups and group["id"] != groups[-1]["id"]:
                row["question_context"] += "\n\nCurrent task context:\n" + group_context(task)
        d = S.task_dir(project, slug)
        if role != "l2" and task.get("state") == "reported":
            task = continue_report(project, task, actor=role, reason="Follow-up message")
        _append_jsonl(d / "conversation.jsonl", row)
        if role in ("burak", "l3"):  # an answer waits in the inbox until the worker reads it
            _append_jsonl(d / "inbox.jsonl", row)
            if task.get("state") == "running" and (d / "report.json").exists():
                if report_current(task, d / "report.json"):
                    S.append_event(project, slug, "report-superseded", report=S.read_json(d / "report.json"),
                                   verified=task.get("verified"), by=role)
                task["report_after"] = datetime.now(timezone.utc).isoformat()
                task.pop("verified", None)
                S.save_task(project, task)
            # I-20260904-062512: the durable inbox is also altd's handoff. The coordinator must not run the
            # provenance gate itself because its deployment-checkout Git metadata is deliberately read-only.
            # #302: a send already in flight, or from a stale running tab, cannot undo Stop.
            continue_stopped = (task.get("stop_id") == stop_id and stop_id is not None
                                and steering_view(task, S.read_events(project, slug),
                                                  job_root=d / "l2-engine")["state"] == "stopped")
            if task.get("state") == "blocked" and wake_blocked and (not task.get("stop_id") or continue_stopped):
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
    # Question anchors and quick-accept messages are saved atomically with their question record.
    # Project them into the ordinary human thread without a second multi-file commit protocol.
    for question in S.load_task(project, slug).get("questions", []):
        rows.append(question["message"])
        if question.get("acceptance_message"):
            rows.append(question["acceptance_message"])
    rows = list({row["id"]: row for row in rows}.values())
    rows.sort(key=lambda row: row["at"])
    if any(row.get("role") not in TASK_MESSAGE_ROLES for row in rows):
        raise ValueError(f"corrupt task conversation of {project}/{slug}: invalid role")
    if limit is not None:
        count = max(0, int(limit))
        rows = rows[-count:] if count else []
    return rows


def steering_view(task: dict, events: list[dict], *, job_root=None) -> dict:
    """UI wording derives from the existing worker operation and its termination receipt."""
    request = task.get("daemon_request") or {}
    stop_id = task.get("stop_id")
    current_stop = request.get("operation") == "stop" and request.get("id") == stop_id
    active_request = request.get("status") in ("pending", "executing")
    confirmed = stop_id and any(e.get("kind") == "stopped" and e.get("stop_id") == stop_id for e in events)
    if stop_id and not confirmed and not active_request and job_root is not None:
        from . import engines
        confirmed = engines.worker_termination(task, job_root=job_root) is True
    state = "running" if task.get("state") == "running" else "idle"
    if stop_id and task.get("state") in ("running", "blocked"):
        if current_stop and active_request:
            state = "stopping"
        elif task.get("resume_after") or (request.get("operation") == "resume" and active_request):
            state = "resuming"
        elif confirmed:
            state = "stopped"
        else:
            state = "stop_unconfirmed"
    elif task.get("state") == "blocked" and task.get("resume_after"):
        state = "resuming"
    return {"state": state, "stop_id": stop_id, "generation": task.get("agent_id"),
            "error": "The worker may still be running." if state == "stop_unconfirmed" else None}


def message_views(project: str, slug: str, task: dict, delivered: list[dict]) -> list[dict]:
    """An inbox claim is still queued; only an evidenced session handoff is delivered."""
    receipts = {row["message_id"]: {"at": row.get("at")} for row in delivered}
    receipts.update(task.get("message_deliveries") or {})
    queued = {row["id"] for row in pending(project, slug)}
    queued.update(row["id"] for row in (task.get("resume_claim") or {}).get("messages", []))
    rows = task_messages(project, slug)
    for row in rows:
        if row["role"] not in (OPERATOR_MESSAGE_ROLE, "l3"):
            continue
        receipt = receipts.get(row["id"])
        row["delivery"] = {"state": "delivered" if receipt else "queued" if row["id"] in queued else "unconfirmed",
                           "at": receipt.get("at") if receipt else None}
    return rows


def pending(project: str, slug: str) -> list[dict]:
    """What waits for the worker's next checkpoint."""
    return _pending_rows(S.load_task(project, slug), S.task_dir(project, slug) / "inbox.jsonl")


def _pending_rows(task: dict, path: Path) -> list[dict]:
    rows = _rows(path, "task inbox")
    if task.get("state") not in ("running", "blocked", "queued"):
        return rows  # historical receipts do not create new delivery work after the owner hands off
    seen = {row["id"] for row in rows}
    for question in task.get("questions", []):
        message = question.get("acceptance_message")
        if message and not question.get("acceptance_delivered") and message["id"] not in seen:
            rows.append(message)
            seen.add(message["id"])
    return sorted(rows, key=lambda row: row["at"])


def _mark_acceptance_delivered(task: dict, ids: set[str]) -> None:
    for question in task.get("questions", []):
        if (question.get("acceptance_message") or {}).get("id") in ids:
            question["acceptance_delivered"] = True


def claim_resume(project: str, slug: str, *, expected_daemon_request: str | None = None,
                 expected_agent_id: object = _UNSET,
                 expected_session_id: object = _UNSET, expected_block_id: object = _UNSET) -> dict | None:
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
                              expected_session_id=expected_session_id, expected_block_id=expected_block_id)
        if task.get("state") != "blocked" or task.get("resume_claim"):
            return None
        _ensure_question(project, task)
        rows = _pending_rows(task, path)
        _mark_acceptance_delivered(task, {row["id"] for row in rows})
        claim = {"id": uuid.uuid4().hex, "at": S.now(), "owner_pid": os.getpid(), "phase": "claimed",
                 "block_id": task.get("block_id"),
                 "request": task.get("resume_request"), "resume_after": task.get("resume_after"), "messages": rows}
        task.update({"resume_claim": claim, "dispatching": claim["at"]})
        task.pop("verified", None)  # A resumed owner must report its current work before completion.
        _save_claim_task(project, task)
        path.unlink(missing_ok=True)
        return claim


def mark_resume_held(project: str, slug: str, hold: str, *,
                     retry_at: str | None = None,
                     expected_daemon_request: str | None = None,
                     expected_agent_id: object = _UNSET,
                     expected_session_id: object = _UNSET, expected_block_id: object = _UNSET) -> dict:
    """Keep a blocked resume due without letting an old request annotate a replacement worker."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id, expected_block_id=expected_block_id)
        if task.get("state") != "blocked":
            raise TransitionError(f"{slug}: expected blocked, found {task.get('state')}")
        after = retry_at or task.get("resume_after") or S.now()
        reason = task.get("blocked_reason") if task.get("fault") else f"waiting: {hold}"
        previous = next((ev for ev in reversed(S.read_events(project, slug)) if ev.get("kind") == "resume-held"), {})
        if (task.get("resume_after") == after and task.get("blocked_reason") == reason
                and (previous.get("block_id"), previous.get("hold")) == (task.get("block_id"), hold)):
            return task
        task["resume_after"] = after
        task["blocked_reason"] = reason
        S.save_task(project, task)
        S.append_event(project, slug, "resume-held", hold=hold, block_id=task.get("block_id"))
        return task


def update_resume_claim(project: str, slug: str, claim_id: str, **updates) -> dict:
    """Advance only the claim this daemon owns; a lifecycle race fails closed before provider binding."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        claim = task.get("resume_claim") or {}
        if (task.get("state") != "blocked" or claim.get("id") != claim_id
                or claim.get("block_id") != task.get("block_id")):
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
        if suppress_retry and claim.get("block_id") == task.get("block_id"):
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
        task = S.load_task(project, slug)
        if task.get("stop_id"):
            return []  # #302: Stop holds hook delivery too, until a replacement worker binds.
        rows = _pending_rows(task, path)
        taken = [row for row in rows if ids is None or row["id"] in ids]
        left = [row for row in rows if row not in taken]
        _mark_acceptance_delivered(task, {row["id"] for row in taken})
        S.save_task(project, task)
        if left:
            S.atomic_write(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in left))
        else:
            path.unlink(missing_ok=True)
    return taken


def render_inbox(rows: list[dict]) -> str:
    """The messages as the worker reads them."""
    return "\n\n".join((row.get("question_context", "") + "\n\n" if row.get("question_context") else "")
                       + f"Message from {str(row.get('by') or 'burak').capitalize()} ({row.get('at') or ''}; message id {row['id']}):\n{row['text']}"
                       for row in rows)


def _clear_block(project: str, task: dict) -> None:
    _ensure_question(project, task)
    task["blocked_reason"] = None
    for key in ("resume_after", "resume_request", "resume_claim", "resume_failed", "waiting_on", "fault", "escalated", "block_actor", "usage_limit", "stop_id"):
        task.pop(key, None)


def _supersede_resume(task: dict) -> None:
    # I-20260908-045037: a new question/block supersedes earlier wake requests and launch claims.
    task["block_id"] = uuid.uuid4().hex
    task.pop("resume_after", None)
    task.pop("resume_request", None)


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
    if to in ("reported", "done", "rejected"):
        _store_groups(task)
        closed_groups = set()
        for question in task.get("questions", []):
            if question["status"] == "open":
                disposition = {"reported": "superseded", "done": "completed", "rejected": "rejected"}[to]
                reason = ("Task handed its report to review; the previous recommendation was not accepted."
                          if to == "reported" else ev.get("reason") or f"Task {to}")
                _close_question(question, disposition, reason, actor)
                closed_groups.add(_group_for(task, question)["id"])
        for group in _groups(task):
            if group["id"] in closed_groups:
                group["revision"] += 1
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
        paths: list[str] | None = None, hold_merge: str | None = None, engine: str | None = None,
        effort: str | None = None) -> dict:
    if source not in ("chat", "recovery"):
        raise TransitionError("task source must be chat or recovery")
    try:
        pin = config.pinned_option("l2", {}, engine=engine, model=model)
        if effort is not None:
            effective_pin = config.pinned_option("l2", config.project(project), engine=engine, model=model)
            config.task_effort(effective_pin["engine"] if effective_pin else None, effort)
    except ValueError as exc:
        raise TransitionError(str(exc)) from exc
    if pin:
        engine = pin["engine"]
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
                "engine_model": None, "routing": None, "effort": effort,
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
        _clear_block(project, task)
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
        if task["state"] != "queued":
            raise TransitionError(f"{slug}: dispatch no longer owns a queued task")
        usage.remember(task)
        task.update({"attempt": attempt, "session_id": session_id, "agent_id": agent_id, "worktree": worktree,
                     "branch": branch, "blocked_reason": None, "l2_engine": l2_engine, "engine_model": engine_model,
                     "routing": routing, "dispatched": S.now()})
        task.pop("next_engine", None)
        return _move(project, task, "running", actor, attempt=attempt, session_id=session_id)


def report(project: str, slug: str, verified: dict, actor: str = "altd", *,
           expected_state: str | None = None, expected_attempt: int | None = None,
           expected_block_from: str | None = None, expected_agent_id: object = _UNSET,
           expected_session_id: object = _UNSET, expected_block_id: object = _UNSET) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id, expected_block_id=expected_block_id)
        if expected_state is not None and task["state"] != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task['state']}")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: expected attempt {expected_attempt}, found {task.get('attempt')}")
        if expected_block_from is not None:
            last_block = next((ev for ev in reversed(S.read_events(project, slug))
                               if ev.get("kind") == "state" and ev.get("to") == "blocked"), None)
            if not last_block or last_block.get("frm") != expected_block_from:
                raise TransitionError(f"{slug}: latest block did not come from {expected_block_from}")
        if task.get("delivery") != verified.get("delivery"):
            raise TransitionError(f"{slug}: delivery changed during report verification; verify current work again")
        if verified.get("owner", None if task.get("report_after") else report_owner(task)) != report_owner(task):
            raise TransitionError(f"{slug}: report belongs to superseded work")
        if any(row.get("wake", True) for row in pending(project, slug)):
            continue_report(project, task, actor="altd", reason="Follow-up messages await the owner", check_pr=False)
            raise TransitionError(f"{slug}: pending messages require continuation before report handoff")
        verified = {**verified, "attempt": task["attempt"]}
        task["verified"] = verified
        _clear_block(project, task)
        if verified.get("prs"):
            task["prs"] = sorted(set(task.get("prs", []) + list(verified["prs"])))
        return _move(project, task, "reported", actor, verdict=verified.get("verdict"))


def block(project: str, slug: str, reason: str, actor: str = "altd", *,
          expected_state: str | None = None, expected_attempt: int | None = None, updates: dict | None = None,
          expected_agent_id: object = _UNSET, expected_session_id: object = _UNSET,
          expected_daemon_request: str | None = None, expected_block_id: object = _UNSET,
          expected_owner: dict | None = None,
          recommendation: str | None = None,
          recommendation_label: str | None = None, recommendation_why: str | None = None,
          questions: dict | None = None, design: dict | None = None, resume_pending: bool = False) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id, expected_session_id=expected_session_id,
                              expected_block_id=expected_block_id)
        if expected_owner is not None and (report_owner(task) != expected_owner or any(
                row.get("wake", True) for row in pending(project, slug))):
            raise TransitionError(f"{slug}: verifier observation belongs to superseded work")
        if expected_state is not None and task.get("state") != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task.get('state')}")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        captured, files = None, {}
        if design is not None:
            if (actor != "l2" or expected_attempt is None or task.get("state") != "running"
                    or questions is not None or (updates or {}).get("fault") or task.get("fault")):
                raise TransitionError("design publication requires the current L2 and one ordinary question")
            captured, files = _capture_design(project, task, design)
        _supersede_resume(task)
        task.update(updates or {})
        if resume_pending:
            # #302: select the final-turn inbox under the same lock as the block; a later Send
            # sees a blocked task and schedules its own wake without losing an earlier message.
            messages = [row for row in pending(project, slug) if row.get("wake", True)]
            if messages:
                reason = "Message queued for the next session turn."
                task.update(resume_after=S.now(), resume_request=messages[-1]["id"])
        task["blocked_reason"] = reason
        task["block_actor"] = actor
        if questions is not None and (actor not in ("l2", "l3") or task.get("fault")):
            raise TransitionError("structured questions require an L2/L3 human dilemma, not an operational block")
        if actor in ("l2", "l3") and not task.get("fault"):
            _publish_block_questions(task, reason, actor, questions, recommendation,
                                     recommendation_label, recommendation_why, design=captured)
        if files:
            _save_design(project, slug, files)
        return _move(project, task, "blocked", actor, reason=reason)


def resume(project: str, slug: str, actor: str = "altd", *, agent_id: str | None = None,
           session_id: str | None = None, expected_claim: str | None = None,
           expected_daemon_request: str | None = None, expected_agent_id: object = _UNSET,
           expected_session_id: object = _UNSET, input_delivered: bool = False, **ev) -> dict:
    """blocked → running. With a worker, the task is bound to it; a resumed Claude session may carry a new id."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id)
        claim = task.get("resume_claim") or {}
        if expected_claim is not None and (claim.get("id") != expected_claim
                                          or claim.get("block_id") != task.get("block_id")):
            raise TransitionError(f"{slug}: resume claim {expected_claim} is no longer current")
        if agent_id:
            usage.remember(task)
            task.update({"agent_id": agent_id, "session_id": session_id or task.get("session_id")})
        if expected_claim is not None and input_delivered:
            receipts = task.setdefault("message_deliveries", {})
            for row in claim.get("messages", []):
                receipts[row["id"]] = {"at": S.now(), "agent_id": agent_id, "session_id": task.get("session_id")}
        task.pop("completion_requested", None)  # #302: a continued turn must supply its own final result.
        _clear_block(project, task)
        return _move(project, task, "running", actor, **ev)


def requeue(project: str, slug: str, actor: str = "altd", *, engine: str | None = None,
            clear_worker: bool = False, expected_daemon_request: str | None = None,
            expected_agent_id: object = _UNSET, expected_session_id: object = _UNSET,
            expected_block_id: object = _UNSET, **ev) -> dict:
    """blocked → queued: a task blocked before any launch, or a fresh attempt after a worker's window ran out.

    The next dispatch routes by quota again unless ``engine`` names the one to use; ``clear_worker`` drops the
    exhausted worker's identity so the fresh attempt starts from the task's saved progress, not its transcript."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_daemon_request=expected_daemon_request,
                              expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id, expected_block_id=expected_block_id)
        if task.get("resume_claim") or task.get("dispatching"):
            raise TransitionError(f"{slug}: an active launch/resume claim prevents requeue")
        if task.get("agent_id") and not clear_worker:
            raise TransitionError(f"{slug}: has an L2 worker; resume it instead")
        usage.capture(project, task)
        task.pop("verified", None)  # A fresh attempt must establish its own current verification.
        task.update({"agent_id": None, "session_id": None, "l2_engine": engine, "engine_model": None,
                     "next_engine": engine or task.get("next_engine"), "routing": None})
        _clear_block(project, task)
        return _move(project, task, "queued", actor, **ev)


def _require_no_code_change(task: dict) -> None:
    """A proposal/research task may close directly; code delivery must use the verified report path."""
    if task.get("prs") or task.get("delivery"):
        raise TransitionError("task has code delivery evidence; use the verified report path")
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
         expected_state: str | None = None, expected_attempt: int | None = None,
         expected_owner: dict | None = None) -> dict:
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
        delivery = task.get("delivery")
        verified = task.get("verified") or {}
        if ((expected_owner is not None and expected_owner != report_owner(task))
                or (task.get("report_after") and verified.get("owner") != report_owner(task))
                or any(row.get("wake", True) for row in pending(project, slug))):
            raise TransitionError(f"{slug}: follow-up work requires a current report before completion")
        if delivery and (not delivery.get("number") or verified.get("delivery") != delivery
                         or verified.get("verdict") != "ok"):
            raise TransitionError(f"{slug}: current delivery requires a verified report before completion")
        d = S.task_dir(project, slug)
        task = _move(project, task, "done", actor)
        if digest:
            S.atomic_write(d / "digest.md", digest.rstrip() + "\n")
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def finalize_completion(project: str, slug: str, actor: str = "altd", *,
                        expected_agent_id: object = _UNSET, expected_session_id: object = _UNSET,
                        expected_block_id: object = _UNSET) -> dict:
    """Archive a no-code L2 completion once its worker has exited."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug, expected_agent_id=expected_agent_id,
                              expected_session_id=expected_session_id, expected_block_id=expected_block_id)
        request = task.pop("completion_requested", None)
        if task.get("state") != "running" or not request:
            raise TransitionError(f"{slug}: no completion to finalize")
        if any(row.get("wake", True) for row in pending(project, slug)):
            continue_report(project, task, actor=actor, reason="Follow-up messages await the owner", check_pr=False)
            raise TransitionError(f"{slug}: pending messages require continuation before completion")
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


# ---- Decisions and FYIs -------------------------------------------------

def fyi(project: str, slug: str | None, text: str, actor: str = "altd") -> dict:
    """Record an FYI in project chat; explicit L3 heads-ups stay outside routine system groups.
    A task FYI is also an event in the task's record (SPEC.md §5.2 note 3)."""
    if slug is not None:
        S.require_task_slug(slug)
    from . import l3
    text = text.strip()
    row = l3.chat_log(project, "system", text, trigger="fyi", slug=slug, by=actor, heads_up=actor == "l3")
    if slug:
        S.append_event(project, slug, "fyi", text=text, by=actor)
    return row


#: "Option A:" / "Option 1)" anywhere, or a sentence-initial "A:" / "A (recommended, ...):".
_OPTION_MARK = re.compile(
    r"(?:\bOption\s+(?P<key>[A-Za-z0-9])\s*(?:\((?P<note>[^)]*)\))?\s*[:)]\s+"
    r"|(?:^|(?<=[.!?;]\s)|(?<=\n))(?P<key2>[A-H])\s*(?:\((?P<note2>[^)]*)\))?\s*[:)]\s+)")
_RECOMMEND = re.compile(r"recommend", re.IGNORECASE)
_RECOMMENDED_KEY = re.compile(r"recommend\w*\s+(?:is\s+|would\s+be\s+|option\s+)*(?P<key>[A-Za-z0-9])\b(?![\w-])",
                              re.IGNORECASE)
LABEL_LIMIT = 48


def option_label(text: str, key: str) -> str:
    """A button-sized label from an option's prose: its first clause, sentence-cased, cut at a word."""
    clause = re.split(r"[,;:]\s|\s[—–]\s|(?<=[.!?])\s", text.strip(), maxsplit=1)[0].strip().rstrip(".!?")
    if not clause:
        return f"Option {key}"
    label = clause[0].upper() + clause[1:]
    if len(label) > LABEL_LIMIT:
        cut = label[:LABEL_LIMIT - 1]
        label = (cut[:cut.rfind(" ")] if " " in cut[10:] else cut).rstrip(" ,;:") + "…"
    return label


def parse_dilemma(text: str) -> dict:
    """Read explicitly labelled options and recommendations from human dilemma prose.
    Structured recommendation fields take precedence when provided. Returns ``question``, ``options``
    ``[{key, label, text}]`` (empty when fewer than two parse), and ``recommendation`` ``{option, why}``."""
    text = (text or "").strip()
    marks = []
    for m in _OPTION_MARK.finditer(text):
        key = (m.group("key") or m.group("key2") or "").upper()
        if key and key not in [k for k, *_ in marks]:
            marks.append((key, m, (m.group("note") or m.group("note2") or "")))
    if len(marks) < 2:
        parts = _sentences(text)
        why = " ".join(s for s in parts if _RECOMMEND.search(s)) if len(parts) > 1 else ""
        return {"question": text, "options": [], "recommendation": {"option": None, "why": why}}
    question = text[:marks[0][1].start()].strip()
    options, recommended, tail = [], None, ""
    for i, (key, m, note) in enumerate(marks):
        body = text[m.end():marks[i + 1][1].start()] if i + 1 < len(marks) else text[m.end():]
        if i + 1 == len(marks):  # the last option ends where the recommendation starts
            parts, rest = _sentences(body), []
            for j, s in enumerate(parts):
                if _RECOMMEND.search(s):
                    rest, parts = parts[j:], parts[:j]
                    break
            body, tail = " ".join(parts), " ".join(rest)
        inline = re.search(r"\s*\((?:[^)]*\b)?recommended\b[^)]*\)", body, re.IGNORECASE)
        if inline:  # "keep it (recommended)" marks the option as well
            body = body[:inline.start()] + body[inline.end():]
        if _RECOMMEND.search(note) or inline:
            recommended = key
        options.append({"key": key, "label": option_label(body, key), "text": body.strip()})
    labels = [o["label"] for o in options]
    for o in options:
        if labels.count(o["label"]) > 1:
            o["label"] = f"Option {o['key']}"
    # "Reply A or B" is the asker's instruction; the option buttons replace it.
    why = " ".join(s for s in _sentences(tail) if not re.match(r"(?:Reply|Answer|Choose|Pick)\b", s)).strip()
    if recommended is None:
        found = _RECOMMENDED_KEY.search(why) or _RECOMMENDED_KEY.search(question)
        if found and found.group("key").upper() in [o["key"] for o in options]:
            recommended = found.group("key").upper()
    return {"question": question or text, "options": options,
            "recommendation": {"option": recommended, "why": why}}


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _close_question(question: dict, disposition: str, text: str, actor: str,
                    message_id: str | None = None, source: str | None = None) -> dict:
    question["status"] = "resolved"
    question["resolution"] = {"disposition": disposition, "text": text, "by": actor, "at": _conversation_time(),
                              "message_id": message_id, "source": source}
    return question["resolution"]


def _groups(task: dict) -> list[dict]:
    if "question_groups" in task:
        return task["question_groups"]
    groups = {}
    for question in task.get("questions", []):
        group = groups.setdefault(question["id"], {"id": question["id"], "revision": 1,
                                  "member_ids": [question["id"]], "anchor_id": question["anchor_id"],
                                  "reason": question["detail"]})
        group["revision"] = max(group["revision"], question["revision"])
    return list(groups.values())


def _group_members(task: dict, group: dict) -> list[dict]:
    latest = {q["id"]: q for q in task.get("questions", [])}
    return [latest[identity] for identity in group["member_ids"] if identity in latest]


def _group_for(task: dict, question: dict) -> dict:
    return next(g for g in _groups(task) if question["id"] in g["member_ids"])


def _store_groups(task: dict) -> list[dict]:
    if "question_groups" not in task:
        task["question_groups"] = _groups(task)
    return task["question_groups"]


def question_choices(question: dict) -> list[dict]:
    if "options" in question:
        return question["options"]
    rec = question.get("recommendation")
    return [{"key": "recommended", "label": rec["label"], "text": rec["text"]}] if rec else []


def _recommended_key(question: dict) -> str | None:
    return question.get("recommended_key", "recommended" if question.get("recommendation") else None)


def _validate_questions(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or set(payload) != {"questions"}:
        raise TransitionError('questions JSON must contain only a "questions" array')
    items = payload["questions"]
    if not isinstance(items, list) or not 1 <= len(items) <= 3:
        raise TransitionError("ask one to three independent questions")
    normalized, ids, texts = [], set(), set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"id", "question", "options", "recommended_key", "why"}:
            raise TransitionError("question fields are id, question, options, recommended_key and why")
        text = item.get("question")
        if not isinstance(text, str) or not text.strip():
            raise TransitionError("every question needs nonempty text")
        if text.strip() in texts:
            raise TransitionError("each independent question appears only once")
        texts.add(text.strip())
        identity = item.get("id")
        if identity is not None and (not isinstance(identity, str) or not identity or identity in ids):
            raise TransitionError("question ids must be distinct existing ids")
        if identity:
            ids.add(identity)
        options, keys = item.get("options", []), set()
        if not isinstance(options, list) or len(options) > 3:
            raise TransitionError("a question has at most three quick options")
        for option in options:
            if (not isinstance(option, dict) or set(option) != {"key", "label", "text"}
                    or any(not isinstance(option[k], str) or not option[k].strip() for k in option)):
                raise TransitionError("each option needs a nonempty key, label and text")
            if option["key"] in keys:
                raise TransitionError("option keys must be distinct")
            keys.add(option["key"])
        recommended = item.get("recommended_key")
        if (options and (not isinstance(recommended, str) or recommended not in keys)) or (not options and recommended is not None):
            raise TransitionError("quick options require one explicit recommended_key; plain questions have none")
        why = item.get("why", "")
        if not isinstance(why, str):
            raise TransitionError("recommendation rationale must be text")
        normalized.append({"id": identity, "question": text.strip(), "options": options,
                           "recommended_key": recommended, "why": why.strip(),
                           "options_supplied": "options" in item, "why_supplied": "why" in item})
    return normalized


def _publish_question(task: dict, text: str, actor: str, *, recommendation: str | None = None,
                      label: str | None = None, why: str | None = None, force_revision: bool = False,
                      previous: object = _UNSET, group: dict | None = None, options: list[dict] | None = None,
                      recommended_key: str | None = None, bump: bool = True, design: object = _UNSET) -> dict:
    questions = task.setdefault("questions", [])
    if previous is _UNSET:
        previous = questions[-1] if questions else None
    if design is _UNSET:
        design = previous.get("design") if previous and (previous["status"] == "open" or force_revision) else None
    groups = _store_groups(task)
    audience = previous["audience"] if force_revision else "l3" if task.get("waiting_on") == "l3" else "operator"
    parsed = parse_dilemma(text)
    structured = options is not None
    selected = next((o for o in parsed["options"] if o["key"] == parsed["recommendation"]["option"]), None)
    recommended = ({"text": recommendation.strip(), "label": (label or "Use recommendation & resume").strip(),
                    "why": (why or "").strip()} if recommendation and recommendation.strip() else
                   {"text": selected["text"], "label": selected["label"],
                    "why": parsed["recommendation"]["why"]} if selected else None)
    if options is not None:
        selected = next((o for o in options if o["key"] == recommended_key), None)
        recommended = {"text": selected["text"], "label": selected["label"], "why": why or ""} if selected else None
    else:
        options = [{"key": "recommended", "text": recommended["text"], "label": recommended["label"]}] if recommended else []
        recommended_key = "recommended" if recommended else None
    if (previous and previous["status"] == "open" and previous["detail"] == text
            and previous["audience"] == audience and previous["recommendation"] == recommended
            and previous["asked_by"] == actor and question_choices(previous) == options
            and _recommended_key(previous) == recommended_key and previous.get("design") == design):
        return previous
    continuing = previous and (previous["status"] == "open" or force_revision)
    if previous and previous["status"] == "open":
        _close_question(previous, "superseded", "Question updated; use the current revision.", actor)
    identity = previous["id"] if continuing else uuid.uuid4().hex
    revision = previous["revision"] + 1 if continuing else 1
    at, anchor = _conversation_time(), uuid.uuid4().hex
    if group is None:
        group = _group_for(task, previous) if continuing else {"id": uuid.uuid4().hex, "revision": 0,
                    "member_ids": [], "anchor_id": anchor, "reason": text}
        if not continuing:
            groups.append(group)
    if identity not in group["member_ids"]:
        group["member_ids"].append(identity)
    if not group.get("anchor_id"):
        group["anchor_id"] = anchor
    message = {"id": anchor, "at": at, "role": actor if actor in ("l2", "l3") else "l2", "by": actor,
               "text": text, "question_id": identity, "question_revision": revision, "group_id": group["id"]}
    if previous and continuing and previous["resolution"].get("message_id") is None:
        previous["resolution"].update(message_id=anchor, source="task")
    question = {"id": identity, "revision": revision, "anchor_id": anchor, "status": "open",
                "audience": audience,
                "question": (previous["question"] if previous and previous["detail"] == text
                             else text if structured else parsed["question"]), "detail": text,
                "recommendation": recommended, "asked_by": actor, "asked": at, "since": at,
                "kind": "asks", "resolution": None, "message": message, "group_id": group["id"],
                "options": options, "recommended_key": recommended_key}
    if design is not None:
        question["design"] = design
    questions.append(question)
    if bump:
        group["revision"] += 1
    return question


def _publish_questions(task: dict, payload: dict, actor: str, reason: str) -> list[dict]:
    inputs = _validate_questions(payload)
    groups = _store_groups(task)
    group = groups[-1] if groups else None
    if not group or not any(q["status"] == "open" for q in _group_members(task, group)):
        group = {"id": uuid.uuid4().hex, "revision": 0, "member_ids": [], "anchor_id": None, "reason": reason}
        groups.append(group)
    members = _group_members(task, group)
    targets, used = [], set()
    for item in inputs:
        previous = next((q for q in members if q["id"] == item["id"]), None) if item["id"] else next(
            (q for q in members if q["status"] == "open" and q["detail"] == item["question"]), None)
        if item["id"] and (not previous or previous["status"] != "open"):
            raise TransitionError("question id must name an open member of the current group")
        if previous and previous["id"] in used:
            raise TransitionError("a question appears twice in the group")
        if previous:
            used.add(previous["id"])
        targets.append((item, previous))
    if len(members) + sum(previous is None for _, previous in targets) > 3:
        raise TransitionError("a group has at most three questions; resolve existing questions before asking a new group")
    before = len(task.get("questions", []))
    for item, previous in targets:
        if previous and previous["audience"] == "operator":
            task["waiting_on"] = OPERATOR_MESSAGE_ROLE
        keep_options = previous and previous["detail"] == item["question"] and not item["options_supplied"]
        options = question_choices(previous) if keep_options else item["options"]
        recommended = _recommended_key(previous) if keep_options else item["recommended_key"]
        why = ((previous.get("recommendation") or {}).get("why", "")
               if keep_options and not item["why_supplied"] else item["why"])
        _publish_question(task, item["question"], actor, previous=previous, group=group, bump=False,
                          options=options, recommended_key=recommended, why=why)
    group["reason"] = reason
    if len(task["questions"]) != before:
        group["revision"] += 1
    return _group_members(task, group)


def _publish_block_questions(task: dict, reason: str, actor: str, payload: dict | None,
                             recommendation: str | None, label: str | None, why: str | None,
                             *, design: dict | None = None) -> list[dict]:
    groups = _store_groups(task)
    group = groups[-1] if groups else None
    members = _group_members(task, group) if group else []
    pending = [q for q in members if q["status"] == "open"]
    if any(q["audience"] == "operator" for q in pending):
        task["waiting_on"] = OPERATOR_MESSAGE_ROLE
    if payload is not None:
        if any(value is not None for value in (recommendation, label, why)):
            raise TransitionError("questions JSON supplies its own options and recommendation")
        return _publish_questions(task, payload, actor, reason)
    previous = next((q for q in pending if q["detail"].strip() == reason.strip()), None)
    no_replacement = all(value is None for value in (recommendation, label, why))
    audience = "l3" if task.get("waiting_on") == "l3" else "operator"
    if design is None and no_replacement and group and reason.strip() == group["reason"].strip() and pending:
        # The ordinary block verb parks the same whole group after discussing a follow-up.
        if all(q["audience"] == audience for q in pending):
            return members
        for question in pending:
            _publish_question(task, question["detail"], actor, previous=question, group=group, bump=False,
                              options=question_choices(question), recommended_key=_recommended_key(question),
                              why=(question.get("recommendation") or {}).get("why"))
        group["revision"] += 1
        return _group_members(task, group)
    if previous is None and len(pending) > 1:
        raise TransitionError("several questions remain open; use --questions-file with their ids or park with the saved group reason")
    previous = previous or (pending[0] if pending else None)
    if previous and previous["detail"].strip() == reason.strip() and no_replacement:
        if previous["audience"] == audience and (design is None or previous.get("design") == design):
            return members
        _publish_question(task, reason, actor, previous=previous, group=group,
                          options=question_choices(previous), recommended_key=_recommended_key(previous),
                          why=(previous.get("recommendation") or {}).get("why"),
                          design=design if design is not None else _UNSET)
    else:
        _publish_question(task, reason, actor, previous=previous, group=group if pending else None,
                          recommendation=recommendation, label=label, why=why,
                          design=design if design is not None else _UNSET)
    current_group = _groups(task)[-1]
    current_group["reason"] = reason
    return _group_members(task, current_group)


def _legacy_question_origin(project: str, task: dict) -> dict | None:
    """Read whether a legacy block has authoritative human-question evidence, without adopting it."""
    if (task.get("questions") or task.get("state") != "blocked" or not task.get("blocked_reason")
            or task.get("fault")):
        return None
    events = S.read_events(project, task["slug"])
    block_index = next((i for i in range(len(events) - 1, -1, -1)
                        if events[i].get("kind") == "state" and events[i].get("to") == "blocked"), -1)
    block = events[block_index] if block_index >= 0 else {}
    escalation = (next((event for event in reversed(events[block_index + 1:])
                        if event.get("kind") == "escalated"), {}) if task.get("escalated") else {})
    origin = escalation or block
    actor = origin.get("by") or task.get("block_actor")
    # A missing actor does not turn legacy capacity/quota holds or operator stops into an L2 question.
    if actor not in ("l2", "l3"):
        return None
    return {"text": origin.get("question") or origin.get("reason") or task["blocked_reason"],
            "actor": actor, "at": origin.get("at")}


def _ensure_question(project: str, task: dict) -> bool:
    """Adopt pre-feature human blocks before a read or wake can lose their durable dilemma."""
    origin = _legacy_question_origin(project, task)
    if not origin:
        return False
    question = _publish_question(task, origin["text"], origin["actor"])
    if origin.get("at"):
        question.update(asked=origin["at"], since=origin["at"])
        question["message"]["at"] = origin["at"]
    return True


def question_context(question: dict) -> str:
    if question["status"] == "resolved":
        resolution = question["resolution"]
        return (f"Task question {question['id']} revision {question['revision']} is resolved "
                f"({resolution['disposition']}, recorded from {resolution.get('source') or 'task lifecycle'} "
                f"by {resolution['by']}): {resolution['text']}\n"
                + (f"L3 authority assessed by {resolution['recorded_by']} (attempt {resolution['recorded_attempt']}): "
                   f"{resolution['l3_authority']}\n" if resolution.get("l3_authority") else "")
                + f"Question: {question['detail']}\n"
                "This closes that question only. Superseded questions do not accept their old recommendation. "
                "Existing task scope and merge holds remain unchanged.")
    recommendation = question.get("recommendation")
    return (f"Pending task question {question['id']} revision {question['revision']} "
            f"(asked by {question['asked_by']}; authority: {question['audience']}): {question['detail']}\n"
            + (f"Recommended approach: {recommendation['text']}\n" if recommendation else "")
            + ("Quick choices: " + "; ".join(f"{o['key']}: {o['text']}" for o in question_choices(question)) + "\n"
               if question_choices(question) else "")
            + "Discussing this question or waking the worker does not authorize the disputed implementation. "
            "Answer follow-ups; clarify ambiguity conversationally. After answering a follow-up with "
            "`alt task reply`, checkpoint progress.md and park using `alt task block \"$ALTITUDE_TASK\" "
            "--reason '<same pending question text>'`. Omit recommendation fields to keep the saved question, "
            "recommendation and required authority; parking leaves it unanswered. When the actual source message settles the "
            "choice, record it before proceeding: alt task resolve \"$ALTITUDE_TASK\" "
            f"--question {question['id']} --revision {question['revision']} --message <message-id> "
            "--source task --disposition answered --reason '<chosen approach>'. A simple contextual answer "
            "is sufficient; no magic approval phrase or redundant confirmation. For partial answers, add "
            "--remaining '<only still-relevant unanswered parts>'; use --disposition superseded when a "
            "changed direction makes the old question irrelevant, without accepting its recommendation. "
            "For an operator decision relayed through L3 cite its original project message with --source project. "
            "For an unnecessary escalation settled within L3's delegated authority, cite the L3 task message "
            "and add --l3-authority '<specific brief/rule/recorded-decision evidence and rationale>'. "
            "The owner checks that authority applies; recommendations and discussion are not settled answers. "
            "L3-authored text alone cannot settle a question requiring operator judgment. Merge holds remain unchanged.")


def group_context(task: dict, group: dict | None = None) -> str:
    groups = _groups(task)
    group = group or (groups[-1] if groups else None)
    if not group:
        return ""
    return (f"Task question group {group['id']} revision {group['revision']}. "
            f"Saved group reason: {group['reason']}\n"
            "Each question is independent. A single source message may answer several; use alt task resolve "
            "for each actually answered or irrelevant question and leave other questions open. "
            "After a follow-up, park the whole group using alt task block with its saved group reason. "
            "To revise a member, use --questions-file and its id; omitted members remain unchanged.\n\n"
            + "\n\n".join(question_context(q) for q in _group_members(task, group)))


def question_view(project: str, task: dict, question: dict) -> dict:
    group = _group_for(task, question)
    return {**{k: v for k, v in question.items() if k not in ("message", "acceptance_message", "acceptance_delivered", "design")},
            **({"design_url": design_url(project, task["slug"], question)} if question.get("design") else {}),
            "options": question_choices(question), "recommended_key": _recommended_key(question),
            "group_id": group["id"], "group_revision": group["revision"], "group_anchor_id": group["anchor_id"],
            "project": project, "slug": task["slug"], "title": task.get("title"),
            "state": task["state"], "resume_after": task.get("resume_after"),
            "resume_failed": task.get("resume_failed")}


def question_group_view(project: str, task: dict, group: dict | None = None) -> dict | None:
    groups = _groups(task)
    group = group or (groups[-1] if groups else None)
    if not group:
        return None
    return {"id": group["id"], "revision": group["revision"], "anchor_id": group["anchor_id"],
            "questions": [question_view(project, task, q) for q in _group_members(task, group)]}


def _group_target(task: dict, identity: str, revision: int) -> dict:
    group = next((g for g in _groups(task) if g["id"] == identity), None)
    if (not group or isinstance(revision, bool) or not isinstance(revision, int)
            or group["revision"] != revision):
        raise TransitionError("question group changed; refresh the conversation")
    return group


def question_views(project: str, slug: str) -> list[dict]:
    task = S.load_task(project, slug)
    if _legacy_question_origin(project, task):
        with S.project_lock(project):
            task = S.load_task(project, slug)
            if _ensure_question(project, task):
                # A one-time durable anchor migration is not new worker or conversation activity.
                S.write_json(S.status_path(project, slug), task)
    return [question_view(project, task, q) for q in task.get("questions", [])]


def _question_target(task: dict, identity: str, revision: int) -> dict:
    if not identity or isinstance(revision, bool) or not isinstance(revision, int):
        raise TransitionError("acceptance must name the question and its integer revision")
    question = next((q for q in task.get("questions", [])
                     if q["id"] == identity and q["revision"] == revision), None)
    if not question:
        raise TransitionError("question is unavailable; refresh the conversation")
    return question


def _decision_messages(project: str, slug: str, source: str) -> list[dict]:
    if source == "task":
        return task_messages(project, slug)
    if source != "project":
        raise TransitionError("resolution source must be task or project")
    path = config.project_dir(project) / "chat.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    messages = [{**r, "id": r.get("turn_id"), "role": OPERATOR_MESSAGE_ROLE,
                 "by": r.get("by", OPERATOR_MESSAGE_ROLE)} for r in rows
                if r.get("role") == "user" and r.get("trigger", "chat") in (None, "", "chat")]
    identities = [r["id"] for r in messages if r["id"] is not None]
    if len(set(identities)) != len(identities):
        raise TransitionError("duplicate project operator message identity")
    return messages


def _decision_source(project: str, slug: str, question: dict, message_id: str, source: str, *,
                     l3_authority: str | None = None) -> dict:
    """Original authority and viewed revision shared by decisions and merge reconciliation."""
    row = next((r for r in _decision_messages(project, slug, source) if r["id"] == message_id), None)
    authorized = row and ((row["role"] == OPERATOR_MESSAGE_ROLE and row.get("by") == OPERATOR_MESSAGE_ROLE)
                          or (source == "task" and (question["audience"] == "l3" or l3_authority)
                              and row["role"] == "l3" and row.get("by") == "l3"))
    if l3_authority and not (source == "task" and row and row["role"] == "l3" and row.get("by") == "l3"):
        raise TransitionError("L3 authority must cite an original L3 task message")
    if not authorized:
        raise TransitionError("resolution must cite an original message with authority for this question")
    if source == "task":
        refs = row.get("question_refs")
        if l3_authority and {"id": question["id"], "revision": question["revision"]} not in (refs or []):
            raise TransitionError("L3 authority source must name this exact question revision")
        if refs is not None:
            if {"id": question["id"], "revision": question["revision"]} not in refs:
                raise TransitionError("source message discusses a different question revision")
        elif row.get("question_id") and (row["question_id"], row.get("question_revision")) != (question["id"], question["revision"]):
            raise TransitionError("source message discusses a different question revision")
    if (row["at"][:19] < question["asked"][:19] if source == "project" else row["at"] < question["asked"]):
        raise TransitionError("source message predates this question revision")
    return row


def resolve_question(project: str, slug: str, identity: str, revision: int, message_id: str, *,
                     disposition: str, reason: str, expected_attempt: int, source: str = "task",
                     remaining: str | None = None, recommendation: str | None = None,
                     recommendation_label: str | None = None, recommendation_why: str | None = None,
                     l3_authority: str | None = None) -> dict:
    """The owning L2 records semantic judgment with durable, original authority; no prose classifier."""
    if disposition not in ("answered", "superseded") or not reason.strip():
        raise TransitionError("resolution needs answered/superseded and a concrete reason")
    if remaining is not None and not remaining.strip():
        raise TransitionError("remaining question must name the still-relevant unanswered parts")
    if l3_authority is not None:
        if not isinstance(l3_authority, str) or not l3_authority.strip():
            raise TransitionError("L3 authority needs specific evidence and a rationale")
        l3_authority = l3_authority.strip()
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        if task.get("state") not in ("running", "blocked"):
            raise TransitionError("only the active task owner may resolve its question")
        _require_daemon_fence(task, slug)
        question = _question_target(task, identity, revision)
        row = _decision_source(project, slug, question, message_id, source, l3_authority=l3_authority)
        receipt = question.get("resolution") or {}
        if question["status"] != "open":
            if (receipt.get("message_id"), receipt.get("source"), receipt.get("disposition"), receipt.get("text"),
                    receipt.get("remaining"), receipt.get("l3_authority")) == (
                    message_id, source, disposition, reason.strip(), remaining, l3_authority):
                return question_view(project, task, question)
            raise TransitionError("question was already resolved or superseded; refresh the conversation")
        actor = OPERATOR_MESSAGE_ROLE if source == "project" else row.get("by") or row["role"]
        if disposition == "answered":
            require_design(project, slug, question)
        receipt = _close_question(question, disposition, reason.strip(), actor, message_id, source)
        receipt["remaining"] = remaining
        if l3_authority:
            receipt.update(l3_authority=l3_authority, recorded_by="l2", recorded_attempt=expected_attempt)
        _store_groups(task)
        group = _group_for(task, question)
        group["revision"] += 1
        if remaining:
            _publish_question(task, remaining.strip(), "l2", recommendation=recommendation,
                              label=recommendation_label, why=recommendation_why, force_revision=True,
                              previous=question, group=group, bump=False)
        S.save_task(project, task)
        S.append_event(project, slug, "question-resolved", question_id=identity, revision=revision, **receipt)
        S.regen_state_md(project)
        return question_view(project, task, question)


def accept_question(project: str, slug: str, identity: str, revision: int, option_key: str | None = None) -> dict:
    """Accept one explicit choice; omission selects only an explicitly recommended choice."""
    return accept_question_result(project, slug, identity, revision, option_key)["question"]


def accept_question_result(project: str, slug: str, identity: str, revision: int, option_key: str | None = None) -> dict:
    return _accept_questions(project, slug, [{"question_id": identity, "revision": revision, "option_key": option_key}])


def accept_questions(project: str, slug: str, group_id: str, group_revision: int, answers: list[dict]) -> dict:
    """Atomically record a selected subset of a group with one normal message and wake request."""
    if (not isinstance(group_id, str) or not group_id or isinstance(group_revision, bool)
            or not isinstance(group_revision, int)):
        raise TransitionError("batch acceptance must name the question group and its integer revision")
    return _accept_questions(project, slug, answers, group_id=group_id, group_revision=group_revision)


def _submission_response(project: str, task: dict, saved: dict) -> dict:
    # The receipt is immutable; current group state prevents a delayed retry reviving old choices in UI caches.
    selected = _question_target(task, saved["question_id"], saved["question_revision"])
    return {"question_group": question_group_view(project, task),
            "question": question_view(project, task, selected)}


def _accept_questions(project: str, slug: str, answers: list[dict], *,
                      group_id: str | None = None, group_revision: int | None = None) -> dict:
    if not isinstance(answers, list) or not 1 <= len(answers) <= 3:
        raise TransitionError("submit one to three explicit answers")
    if any(not isinstance(answer, dict) or set(answer) != {"question_id", "revision", "option_key"}
           for answer in answers):
        raise TransitionError("answers require question_id, revision and option_key")
    batch = group_id is not None or group_revision is not None
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _store_groups(task)
        chosen, seen = [], set()
        for answer in answers:
            question = _question_target(task, answer["question_id"], answer["revision"])
            if question["id"] in seen:
                raise TransitionError("submit each question only once")
            seen.add(question["id"])
            key = answer["option_key"]
            if key is None and not batch:
                key = _recommended_key(question)
            option = next((o for o in question_choices(question) if o["key"] == key), None)
            if not option:
                raise TransitionError("this question has no explicit recommendation or matching quick option; answer in the conversation")
            chosen.append((question, option))
        group = _group_for(task, chosen[0][0])
        if any(_group_for(task, q)["id"] != group["id"] for q, _ in chosen):
            raise TransitionError("all answers must belong to the same question group")
        canonical = {"group_id": group_id, "group_revision": group_revision,
                     "answers": sorted([{"question_id": q["id"], "revision": q["revision"], "option_key": o["key"]}
                                        for q, o in chosen], key=lambda answer: answer["question_id"])}
        saved = next((s for s in group.get("submissions", []) if s["request"] == canonical), None)
        if saved:
            _queue_acceptance(project, task, chosen[0][0])
            return _submission_response(project, task, saved)
        # Compatibility with pre-group single recommendation receipts.
        if (not batch and len(chosen) == 1 and chosen[0][0].get("acceptance_message")
                and "question_refs" not in chosen[0][0]["acceptance_message"]
                and chosen[0][1]["key"] == _recommended_key(chosen[0][0])):
            _queue_acceptance(project, task, chosen[0][0])
            return {"question": question_view(project, task, chosen[0][0]),
                    "question_group": question_group_view(project, task)}
        if batch:
            checked = _group_target(task, group_id, group_revision)
            if checked["id"] != group["id"]:
                raise TransitionError("answers do not belong to this question group")
        _require_daemon_fence(task, slug)
        if (any(q["status"] != "open" or q["audience"] != "operator" for q, _ in chosen)
                or task["state"] not in ("running", "blocked", "queued")):
            raise TransitionError("question is no longer open for acceptance; refresh the conversation")
        for question, _ in chosen:
            require_design(project, slug, question)
        at, message_id = _conversation_time(), uuid.uuid4().hex
        text = (f"Use this approach and continue: {chosen[0][1]['text']}" if len(chosen) == 1 else
                "Use these answers and continue:\n" + "\n".join(f"{q['question']} — {o['text']}" for q, o in chosen))
        row = {"id": message_id, "at": at, "role": OPERATOR_MESSAGE_ROLE, "by": OPERATOR_MESSAGE_ROLE,
               "text": text, "group_id": group["id"], "group_revision": group["revision"],
               "question_refs": [{"id": q["id"], "revision": q["revision"]} for q, _ in chosen]}
        if len(chosen) == 1:
            row.update(question_id=chosen[0][0]["id"], question_revision=chosen[0][0]["revision"])
        for question, option in chosen:
            question["acceptance_message"] = row
            receipt = _close_question(question, "answered", option["text"], OPERATOR_MESSAGE_ROLE, message_id, "task")
            receipt["option_key"] = option["key"]
        group["revision"] += 1
        row["question_context"] = group_context(task, group)
        saved = {"request": canonical, "question_id": chosen[0][0]["id"], "question_revision": chosen[0][0]["revision"]}
        group.setdefault("submissions", []).append(saved)
        # Receipt and human message share one atomic status write. Inbox delivery is recovered by pending().
        if task["state"] == "blocked" and not task.get("stop_id"):
            task["resume_request"] = message_id
            task["resume_after"] = task.get("resume_after") or S.now()
            task.pop("resume_failed", None)
        S.save_task(project, task)
        _queue_acceptance(project, task, chosen[0][0])
        for question, _ in chosen:
            S.append_event(project, slug, "question-resolved", question_id=question["id"], revision=question["revision"],
                           **question["resolution"])
        S.regen_state_md(project)
        return _submission_response(project, task, saved)


def _queue_acceptance(project: str, task: dict, question: dict) -> None:
    """Materialize the saved message for native hooks; failed writes recover on the same acceptance retry."""
    if task.get("state") not in ("running", "blocked", "queued") or question.get("acceptance_delivered"):
        return
    path = S.task_dir(project, task["slug"]) / "inbox.jsonl"
    message = question["acceptance_message"]
    if message["id"] not in {row["id"] for row in _rows(path, "task inbox")}:
        _append_jsonl(path, message)


def decision_row(project: str, task: dict) -> dict:
    """Project an operational block for navigation, without inventing a recommendation or decision action."""
    events = S.read_events(project, task["slug"])
    reason = (task.get("blocked_reason") or "no reason recorded").strip()
    blocks = [e for e in events if e.get("kind") == "state" and e.get("to") == "blocked"]
    escalations = [e for e in events if e.get("kind") == "escalated"]
    decided = [e for e in events if e.get("kind") == "decided"]
    escalated = bool(task.get("escalated"))
    by = (blocks[-1].get("by") if blocks else None) or "altd"
    if task.get("fault"):
        kind = "fault"
    elif escalated or by == "l2":
        kind = "asks"
    else:
        kind = "stopped"
    floor = decided[-1]["at"] if decided else (task.get("created") or "")
    window = [e["at"] for e in blocks if e.get("at", "") >= floor]
    asked = (escalations[-1]["at"] if escalated and escalations else blocks[-1]["at"] if blocks
             else task.get("updated"))
    return {"project": project, "slug": task["slug"], "title": task.get("title"), "kind": kind,
            "asked_by": "l3" if escalated else by, "question": short_reason(reason), "detail": reason,
            "recommendation": None, "asked": asked,
            "since": window[0] if window else floor or asked}


def decisions(project: str) -> list[dict]:
    """Tasks blocked on the operator: an L2's block flagged for them, L3's escalation, or a block from before
    L3 saw blocks first. A block waiting on L3, or on a timed hold, is Altitude's wait, not a decision."""
    rows = []
    for task in S.list_tasks(project):
        questions = question_views(project, task["slug"])
        rows.extend(q for q in questions if q["status"] == "open" and q["audience"] == "operator")
        if (not questions and task["state"] == "blocked" and not task.get("resume_after")
                and task.get("waiting_on", OPERATOR_MESSAGE_ROLE) == OPERATOR_MESSAGE_ROLE):
            rows.append(decision_row(project, task))
    return rows


def block_question(task: dict) -> str:
    """Notify the coordinator of published questions without transferring decision authority."""
    slug = task["slug"]
    questions = "\n".join(f"- {q['id']} revision {q['revision']} (authority: {q['audience']}): {q['detail']}"
                          for q in task.get("questions", []) if q["status"] == "open")
    return (f"Task `{slug}` blocked and asks: {task['blocked_reason'][:800]}\n{questions}\n\n"
            f"Read `alt task messages {slug}` and `alt task show {slug}`. When the brief, the docs, or a recorded "
            f"decision settles a member, answer with `alt task message {slug} \"<answer and evidence>\"` so its owner "
            "can record the resolution. This notification grants no operator authority. Keep operator-required "
            "proposal, security and product decisions open; do not re-escalate members already addressed to the operator. "
            f"For a new operator choice use `alt task escalate {slug}` with its question and recommendation. "
            "Coordinate only the parts you can settle; preserve merge holds and verified fault recovery.")


def escalate(project: str, slug: str, question: str, actor: str = "l3", *,
             recommendation: str | None = None, recommendation_label: str | None = None,
             recommendation_why: str | None = None, questions: dict | None = None) -> dict:
    """L3 hands a blocked task's question to Burak as one plain dilemma; the L2's own words stay in the events."""
    question = (question or "").strip()
    if not question:
        raise TransitionError("escalation needs the question")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") != "blocked":
            raise TransitionError(f"{slug} is {task.get('state')}, not blocked")
        _supersede_resume(task)
        _ensure_question(project, task)
        task.update({"waiting_on": "burak", "escalated": True,
                     "blocked_reason": task.get("blocked_reason") if task.get("fault") else question})
        current = _publish_block_questions(task, question, actor, questions, recommendation,
                                           recommendation_label, recommendation_why)
        S.save_task(project, task)
        handoff = {**current[-1]["message"], "question_context": group_context(task), "wake": False}
        _append_jsonl(S.task_dir(project, slug) / "inbox.jsonl", handoff)
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
                         reason: str, actor: str,
                         question: str | None = None, revision: int | None = None,
                         source: str = "task") -> dict:
    """L3 judges scope and later corrections; altd binds original authority to this hold and PR.

    I-20260909-074919: UI decisions and contextual reaffirmations use the common decision contract.
    L3 rejects ambiguity, revocation and implementation-only permission.
    """
    if actor != "l3" or not reason.strip():
        raise TransitionError("recorded approval requires the coordinator daemon and a reason")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        try:
            if not task.get("hold_merge") or task["state"] not in ("running", "blocked", "reported"):
                raise ValueError("task has no active merge hold")
            from . import l3
            if any(r.get("trigger") == "chat" for r in l3._queue_rows(l3.queue_path(project))):
                raise ValueError("review pending project chat before applying approval")
            sources = _decision_messages(project, slug, source)
            approvals = [r for r in sources if r["id"] == approval]
            operator = approvals[0] if len(approvals) == 1 else {}
            if operator.get("role") != OPERATOR_MESSAGE_ROLE or operator.get("by") != OPERATOR_MESSAGE_ROLE:
                raise ValueError("cite the original operator approval")
            def timestamp(value):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("approval evidence needs timezone-aware timestamps")
                return parsed
            decision, resolution = None, {}
            if question is not None:
                decision = _question_target(task, question, revision)
                saved = decision.get("resolution") or {}
                resolution = saved if (saved.get("message_id"), saved.get("source")) == (approval, source) else {}
                if (decision != next(q for q in reversed(task["questions"]) if q["id"] == question)
                        or decision["status"] != "resolved"
                        or resolution and (decision["audience"] != "operator"
                            or resolution.get("disposition") != "answered" or resolution.get("remaining")
                            or (resolution.get("source"), resolution.get("by")) != (source, OPERATOR_MESSAGE_ROLE))):
                    raise ValueError("approval needs the current answered operator question revision")
                _decision_source(project, slug, decision, approval, source)
                if not resolution and not timestamp(saved["at"]) < timestamp(operator["at"]):
                    raise ValueError("context-only approval must follow the earlier question resolution")
                if resolution and decision.get("acceptance_message") and not any(
                        o["key"] == resolution.get("option_key") and o["text"] == resolution.get("text")
                        for o in question_choices(decision)):
                    raise ValueError("approval choice does not match its recorded option")
            elif revision is not None or operator.get("question_refs") or operator.get("question_id"):
                raise ValueError("cite the approval's question and revision")
            events = [json.loads(line) for line in (S.task_dir(project, slug) / "events.log").read_text().splitlines()
                      if line.strip()]  # a corrupt later hold must not disappear from authorization evidence
            hold = next((event for event in reversed(events)
                         if event["kind"] in ("new", "hold-merge", "release-merge")), {})
            if (hold.get("kind") not in ("new", "hold-merge")
                    or task.get("hold_merge_id") != hold.get("hold_id")
                    or hold["kind"] == "hold-merge" and hold.get("why") != task["hold_merge"]):
                raise ValueError("current hold has no matching recorded generation")
            # Restoration repeats this requirement; only an explicit hold change creates a new ID.
            generation = next(i for i, event in enumerate(events)
                              if event["kind"] in ("new", "hold-merge")
                              and event.get("hold_id") == hold.get("hold_id"))
            hold = events[generation]
            if not timestamp(hold["at"]) < timestamp(operator["at"]):
                raise ValueError("approval is stale: it predates the current merge hold")
            adopted = task.get("adopted_pr") or {}
            if adopted and (pull.get("number") != adopted["number"] or pull.get("url") != adopted["url"]):
                raise ValueError("approval PR must match the adopted PR")
            if task.get("prs") and pull.get("number") != task["prs"][-1]:
                raise ValueError("approval PR must match the task's active PR")
            if (pull.get("state") != "OPEN" or pull.get("isDraft") is not False
                    or pull.get("isCrossRepository") is not False or pull.get("baseRefName") != "main"
                    or pull.get("headRefName") != (adopted.get("branch") or task.get("branch"))
                    or pull.get("headRefOid") != head):
                raise ValueError("approval PR must be open, ready, and match the task branch and observed head")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, TransitionError) as exc:
            S.append_event(project, slug, "merge-approval-refused", actor=actor, approval=approval,
                           question=question, revision=revision, reason=reason, source=source, error=str(exc))
            raise TransitionError(f"recorded merge approval refused: {exc}") from exc
        receipt = {"actor": actor, "authorized_by": operator["role"], "reason": reason,
                   "source": source,
                   "approval": approval, "approved_at": operator["at"],
                   "question": question, "revision": revision,
                   "question_context_only": decision is not None and not resolution,
                   "option_key": resolution.get("option_key"),
                   "hold": task["hold_merge"], "hold_event": generation, "hold_at": hold["at"],
                   "hold_id": task.get("hold_merge_id"),
                   "pr": pull["number"], "url": pull["url"], "head": head, "at": S.now()}
        task.update(hold_merge=None, merge_approval=receipt)
        S.save_task(project, task)
        S.append_event(project, slug, "release-merge", **receipt)
        return receipt
