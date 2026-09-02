"""Engine-neutral, read-only L2 transcript projection for the local web viewer.

Only task-owned identities select files. Browser input never becomes a filesystem path. Raw
records are still redacted: local visibility is not permission to disclose credentials.
"""
from __future__ import annotations

import json
import hashlib
import re
import shutil
import tarfile
from pathlib import Path

from . import config, dispatch, state as S

MAX_DEFAULT_TEXT = 4000
_SECRET_KEY = re.compile(r"(authorization|cookie|password|passwd|secret|token|api[_-]?key|credential)", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}|\b(?:sk|gh[oprsu])_[A-Za-z0-9_-]{12,}")
BOUNDARIES = {"dispatched", "resumed", "resume-cancelled", "resume-failed", "resume-held",
              "replaced", "compacted", "engine-changed", "recovery"}
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
SCHEMA_VERSION = "altitude.transcript/v1"


class TranscriptAccessError(ValueError):
    pass


def _require_task_access(project: str, slug: str) -> None:
    """Authorize named managed task identity before any task path is resolved."""
    if not _IDENTIFIER.fullmatch(project) or not _IDENTIFIER.fullmatch(slug):
        raise TranscriptAccessError("invalid task identity")
    if project not in config.load_projects():
        raise TranscriptAccessError("unknown project")
    root = (config.project_dir(project) / "tasks").resolve()
    archive = (config.project_dir(project) / "archive").resolve()
    candidate = S.task_dir(project, slug).resolve()
    if candidate.parent not in (root, archive):
        raise TranscriptAccessError("task path escaped its project")


def require_generation(task: dict, *, dispatch_id: str, engine: str, session_id: str) -> None:
    expected = (str(task.get("dispatch_id") or ""), str(task.get("l2_engine") or "claude"),
                str(task.get("session_id") or ""))
    supplied = (str(dispatch_id), str(engine), str(session_id))
    if not all(supplied) or supplied != expected:
        raise TranscriptAccessError("task generation changed; refresh the Live session view")


def _redact(value):
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if _SECRET_KEY.search(str(k)) else _redact(v)) for k, v in value.items()
                if str(k) not in ("encrypted_content",)}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(lambda m: (m.group(1) or "") + "[REDACTED]", value)
    return value


