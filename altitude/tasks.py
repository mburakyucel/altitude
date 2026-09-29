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
from urllib.parse import quote, unquote, urlsplit

from . import config, github_intake, images as image_store, state as S, usage

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


TASK_MESSAGE_ROLES = (config.OPERATOR_ACTOR, "l2", "l3")
SUMMARY_LIMIT = 100  # one folded conversation row on a phone
OPERATOR_MESSAGE_ROLE = TASK_MESSAGE_ROLES[0]
_UNSET = object()

# Pending designs are selected raster captures, never executable worktree documents.
DESIGN_IMAGE_LIMIT = 8 << 20
DESIGN_TOTAL_LIMIT = 32 << 20
DESIGN_TEXT_LIMIT = 64 << 10
DESIGN_IMAGE_COUNT = 12


@contextmanager
def _design_directory(root: Path | int, parts: list[str], *, create: bool = False):
    """Walk relative to an open root without following any symlink, including racing replacements."""
    fd = os.dup(root) if isinstance(root, int) else os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
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


def _design_bytes(root: Path | int, relative: str, limit: int) -> bytes:
    parts = relative.split("/")
    if any(not p or p in (".", "..") or "\\" in p for p in parts):
        raise ValueError("invalid design path")
    with _design_directory(root, parts[:-1]) as directory:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            os.close(fd)
            raise ValueError("design file is not a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
    if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("design file changed during capture")
    return data


TASK_FILE_LIMIT = 1 << 20


class TaskFileError(ValueError):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


def task_file(project: str, reference: str) -> dict:
    """Read a direct task document; every component and the task record stay descriptor-bound."""
    outside = "Only text documents directly in this project's task folders can open here."
    unsupported = "Only regular UTF-8 .md or .txt documents up to 1 MiB can open here."
    unavailable = "This document is missing or cannot be read."
    if not config.is_managed(project) or any(ord(c) < 32 for c in reference):
        raise TaskFileError(outside)
    path = reference
    if reference.startswith("file:"):
        try:
            uri = urlsplit(reference)
            path = unquote(uri.path, errors="strict")
        except ValueError:
            raise TaskFileError(outside) from None
        if not reference.startswith("file:///") or uri.netloc or uri.query or uri.fragment:
            raise TaskFileError(outside)
    if (not path.startswith("/") or any(ord(c) < 32 for c in path)
            or any(p in ("", ".", "..") or "\\" in p for p in path.split("/")[1:])):
        raise TaskFileError(outside)
    try:
        relative = Path(path).relative_to(config.project_dir(project))
    except ValueError:
        raise TaskFileError(outside) from None
    if len(relative.parts) != 3 or relative.parts[0] not in ("tasks", "archive"):
        raise TaskFileError(outside)
    _, slug, name = relative.parts
    try:
        S.require_task_slug(slug)
    except ValueError:
        raise TaskFileError(outside) from None
    if Path(name).suffix.lower() not in (".md", ".txt"):
        raise TaskFileError(unsupported, 415)
    try:
        # Starting at / also refuses symlinks in ancestors of the configured runtime home.
        with _design_directory(Path("/"), list(config.project_dir(project).parts[1:])) as root:
            for location in ("tasks", "archive"):
                try:
                    with _design_directory(root, [location, slug]) as directory:
                        try:
                            record = json.loads(_design_bytes(directory, "status.json", DESIGN_IMAGE_LIMIT))
                        except (OSError, ValueError):
                            raise TaskFileError(unavailable, 404) from None
                        if (not isinstance(record, dict) or record.get("slug") != slug
                                or record.get("state") not in tuple(TRANSITIONS)):
                            raise TaskFileError(unavailable, 404)
                        try:
                            text = _design_bytes(directory, name, TASK_FILE_LIMIT).decode("utf-8")
                        except (ValueError, UnicodeError):
                            raise TaskFileError(unsupported, 415) from None
                        except OSError:
                            raise TaskFileError(unavailable, 404) from None
                        return {"name": name, "path": path,
                                "current_path": str(config.project_dir(project) / location / slug / name),
                                "text": text, "markdown": Path(name).suffix.lower() == ".md"}
                except FileNotFoundError:
                    # Only a missing task directory falls through to its archived identity.
                    if location == "archive":
                        raise
    except TaskFileError:
        raise
    except (OSError, ValueError):
        raise TaskFileError(unavailable, 404) from None
    raise TaskFileError(unavailable, 404)


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
    return task.get("state") == "blocked" and record.get("identity") == ci_recheck_identity(task)


def recheck_ci(project: str, slug: str, run: int, at: str, reason: str, *, actor: str) -> dict:
    """One finite coordinator probe of a blocked owner's CI run, never a resume.

    A fault-blocked owner (2026-09-09 stalled recovery) may get one rerun of an old failed run; a question-blocked
    owner waiting on a queued or running check (#420) only observes it until it is terminal.
    """
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
        if task["state"] != "blocked":
            raise TransitionError("CI recheck requires a blocked task")
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
                  "status": "pending", "reads": 0, "deadline": (due + timedelta(hours=2)).isoformat(),
                  "wait": not task.get("fault")}
        task["ci_recheck"] = record
        S.save_task(project, task)
        S.append_event(project, slug, "ci-recheck", request_id=record["id"], by=actor,
                       reason=record["reason"], run=run, due_at=record["due_at"])
        return record


def _conversation_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")  # string order is time order


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
    try:
        contents = path.read_text()
    except FileNotFoundError:
        contents = ""
    S.atomic_write(path, contents + json.dumps(row, sort_keys=True) + "\n")


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


def _report_correction(task: dict, actor: str | None) -> bool:
    """#553: L3 can return the current contradicted report without an open delivery PR."""
    verified = task.get("verified") or {}
    return (actor == "l3" and verified.get("verdict") == "contradicted"
            and verified.get("owner") == report_owner(task)
            and verified.get("delivery") == task.get("delivery"))


def reported_continuable(task: dict, report: dict | None, *, actor: str | None = None) -> bool:
    """Offer open-PR continuation, or L3 correction of the current contradicted report."""
    return bool(task.get("state") == "reported" and task.get("agent_id") and task.get("session_id")
                and task.get("worktree") and (_report_correction(task, actor) or any(
                    pr.get("number") in task.get("prs", []) and pr.get("merged") is False
                    for pr in ((report or {}).get("landed") or {}).get("prs", []))))


