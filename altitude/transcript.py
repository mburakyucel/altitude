"""Engine-neutral, read-only L2 transcript projection for the local web viewer.

Only task-owned identities select files. Browser input never becomes a filesystem path. Raw records are
still redacted: local visibility is not permission to disclose credentials, and model reasoning (Claude
thinking blocks, Codex reasoning items) never reaches the page in either mode.

Every row carries what a conversation needs: `role` (user = the dispatcher prompt or a task message,
assistant = the worker, tool = a tool's output, system = engine bookkeeping and task events); for a tool
call the tool name, a one-line input `summary`, and the `tool_use_id` that ties a Claude result row to its
call so the page nests output under the command that produced it. A Codex command carries its own
`output`, since one thread item records both. Rows are ordered by time: platform events, prompts, and
engine records interleave into one timeline.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import config, dispatch, engines, state as S, tasks

MAX_DEFAULT_TEXT = 4000
MAX_SUMMARY = 160
_SECRET_KEY = re.compile(r"(authorization|cookie|password|passwd|secret|token|api[_-]?key|credential)", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}|\b(?:sk|gh[oprsu])_[A-Za-z0-9_-]{12,}")
BOUNDARIES = {"state", "stopped", "resume-held", "resume-failed", "dispatch-failed"}
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
_SUMMARY_KEYS = ("description", "command", "file_path", "notebook_path", "path", "pattern", "query", "skill", "url",
                 "prompt", "text", "message")
_SHELL = re.compile(r"^(?:/(?:usr/)?bin/)?(?:ba|z|da)?sh\s+-l?c\s+(['\"])(.*)\1$", re.S)
_TASK_MESSAGE = re.compile(r"^Message from \w+ \(")
RESUME_PROMPT = "Continue from your progress file."


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
    expected = (str(task.get("l2_engine") or "claude"), str(task.get("session_id") or ""))
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


def _hide_reasoning(record: dict) -> dict:
    """Model reasoning stays on the machine: Claude thinking blocks disappear from the record and a Codex
    reasoning item keeps only its shell. Applied to the redacted copy, so raw mode never carries it either."""
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        message["content"] = [b for b in message["content"]
                              if not (isinstance(b, dict) and str(b.get("type") or "").endswith("thinking"))]
    item = record.get("item")
    if isinstance(item, dict) and item.get("type") == "reasoning":
        record["item"] = {k: v for k, v in item.items() if k in ("id", "type", "status")}
    return record


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


def _engine_paths(project: str, slug: str, task: dict) -> list[Path]:
    """The session files behind the task's current worker: Claude's one session JSONL, or every turn of the
    Codex thread (each turn is a worker record in the task's job root)."""
    engine, session_id = str(task.get("l2_engine") or "claude"), str(task.get("session_id") or "")
    if engine == "codex":
        return engines.codex_turns(dispatch.l2_job_root(project, slug), session_id)
    path = _claude_path(session_id)
    return [path] if path and path.is_file() else []


def _when(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _one_line(text, limit: int = MAX_SUMMARY) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _relative(path, worktree: str) -> str:
    """A path inside the task's worktree reads as the repository path, the way the worker names it."""
    path = str(path or "")
    root = worktree.rstrip("/")
    return path[len(root) + 1:] if root and path.startswith(root + "/") else path


def _shell_command(command) -> str:
    """The command as typed: Codex wraps it in `bash -lc '…'`."""
    match = _SHELL.match(str(command or "").strip())
    if not match:
        return str(command or "")
    quote, inner = match.groups()
    return inner.replace("'\\''", "'") if quote == "'" else inner


def _first_text(value: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        if isinstance(value.get(key), str) and value[key].strip():
            return value[key]
    return ""


def _row(source: str, kind: str, role: str, text: str = "", *, type: str = "event", at=None, **fields) -> dict:
    row = {"source": source, "kind": kind, "role": role, "type": type, "at": at, "text": text}
    row.update({k: v for k, v in fields.items() if v is not None})
    return row


def _platform_row(event: dict, raw: bool) -> dict:
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
                at=event.get("at"), session_id=event.get("session_id"), raw=_redact(event) if raw else None)


def _tool_call(block: dict, worktree: str, at) -> dict:
    """One Claude tool_use block: the tool, a one-line summary, and the call's own detail as text (the full
    command, an edit as removed and added lines, a written file's content, otherwise the input)."""
    name = str(block.get("name") or "tool")
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    tool_use_id = str(block.get("id") or "") or None
    if name == "Bash":
        command = str(inp.get("command") or "")
        return _row("claude", "command", "assistant", command, type="tool_use", at=at, tool=name,
                    summary=_one_line(command), tool_use_id=tool_use_id)
    if name in _FILE_TOOLS:
        path = _relative(inp.get("file_path") or inp.get("notebook_path"), worktree)
        old, new = str(inp.get("old_string") or ""), str(inp.get("new_string") or "")
        if name == "Write":
            detail = str(inp.get("content") or "")
        elif old or new:
            detail = "\n".join([f"- {line}" for line in old.splitlines()] + [f"+ {line}" for line in new.splitlines()])
        else:
            detail = ""
        return _row("claude", "file", "assistant", detail, type="tool_use", at=at, tool=name, summary=path,
                    tool_use_id=tool_use_id)
    summary = _first_text(inp, _SUMMARY_KEYS) or (json.dumps(inp, ensure_ascii=False) if inp else "")
    return _row("claude", "tool", "assistant", json.dumps(inp, ensure_ascii=False, indent=2) if inp else "",
                type="tool_use", at=at, tool=name, summary=_one_line(summary), tool_use_id=tool_use_id)


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, dict):
            parts.append(part["text"] if isinstance(part.get("text"), str) else f"[{part.get('type') or 'block'}]")
    return "\n".join(parts)


def _claude_rows(record: dict, worktree: str) -> list[dict]:
    """Rows for one record of Claude's session JSONL, one per content block in the order the model wrote them."""
    typ = str(record.get("type") or "event")
    at = record.get("timestamp")
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content")
    rows: list[dict] = []
    if typ in ("user", "assistant"):
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
        injected = typ == "user" and bool(record.get("isMeta") or record.get("isCompactSummary"))
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":  # injected context (caveats, skill text, a compaction summary) is not a prompt
                text = str(block.get("text") or "")
                rows.append(_row("claude", "engine" if injected else "message", "system" if injected else typ, text,
                                 type=typ, at=at))
            elif btype == "tool_use":
                rows.append(_tool_call(block, worktree, at))
            elif btype == "tool_result":
                rows.append(_row("claude", "result", "tool", _result_text(block), type="tool_result", at=at,
                                 tool_use_id=str(block.get("tool_use_id") or "") or None,
                                 error=bool(block.get("is_error"))))
    elif typ == "attachment":  # a task message, delivered by the inbox hook at the worker's checkpoint
        attachment = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
        parts = attachment.get("content") if isinstance(attachment.get("content"), list) else []
        rows.extend(_row("claude", "message", "user", text, type="task-message", at=at)
                    for text in parts if isinstance(text, str) and _TASK_MESSAGE.match(text))
    elif typ == "system":
        text = record.get("content") if isinstance(record.get("content"), str) else ""
        rows.append(_row("claude", "message" if text else "engine", "system", text,
                         type=str(record.get("subtype") or typ), at=at))
    return rows or [_row("claude", "engine", "system", "", type=typ, at=at)]


def _codex_error(record: dict) -> str:
    error = record.get("error") if isinstance(record.get("error"), dict) else {}
    return _first_text(record, ("message",)) or _first_text(error, ("message",)) or json.dumps(record)


def _codex_rows(record: dict, worktree: str) -> list[dict]:
    """Rows for one event of a Codex thread. A command item carries its command and its output together."""
    typ = str(record.get("type") or "event")
    item = record.get("item") if isinstance(record.get("item"), dict) else {}
    item_type = str(item.get("type") or "")
    if typ in ("error", "turn.failed"):
        return [_row("codex", "error", "system", _codex_error(record), type=typ)]
    if not item_type or item_type == "reasoning":
        return [_row("codex", "engine", "system", "", type=typ)]
    item_id = str(item.get("id") or "") or None
    status = item.get("status") if isinstance(item.get("status"), str) else None
    if item_type == "agent_message":
        return [_row("codex", "message", "assistant", str(item.get("text") or ""), type=typ)]
    if item_type == "error":
        return [_row("codex", "error", "system", _first_text(item, ("message", "text")), type=typ)]
    if item_type == "command_execution":
        command = _shell_command(item.get("command"))
        return [_row("codex", "command", "assistant", command, type=typ, tool="command", summary=_one_line(command),
                     tool_use_id=item_id, status=status, output=str(item.get("aggregated_output") or ""),
                     error=item.get("exit_code") not in (None, 0))]
    if item_type == "file_change":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        lines = [f"{c.get('kind') or 'change'} {_relative(c.get('path'), worktree)}".rstrip()
                 for c in changes if isinstance(c, dict)]
        return [_row("codex", "file", "assistant", "\n".join(lines), type=typ, tool="file_change",
                     summary=_one_line(", ".join(lines)), tool_use_id=item_id, status=status)]
    if item_type == "mcp_tool_call":
        summary = ".".join(str(item.get(k) or "") for k in ("server", "tool") if item.get(k)) or item_type
    else:
        summary = _first_text(item, ("query", "text", "message", "command")) or item_type
    return [_row("codex", "tool", "assistant", _first_text(item, ("text", "output", "result", "message")), type=typ,
                 tool=item_type, summary=_one_line(summary), tool_use_id=item_id, status=status)]


def _codex_prompt(project: str, slug: str, turn: dict, previous_started: float | None) -> str:
    """Codex does not echo its prompt into the thread's stdout, so the task's own records stand in: the brief
    for the first turn, the task messages a resume delivered for a later one."""
    if not turn.get("resume"):
        try:
            return (S.task_dir(project, slug) / "brief.md").read_text()
        except OSError:
            return ""
    started = _when(turn.get("started_at"))
    delivered = [m for m in tasks.task_messages(project, slug) if m.get("role") in ("burak", "l3")
                 and (previous_started or 0.0) < (_when(m.get("at")) or 0.0) <= (started or float("inf"))]
    return tasks.render_inbox(delivered) or RESUME_PROMPT


def _engine_timeline(project: str, slug: str, task: dict, *, engine: str, raw: bool) -> list[tuple[float, int, dict]]:
    """Engine rows keyed by time. Claude records carry timestamps; a Codex turn's records share the turn's start."""
    worktree = str(task.get("worktree") or "")
    timeline: list[tuple[float, int, dict]] = []
    previous_started: float | None = None
    for path in _engine_paths(project, slug, task):
        when = 0.0
        if engine == "codex":
            turn = S.read_json(path.parent / f"{path.name.split('.')[0]}.json", None) or {}
            when = _when(turn.get("started_at")) or 0.0
            try:
                prompt = _codex_prompt(project, slug, turn, previous_started)
            except ValueError as exc:
                timeline.append((when, 1, _row("platform", "error", "system", str(exc), type="record-error")))
            else:
                if prompt:
                    timeline.append((when, 1, _row("platform", "message", "user", prompt, type="prompt",
                                                   at=turn.get("started_at"))))
            previous_started = when
        for record, error in _read_jsonl(path):
            if error:
                timeline.append((when, 1, _row(engine, "error", "system", error, type="record-error")))
                continue
            assert record is not None
            safe = _hide_reasoning(_redact(record))
            rows = _codex_rows(safe, worktree) if engine == "codex" else _claude_rows(safe, worktree)
            when = _when(safe.get("timestamp")) or when
            for row in rows:
                row["raw"] = safe if raw else None
                timeline.append((when, 1, row))
    return timeline


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
