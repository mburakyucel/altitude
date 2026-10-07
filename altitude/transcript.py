"""Engine-neutral, read-only L2 transcript projection for the local web viewer.

Only task-owned identities select files. Browser input never becomes a filesystem path. Raw records are
still redacted: local visibility is not permission to disclose credentials, and model reasoning
never reaches the page in either mode.

Every row carries what a conversation needs: `role` (user = the dispatcher prompt or a task message,
assistant = the worker, tool = a tool's output, system = engine bookkeeping and task events); for a tool
call the tool name, a one-line input `summary`, and the `tool_use_id` that ties a result row to its
call so the page nests output under the command that produced it. Some commands carry their own
`output`, since one thread item records both. Rows are ordered by time: platform events, prompts, and
engine records interleave into one timeline.
"""
from __future__ import annotations

import json
import hashlib
import re
import struct
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config, dispatch, engines, state as S, tasks
from .engines import _shell_command  # Shared projection formatter used by the coordinator tool log.

MAX_DEFAULT_TEXT = 4000
_SECRET_KEY = re.compile(r"(authorization|cookie|password|passwd|secret|token|api[_-]?key|credential)", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}|\b(?:sk|gh[oprsu])_[A-Za-z0-9_-]{12,}")
BOUNDARIES = {"state", "stopped", "resume-held", "resume-failed", "dispatch-failed"}
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
RESUME_PROMPT = "Continue from your progress file."
UNKNOWN_RESUME_PROMPT = "Session continued; input delivery unconfirmed."


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


def require_generation(task: dict, *, engine: str, session_id: str, attempt: int) -> None:
    expected = (engines.transcript_engine(task), str(task.get("session_id") or ""), int(task.get("attempt") or 0))
    supplied = (str(engine), str(session_id), attempt)
    if not engine or not session_id or supplied != expected:
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
        return [(None, "source unavailable")]
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