def continue_report(project: str, task: dict, *, actor: str, reason: str, check_pr: bool = True) -> dict:
    """Under the project lock, retire completion evidence and enter the ordinary resume path."""
    from . import verify
    slug = task["slug"]
    _require_daemon_fence(task, slug)
    report = S.read_json(S.task_dir(project, slug) / "report.json", {})
    if check_pr:
        if not reported_continuable(task, report, actor=actor):
            raise TransitionError(f"{slug}: continuation requires a reported owner with an open PR "
                                  "or L3 correction of its current contradicted report")
    if check_pr and not _report_correction(task, actor):
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
            stop_id: str | None = None,
            uploads: list[dict] | None = None, image_ids: list[str] | None = None,
            request_id: str | None = None, request_digest: str | None = None,
            summary: str | None = None) -> dict:
    """Append one message to the task conversation. The operator's and L3's messages also wait in the task's inbox until
    the worker reads them at its next checkpoint. An L2 names its attempt, so a worker of an earlier attempt cannot speak for
    the current one. L3's one-line `summary` describes its message in the conversation's folded row."""
    if role not in TASK_MESSAGE_ROLES:
        raise TransitionError(f"task message role must be one of {TASK_MESSAGE_ROLES}")
    text = str(text or "").strip()
    if not text and not (uploads or image_ids):
        raise TransitionError("task message is empty")
    summary = " ".join(str(summary or "").split())
    if summary and role != "l3":
        raise TransitionError("only L3's coordination messages carry a summary")
    if len(summary) > SUMMARY_LIMIT:
        raise TransitionError(f"task message summary exceeds {SUMMARY_LIMIT} characters")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if request_id:
            previous = next((row for row in task.get("image_messages", []) if row["id"] == request_id), None)
            if previous:
                if previous.get("request_digest") != request_digest:
                    raise TransitionError("This submission identity already belongs to another message.")
                return {key: value for key, value in previous.items() if key != "delivered"}
        if stop_id is not None and stop_id != task.get("stop_id"):
            raise TransitionError("The stopped session changed. Refresh before sending this correction.")
        allowed = ("running", "blocked", "reported")
        if role != "l2" and (task.get("questions") or not task.get("attempt")):
            allowed += ("queued",)  # prelaunch updates never release a planned wait
        if task.get("state") not in allowed:
            raise TransitionError(f"{slug}: cannot message the L2 in {task.get('state')} state")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        if _ensure_question(project, task):
            S.save_task(project, task)
        row = {"id": request_id or uuid.uuid4().hex, "at": _conversation_time(), "role": role, "text": text, "by": by or role}
        if summary:
            row["summary"] = summary
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
            row.update(question_id=target["id"], question_revision=target["revision"], answers=[target["id"]],
                       question_refs=[{"id": target["id"], "revision": target["revision"]}])
        elif groups or group_id is not None or group_revision is not None:
            group = (_group_target(task, group_id, group_revision)
                     if group_id is not None or group_revision is not None else groups[-1])
            members = _group_members(task, group)
            row.update(group_id=group["id"], group_revision=group["revision"],
                       question_refs=[{"id": q["id"], "revision": q["revision"]} for q in members])
            if len(members) == 1:
                row.update(question_id=members[0]["id"], question_revision=members[0]["revision"])
        d = S.task_dir(project, slug)
        if uploads or image_ids:
            if role not in (OPERATOR_MESSAGE_ROLE, "l3") or uploads and image_ids:
                raise TransitionError("Images must be operator input or an explicit coordinator handoff.")
            refs = (image_store.store(project, uploads, message_id=row["id"], task=slug) if uploads
                    else image_store.lookup(project, image_ids))
            row.update(images=refs, request_digest=request_digest)
        if role != "l2" and task.get("state") == "reported":
            task = continue_report(project, task, actor=role, reason="Follow-up message")
        if row.get("images"):
            # One atomic record owns image-message admission, its receipt and pending delivery,
            # just as question acceptance does. A lost response cannot split conversation/inbox.
            task.setdefault("image_messages", []).append({**row, "delivered": False})
        else:
            _append_jsonl(d / "conversation.jsonl", row)
        if role in (OPERATOR_MESSAGE_ROLE, "l3"):  # an answer waits in the inbox until the worker reads it
            if not row.get("images"):
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
        if role == OPERATOR_MESSAGE_ROLE:
            task["handed_back"] = row["at"]
        if row.get("images") or role == OPERATOR_MESSAGE_ROLE:
            S.save_task(project, task)
        S.append_event(project, slug, "task-message", message_id=row["id"], role=role, by=row["by"])
        return row


def notify(project: str, slug: str, text: str, *, by: str, attempt: int) -> dict | None:
    """Leave an automatic notice for the owner's next checkpoint, without a conversation entry, and wake an owner
    that is blocked. Stop and a fault still hold it: the notice waits for the next resume. A task with no owner
    session, or one on a later attempt than the notice's, gets none."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") not in ("running", "blocked") or task.get("attempt") != attempt:
            return None
        row = {"id": uuid.uuid4().hex, "at": S.now(), "text": text, "by": by}
        _append_jsonl(S.task_dir(project, slug) / "inbox.jsonl", row)
        if task["state"] == "blocked" and not task.get("stop_id") and not task.get("fault"):
            task["resume_request"] = row["id"]
            task["resume_after"] = task.get("resume_after") or S.now()
            task.pop("resume_failed", None)
            S.save_task(project, task)
            S.append_event(project, slug, "resume-requested", by=by, reason="notice", message_id=row["id"])
            S.regen_state_md(project)
    return row


def task_messages(project: str, slug: str, limit: int | None = None) -> list[dict]:
    """The durable task conversation."""
    rows = _rows(S.task_dir(project, slug) / "conversation.jsonl", "task conversation")
    rows.extend({key: value for key, value in row.items() if key != "delivered"}
                for row in S.load_task(project, slug).get("image_messages", []))
    # Question anchors and quick-accept messages are saved atomically with their question record.
    # Project them into the ordinary human thread without a second multi-file commit protocol.
    task = S.load_task(project, slug)
    for question in task.get("questions", []):
        rows.append(question["message"])
        if question.get("acceptance_message"):
            rows.append(question["acceptance_message"])
    rows = list({row["id"]: row for row in rows}.values())
    for row in rows:
        receipt = (task.get("message_deliveries") or {}).get(row["id"], {})
        if receipt.get("state") == "removed":
            row["removed_at"] = receipt["at"]
    rows.sort(key=lambda row: row["at"])
    if any(row.get("role") not in TASK_MESSAGE_ROLES for row in rows):
        raise ValueError(f"corrupt task conversation of {project}/{slug}: invalid role")
    rows.extend(review["message"] for review in task.get("reviews", []))
    rows.sort(key=lambda row: row["at"])
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


def removable_messages(project: str, slug: str, task: dict) -> set[str]:
    """Only unclaimed operator text can be withdrawn; recorded decisions keep their evidence."""
    if task.get("state") not in ("running", "blocked", "queued"):
        return set()
    protected = {row["id"] for row in (task.get("resume_claim") or {}).get("messages", [])}
    protected.update(task.get("message_deliveries") or {})
    for question in task.get("questions", []):
        protected.add((question.get("acceptance_message") or {}).get("id"))
        resolution = question.get("resolution") or {}
        if resolution.get("source") == "task":
            protected.add(resolution.get("message_id"))
    for receipt in [task.get("merge_approval") or {},
                    *(event for event in S.read_events(project, slug) if event["kind"] == "release-merge")]:
        if receipt.get("source", "task") == "task":
            protected.update((receipt.get("approval"), receipt.get("latest_operator")))
        else:
            protected.add(receipt.get("latest_other_operator"))
    return {row["id"] for row in pending(project, slug)
            if row.get("role") == row.get("by") == OPERATOR_MESSAGE_ROLE and row["id"] not in protected}


def remove_message(project: str, slug: str, message_id: str) -> None:
    """Withdraw one inbox message without erasing original text or changing lifecycle requests."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if message_id not in removable_messages(project, slug, task):
            raise TransitionError("This message can no longer be removed. Refresh its delivery status.")
        task.setdefault("message_deliveries", {})[message_id] = {"state": "removed", "at": S.now()}
        S.save_task(project, task)


def message_views(project: str, slug: str, delivered: list[dict]) -> list[dict]:
    with S.project_lock(project):
        return _message_views(project, slug, S.load_task(project, slug), delivered)


