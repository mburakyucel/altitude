"""Step lines: what an agent's session did, as a repair's page and an agent chat's turns show it, from the rows its
engine's events give (`engines.transcript_rows`): what the agent said, the commands it ran (failed ones with their
exit status, unfinished ones running, or cut off once the session ended), files it changed and pages it fetched."""
from __future__ import annotations

import re

from . import config

TEXT_LIMIT = 4000
_EXIT = re.compile(r"(?:Exit code|exit status|exited with code)\s+(\d+)", re.I)
_CHANGES = {"Edit", "Write", "MultiEdit", "NotebookEdit", "file_change"}
_FETCHES = {"WebFetch", "WebSearch", "web_search"}


def _short(text: str) -> str:
    return (text or "").replace(str(config.HOME), "~")


def _failed(step: dict, output: str) -> None:
    code = _EXIT.search(output or "")
    step.update(verb="failed", exit=int(code[1]) if code else None)


def lines(rows: list[dict], ended: bool, *, own: re.Pattern | None = None) -> list[dict]:
    """The step lines of `rows`, oldest first. A command `own` matches is the session's own verb, which its page shows
    in its own way, so it is no step."""
    steps: list[dict] = []
    calls: dict[str, dict] = {}
    for row in rows:
        kind, tool = row.get("kind"), row.get("tool")
        if kind == "message" and row.get("role") == "assistant" and (row.get("text") or "").strip():
            steps.append({"kind": "say", "at": row.get("at"), "text": row["text"].strip()})
        elif kind == "result" and row.get("tool_use_id") in calls:
            step = calls[row["tool_use_id"]]
            output = row.get("text") or ""
            step.update(done=True, output=output[:TEXT_LIMIT])
            if row.get("error"):
                _failed(step, output)
        elif kind in ("command", "file", "tool"):
            text = row.get("summary") or row.get("text") or ""
            if kind == "command" and own is not None and own.match(row.get("text") or ""):
                if row.get("tool_use_id"):
                    calls[row["tool_use_id"]] = {}
                continue
            if kind == "file" and tool not in _CHANGES or kind == "tool" and tool not in _FETCHES:
                continue
            identity = row.get("tool_use_id")
            if identity and identity in calls and calls[identity]:  # a later event of the same item: its outcome
                step = calls[identity]
                if row.get("status") in ("completed", "failed", "declined"):
                    step.update(done=True, output=(row.get("output") or "")[:TEXT_LIMIT])
                    if row.get("error"):
                        _failed(step, row.get("output") or "")
                continue
            verb = "changed" if kind == "file" else "fetched" if kind == "tool" else "ran"
            done = kind != "command" or row.get("status") in ("completed", "failed", "declined")
            step = {"kind": "step", "at": row.get("at"), "verb": verb, "text": _short(text)[:TEXT_LIMIT],
                    "detail": _short(row.get("text") or "")[:TEXT_LIMIT], "done": done,
                    "output": (row.get("output") or "")[:TEXT_LIMIT] if done else ""}
            if row.get("error"):
                _failed(step, row.get("output") or "")
            if identity:
                calls[identity] = step
            steps.append(step)
    for step in steps:
        if step["kind"] == "step" and step["verb"] == "ran" and not step.pop("done"):
            step["verb"] = "cut" if ended else "running"
        step.pop("done", None)
    return steps
