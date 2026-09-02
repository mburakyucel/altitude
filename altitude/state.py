"""Durable file primitives plus task folders, state, events, and STATE.md regeneration."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path

from . import config

STATES = ("queued", "running", "reported", "done", "rejected", "blocked")
OPEN_STATES = ("queued", "running", "reported", "blocked")


class LockOrderError(RuntimeError):
    """Raised before acquiring a lock that would violate the global lock order."""


class LockLevel(IntEnum):
    """Global outer-to-inner lock order for every process that mutates durable state."""

    ACTIVATION_MAINTENANCE = 10
    RECOVERY = 20
    PROJECT = 30
    TASK = 40
    OPERATION = 50
    GIT_PUBLICATION = 60


LOCK_ORDER = tuple(LockLevel)
_held_locks = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fsync_directory(directory: Path) -> None:
    """Persist directory-entry changes after a create or rename."""
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, text: str) -> None:
    """Replace ``path`` only after its contents are durable, then persist the rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default=None):
    """A missing file returns the default; corrupt state raises instead of hiding a fault."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return default
    try:
        return json.loads(text) if text.strip() else default
    except ValueError as e:
        raise ValueError(f"corrupt JSON in {path}: {e}") from e


def write_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _lock_stack() -> list[tuple[LockLevel, str]]:
    stack = getattr(_held_locks, "stack", None)
    if stack is None:
        stack = []
        _held_locks.stack = stack
    return stack


@contextmanager
def ordered_file_lock(path: Path, level: LockLevel):
    """Take an exclusive file lock while enforcing the global acquisition order.

    Locks at the same level must be acquired in increasing canonical-path order.
    Exact re-entry is rejected because ``flock`` recursion is not portable.
    """
    path = Path(path)
    level = LockLevel(level)
    canonical = str(path.resolve(strict=False))
    stack = _lock_stack()
    if (level, canonical) in stack:
        # Domain operations legitimately re-enter an outer owner lock through legacy
        # helpers.  The original descriptor remains held; opening/flocking it again is
        # neither necessary nor portable.
        yield
        return
    if stack:
        held_level, held_path = stack[-1]
        if level < held_level:
            raise LockOrderError(
                f"lock order inversion: {level.name} {canonical} after "
                f"{held_level.name} {held_path}"
            )
        if level == held_level and canonical <= held_path:
            raise LockOrderError(
                f"same-level locks require increasing paths: {canonical} after {held_path}"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        stack.append((level, canonical))
        try:
            yield
        finally:
            popped = stack.pop()
            if popped != (level, canonical):
                raise RuntimeError("lock stack corrupted")
            fcntl.flock(lock_file, fcntl.LOCK_UN)


@contextmanager
def project_lock(project: str):
    """One writer per project across processes (server and `alt` CLI)."""
    d = config.project_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    with ordered_file_lock(d / ".lock", LockLevel.PROJECT):
        yield


@contextmanager
def task_lock(path: Path):
    """Acquire one canonical task/helper/resume lock after its project lock."""
    with ordered_file_lock(path, LockLevel.TASK):
        yield


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or "task"


def _decode_jsonl_line(path: Path, raw: bytes, line_number: int) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"corrupt JSONL in {path} at line {line_number}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"corrupt JSONL in {path} at line {line_number}: expected object")
    return value


def _incomplete_json(exc: json.JSONDecodeError, text: str) -> bool:
    """Conservatively recognize an EOF-truncated JSON prefix, never general corruption."""
    # JSONL records are objects.  An incomplete array/string/scalar is corruption,
    # not a record we are authorized to discard merely because it ends at EOF.
    if not text.lstrip().startswith("{"):
        return False
    if exc.msg.startswith("Unterminated string"):
        return True
    if exc.pos >= len(text.rstrip()):
        return True
    suffix = text[exc.pos:].strip()
    return bool(suffix) and any(token.startswith(suffix) and token != suffix
                                for token in ("true", "false", "null"))


def _jsonl_records(path: Path, data: bytes) -> tuple[list[dict], int, bool]:
    """Parse append input and identify the only safely repairable failure: its final tail."""
    parts = data.split(b"\n")
    complete = parts[:-1]
    tail = parts[-1]
    records = [
        _decode_jsonl_line(path, raw, line_number)
        for line_number, raw in enumerate(complete, start=1)
    ]
    if not tail:
        return records, len(data), False
    try:
        text = tail.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"corrupt JSONL in {path} at line {len(complete) + 1}: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        if _incomplete_json(exc, text):
            return records, len(data) - len(tail), False
        raise ValueError(f"corrupt JSONL in {path} at line {len(complete) + 1}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"corrupt JSONL in {path} at line {len(complete) + 1}: expected object")
    records.append(value)
    return records, len(data), True


def append_jsonl(path: Path, record: dict, *, key_field: str) -> bool:
    """Durably append one keyed object, repairing only an invalid partial final row.

    Returns ``False`` when an identical record with the same stable key is already
    present. A reused key with different content and corruption before the final
    unterminated tail both fail loudly.
    """
    if not isinstance(record, dict):
        raise TypeError("JSONL record must be an object")
    key = record.get(key_field)
    if not isinstance(key, str) or not key:
        raise ValueError(f"JSONL record requires non-empty string {key_field!r}")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    canonical = str(path.resolve(strict=False))
    stack = _lock_stack()
    if stack:
        held_level, held_path = stack[-1]
        if LockLevel.OPERATION < held_level:
            raise LockOrderError(
                f"lock order inversion: OPERATION {canonical} after {held_level.name} {held_path}"
            )
        if LockLevel.OPERATION == held_level and canonical <= held_path:
            raise LockOrderError(
                f"nested append locks require increasing paths: {canonical} after {held_path}"
            )
    created = False
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(path, os.O_RDWR)
    with os.fdopen(fd, "r+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stack.append((LockLevel.OPERATION, canonical))
        try:
            data = stream.read()
            records, safe_length, unterminated_valid = _jsonl_records(path, data)
            duplicate = False
            seen: dict[str, dict] = {}
            for existing in records:
                existing_key = existing.get(key_field)
                if not isinstance(existing_key, str) or not existing_key:
                    continue
                previous = seen.get(existing_key)
                if previous is not None and previous != existing:
                    raise ValueError(
                        f"conflicting JSONL record for {key_field}={existing_key!r} in {path}"
                    )
                seen[existing_key] = existing
                if existing_key != key:
                    continue
                if existing != record:
                    raise ValueError(
                        f"conflicting JSONL record for {key_field}={key!r} in {path}"
                    )
                duplicate = True

            if safe_length != len(data):
                stream.seek(safe_length)
                stream.truncate()
            stream.seek(0, os.SEEK_END)
            if unterminated_valid:
                stream.write(b"\n")
            if not duplicate:
                stream.write(encoded)
            # A duplicate retry is also a durability barrier. The preceding attempt may
            # have made bytes visible before reporting an fsync failure.
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            stack.pop()
            fcntl.flock(stream, fcntl.LOCK_UN)
    # Always close the create/fsync ambiguity from a failed preceding attempt.
    _fsync_directory(path.parent)
    return not duplicate


def read_jsonl(
    path: Path, *, key_field: str | None = None, ignore_invalid: bool = False
) -> list[dict]:
    """Read JSONL, logically reconciling exact duplicates of stable keyed records."""
    path = Path(path)
    try:
        with open(path, "rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            try:
                data = stream.read()
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    except FileNotFoundError:
        return []

    raw_lines = data.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    records: list[dict] = []
    keyed: dict[str, dict] = {}
    for line_number, raw in enumerate(raw_lines, start=1):
        try:
            record = _decode_jsonl_line(path, raw, line_number)
        except ValueError:
            if ignore_invalid:
                continue
            raise
        if key_field is not None:
            key = record.get(key_field)
            if isinstance(key, str) and key:
                previous = keyed.get(key)
                if previous is not None:
                    if previous != record:
                        raise ValueError(
                            f"conflicting JSONL record for {key_field}={key!r} in {path}"
                        )
                    continue
                keyed[key] = record
        records.append(record)
    return records


# ---- task folders -----------------------------------------------------------

def tasks_dir(project: str) -> Path:
    return config.project_dir(project) / "tasks"


def archive_dir(project: str) -> Path:
    return config.project_dir(project) / "archive"


def task_dir(project: str, slug: str) -> Path:
    d = tasks_dir(project) / slug
    if d.is_dir():
        return d
    a = archive_dir(project) / slug
    return a if a.is_dir() else d


def status_path(project: str, slug: str) -> Path:
    return task_dir(project, slug) / "status.json"


def load_task(project: str, slug: str) -> dict:
    t = read_json(status_path(project, slug))
    if not t:
        raise KeyError(f"no task {slug!r} in {project!r}")
    return t


def save_task(project: str, task: dict) -> None:
    # Do not overwrite the only durable copy of a state-first transition before
    # its audit projection has been reconciled.
    current = read_json(status_path(project, task["slug"]), None)
    if isinstance(current, dict):
        reconcile_task_transition(project, current)
    task["updated"] = now()
    write_json(status_path(project, task["slug"]), task)


def _transition_event(envelope: dict) -> dict:
    return {
        "at": envelope["at"],
        "kind": envelope["event_kind"],
        "actor": envelope["actor"],
        **envelope["payload"],
        "event_id": envelope["transition_id"],
    }


def _projection_path(projection: object) -> Path:
    if not isinstance(projection, dict):
        raise ValueError("transition projection must be an object")
    scope = projection.get("scope")
    if scope == "task" and set(projection) == {"scope", "project", "slug"}:
        project, slug = projection.get("project"), projection.get("slug")
        if all(isinstance(value, str) and value for value in (project, slug)):
            return task_dir(project, slug) / "events.log"
    if scope == "project" and set(projection) == {"scope", "project"}:
        project = projection.get("project")
        if isinstance(project, str) and project:
            return config.project_dir(project) / "events.log"
    raise ValueError("invalid transition projection")


def task_projection(project: str, slug: str) -> dict:
    return {"scope": "task", "project": project, "slug": slug}


def project_projection(project: str) -> dict:
    return {"scope": "project", "project": project}


def reconcile_transition(record: dict) -> bool:
    """Project one aggregate's latest state-first transition by its stable id."""
    envelope = record.get("transition")
    if not isinstance(envelope, dict):
        return False
    required = {"version", "transition_id", "event_kind", "subject", "actor", "at",
                "payload", "payload_hash", "projection"}
    if set(envelope) != required or envelope.get("version") != 1:
        raise ValueError(f"invalid transition envelope for {envelope.get('subject') or '(unknown subject)'}")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"invalid transition payload for {envelope.get('subject')}")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != envelope.get("payload_hash"):
        raise ValueError(f"transition payload hash mismatch for {envelope.get('subject')}")
    return append_jsonl(_projection_path(envelope.get("projection")),
                        _transition_event(envelope), key_field="event_id")