def _read_jsonl(path: Path) -> list[tuple[dict | None, str | None]]:
    """Return complete records plus visible errors. An unfinished final line is retried next poll."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    lines = data.splitlines(keepends=True)
    out = []
    for index, raw in enumerate(lines, 1):
        if index == len(lines) and not raw.endswith((b"\n", b"\r")):
            out.append((None, "partial record; waiting for completion"))
            continue
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            out.append((None, f"corrupt record {index}"))
            continue
        if isinstance(value, dict):
            out.append((value, None))
        else:
            out.append((None, f"non-object record {index}"))
    return out


def _claude_path(session_id: str) -> Path | None:
    if not session_id or "/" in session_id or "\\" in session_id:
        return None
    matches = list((config.HOME / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    return matches[0] if len(matches) == 1 else None


def _engine_paths(project: str, slug: str, task: dict, events: list[dict]) -> list[tuple[str, str, Path]]:
    engine = str(task.get("l2_engine") or "claude")
    identities = []
    for ev in [*events, task]:
        sid, aid = str(ev.get("session_id") or ""), str(ev.get("agent_id") or "")
        eng = str(ev.get("engine") or engine)
        key = (eng, sid, aid)
        if sid and key not in identities:
            identities.append(key)
    paths = []
    for eng, sid, aid in identities:
        if eng == "claude":
            path = _claude_path(sid)
        elif eng == "codex" and aid and "/" not in aid and "\\" not in aid:
            path = dispatch.l2_job_root(project, slug) / f"{aid}.stdout.jsonl"
        else:
            path = None
        if path and path.is_file():
            paths.append((eng, sid, path))
    return paths


def _kind(engine: str, record: dict) -> str:
    typ = str(record.get("type") or record.get("kind") or "event")
    if typ in ("assistant", "user", "system"):
        return "message"
    if "tool" in typ or typ.startswith("item."):
        item = record.get("item") if isinstance(record.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        if "command" in item_type:
            return "command"
        if "file" in item_type or "patch" in item_type:
            return "file"
        return "tool"
    if typ in ("error", "turn.failed"):
        return "error"
    return "engine"


def _text(record: dict) -> str:
    for key in ("text", "message", "result", "output", "content"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    item = record.get("item")
    if isinstance(item, dict):
        for key in ("text", "command", "aggregated_output", "path"):
            if isinstance(item.get(key), str):
                return item[key]
    message = record.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "\n".join(str(x.get("text") or x.get("content") or "") for x in content if isinstance(x, dict))
    return ""


def view(project: str, slug: str, *, dispatch_id: str, engine: str, session_id: str,
         cursor: int = 0, raw: bool = False) -> dict:
    _require_task_access(project, slug)
    task = S.load_task(project, slug)
    require_generation(task, dispatch_id=dispatch_id, engine=engine, session_id=session_id)
    platform = S.read_events(project, slug)
    rows = []
    for event in platform:
        kind = str(event.get("kind") or "platform")
        rows.append({"source": "platform", "kind": "boundary" if kind in BOUNDARIES else "platform",
                     "type": kind, "at": event.get("at"), "session_id": event.get("session_id"),
                     "text": kind.replace("-", " "), "raw": _redact(event) if raw else None})
    for eng, sid, path in _engine_paths(project, slug, task, platform):
        for record, error in _read_jsonl(path):
            if error:
                rows.append({"source": eng, "kind": "error", "type": "record-error", "at": None,
                             "session_id": sid, "text": error, "raw": None})
                continue
            assert record is not None
            safe = _redact(record)
            text = _text(safe)
            rows.append({"source": eng, "kind": _kind(eng, safe),
                         "type": str(safe.get("type") or safe.get("kind") or "event"),
                         "at": safe.get("timestamp") or safe.get("at"), "session_id": sid,
                         "text": text if raw or len(text) <= MAX_DEFAULT_TEXT else text[:MAX_DEFAULT_TEXT] + "\n… output collapsed",
                         "truncated": not raw and len(text) > MAX_DEFAULT_TEXT,
                         "raw": safe if raw else None})
    for seq, row in enumerate(rows):
        row["seq"] = seq
    start = max(0, int(cursor or 0))
    return {"project": project, "slug": slug, "dispatch_id": dispatch_id, "engine": engine,
            "session_id": session_id, "cursor": len(rows), "events": rows[start:],
            "redaction": "credential-shaped keys and values are redacted; hidden model reasoning is never exposed"}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _event_id(attempt: str, source: str, ordinal: int, value: dict) -> str:
    seed = json.dumps([attempt, source, ordinal, value], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(seed.encode()).hexdigest()[:24]


def _attempt_dir(project: str, slug: str, dispatch_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", dispatch_id).strip("-.") or "undispatched"
    return S.task_dir(project, slug) / "transcripts" / safe


def sync(project: str, slug: str) -> Path | None:
    """Materialize the current attempt without depending on its provider store afterward.

    Rewriting from append-only sources makes this idempotent, crash-tolerant, and able to pick up
    a provider's partially written final record on the next lifecycle event.
    """
    task = S.load_task(project, slug)
    dispatch_id = str(task.get("dispatch_id") or "")
    if not dispatch_id:
        return None
    root = _attempt_dir(project, slug, dispatch_id)
    root.mkdir(parents=True, exist_ok=True)
    prior_manifest = S.read_json(root / "manifest.json", {}) or {}
    platform = S.read_events(project, slug)
    canonical: list[dict] = []
    native_files: list[dict] = []
    prior_events = [record for record, error in _read_jsonl(root / "events.jsonl")
                    if record is not None and not error]

    def add(source: str, ordinal: int, event_type: str, at, payload: dict, **links) -> None:
        safe = _redact(payload)
        canonical.append({"id": _event_id(dispatch_id, source, ordinal, safe), "seq": len(canonical),
                          "at": at, "source": source, "type": event_type,
                          "attempt_id": dispatch_id, **links, "payload": safe})

    for index, event in enumerate(platform):
        add("altitude", index, str(event.get("kind") or "event"), event.get("at"), event,
            task_slug=slug, message_id=event.get("message_id"), helper=event.get("helper"))
    conversation = S.task_dir(project, slug) / "conversation.jsonl"
    for index, pair in enumerate(_read_jsonl(conversation)):
        record, error = pair
        if record is not None:
            add("conversation", index, "message", record.get("at"), record,
                task_slug=slug, message_id=record.get("id"))
        elif error:
            add("conversation", index, "record-error", None, {"error": error}, task_slug=slug)
    for file_index, (engine, session_id, path) in enumerate(_engine_paths(project, slug, task, platform)):
        records = []
        for record_index, (record, error) in enumerate(_read_jsonl(path)):
            value = _redact(record) if record is not None else {"error": error}
            records.append(value)
            add(engine, record_index + file_index * 1_000_000,
                str(value.get("type") or value.get("kind") or "record-error"),
                value.get("timestamp") or value.get("at"), value,
                task_slug=slug, session_id=session_id, worker_id=path.name.split(".stdout", 1)[0])
        native_key = hashlib.sha256(f"{engine}\0{session_id}\0{path.name}".encode()).hexdigest()[:12]
        native_name = f"native-{engine}-{native_key}.jsonl"
        native_data = "".join(json.dumps(v, sort_keys=True) + "\n" for v in records).encode()
        S.atomic_write(root / native_name, native_data.decode())
        native_files.append({"path": native_name, "engine": engine, "session_id": session_id,
                             "records": len(records), "sha256": _digest(native_data)})
    # A provider store may disappear before a late terminal/archive event. Never replace an
    # already captured native snapshot with absence.
    known_native = {entry["path"] for entry in native_files}
    for entry in prior_manifest.get("native") or []:
        path = root / str(entry.get("path") or "")
        if (entry.get("path") not in known_native and path.is_file()
                and _digest(path.read_bytes()) == entry.get("sha256")):
            native_files.append(entry)
    # If a replaced provider worker's source vanished, retain its last canonical records too.
    current_ids = {event["id"] for event in canonical}
    for event in prior_events:
        if event.get("source") not in ("altitude", "conversation") and event.get("id") not in current_ids:
            canonical.append(event)
    for seq, event in enumerate(canonical):
        event["seq"] = seq
    event_data = "".join(json.dumps(e, sort_keys=True) + "\n" for e in canonical).encode()
    S.atomic_write(root / "events.jsonl", event_data.decode())
    outcome_files = {}
    for name in ("report.json", "digest.md"):
        path = S.task_dir(project, slug) / name
        if path.is_file():
            data = path.read_bytes()
            shutil.copyfile(path, root / name)
            outcome_files[name] = _digest(data)
    manifest = {
        "schema_version": SCHEMA_VERSION, "project": project, "task_slug": slug,
        "task_created": task.get("created"), "level": "l2", "role": "task-owner",
        "attempt_id": dispatch_id, "attempt_number": task.get("attempt"),
        "engine": task.get("l2_engine"), "model": task.get("engine_model") or task.get("model"),
        "session_id": task.get("session_id"), "worker_id": task.get("agent_id"),
        "state": task.get("state"), "updated": task.get("updated"),
        "previous_attempt": task.get("previous_dispatch_id"),
        "canonical": {"path": "events.jsonl", "events": len(canonical), "sha256": _digest(event_data)},
        "native": native_files, "outcome": outcome_files,
        "privacy": {"classification": "private", "redaction": "credential-shaped keys and values removed",
                    "future_context": "not automatically injected"},
    }
    S.write_json(root / "manifest.json", manifest)
    return root


def validate_bundle(root: Path) -> dict:
    root = Path(root)
    manifest = S.read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise TranscriptAccessError("unsupported or missing transcript manifest")
    checked = []
    entries = [manifest.get("canonical") or {}, *(manifest.get("native") or [])]
    entries += [{"path": path, "sha256": digest} for path, digest in (manifest.get("outcome") or {}).items()]
    for entry in entries:
        rel = str(entry.get("path") or "")
        path = (root / rel).resolve()
        if not rel or root.resolve() not in path.parents or not path.is_file():
            raise TranscriptAccessError(f"bundle file unavailable: {rel}")
        if _digest(path.read_bytes()) != entry.get("sha256"):
            raise TranscriptAccessError(f"checksum mismatch: {rel}")
        checked.append(rel)
    previous = -1
    for record, error in _read_jsonl(root / "events.jsonl"):
        if error or record is None or record.get("seq") != previous + 1 or not record.get("id"):
            raise TranscriptAccessError("canonical event ordering is invalid")
        previous += 1
    return {"valid": True, "schema_version": SCHEMA_VERSION, "files": checked, "events": previous + 1}


def export(project: str, slug: str, destination: Path, dispatch_id: str | None = None) -> Path:
    _require_task_access(project, slug)
    task = S.load_task(project, slug)
    wanted = dispatch_id or str(task.get("dispatch_id") or "")
    if wanted == task.get("dispatch_id"):
        sync(project, slug)
    root = _attempt_dir(project, slug, wanted)
    validate_bundle(root)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(root, arcname=f"{slug}-{root.name}")
    return destination
