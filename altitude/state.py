"""Durable file primitives plus task folders, state, events, and STATE.md regeneration."""
from __future__ import annotations
import fcntl
import json
import math
import os
import re
import tempfile
import threading
import time
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
    ACTIVATION_MAINTENANCE = 10
    RECOVERY = 20
    PROJECT = 30
    TASK = 40
    OPERATION = 50
    GIT_PUBLICATION = 60


LOCK_ORDER = tuple(LockLevel)
_held_locks = threading.local()
_LockIdentity = tuple[int, int]
_HeldLock = tuple[LockLevel, str, _LockIdentity]


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, text: str) -> None:
    """Replace a file after its contents, rename, and parent entry are durable."""
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


def _lock_stack() -> list[_HeldLock]:
    stack = getattr(_held_locks, "stack", None)
    if stack is None:
        stack = []
        _held_locks.stack = stack
    return stack


def _path_identity(path: Path) -> _LockIdentity | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino


def _check_lock_order(
    level: LockLevel, canonical: str, identity: _LockIdentity | None, *, reentry: bool
) -> tuple[list[_HeldLock], bool]:
    stack = _lock_stack()
    for index, (held_level, held_path, held_identity) in enumerate(stack):
        same_path = held_path == canonical
        same_inode = identity is not None and held_identity == identity
        if not same_path and not same_inode:
            continue
        if (reentry and index == len(stack) - 1 and held_level == level
                and same_path and identity == held_identity):
            return stack, True
        if held_level != level:
            raise LockOrderError(
                f"lock path/inode already held at {held_level.name}, not {level.name}: {canonical}"
            )
        raise LockOrderError(f"lock reentry must be the current lock: {level.name} {canonical}")
    if stack:
        held_level, held_path, _held_identity = stack[-1]
        if level < held_level:
            raise LockOrderError(
                f"lock order inversion: {level.name} {canonical} after "
                f"{held_level.name} {held_path}"
            )
        if level == held_level and canonical <= held_path:
            raise LockOrderError(
                f"same-level locks require increasing paths: {canonical} after {held_path}"
            )
    return stack, False


def _opened_lock_entry(stream, level: LockLevel, canonical: str, *, reentry: bool):
    info = os.fstat(stream.fileno())
    entry = (level, canonical, (info.st_dev, info.st_ino))
    stack, entered = _check_lock_order(level, canonical, entry[2], reentry=reentry)
    return stack, entry, entered


@contextmanager
def ordered_file_lock(path: Path, level: LockLevel):
    """Take a file lock in declared outer-to-inner and canonical-path order.

    Exact re-entry shares the descriptor already owned by this thread. Other locks
    at one level must use increasing canonical paths.
    """
    path, level = Path(path), LockLevel(level)
    canonical = str(path.resolve(strict=False))
    stack, entered = _check_lock_order(
        level, canonical, _path_identity(path), reentry=True
    )
    if entered:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as lock_file:
        stack, entry, entered = _opened_lock_entry(
            lock_file, level, canonical, reentry=True
        )
        if entered:
            yield
            return
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        stack.append(entry)
        try:
            yield
        finally:
            if stack.pop() != entry:
                raise RuntimeError("lock stack corrupted")
            fcntl.flock(lock_file, fcntl.LOCK_UN)


@contextmanager
def project_lock(project: str):
    """One writer per project across processes (server and `alt` CLI)."""
    with ordered_file_lock(config.project_dir(project) / ".lock", LockLevel.PROJECT):
        yield


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or "task"


def _validate_json_value(value) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("JSON string contains an unpaired surrogate")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_json_value(key)
            _validate_json_value(item)
        return
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _strict_json_loads(text: str):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r}")
            result[key] = value
        return result

    def finite_float(token: str) -> float:
        value = float(token)
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number {token}")
        return value

    value = json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_float=finite_float,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number {value}")
        ),
    )
    _validate_json_value(value)
    return value


def _decode_jsonl_line(path: Path, raw: bytes, line_number: int) -> dict:
    try:
        value = _strict_json_loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"corrupt JSONL in {path} at line {line_number}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"corrupt JSONL in {path} at line {line_number}: expected object")
    return value


