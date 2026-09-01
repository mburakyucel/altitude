"""Engine-neutral, read-only L2 transcript projection for the local web viewer.

Only task-owned identities select files. Browser input never becomes a filesystem path. Raw
records are still redacted: local visibility is not permission to disclose credentials.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, dispatch, state as S

MAX_DEFAULT_TEXT = 4000
_SECRET_KEY = re.compile(r"(authorization|cookie|password|passwd|secret|token|api[_-]?key|credential)", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}|\b(?:sk|gh[oprsu])_[A-Za-z0-9_-]{12,}")
BOUNDARIES = {"dispatched", "resumed", "resume-cancelled", "resume-failed", "resume-held",
              "replaced", "compacted", "engine-changed", "recovery"}
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


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