def _when(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except ValueError:
        return None


def _row(source: str, kind: str, role: str, text: str = "", *, type: str = "event", at=None, **fields) -> dict:
    row = {"source": source, "kind": kind, "role": role, "type": type, "at": at, "text": text}
    row.update({k: v for k, v in fields.items() if v is not None})
    return row


def _platform_row(event: dict, raw: bool) -> dict:
    event = _redact(event)
    kind = str(event.get("kind") or "platform")
    if kind == "state":
        frm, to = event.get("frm"), event.get("to")
        text = f"{frm} → {to}" if frm and to else str(to or kind)
    else:
        text = kind.replace("-", " ")
        detail = event.get("reason") or event.get("hold") or event.get("note")
        text = f"{text}: {detail}" if detail else text
    if event.get("by"):
        text = f"{text} · {event['by']}"
    return _row("platform", "boundary" if kind in BOUNDARIES else "platform", "system", text, type=kind,
                at=event.get("at"), session_id=event.get("session_id"), raw=event if raw else None)


def _turn_prompt(project: str, slug: str, task: dict, turn: dict) -> str:
    """When the engine does not echo its prompt, the task's own records stand in: the brief
    for the first turn, the task messages a resume delivered for a later one."""
    if not turn.get("resume"):
        try:
            return (S.task_dir(project, slug) / "brief.md").read_text()
        except OSError:
            return ""
    receipts = task.get("message_deliveries") or {}
    delivered = [m for m in tasks.task_messages(project, slug)
                 if receipts.get(m["id"], {}).get("agent_id") == turn.get("id")
                 and receipts.get(m["id"], {}).get("session_id") == task.get("session_id")]
    return tasks.render_inbox(delivered) or (RESUME_PROMPT if turn.get("input_delivered") else UNKNOWN_RESUME_PROMPT)


def activity(project: str, slug: str) -> dict:
    """Replaceable public output for this worker, with source time and positive message receipts."""
    _require_task_access(project, slug)
    task = S.load_task(project, slug)
    generation = task.get("agent_id") or None
    result = {"generation": generation, "state": "empty", "commentary": None, "observation": None, "delivered": []}
    engine = engines.transcript_engine(task)
    worktree = str(task.get("worktree") or "")
    source = engines.activity_source(task, job_root=dispatch.l2_job_root(project, slug))
    context = engines.session_context_source(task)
    # These timestamps stay attached to actual native source messages, never poll/turn start times.
    native_text: dict[tuple[str, int, str], str] = {}
    started = _when(source[1].get("started_at")) if source else None
    if context:
        messages = [(message["id"], tasks.render_inbox([message])) for message in tasks.task_messages(project, slug)]
        delivered: dict[str, str | None] = {}
        for record, _ in _read_jsonl(context):
            if record is None:
                continue
            at = record.get("timestamp") if _when(record.get("timestamp")) is not None else None
            for text in engines.delivered_context(engine, record):
                while text:
                    match = next(((identity, rendered) for identity, rendered in messages
                                  if text == rendered or text.startswith(rendered + "\n\n")), None)
                    if match is None:
                        break
                    identity, rendered = match
                    delivered[identity] = at
                    text = text[len(rendered):].removeprefix("\n\n")
            if started is not None and at and _when(at) >= started:
                identity = engines.public_message_identity(engine, record)
                for block, row in enumerate(engines.transcript_rows(engine, _redact(record), worktree)):
                    if identity and row["role"] == "assistant" and row["kind"] == "message" and row["text"]:
                        native_text.setdefault((identity, block, row["text"]), at)
        result["delivered"] = [{"message_id": identity, "at": at} for identity, at in delivered.items()]
    if not source:
        if generation:
            result.update(state="unavailable", error="Current activity records are unavailable.")
        return result
    path, _ = source
    records = _read_jsonl(path)
    for index, (record, error) in enumerate(records):
        if error:
            result.update(state="unavailable", error="Activity records are incomplete or unreadable.")
            continue
        if result["state"] != "unavailable":
            result["state"] = "available"
        identity = engines.public_message_identity(engine, record)
        for block, row in enumerate(engines.transcript_rows(engine, _redact(record), worktree)):
            if row["role"] != "assistant" or row["kind"] != "message" or not row["text"]:
                continue
            at = row.get("at") if _when(row.get("at")) is not None else native_text.get((identity, block, row["text"]))
            result["commentary"] = {"id": f"{generation}:{index}:{block}", "text": row["text"], "at": at,
                                    "time_kind": "source" if at else "unknown"}
    if records:
        try:
            result["observation"] = {"at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                                     "label": "Recorded output changed"}
        except OSError:
            result.update(state="unavailable", error="Current activity records are unavailable.")
    return result


PAGE_ITEMS = 50
PAGE_BYTES = 64 * 1024
INDEX_LIMIT = 4
INDEX_TTL = 60.0
TOMBSTONE_LIMIT = 4096
_REDACTION = "credential-shaped keys and values are redacted; model reasoning is never shown"
_CALLS = {"command", "file", "tool"}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _stat(path: Path) -> tuple:
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except OSError:
        return ()


def _inputs(project: str, slug: str, task: dict) -> tuple[str, list]:
    sources = engines.transcript_sources(task, job_root=dispatch.l2_job_root(project, slug))
    directory = S.task_dir(project, slug)
    files = [(name, _stat(directory / name)) for name in ("events.log", "brief.md", "conversation.jsonl")]
    fields = {key: task.get(key) for key in ("l2_engine", "session_id", "attempt", "agent_id", "worktree", "message_deliveries")}
    return _digest([files, fields, config.operator_label(),
                    [(str(path), _stat(path), turn) for path, turn in sources]]), sources


def _clock_order(value: float) -> str:
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    return f"{(~bits & ((1 << 64) - 1)) if bits >> 63 else bits ^ (1 << 63):016x}"


def _order(when: float, priority: int, source_order: int, turn_time: float, offset: int, block: int = 0) -> str:
    """Fixed-width ASCII hex: ordinary string comparison is chronological, never locale sorting."""
    return f"{_clock_order(when)}{priority:x}{_clock_order(turn_time)}{source_order:016x}{offset + 1:016x}{block:08x}"


def _preview(row: dict) -> dict:
    row = dict(row, raw=None, truncated=False)
    for key, value in list(row.items()):
        if not isinstance(value, str):
            continue
        limit = MAX_DEFAULT_TEXT if key in ("text", "output") else 512
        if len(value) > limit:
            row[key] = value[:limit] + "\n… output collapsed"
            row["truncated"] = True
    # Non-BMP text expands twelvefold under the server's default JSON encoding. Keep enough room
    # for the response envelope and one deletion even when both text and output are oversized.
    while len(json.dumps(row).encode()) > PAGE_BYTES - 4096:
        key = max(("text", "output", "summary"), key=lambda key: len(row.get(key, "")))
        value = row.get(key, "")
        row[key] = value[:len(value) // 2] + "\n… output collapsed"
        row["truncated"] = True
    return row


@dataclass
class _Meta:
    signature: str
    order: str
    version: int
    highest_order: str


@dataclass
class _Index:
    epoch: str = field(default_factory=lambda: uuid.uuid4().hex)
    version: int = 0
    fingerprint: str = ""
    rows: dict[str, _Meta] = field(default_factory=dict)
    deleted: dict[str, tuple[int, str]] = field(default_factory=dict)
    sources: dict[str, tuple] = field(default_factory=dict)
    has_engine_records: bool = False
    touched: float = field(default_factory=time.monotonic)
    lock: object = field(default_factory=threading.Lock)

    def reset(self):
        self.epoch = uuid.uuid4().hex
        self.version = 0
        self.rows.clear()
        self.deleted.clear()


_indexes: OrderedDict[tuple, _Index] = OrderedDict()
_indexes_lock = threading.Lock()


def _index(key: tuple) -> _Index:
    now = time.monotonic()
    with _indexes_lock:
        for expired in [key for key, value in _indexes.items() if now - value.touched >= INDEX_TTL]:
            del _indexes[expired]
        index = _indexes.pop(key, None) or _Index()
        index.touched = now
        _indexes[key] = index
        while len(_indexes) > INDEX_LIMIT:
            _indexes.popitem(last=False)
        return index


def _projection(project: str, slug: str, task: dict, engine: str, raw: bool, sources: list,
                previous_sources: dict) -> tuple[dict, dict, dict, bool, bool]:
    """Materialize one canonical read, returning bodies only to this request, never to the index."""
    timeline: list[dict] = []
    records: dict[str, dict] = {}
    folded: set[str] = set()
    source_state: dict[str, tuple] = {}
    replacement = False
    has_engine = False
    worktree = str(task.get("worktree") or "")
    source_order = 0

    def add(row, safe, source, when, priority, turn_time, offset, block=0):
        identity = _digest([source, offset, safe, block])
        row = dict(row, id=identity, order=_order(when, priority, source_order, turn_time, offset, block),
                   session_id=row.get("session_id") or task.get("session_id"), raw=None)
        records[identity] = safe
        if raw:
            row["text"] = json.dumps(safe, ensure_ascii=False, indent=2)
            row.pop("output", None)
            row.pop("summary", None)
        timeline.append(row)
        return row

    event_path = S.task_dir(project, slug) / "events.log"
    event_source = f"platform:{_stat(event_path)[:2]}"
    for offset, event in enumerate(S.read_events(project, slug)):
        safe = _redact(event)
        add(_platform_row(event, False), safe, event_source, _when(event.get("at")) or 0.0, 0, 0.0, offset)
    for source_order, (path, turn) in enumerate(sources):
        turn = turn or {}
        source = f"{path}:{_stat(path)[:2]}"
        turn_time = _when(turn.get("started_at")) or 0.0
        when = turn_time
        if turn:
            try:
                prompt = _turn_prompt(project, slug, task, turn)
            except ValueError as exc:
                prompt = ""
                add(_row("platform", "error", "system", str(exc), type="record-error"), {"error": str(exc)},
                    source, when, 1, turn_time, -1)
            if prompt:
                safe = {"prompt": _redact(prompt)}
                add(_row("platform", "message", "user", safe["prompt"], type="prompt", at=turn.get("started_at")),
                    safe, source, when, 1, turn_time, -1)
        try:
            data = path.read_bytes()
        except OSError:
            add(_row(engine, "error", "system", "source unavailable", type="record-error"),
                {"error": "source unavailable"}, source, when, 1, turn_time, 0)
            continue
        has_engine = has_engine or bool(data)
        # Only complete bytes are append-stable; finishing a partial last line is not replacement.
        complete = max(data.rfind(b"\n"), data.rfind(b"\r")) + 1
        old = previous_sources.get(str(path))
        if old and (old[0] != source or old[3] != source_order or len(data) < old[1]
                    or hashlib.sha256(data[:old[1]]).hexdigest() != old[2]):
            replacement = True
        source_state[str(path)] = (source, complete, hashlib.sha256(data[:complete]).hexdigest(), source_order)
        calls: dict[str, dict] = {}
        pending: dict[str, list[dict]] = {}
        offset = 0
        for number, line in enumerate(data.splitlines(keepends=True), 1):
            error = None
            record = None
            if not line.endswith((b"\n", b"\r")):
                error = "partial record; waiting for completion"
            else:
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        error = f"non-object record {number}"
                except (ValueError, UnicodeDecodeError):
                    error = f"corrupt record {number}"
            if error:
                add(_row(engine, "error", "system", error, type="record-error"), {"error": error},
                    source, when, 1, turn_time, offset)
                offset += len(line)
                continue
            safe = _redact(record)
            rows = engines.transcript_rows(engine, safe, worktree)  # Also removes private reasoning from safe.
            when = _when(safe.get("timestamp")) or when
            if raw:
                representative = rows[0] if rows else _row(engine, "engine", "system", type=str(safe.get("type") or "event"))
                add(representative, safe, source, when, 1, turn_time, offset)
            else:
                for block, row in enumerate(rows):
                    item = add(row, safe, source, when, 1, turn_time, offset, block)
                    tool_id = row.get("tool_use_id")
                    if tool_id and row["kind"] in _CALLS:
                        known = calls.get(tool_id)
                        if known:
                            identity, order = known["id"], known["order"]
                            known.update(item, id=identity, order=order)
                            folded.add(item["id"])
                        else:
                            calls[tool_id] = item
                    elif tool_id and row["kind"] == "result":
                        pending.setdefault(tool_id, []).append(item)
            offset += len(line)
        if not raw:
            for tool_id, results in pending.items():
                if tool_id in calls:
                    call = calls[tool_id]
                    call["output"] = "\n".join(result["text"] for result in results)
                    call["error"] = call.get("error", False) or any(result.get("error") for result in results)
                    call["status"] = "completed"
                    for result in results:
                        folded.add(result["id"])
    if set(previous_sources) - set(source_state):
        replacement = True
    if not raw:
        timeline = [row for row in timeline if row["id"] not in folded and row["kind"] not in {"engine", "platform"}
                    and not (row["kind"] == "message" and not row["text"].strip())]
    return {row["id"]: row for row in sorted(timeline, key=lambda row: row["order"])}, records, source_state, has_engine, replacement


def _publish(index: _Index, rows: dict, sources: dict, has_engine: bool, fingerprint: str, replacement: bool):
    if replacement:
        index.reset()
    for identity in index.rows.keys() - rows.keys():
        old = index.rows[identity]
        index.version += 1
        index.deleted[identity] = (index.version, old.highest_order)
    updated = {}
    for identity, row in rows.items():
        signature = _digest(row)
        old = index.rows.get(identity)
        if old is None or old.signature != signature:
            index.version += 1
            old = _Meta(signature, row["order"], index.version, max(row["order"], old.highest_order if old else ""))
        updated[identity] = old
        index.deleted.pop(identity, None)
    index.rows = updated
    if len(index.deleted) > TOMBSTONE_LIMIT:
        index.reset()
        for identity, row in rows.items():
            index.version += 1
            index.rows[identity] = _Meta(_digest(row), row["order"], index.version, row["order"])
    index.sources = sources
    index.has_engine_records = has_engine
    index.fingerprint = fingerprint


def view(project: str, slug: str, *, engine: str, session_id: str, attempt: int, raw: bool = False,
         mode: str = "initial", cursor: str = "", before: str = "", lower: str = "", after: str = "",
         record: str = "", offset: int = 0) -> dict:
    """Recent first, demand-loaded history and coalesced deltas over a metadata-only change index."""
    _require_task_access(project, slug)
    if mode not in {"initial", "history", "delta", "reconcile", "record"}:
        raise ValueError("invalid transcript mode")
    if offset < 0:
        raise ValueError("invalid record offset")
    if any(value and not re.fullmatch(r"[0-9a-f]{73}", value) for value in (before, lower, after)):
        raise ValueError("invalid transcript boundary")
    if len(cursor) > 64 or record and not re.fullmatch(r"[0-9a-f]{64}", record):
        raise ValueError("invalid transcript cursor or record")
    task = S.load_task(project, slug)
    require_generation(task, engine=engine, session_id=session_id, attempt=attempt)
    key = (str(config.project_dir(project)), slug, engine, session_id, attempt, raw)
    index = _index(key)
    with index.lock:
        fingerprint, sources = _inputs(project, slug, task)
        rows = records = None
        if fingerprint != index.fingerprint or mode != "delta":
            rows, records, source_state, has_engine, replacement = _projection(
                project, slug, task, engine, raw, sources, index.sources)
            current = S.load_task(project, slug)
            _require_task_access(project, slug)
            require_generation(current, engine=engine, session_id=session_id, attempt=attempt)
            fresh, _ = _inputs(project, slug, current)
            if fresh != fingerprint:
                # A changing read never becomes an authoritative cursor/index version.
                raise ValueError("session changed while reading; retry")
            _publish(index, rows, source_state, has_engine, fingerprint, replacement)
        _require_task_access(project, slug)
        require_generation(S.load_task(project, slug), engine=engine, session_id=session_id, attempt=attempt)
        result = {"project": project, "slug": slug, "engine": engine, "session_id": session_id, "attempt": attempt,
                  "cursor": f"{index.epoch}:{index.version}", "events": [], "deleted": [], "redaction": _REDACTION,
                  "has_earlier": bool(lower and any(meta.order < lower for meta in index.rows.values())),
                  "has_engine_records": index.has_engine_records, "lower": lower, "more": False, "reset": False, "next": ""}
        since = 0
        if cursor:
            try:
                epoch, value = cursor.split(":")
                since = int(value)
            except (ValueError, AttributeError):
                epoch, since = "", -1
            if epoch != index.epoch or not 0 <= since <= index.version:
                result["reset"] = True
                return result
        elif mode == "delta":
            result["reset"] = True
            return result
        if mode == "record":
            detail = records.get(record) if records is not None else None
            if detail is None:
                raise TranscriptAccessError("record changed or unavailable; refresh the Live session view")
            text = json.dumps(detail, ensure_ascii=False, indent=2)
            result.update(text=text[offset:offset + MAX_DEFAULT_TEXT],
                          next_offset=offset + MAX_DEFAULT_TEXT if offset + MAX_DEFAULT_TEXT < len(text) else None)
            return result

        if mode == "delta":
            changes = [(meta.version, identity, meta.order >= lower) for identity, meta in index.rows.items()
                       if meta.version > since and meta.highest_order >= lower]
            changes.extend((version, identity, False) for identity, (version, order) in index.deleted.items()
                           if version > since and order >= lower)
            changes.sort()
            if changes and rows is None:
                rows, _, _, _, _ = _projection(project, slug, task, engine, raw, sources, index.sources)
                current = S.load_task(project, slug)
                _require_task_access(project, slug)
                require_generation(current, engine=engine, session_id=session_id, attempt=attempt)
                fresh, _ = _inputs(project, slug, current)
                if fresh != fingerprint:
                    raise ValueError("session changed while reading; retry")
            for version, identity, present in changes:
                target = "events" if present else "deleted"
                item = dict(_preview(rows[identity]), version=version) if present else identity
                result[target].append(item)
                if len(result["events"]) + len(result["deleted"]) > PAGE_ITEMS or len(json.dumps(result).encode()) > PAGE_BYTES:
                    result[target].pop()
                    result["more"] = True
                    break
                since = version
            result["cursor"] = f"{index.epoch}:{since if result['more'] else index.version}"
            return result

        ordered = list(rows.values())
        if mode in {"initial", "history"}:
            selected = [row for row in ordered if mode == "initial" or row["order"] < before]
            selected.reverse()
        else:
            selected = [row for row in ordered if row["order"] >= lower and (not after or row["order"] > after)]
        for row in selected:
            item = dict(_preview(row), version=index.rows[row["id"]].version)
            result["events"].append(item)
            if len(result["events"]) > PAGE_ITEMS or len(json.dumps(result).encode()) > PAGE_BYTES - 512:
                result["events"].pop()
                result["more"] = True
                break
        if mode in {"initial", "history"}:
            result["events"].reverse()
            first = result["events"][0]["order"] if result["events"] else before
            result["has_earlier"] = any(row["order"] < first for row in ordered)
            result["lower"] = first if result["has_earlier"] else ""
            result["more"] = False  # History is demand driven; more is reserved for automatic catch-up.
        elif result["more"]:
            result["next"] = result["events"][-1]["order"]
        return result
