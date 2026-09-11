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
import re
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


def require_generation(task: dict, *, engine: str, session_id: str) -> None:
    expected = (engines.transcript_engine(task), str(task.get("session_id") or ""))
    supplied = (str(engine), str(session_id))
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


def _engine_timeline(project: str, slug: str, task: dict, *, engine: str, raw: bool) -> list[tuple[float, int, dict]]:
    """Engine rows ordered by source time or their containing turn (without inventing prose timestamps)."""
    worktree = str(task.get("worktree") or "")
    timeline: list[tuple[float, int, dict]] = []
    for path, turn in engines.transcript_sources(task, job_root=dispatch.l2_job_root(project, slug)):
        when = 0.0
        if turn:
            when = _when(turn.get("started_at")) or 0.0
            try:
                prompt = _turn_prompt(project, slug, task, turn)
            except ValueError as exc:
                timeline.append((when, 1, _row("platform", "error", "system", str(exc), type="record-error")))
            else:
                if prompt:
                    timeline.append((when, 1, _row("platform", "message", "user", _redact(prompt), type="prompt",
                                                   at=turn.get("started_at"))))
        for record, error in _read_jsonl(path):
            if error:
                timeline.append((when, 1, _row(engine, "error", "system", error, type="record-error")))
                continue
            assert record is not None
            safe = _redact(record)
            rows = engines.transcript_rows(engine, safe, worktree)
            when = _when(safe.get("timestamp")) or when
            for row in rows:
                row["raw"] = safe if raw else None
                timeline.append((when, 1, row))
    return timeline


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


def _bounded(row: dict, key: str, raw: bool) -> bool:
    text = row.get(key)
    if raw or not isinstance(text, str) or len(text) <= MAX_DEFAULT_TEXT:
        return False
    row[key] = text[:MAX_DEFAULT_TEXT] + "\n… output collapsed"
    return True


def view(project: str, slug: str, *, engine: str, session_id: str, cursor: int = 0, raw: bool = False) -> dict:
    _require_task_access(project, slug)
    task = S.load_task(project, slug)
    require_generation(task, engine=engine, session_id=session_id)
    timeline = [(_when(event.get("at")) or 0.0, 0, _platform_row(event, raw)) for event in S.read_events(project, slug)]
    timeline.extend(_engine_timeline(project, slug, task, engine=engine, raw=raw))
    rows = [row for _, _, row in sorted(timeline, key=lambda entry: entry[:2])]
    for seq, row in enumerate(rows):
        row["seq"] = seq
        row.setdefault("session_id", session_id)
        row.setdefault("raw", None)
        row["truncated"] = _bounded(row, "text", raw) | _bounded(row, "output", raw)
    start = max(0, int(cursor or 0))
    return {"project": project, "slug": slug, "engine": engine, "session_id": session_id, "cursor": len(rows), "events": rows[start:],
            "redaction": "credential-shaped keys and values are redacted; model reasoning is never shown"}