def _message_views(project: str, slug: str, task: dict, delivered: list[dict]) -> list[dict]:
    """Claimed and uncertain handoffs never promise cancellation or confirmed delivery."""
    receipts = {row["message_id"]: {"at": row.get("at")} for row in delivered}
    receipts = {**(task.get("message_deliveries") or {}), **receipts}
    queued = {row["id"] for row in pending(project, slug)}
    claimed = {row["id"] for row in (task.get("resume_claim") or {}).get("messages", [])}
    removable = removable_messages(project, slug, task) - receipts.keys()
    rows = task_messages(project, slug)
    for row in rows:
        if row["role"] not in (OPERATOR_MESSAGE_ROLE, "l3"):
            continue
        receipt = receipts.get(row["id"])
        state = ("removed" if row.get("removed_at") else "sending" if row["id"] in claimed
                 else receipt.get("state", "delivered") if receipt else "queued" if row["id"] in queued else "unconfirmed")
        row["delivery"] = {"state": state, "at": receipt.get("at") if receipt else None,
                           "removable": row["id"] in removable}
    return rows


def pending(project: str, slug: str) -> list[dict]:
    """What waits for the worker's next checkpoint."""
    return _pending_rows(S.load_task(project, slug), S.task_dir(project, slug) / "inbox.jsonl")


def _pending_rows(task: dict, path: Path) -> list[dict]:
    receipts = task.get("message_deliveries") or {}
    finished = {key for key, receipt in receipts.items() if receipt.get("state", "delivered") in ("removed", "delivered")}
    rows = [row for row in _rows(path, "task inbox") if row["id"] not in finished]
    if task.get("state") not in ("running", "blocked", "queued"):
        return rows  # historical receipts do not create new delivery work after the owner hands off
    seen = {row["id"] for row in rows}
    for review in task.get("reviews", []):
        message = review["message"]
        if (review["state"] == "requested" and not review.get("delivered")
                and message["id"] not in seen and message["id"] not in finished):
            rows.append(message)
            seen.add(message["id"])
    for message in task.get("image_messages", []):
        if (not message.get("delivered") and message["id"] not in seen
                and message["id"] not in finished):
            rows.append({key: value for key, value in message.items() if key != "delivered"})
            seen.add(message["id"])
    for question in task.get("questions", []):
        message = question.get("acceptance_message")
        if message and not question.get("acceptance_delivered") and message["id"] not in seen:
            rows.append(message)
            seen.add(message["id"])
    return sorted(rows, key=lambda row: row["at"])


def _mark_acceptance_delivered(task: dict, ids: set[str]) -> None:
    for review in task.get("reviews", []):
        if review["id"] in ids:
            review["delivered"] = True
    for message in task.get("image_messages", []):
        if message["id"] in ids:
            message["delivered"] = True
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
        request = task.get("daemon_request") or {}
        if expected_daemon_request and request.get("deliver_reason") and not request.get("message_id"):
            # I-20260927-193716: the reason that authorizes a resume reaches the owner as the requester's message.
            # Its request was the wake, so a batch restored after a failed launch holds it without waking again.
            reason = {"id": request["id"], "at": _conversation_time(), "role": request["actor"],
                      "by": request["actor"], "text": request["reason"], "resume": True, "wake": False}
            if request["actor"] == "l3":
                reason["summary"] = "Resumed the task"
            _append_jsonl(S.task_dir(project, slug) / "conversation.jsonl", reason)
            request["message_id"] = reason["id"]
            rows.append(reason)
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
        if updates.get("phase") in ("launching", "launched"):
            for row in claim["messages"]:
                task.setdefault("message_deliveries", {}).setdefault(row["id"], {"state": "unconfirmed", "at": None})
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
        if claim.get("phase") != "claimed":
            for row in claimed:
                task.setdefault("message_deliveries", {}).setdefault(row["id"], {"state": "unconfirmed", "at": None})
            _save_claim_task(project, task)
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


def take_inbox(project: str, slug: str, ids: set[str] | None = None, *, running_only: bool = False) -> list[dict]:
    """Remove delivered messages from the inbox (all of them, or only `ids`) and return them."""
    path = S.task_dir(project, slug) / "inbox.jsonl"
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("stop_id"):
            return []  # #302: Stop holds hook delivery too, until a replacement worker binds.
        if running_only and task.get("state") != "running":
            return []
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


def _sender(row: dict) -> str:
    by = row.get("by") or OPERATOR_MESSAGE_ROLE
    return config.operator_label() if by == OPERATOR_MESSAGE_ROLE else by.capitalize()


def render_inbox(rows: list[dict]) -> str:
    """The messages as the worker reads them: the words, their sender and the id a decision cites.
    The worker already holds its persona, brief and its own questions; nothing else is repeated here."""
    return "\n\n".join((f"Resumed by {_sender(row)} at {row['at']} with this reason (message id {row['id']}"
                        if row.get("resume") else f"Message from {_sender(row)} (message id {row['id']}")
                       + (f"; answers question {', '.join(row['answers'])}" if row.get("answers") else "")
                       + f"):\n{row['text']}"
                       + ("\nImages: " + ", ".join(f"{image['id']} ({image['name']})" for image in row["images"])
                          if row.get("images") else "")
                       for row in rows)


def _clear_block(project: str, task: dict) -> None:
    _ensure_question(project, task)
    task["blocked_reason"] = None
    for key in ("resume_after", "resume_request", "resume_claim", "resume_failed", "waiting_on", "fault", "escalated", "block_actor", "usage_limit", "stop_id"):
        task.pop(key, None)


def _take_turn(task: dict) -> None:
    """An owner's park or report gives the operator the turn again; what they discussed is asked again."""
    since = task.pop("handed_back", None)
    if not since:
        return
    for question in task.get("questions", []):
        if question["status"] == "open" and not question.get("response") and question["asked"] < since:
            question["asked_again"] = True


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
        effort: str | None = None, image_ids: list[str] | None = None,
        wait: str | None = None, after: str | None = None) -> dict:
    if source not in ("chat", "recovery"):
        raise TransitionError("task source must be chat or recovery")
    if wait is not None and after is not None:
        raise TransitionError("choose --wait or --after, not both")
    planned = None
    if wait is not None or after is not None:
        reason = (wait if wait is not None else S.require_task_slug(after)).strip()
        if not reason or len(reason) > 160 or len(reason.splitlines()) != 1 or not request.strip():
            raise TransitionError("a planned task needs a written brief and one wait reason of 1–160 characters")
        planned = {"reason": reason, "after": after}
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
        if after is not None:
            S.load_task(project, after)  # dependencies are existing tasks in this project
        refs = image_store.lookup(project, image_ids) if image_ids else []
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
        if planned:
            task["planned_wait"] = None if after and _dependency_done(project, after) else planned
        if refs:
            task["images"] = refs
        S.save_task(project, task)
        S.append_event(project, slug, "new", by=actor, title=title, source=source, queued=True, planned_wait=planned)
        S.regen_state_md(project)
        return task


def _dependency_done(project: str, slug: str) -> bool:
    return (S.read_json(S.archive_dir(project) / S.require_task_slug(slug) / "status.json", {})
            .get("state") == "done")


def _release_wait(project: str, task: dict, reason: str, actor: str) -> dict:
    previous = task.pop("planned_wait")
    S.save_task(project, task)
    S.append_event(project, task["slug"], "released", by=actor, reason=reason, planned_wait=previous)
    S.regen_state_md(project)
    return task