def _canonical_json(value: dict) -> bytes:
    _validate_json_value(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _jsonl_records(path: Path, data: bytes) -> tuple[list[dict], int]:
    """Return LF-committed rows and the offset before any uncommitted tail."""
    committed_length = data.rfind(b"\n") + 1
    committed = data[:committed_length]
    rows = committed[:-1].split(b"\n") if committed else []
    return ([_decode_jsonl_line(path, raw, line)
             for line, raw in enumerate(rows, start=1)], committed_length)


def append_jsonl(path: Path, record: dict, *, key_field: str) -> bool:
    """Append one stable-keyed object after discarding any uncommitted tail."""
    if not isinstance(record, dict):
        raise TypeError("JSONL record must be an object")
    key = record.get(key_field)
    if not isinstance(key, str) or not key:
        raise ValueError(f"JSONL record requires non-empty string {key_field!r}")
    path = Path(path)
    encoded_record = _canonical_json(record)
    encoded = encoded_record + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical = str(path.resolve(strict=False))
    stack, _ = _check_lock_order(
        LockLevel.OPERATION, canonical, _path_identity(path), reentry=False
    )
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDWR)
    with os.fdopen(fd, "r+b") as stream:
        stack, entry, _ = _opened_lock_entry(
            stream, LockLevel.OPERATION, canonical, reentry=False
        )
        fcntl.flock(stream, fcntl.LOCK_EX)
        stack.append(entry)
        try:
            data = stream.read()
            records, safe_length = _jsonl_records(path, data)
            seen: dict[str, bytes] = {}
            duplicate = False
            for existing in records:
                existing_key = existing.get(key_field)
                if not isinstance(existing_key, str) or not existing_key:
                    raise ValueError(
                        f"JSONL record requires non-empty string {key_field!r} in {path}"
                    )
                existing_encoded = _canonical_json(existing)
                previous = seen.get(existing_key)
                if previous is not None and previous != existing_encoded:
                    raise ValueError(
                        f"conflicting JSONL record for {key_field}={existing_key!r} in {path}"
                    )
                seen[existing_key] = existing_encoded
                if existing_key == key:
                    if existing_encoded != encoded_record:
                        raise ValueError(
                            f"conflicting JSONL record for {key_field}={key!r} in {path}"
                        )
                    duplicate = True
            if safe_length != len(data):
                stream.seek(safe_length)
                stream.truncate()
            stream.seek(0, os.SEEK_END)
            if not duplicate:
                stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            if stack.pop() != entry:
                raise RuntimeError("lock stack corrupted")
            fcntl.flock(stream, fcntl.LOCK_UN)
    # Also closes create/fsync ambiguity from a prior attempt that made bytes visible.
    _fsync_directory(path.parent)
    return not duplicate


def read_jsonl(path: Path, *, key_field: str | None = None) -> list[dict]:
    """Read objects under the writer lock and reconcile exact stable-key duplicates."""
    path = Path(path)
    canonical = str(path.resolve(strict=False))
    stack, _ = _check_lock_order(
        LockLevel.OPERATION, canonical, _path_identity(path), reentry=False
    )
    try:
        with open(path, "rb") as stream:
            stack, entry, _ = _opened_lock_entry(
                stream, LockLevel.OPERATION, canonical, reentry=False
            )
            fcntl.flock(stream, fcntl.LOCK_SH)
            stack.append(entry)
            try:
                data = stream.read()
            finally:
                if stack.pop() != entry:
                    raise RuntimeError("lock stack corrupted")
                fcntl.flock(stream, fcntl.LOCK_UN)
    except FileNotFoundError:
        return []
    committed, _safe_length = _jsonl_records(path, data)
    records, keyed = [], {}
    for record in committed:
        if key_field is not None:
            key = record.get(key_field)
            if not isinstance(key, str) or not key:
                raise ValueError(f"JSONL record requires non-empty string {key_field!r} in {path}")
            encoded_record = _canonical_json(record)
            previous = keyed.get(key)
            if previous is not None:
                if previous != encoded_record:
                    raise ValueError(
                        f"conflicting JSONL record for {key_field}={key!r} in {path}"
                    )
                continue
            keyed[key] = encoded_record
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
    task["updated"] = now()
    write_json(status_path(project, task["slug"]), task)


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


def append_event(project: str, slug: str, kind: str, **data) -> dict:
    ev = {"at": now(), "kind": kind, **data}
    p = task_dir(project, slug) / "events.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(ev, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
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
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def project_log(project: str, kind: str, **data) -> None:
    """Project-level events such as L3 rotations and incidents, not tied to a task."""
    p = config.project_dir(project) / "events.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"at": now(), "kind": kind, **data}, sort_keys=True) + "\n")


def read_project_log(project: str, limit: int = 200) -> list[dict]:
    p = config.project_dir(project) / "events.log"
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


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