def write_json_transition(path: Path, record: dict, event_kind: str, *, subject: str,
                          actor: str, projection: dict, **payload) -> dict:
    """Commit one JSON aggregate transition, then its derived audit projection."""
    current = read_json(path, None)
    if isinstance(current, dict):
        reconcile_transition(current)
    event_payload = dict(payload)
    envelope = {
        "version": 1,
        "transition_id": uuid.uuid4().hex,
        "event_kind": event_kind,
        "subject": subject,
        "actor": actor,
        "at": now(),
        "payload": event_payload,
        "payload_hash": hashlib.sha256(
            json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "projection": projection,
    }
    record["transition"] = envelope
    write_json(path, record)
    reconcile_transition(record)
    return envelope


def reconcile_task_transition(project: str, task: dict) -> bool:
    """Compatibility wrapper for the task aggregate's generic transition."""
    envelope = task.get("transition")
    if isinstance(envelope, dict) and "projection" not in envelope:
        # Read the first Phase-0C development shape without accepting it as a
        # second permanent schema.
        envelope["projection"] = task_projection(project, str(task.get("slug") or ""))
    return reconcile_transition(task)


def save_task_transition(project: str, task: dict, event_kind: str, *, transition_actor: str, **payload) -> dict:
    """Persist authoritative task state first, then its stable keyed audit event."""
    task["updated"] = now()
    return write_json_transition(
        status_path(project, task["slug"]), task, event_kind,
        subject=f"{project}/{task['slug']}", actor=transition_actor,
        projection=task_projection(project, task["slug"]), **payload,
    )


def reconcile_task_message_projections(project: str, slug: str) -> None:
    """Replay task-message audit rows from the canonical conversation append log."""
    path = task_dir(project, slug) / "conversation.jsonl"
    for message in read_jsonl(path, key_field="id"):
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            continue
        append_jsonl(
            task_dir(project, slug) / "events.log",
            {"at": message.get("at") or now(), "kind": "task-message",
             "message_id": message_id, "role": message.get("role"),
             "dispatch_id": message.get("dispatch_id"), "by": message.get("by"),
             "event_id": f"task-message:{message_id}"},
            key_field="event_id",
        )


def reconcile_fyi_projections(project: str) -> None:
    """Replay task FYI events from the canonical project inbox rows."""
    path = config.project_dir(project) / "inbox.jsonl"
    for item in read_jsonl(path, key_field="id"):
        item_id, slug = item.get("id"), item.get("slug")
        if not isinstance(item_id, str) or not item_id or not isinstance(slug, str) or not slug:
            continue
        # Archived task evidence is immutable; Phase 0 startup repairs active rows only.
        if not (tasks_dir(project) / slug).is_dir():
            continue
        append_jsonl(
            tasks_dir(project) / slug / "events.log",
            {"at": item.get("at") or now(), "kind": "fyi", "text": item.get("text"),
             "by": item.get("by"), "event_id": f"fyi:{item_id}"},
            key_field="event_id",
        )


def reconcile_startup() -> None:
    """Bounded replay of active aggregate and keyed-row audit projections."""
    recovery_hold = read_json(config.MONITOR_DIR / "recovery-hold.json", None)
    if isinstance(recovery_hold, dict):
        reconcile_transition(recovery_hold)
    for project in config.load_projects():
        for task in list_tasks(project):
            reconcile_task_transition(project, task)
            slug = task["slug"]
            reconcile_task_message_projections(project, slug)
            helper_dir = tasks_dir(project) / slug / "l1"
            if helper_dir.is_dir():
                for path in sorted(helper_dir.glob("*.json")):
                    record = read_json(path, None)
                    if isinstance(record, dict):
                        reconcile_transition(record)
        reconcile_fyi_projections(project)


def list_tasks(project: str, include_archive: bool = False) -> list[dict]:
    out = []
    dirs = [tasks_dir(project)] + ([archive_dir(project)] if include_archive else [])
    for d in dirs:
        if not d.is_dir():
            continue
        for td in sorted(d.iterdir()):
            t = read_json(td / "status.json")
            if t:
                out.append(t)
    return out


def append_event(
    project: str, slug: str, kind: str, *, event_id: str | None = None, **data
) -> dict:
    ev = {
        "at": now(),
        "kind": kind,
        **data,
        "event_id": event_id if event_id is not None else uuid.uuid4().hex,
    }
    p = task_dir(project, slug) / "events.log"
    append_jsonl(p, ev, key_field="event_id")
    # Provider stores are ephemeral. Snapshot after every durable boundary so failures,
    # cancellation, malformed actions, and worker replacement retain evidence to this point.
    try:
        from . import transcript
        transcript.sync(project, slug)
    except (KeyError, OSError, ValueError):
        # The lifecycle event is primary and must remain writable during early task creation or
        # while a provider has an incomplete record. A later boundary retries the full snapshot.
        pass
    return ev


def read_events(project: str, slug: str) -> list[dict]:
    p = task_dir(project, slug) / "events.log"
    return read_jsonl(p, key_field="event_id")


def project_log(
    project: str, kind: str, *, event_id: str | None = None, **data
) -> None:
    """Project-level events such as L3 rotations and incidents, not tied to a task."""
    p = config.project_dir(project) / "events.log"
    event = {
        "at": now(),
        "kind": kind,
        **data,
        "event_id": event_id if event_id is not None else uuid.uuid4().hex,
    }
    append_jsonl(p, event, key_field="event_id")


def read_project_log(project: str, limit: int = 200) -> list[dict]:
    p = config.project_dir(project) / "events.log"
    return read_jsonl(p, key_field="event_id")[-limit:]


# ---- STATE.md: the L3's memory, regenerated from status.json ----------------

def age(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "?"
    s = int(time.time() - dt.timestamp())
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def regen_state_md(project: str) -> str:
    tasks = list_tasks(project)
    by = {s: [t for t in tasks if t["state"] == s] for s in STATES}
    lines = [f"# STATE — {project}", "",
             f"*Regenerated {now()} from `status.json` files. Never edit by hand; never trust memory over this file.*", ""]
    pending = [t for t in by["blocked"] if not t.get("resume_after")]
    lines += ["## Needs user input", ""]
    lines += [f"- **{t['slug']}** ({age(t['updated'])}): {short[:200]}"
              for t in pending if (short := str(t.get("blocked_reason") or "blocked"))] or ["- none"]
    lines += ["", "## Tasks", ""]
    for s in ("blocked", "running", "reported", "queued"):
        ts = by[s]
        if not ts:
            continue
        lines.append(f"### {s} ({len(ts)})")
        for t in ts:
            extra = []
            if t.get("dispatch_id"):
                extra.append(t["dispatch_id"])
            if t.get("prs"):
                extra.append("PRs " + ", ".join(str(p) for p in t["prs"]))
            if t.get("blocked_reason"):
                extra.append("blocked: " + t["blocked_reason"][:120])
            sp = t.get("spend") or {}
            if sp.get("turns"):
                extra.append(f"turns {sp['turns']}")
            lines.append(f"- **{t['slug']}** {t['title']} — {age(t['updated'])}" + (" — " + "; ".join(extra) if extra else ""))
        lines.append("")
    text = "\n".join(lines) + "\n"
    atomic_write(config.project_dir(project) / "STATE.md", text)
    return text