def release(project: str, slug: str, reason: str, actor: str = "l3") -> dict:
    """Clear one explicit wait; dispatch and merge gates retain their own authority."""
    if actor not in ("l3", OPERATOR_MESSAGE_ROLE) or not reason.strip():
        raise TransitionError("only L3 or the operator can release a planned task, with a reason")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "queued" or not task.get("planned_wait"):
            raise TransitionError(f"{slug}: no planned wait to release")
        return _release_wait(project, task, reason.strip(), actor)


def release_dependency(project: str, slug: str) -> dict:
    """Recheck on each dispatch pass, including after a restart or interrupted archive."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        after = (task.get("planned_wait") or {}).get("after")
        if task["state"] == "queued" and after and _dependency_done(project, after):
            return _release_wait(project, task, f"{after} archived done", "altd")
        return task


def reject(project: str, slug: str, reason: str, actor: str = OPERATOR_MESSAGE_ROLE, *,
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
             engine_model: str | None = None, routing: str | None = None, actor: str = "altd",
             messages: list[dict] | None = None, input_delivered: bool = False) -> dict:
    if not session_id or not agent_id:
        raise TransitionError(f"{slug}: dispatch requires a concrete worker and session")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug)
        if task["state"] != "queued":
            raise TransitionError(f"{slug}: dispatch no longer owns a queued task")
        if task.get("planned_wait"):
            raise TransitionError(f"{slug}: planned wait must be released before dispatch")
        usage.remember(task)
        task.update({"attempt": attempt, "session_id": session_id, "agent_id": agent_id, "worktree": worktree,
                     "branch": branch, "blocked_reason": None, "l2_engine": l2_engine, "engine_model": engine_model,
                     "routing": routing, "dispatched": S.now()})
        task.pop("next_engine", None)
        ids = {row["id"] for row in (messages or [])} if input_delivered else set()
        for message_id in ids:
            task.setdefault("message_deliveries", {})[message_id] = {
                "at": S.now(), "agent_id": agent_id, "session_id": session_id}
        _mark_acceptance_delivered(task, ids)
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
        _take_turn(task)
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
                    or (questions is not None and len(_validate_questions(questions)) != 1)
                    or (updates or {}).get("fault") or task.get("fault")):
                raise TransitionError("design publication requires the current L2 and one question")
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
            if not task.get("resume_after"):  # a message queued for the next turn keeps it the L2's
                _take_turn(task)
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


def _reverify(project: str, slug: str) -> dict | None:
    """A reported delivery whose recorded verdict is not ok is checked against GitHub again at completion (#456)."""
    from . import verify
    task = S.load_task(project, slug)
    if task.get("state") != "reported" or not task.get("delivery") or (task.get("verified") or {}).get("verdict") == "ok":
        return None
    try:
        return {**verify._verify(project, slug), "owner": report_owner(task)}
    except verify.VerifierFault as exc:
        raise TransitionError(f"{slug}: cannot verify the current delivery: {exc}") from exc


def done(project: str, slug: str, actor: str = "l3", digest: str = "", *,
         expected_state: str | None = None, expected_attempt: int | None = None,
         expected_owner: dict | None = None, findings_tracked: str = "") -> dict:
    """Complete a reported task. `findings_tracked` names where coordination tracks the report's open review
    findings (#462): a verified merged delivery then completes with those findings recorded in the done event
    and digest instead of staying stuck behind the verifier's open-finding problem."""
    from . import verify
    findings_tracked = str(findings_tracked or "").strip()
    fresh = _reverify(project, slug) if actor != "l2" else None
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _require_daemon_fence(task, slug)
        if expected_state is not None and task.get("state") != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task.get('state')}")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: attempt {expected_attempt} is no longer current")
        if actor == "l2":  # archived by the server once this worker has exited
            if findings_tracked:
                raise TransitionError(f"{slug}: coordination records where open review findings are tracked")
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
        if fresh and task.get("state") == "reported" and fresh.get("delivery") == delivery \
                and fresh.get("owner") == report_owner(task):
            verified = task["verified"] = {**fresh, "attempt": task["attempt"]}
            S.save_task(project, task)
            S.append_event(project, slug, "report-reverified", verdict=fresh["verdict"], problems=fresh["problems"], by=actor)
        if ((expected_owner is not None and expected_owner != report_owner(task))
                or (task.get("report_after") and verified.get("owner") != report_owner(task))
                or any(row.get("wake", True) for row in pending(project, slug))):
            raise TransitionError(f"{slug}: follow-up work requires a current report before completion")
        d = S.task_dir(project, slug)
        tracked = _tracked_findings(slug, S.read_json(d / "report.json"), findings_tracked)
        if delivery and (not delivery.get("number") or verified.get("delivery") != delivery
                         or (verified.get("verdict") != "ok"
                             and not (tracked and verified.get("problems") == [verify.OPEN_FINDINGS]))):
            raise TransitionError("; ".join([f"{slug}: current delivery requires a verified report before completion",
                                             *(verified.get("problems") or [])]))
        task = _move(project, task, "done", actor, **({"findings_tracked": tracked} if tracked else {}))
        if tracked:
            digest = "\n".join([digest.rstrip(), "", f"Open review findings tracked at {tracked['reference']}:",
                                *(f"- {finding}" for finding in tracked["findings"])]).lstrip()
        if digest:
            S.atomic_write(d / "digest.md", digest.rstrip() + "\n")
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def _tracked_findings(slug: str, report: dict | None, reference: str) -> dict | None:
    """The coordinator's tracking reference names every open finding on the report, or nothing."""
    from . import verify
    if not reference:
        return None
    findings = [str(finding.get("summary") or finding.get("reason") or "untitled finding")
                for finding in verify.open_findings(report)]
    if not findings:
        raise TransitionError(f"{slug}: the report has no open review findings to track")
    return {"reference": reference, "findings": findings}


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
    fyi(project, slug, " ".join(filter(None, (f"{task['title']} completed without code changes.", digest.strip(),
                                              "Its findings stay in the task conversation."))), actor=actor)
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


def _audience(block: str, previous: dict | None, text: str) -> str:
    """A member follows the audience of the block that publishes it. Re-parking an unchanged operator question keeps
    it the operator's, so an escalation is never parked away; a reworded one, such as a wait on L3 or an external
    event, leaves the operator's list."""
    unchanged = previous is not None and previous["detail"].strip() == text.strip()
    return "operator" if block == "operator" or (unchanged and previous["audience"] == "operator") else "l3"


def _publish_question(task: dict, text: str, actor: str, *, recommendation: str | None = None,
                      label: str | None = None, why: str | None = None, force_revision: bool = False,
                      previous: object = _UNSET, group: dict | None = None, options: list[dict] | None = None,
                      recommended_key: str | None = None, bump: bool = True, design: object = _UNSET,
                      audience: str | None = None) -> dict:
    questions = task.setdefault("questions", [])
    if previous is _UNSET:
        previous = questions[-1] if questions else None
    if design is _UNSET:
        design = previous.get("design") if previous and (previous["status"] == "open" or force_revision) else None
    groups = _store_groups(task)
    audience = audience or (previous["audience"] if force_revision else "l3" if task.get("waiting_on") == "l3" else "operator")
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
    if (previous and previous["status"] == "open" and not force_revision and previous["detail"] == text
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


def _publish_questions(task: dict, payload: dict, actor: str, reason: str, audience: str, *,
                       design: dict | None = None) -> list[dict]:
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
    if sum(q["status"] == "open" for q in members) + sum(previous is None for _, previous in targets) > 3:
        raise TransitionError("a group has at most three open questions; resolve existing questions before adding another")
    before = len(task.get("questions", []))
    for item, previous in targets:
        keep_options = previous and previous["detail"] == item["question"] and not item["options_supplied"]
        options = question_choices(previous) if keep_options else item["options"]
        recommended = _recommended_key(previous) if keep_options else item["recommended_key"]
        why = ((previous.get("recommendation") or {}).get("why", "")
               if keep_options and not item["why_supplied"] else item["why"])
        _publish_question(task, item["question"], actor, previous=previous, group=group, bump=False,
                          force_revision=bool(previous and previous.get("response")),
                          options=options, recommended_key=recommended, why=why,
                          design=design if design is not None else _UNSET,
                          audience=_audience(audience, previous, item["question"]))
    group["reason"] = reason
    if len(task["questions"]) != before:
        group["revision"] += 1
    return _group_members(task, group)


def _publish_block_questions(task: dict, reason: str, actor: str, payload: dict | None,
                             recommendation: str | None, label: str | None, why: str | None,
                             *, design: dict | None = None) -> list[dict]:
    members = _publish_block_members(task, reason, actor, payload, recommendation, label, why, design=design)
    # The operator has the turn only while an open member is theirs; otherwise the block waits on L3.
    if any(q["status"] == "open" and q["audience"] == "operator" for q in members):
        task["waiting_on"] = OPERATOR_MESSAGE_ROLE
    return members


def _publish_block_members(task: dict, reason: str, actor: str, payload: dict | None,
                           recommendation: str | None, label: str | None, why: str | None,
                           *, design: dict | None = None) -> list[dict]:
    groups = _store_groups(task)
    group = groups[-1] if groups else None
    members = _group_members(task, group) if group else []
    pending = [q for q in members if q["status"] == "open"]
    audience = "l3" if task.get("waiting_on") == "l3" else "operator"
    if payload is not None:
        if any(value is not None for value in (recommendation, label, why)):
            raise TransitionError("questions JSON supplies its own options and recommendation")
        return _publish_questions(task, payload, actor, reason, audience, design=design)
    previous = next((q for q in pending if q["detail"].strip() == reason.strip()), None)
    no_replacement = all(value is None for value in (recommendation, label, why))
    if design is None and no_replacement and group and reason.strip() == group["reason"].strip() and pending:
        # The ordinary block verb parks the same whole group after discussing a follow-up.
        if all(q["audience"] == _audience(audience, q, q["detail"]) for q in pending):
            return members
        for question in pending:
            _publish_question(task, question["detail"], actor, previous=question, group=group, bump=False,
                              options=question_choices(question), recommended_key=_recommended_key(question),
                              why=(question.get("recommendation") or {}).get("why"),
                              audience=_audience(audience, question, question["detail"]))
        group["revision"] += 1
        return _group_members(task, group)
    if previous is None and len(pending) > 1:
        raise TransitionError("several questions remain open; use --questions-file with their ids or park with the saved group reason")
    previous = previous or (pending[0] if pending else None)
    target = _audience(audience, previous, reason)
    if previous and previous["detail"].strip() == reason.strip() and no_replacement:
        if previous["audience"] == target and (design is None or previous.get("design") == design):
            return members
        _publish_question(task, reason, actor, previous=previous, group=group,
                          options=question_choices(previous), recommended_key=_recommended_key(previous),
                          why=(previous.get("recommendation") or {}).get("why"),
                          design=design if design is not None else _UNSET, audience=target)
    else:
        _publish_question(task, reason, actor, previous=previous, group=group if pending else None,
                          recommendation=recommendation, label=label, why=why,
                          design=design if design is not None else _UNSET, audience=target)
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


def open_questions(task: dict) -> str:
    """A replacement session never saw its predecessor's questions; a running one already holds them."""
    rows = [q for q in task.get("questions", []) if q["status"] == "open"]
    return "".join(f"\n- Open question {q['id']} (for {q['audience']}): {q['detail']}"
                   + (f" Response in message {q['response']['message_id']}: {q['response']['text']}"
                      if q.get("response") else "") for q in rows)


def question_view(project: str, task: dict, question: dict) -> dict:
    group = _group_for(task, question)
    return {**{k: v for k, v in question.items() if k not in ("message", "acceptance_message", "acceptance_delivered", "design")},
            **({"design_url": design_url(project, task["slug"], question)} if question.get("design") else {}),
            "options": question_choices(question), "recommended_key": _recommended_key(question),
            "response": question.get("response"),
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
        raise TransitionError("response must name the question and its integer revision")
    question = next((q for q in task.get("questions", [])
                     if q["id"] == identity and q["revision"] == revision), None)
    if not question:
        raise TransitionError("question is unavailable; refresh the conversation")
    return question


def _decision_messages(project: str, slug: str, source: str) -> list[dict]:
    if source == "task":
        # A resume reason authorizes that resume only; it never answers a question or approves a merge.
        return [row for row in task_messages(project, slug) if not row.get("removed_at") and not row.get("resume")]
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
                     l3_authority: str | None = None, exact: bool = False) -> dict:
    """Original authority and viewed revision shared by decisions and merge reconciliation."""
    row = next((r for r in _decision_messages(project, slug, source) if r["id"] == message_id), None)
    authorized = row and ((row["role"] == OPERATOR_MESSAGE_ROLE and row.get("by") == OPERATOR_MESSAGE_ROLE)
                          or (source == "task" and (question["audience"] == "l3" or l3_authority)
                              and row["role"] == "l3" and row.get("by") == "l3"))
    if l3_authority and not (source == "task" and row and row["role"] == "l3" and row.get("by") == "l3"):
        raise TransitionError("L3 authority must cite an original L3 task message")
    if not authorized:
        raise TransitionError("resolution must cite an original message with authority for this question")
    # An operator answer survives re-publication. Design approvals and machine grants (`exact`) stay bound
    # to the captures or purpose the operator actually read.
    relaxed = row["role"] == OPERATOR_MESSAGE_ROLE and not exact and not question.get("design")
    if source == "task" and not relaxed:
        refs = row.get("question_refs")
        if l3_authority and {"id": question["id"], "revision": question["revision"]} not in (refs or []):
            raise TransitionError("L3 authority source must name this exact question revision")
        if refs is not None:
            if {"id": question["id"], "revision": question["revision"]} not in refs:
                raise TransitionError("source message discusses a different question revision")
        elif row.get("question_id") and (row["question_id"], row.get("question_revision")) != (question["id"], question["revision"]):
            raise TransitionError("source message discusses a different question revision")
    if relaxed and (row.get("question_refs") or row.get("question_id")) and question["id"] not in (
            {r["id"] for r in row.get("question_refs") or []} | {row.get("question_id")}):
        raise TransitionError("source message answers a different question")
    asked = min(q["asked"] for q in S.load_task(project, slug).get("questions", [question])
                if q["id"] == question["id"]) if relaxed else question["asked"]
    if (row["at"][:19] < asked[:19] if source == "project" else row["at"] < asked):
        raise TransitionError("source message predates this question")
    return row


def resolve_question(project: str, slug: str, identity: str, revision: int | None, message_id: str | None, *,
                     disposition: str, reason: str, expected_attempt: int, source: str = "task",
                     remaining: str | None = None, recommendation: str | None = None,
                     recommendation_label: str | None = None, recommendation_why: str | None = None,
                     l3_authority: str | None = None) -> dict:
    """The owner records a sourced decision or withdraws its question without granting authority."""
    if disposition not in ("answered", "superseded", "withdrawn") or not reason.strip():
        raise TransitionError("resolution needs answered/superseded/withdrawn and a concrete reason")
    withdrawn = disposition == "withdrawn"
    if not withdrawn and not message_id:
        raise TransitionError("answered/superseded resolution requires an original source message id")
    if withdrawn and (source != "task" or any(value is not None for value in (
            message_id, remaining, recommendation, recommendation_label, recommendation_why, l3_authority))):
        raise TransitionError("withdrawal records only the owner's reason, not a sourced decision or remaining question")
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
        if revision is None:  # the current revision of this question
            revision = next((q["revision"] for q in reversed(task.get("questions", [])) if q["id"] == identity), None)
        question = _question_target(task, identity, revision)
        row = ({"role": "l2", "by": "l2"} if withdrawn else
               _decision_source(project, slug, question, message_id, source, l3_authority=l3_authority))
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


def accept_question(project: str, slug: str, identity: str, revision: int, option_key: str | None = None,
                    *, text: object = _UNSET) -> dict:
    """Send one response; omission selects only an explicitly recommended choice."""
    return accept_question_result(project, slug, identity, revision, option_key, text=text)["question"]


def accept_question_result(project: str, slug: str, identity: str, revision: int, option_key: str | None = None,
                           *, text: object = _UNSET) -> dict:
    if text is not _UNSET and option_key is not None:
        raise TransitionError("send a quick option or custom text, not both")
    answer = {"option_key": option_key} if text is _UNSET else {"text": text}
    return _accept_questions(project, slug, [{"question_id": identity, "revision": revision, **answer}])


def accept_questions(project: str, slug: str, group_id: str, group_revision: int, answers: list[dict]) -> dict:
    """Atomically record a selected subset of a group with one normal message and wake request."""
    if (not isinstance(group_id, str) or not group_id or isinstance(group_revision, bool)
            or not isinstance(group_revision, int)):
        raise TransitionError("batch response must name the question group and its integer revision")
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
    if any(not isinstance(answer, dict) or set(answer) not in (
            {"question_id", "revision", "option_key"}, {"question_id", "revision", "text"})
           for answer in answers):
        raise TransitionError("answers require question_id, revision and either option_key or text")
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
            if "text" in answer:
                if not isinstance(answer["text"], str) or not answer["text"].strip():
                    raise TransitionError("custom response needs nonempty text")
                option = {"text": answer["text"].strip()}
            else:
                key = answer["option_key"]
                if key is None and not batch:
                    key = _recommended_key(question)
                option = next((o for o in question_choices(question) if o["key"] == key), None)
                if not option:
                    raise TransitionError("this question has no explicit recommendation or matching quick option")
            chosen.append((question, option))
        group = _group_for(task, chosen[0][0])
        if any(_group_for(task, q)["id"] != group["id"] for q, _ in chosen):
            raise TransitionError("all answers must belong to the same question group")
        canonical = {"group_id": group_id, "group_revision": group_revision,
                     "answers": sorted([{"question_id": q["id"], "revision": q["revision"],
                                          **({"option_key": o["key"]} if "key" in o else {"text": o["text"]})}
                                        for q, o in chosen], key=lambda answer: answer["question_id"])}
        saved = next((s for s in group.get("submissions", []) if s["request"] == canonical), None)
        if saved:
            _queue_acceptance(project, task, chosen[0][0])
            return _submission_response(project, task, saved)
        if batch:
            checked = _group_target(task, group_id, group_revision)
            if checked["id"] != group["id"]:
                raise TransitionError("answers do not belong to this question group")
        _require_daemon_fence(task, slug)
        if (any(q["status"] != "open" or q["audience"] != "operator" or q.get("response") for q, _ in chosen)
                or task["state"] not in ("running", "blocked", "queued")):
            raise TransitionError("question is no longer open for responses; refresh the conversation")
        at, message_id = _conversation_time(), uuid.uuid4().hex
        text = "\n\n".join(f"{q['question']}\n{o['text']}" for q, o in chosen)
        row = {"id": message_id, "at": at, "role": OPERATOR_MESSAGE_ROLE, "by": OPERATOR_MESSAGE_ROLE,
               "text": text, "group_id": group["id"], "group_revision": group["revision"],
               "question_refs": [{"id": q["id"], "revision": q["revision"]} for q, _ in chosen]}
        if len(chosen) == 1:
            row.update(question_id=chosen[0][0]["id"], question_revision=chosen[0][0]["revision"])
        for question, option in chosen:
            question["acceptance_message"] = row
            question["response"] = {"text": option["text"], "at": at, "message_id": message_id}
        task["handed_back"] = at
        group["revision"] += 1
        row["answers"] = [q["id"] for q, _ in chosen]
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
            S.append_event(project, slug, "question-response", question_id=question["id"], revision=question["revision"],
                           **question["response"])
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


def operator_questions(task: dict) -> list[dict]:
    """Open operator questions on the operator's turn; a reply hands the task's earlier questions back to its L2."""
    since = task.get("handed_back") or ""
    return [q for q in task.get("questions", []) if q["status"] == "open" and q["audience"] == "operator"
            and not q.get("response") and q["asked"] > since]


def _names_pr(number: int) -> re.Pattern:
    return re.compile(rf"(/pull/|PR #?){number}\b")


def _closed(events: list[dict], number: int) -> bool:
    """#575: the latest delivery, adoption or observed state of this PR says it closed without merging."""
    latest = next((e for e in reversed(events) if e.get("number") == number and e.get("kind") in (
        "delivery", "pr-adopted", "pr-closed", "pr-reopened")), {})
    return latest.get("kind") == "pr-closed"


def _held_pr(task: dict, events: list[dict]) -> int | None:
    """The task's current PR under a merge hold, unless Altitude has observed it closed without merging."""
    number = (task.get("prs") or [None])[-1]
    return number if number and task.get("hold_merge") and not _closed(events, number) else None


def record_pr_state(project: str, slug: str, number: int, state: str, *, by: str) -> bool:
    """Record an observed closure or reopening of the task's current PR once; delivery, hold and history stay."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if (state not in ("OPEN", "CLOSED") or (task.get("prs") or [None])[-1] != number
                or _closed(S.read_events(project, slug), number) == (state == "CLOSED")):
            return False
        S.append_event(project, slug, "pr-closed" if state == "CLOSED" else "pr-reopened", number=number, by=by)
    return True


def observe_held_pr(project: str, slug: str, *, by: str) -> str | None:
    """Read the held PR's GitHub state as its owner stops; an unreadable state leaves the recorded review as is.
    The read names the checkout origin's repository and checks the returned identity, so an inherited
    GH_REPO or a same-numbered PR elsewhere cannot record a closure (review of #575)."""
    from . import github_intake, verify
    task = S.load_task(project, slug)
    number = (task.get("prs") or [None])[-1]
    if not (number and task.get("hold_merge")):
        return None
    try:
        owner, repo = github_intake.project_repo(project)
        info = verify.gh(["pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", "number,url,state"],
                         config.project_path(project))
    except (verify.VerifierFault, github_intake.IssueIntakeError, KeyError) as exc:
        return f"PR #{number} state unavailable: {exc}"
    url = f"https://github.com/{owner}/{repo}/pull/{number}"
    if not (isinstance(info, dict) and info.get("number") == number and str(info.get("url", "")).lower() == url.lower()
            and info.get("state") in ("OPEN", "CLOSED", "MERGED")):
        return f"PR #{number} state unavailable: GitHub returned no matching record"
    record_pr_state(project, slug, number, info["state"], by=by)
    return None


def approved_pr(project: str, task: dict) -> int | None:
    """#451: the operator's review-card approval of the held PR stands through routine integration until the
    hold changes or a later operator message about the PR; the owner judges scope and applies it with
    `alt land --merge --approval`, asking again only when the change materially conflicts with it."""
    events = S.read_events(project, task["slug"])
    number = _held_pr(task, events)
    if not number:
        return None
    holds = [e["at"] for e in events if e.get("kind") in ("new", "hold-merge")]
    # Records keep whole seconds; an approval must come in a later second than the hold.
    second = lambda at: datetime.fromisoformat(at.replace("Z", "+00:00")).replace(microsecond=0)
    since = second(holds[-1] if holds else "1970-01-01T00:00:00+00:00")
    approved, names = False, _names_pr(number)
    # The card's message; earlier cards also named the head they showed, which records no restriction.
    card = re.compile(rf"Approved: merge PR #{number}( at [0-9a-f]{{7}})?\.")
    for row in task_messages(project, task["slug"]):
        if row.get("role") != OPERATOR_MESSAGE_ROLE or row.get("removed_at") or second(row["at"]) <= since:
            continue
        if card.fullmatch(row.get("text", "").strip()):
            approved = True
        elif names.search(row.get("text", "")):
            approved = False  # a later word about the PR may condition or revoke it
    return number if approved else None


def _references_held_pr(task: dict, question: dict) -> bool:
    """The owning PR question supplies the response surface, including a freeform field."""
    number = (task.get("prs") or [None])[-1]
    return bool(number and task.get("hold_merge") and question["audience"] == "operator"
                and _names_pr(number).search(" ".join([question["detail"], *(
                    f"{o['label']} {o['text']}" for o in question_choices(question))])))


def review_pr(project: str, task: dict) -> int | None:
    """#419: a held delivery whose owner has stopped waits for the operator's review, question or not.
    An open operator question naming the PR supplies its single response surface, with quick choices
    or a freeform field. Its submitted response stays there until the owner resolves the question.
    The question does not supply merge authority; recorded approval keeps its separate rules (#451).
    A PR observed closed without merging asks for no review (#575)."""
    number = _held_pr(task, S.read_events(project, task["slug"]))
    if (number and (task.get("delivery") or task.get("adopted_pr"))
            and task.get("state") in ("blocked", "reported") and not any(
                task.get(key) for key in ("handed_back", "resume_after", "fault", "stop_id"))
            and not any(_references_held_pr(task, q) for q in task.get("questions", []) if q["status"] == "open")
            and not approved_pr(project, task)):
        return number
    return None


def block_status(project: str, task: dict) -> tuple[str, str]:
    """One wait label for the CLI, queue and restart notice; "paused" never hides an operator decision."""
    name = config.operator_label()
    if task.get("fault"):
        return "fault", f"paused · fault {task['fault']}"
    if task.get("stop_id"):
        return "stopped", f"stopped by {name}"
    stopped = task.get("state") in ("blocked", "reported")
    count, number = len(operator_questions(task)) if stopped else 0, review_pr(project, task)
    waits = []
    if count:
        waits.append(f"{count} question{'s' if count != 1 else ''}")
    if number:
        waits.append(f"review PR #{number}")
    if waits:
        return f"waiting-{OPERATOR_MESSAGE_ROLE}", f"{name}'s turn · " + " · ".join(waits)
    if task.get("handed_back") and (task.get("state") == "running" or task.get("resume_after")):
        return "replying", f"L2 replying to {name}"
    who = task.get("waiting_on")
    approved = f" · PR #{number} approved" if stopped and (number := approved_pr(project, task)) else ""
    if who in (OPERATOR_MESSAGE_ROLE, "l3"):
        return f"waiting-{who}", f"waiting on {name if who == OPERATOR_MESSAGE_ROLE else 'L3'}{approved}"
    return "paused", f"paused{approved}"


def wait_label(project: str, task: dict) -> str | None:
    kind, label = block_status(project, task)
    return label if task.get("state") == "blocked" or kind != "paused" else None


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


def review_row(project: str, task: dict) -> dict | None:
    number = review_pr(project, task)
    if not number:
        return None
    delivery = task.get("delivery") or {}
    at = delivery.get("at") or task.get("updated")
    return {"project": project, "slug": task["slug"], "title": task.get("title"), "kind": "review", "pr": number,
            "question": f"Review PR #{number} before merge", "detail": task["hold_merge"],
            "recommendation": None, "asked": at, "since": at}


def decisions(project: str) -> list[dict]:
    """Questions and held reviews on the operator's turn, and explicit operator blocks; a pause needs no answer."""
    rows = []
    for task in S.list_tasks(project):
        questions = question_views(project, task["slug"])
        turn = {(q["id"], q["revision"]) for q in operator_questions(task)}
        rows.extend(q for q in questions if (q["id"], q["revision"]) in turn)
        if review := review_row(project, task):
            rows.append(review)
        if (not questions and task["state"] == "blocked" and not task.get("resume_after")
                and (task.get("waiting_on") == OPERATOR_MESSAGE_ROLE or task.get("stop_id"))):
            rows.append(decision_row(project, task))
    return rows


def block_question(task: dict) -> str:
    """Notify the coordinator of published questions without transferring decision authority."""
    slug = task["slug"]
    questions = "\n".join(f"- {q['id']} revision {q['revision']} (authority: {q['audience']}): {q['detail']}"
                          for q in task.get("questions", []) if q["status"] == "open")
    return (f"Task `{slug}` blocked and asks: {task['blocked_reason'][:800]}\n{questions}\n\n"
            f"Read `alt task messages {slug}` and `alt task show {slug}`. When the brief, the docs, or a recorded "
            f"decision settles a member, answer with `alt task message {slug} \"<answer and evidence>\" --summary \"<one line>\"` so its owner "
            "can record the resolution. This notification grants no operator authority. Keep operator-required "
            "proposal, security and product decisions open; do not re-escalate members already addressed to the operator. "
            f"For a new operator choice use `alt task escalate {slug}` with its question and recommendation. "
            "Coordinate the parts you can settle; preserve merge holds and verified fault recovery. If a member exists "
            "only because an Altitude rule or mechanism re-asks a settled decision, keep it open unless settled and "
            "repair that friction under your durable-feedback rule.")


def escalate(project: str, slug: str, question: str, actor: str = "l3", *,
             recommendation: str | None = None, recommendation_label: str | None = None,
             recommendation_why: str | None = None, questions: dict | None = None) -> dict:
    """L3 hands a blocked task's question to the operator as one plain dilemma; the L2's own words stay in the events."""
    question = (question or "").strip()
    if not question:
        raise TransitionError("escalation needs the question")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") != "blocked":
            raise TransitionError(f"{slug} is {task.get('state')}, not blocked")
        _supersede_resume(task)
        _ensure_question(project, task)
        task.update({"waiting_on": OPERATOR_MESSAGE_ROLE, "escalated": True,
                     "blocked_reason": task.get("blocked_reason") if task.get("fault") else question})
        current = _publish_block_questions(task, question, actor, questions, recommendation,
                                           recommendation_label, recommendation_why)
        _take_turn(task)
        S.save_task(project, task)
        handoff = {**current[-1]["message"], "wake": False}
        _append_jsonl(S.task_dir(project, slug) / "inbox.jsonl", handoff)
    S.append_event(project, slug, "escalated", by=actor, question=question)
    return task


def set_hold_merge(project: str, slug: str, why: str | None, actor: str = "l3") -> dict:
    """PRs merge by default; a hold is an explicit, reasoned exception."""
    why = (why or "").strip() or None
    if why is None and actor != OPERATOR_MESSAGE_ROLE:
        raise TransitionError("only the operator may release a merge hold")
    with S.project_lock(project):
        t = S.load_task(project, slug)
        t["hold_merge"] = why
        t["hold_merge_id"] = uuid.uuid4().hex
        S.save_task(project, t)
        S.append_event(project, slug, "hold-merge" if why else "release-merge", why=why, actor=actor,
                       hold_id=t["hold_merge_id"])
    return t


def grant_machine_access(project: str, slug: str, approval: str, *, question: str, revision: int, reason: str,
                         actor: str, source: str = "task") -> dict:
    """The operator's answer to the owner's purpose question is the only authority that opens the machine to a task.

    The check is mechanical: the cited operator message resolved that exact current question revision as answered
    with no remainder, so the recorded purpose is the question the operator actually read. L3 or the operator
    records it after judging that the answer is a yes to that purpose; the owner cannot record its own grant,
    and nobody can widen one. The grant binds to the current attempt.
    """
    if actor not in ("l3", OPERATOR_MESSAGE_ROLE) or not reason.strip():
        raise TransitionError("a machine grant needs the coordinator or the operator and a reason")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        try:
            if task["state"] not in ("running", "blocked", "reported"):
                raise ValueError("task is not active")
            decision = _question_target(task, question, revision)
            saved = decision.get("resolution") or {}
            if (decision != next(q for q in reversed(task["questions"]) if q["id"] == question)
                    or decision["status"] != "resolved" or decision["audience"] != "operator"
                    or saved.get("disposition") != "answered" or saved.get("remaining")
                    or (saved.get("message_id"), saved.get("source"), saved.get("by")) != (approval, source, OPERATOR_MESSAGE_ROLE)):
                raise ValueError("cite the operator message that answered the current operator question revision")
            operator = _decision_source(project, slug, decision, approval, source, exact=True)
        except (ValueError, KeyError, TypeError, TransitionError) as exc:
            S.append_event(project, slug, "machine-grant-refused", actor=actor, approval=approval, question=question,
                           revision=revision, reason=reason, error=str(exc))
            raise TransitionError(f"machine grant refused: {exc}") from exc
        grant = {"purpose": decision["detail"], "answer": operator.get("text"), "approval": approval,
                 "approved_at": operator["at"], "question": question, "revision": revision, "source": source,
                 "attempt": task.get("attempt"), "actor": actor, "reason": reason.strip(), "at": S.now()}
        task["machine_access"] = grant
        S.save_task(project, task)
        S.append_event(project, slug, "machine-grant", **grant)
        return grant


def revoke_machine_access(project: str, slug: str, reason: str, *, actor: str,
                          expected_attempt: int | None = None) -> dict:
    """Revocation narrows authority: the coordinator or the operator at any time, the owner for its own attempt."""
    if actor not in ("l2", "l3", OPERATOR_MESSAGE_ROLE) or not reason.strip():
        raise TransitionError("revoking a machine grant needs the owner, the coordinator or the operator and a reason")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        previous = task.get("machine_access")
        if not previous:
            raise TransitionError("task has no machine grant")
        if actor == "l2" and expected_attempt != task.get("attempt"):
            raise TransitionError("the owner revokes a grant only for its current attempt")
        task["machine_access"] = None
        S.save_task(project, task)
        S.append_event(project, slug, "machine-revoke", actor=actor, reason=reason.strip(),
                       purpose=previous["purpose"], approval=previous["approval"])
        return task


def start_machine_run(project: str, slug: str, fields) -> dict:
    """Number and record a command altd runs outside the worker sandbox (`machine.jsonl`) before its unit starts, so
    a command that restarts altd keeps its number and unit. `fields(n)` gives the row's purpose, command and unit."""
    runs = S.task_dir(project, slug) / "machine.jsonl"
    with S.project_lock(project):
        rows = [json.loads(line) for line in runs.read_text().splitlines() if line.strip()] if runs.exists() else []
        row = {"n": len(rows) + 1, **fields(len(rows) + 1), "exit": None, "timed_out": False, "started": S.now(), "finished": None,
               "error": "still running or interrupted with altd"}
        _append_jsonl(runs, row)
    return row


def finish_machine_run(project: str, slug: str, row: dict) -> None:
    """Add the run's task and project events once, then replace its row with the outcome. The row is written last,
    so an interruption between the writes leaves it unfinished to finish again, and a second finish keeps the
    outcome the first one recorded."""
    runs = S.task_dir(project, slug) / "machine.jsonl"
    with S.project_lock(project):
        recorded = next((e for e in S.read_events(project, slug)
                         if e["kind"] == "machine-run" and e.get("unit") == row["unit"]), None)
        if recorded is None:
            S.append_event(project, slug, "machine-run", actor="l2", **row)
            S.project_log(project, "machine-run", slug=slug, command=row["command"], unit=row["unit"],
                          exit=row["exit"], timed_out=row["timed_out"], purpose=row["purpose"])
        else:
            row = {key: value for key, value in recorded.items() if key not in ("at", "kind", "actor")}
        rows = [json.loads(line) for line in runs.read_text().splitlines() if line.strip()]
        S.atomic_write(runs, "".join(json.dumps(row if r["n"] == row["n"] else r, sort_keys=True) + "\n"
                                     for r in rows))


def apply_merge_approval(project: str, slug: str, approval: str, pull: dict, *, head: str,
                         reason: str, actor: str, source: str = "task") -> dict:
    """Release a merge hold on the operator's own approval of the task's current PR.

    The owner applies an approval from the task chat; L3 applies one from project chat. Either judges that the
    message approves the current scope; altd binds that original message, sent after this hold, to this PR head.
    """
    if actor not in ("l2", "l3") or not reason.strip() or (actor == "l2" and source != "task"):
        raise TransitionError("the owner applies task-chat approval; L3 applies project-chat approval")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        try:
            if not task.get("hold_merge") or task["state"] not in ("running", "blocked", "reported"):
                raise ValueError("task has no active merge hold")
            approvals = [r for r in _decision_messages(project, slug, source) if r["id"] == approval]
            operator = approvals[0] if len(approvals) == 1 else {}
            if operator.get("role") != OPERATOR_MESSAGE_ROLE or operator.get("by") != OPERATOR_MESSAGE_ROLE:
                raise ValueError("cite the original operator approval")
            def timestamp(value):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("approval evidence needs timezone-aware timestamps")
                return parsed
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
                           reason=reason, source=source, error=str(exc))
            raise TransitionError(f"recorded merge approval refused: {exc}") from exc
        receipt = {"actor": actor, "authorized_by": operator["role"], "reason": reason, "source": source,
                   "approval": approval, "approved_at": operator["at"],
                   "hold": task["hold_merge"], "hold_event": generation, "hold_at": hold["at"],
                   "hold_id": task.get("hold_merge_id"),
                   "pr": pull["number"], "url": pull["url"], "head": head, "at": S.now()}
        task.update(hold_merge=None, merge_approval=receipt)
        S.save_task(project, task)
        S.append_event(project, slug, "release-merge", **receipt)
        return receipt
