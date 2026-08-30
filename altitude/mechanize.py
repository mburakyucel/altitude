"""Nightly tool-shape histogram and mechanization incidents (decision 47)."""
from __future__ import annotations

import json
import re
import shlex
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import config, improve, state as S, tasks as T

WINDOW_DAYS = 7
WINDOW_SECONDS = WINDOW_DAYS * 86400
RUN_INTERVAL_SECONDS = 24 * 3600
INCIDENT_TURNS = 25
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SUBCOMMANDS = {"git", "gh", "alt", "claude"}
_TARGET_COMMANDS = {"cat", "sed", "grep", "ls", "head", "tail"}
_VALUE_FLAGS = {
    "git": {"-C", "-c", "--git-dir", "--work-tree"},
    "gh": {"-R", "--repo"},
}
_EXCLUDED_INCIDENT_SHAPES = {"Read", "Edit", "Write"}
# Decision 47 / mechanize-histogram-exclude-alt-by-basen: R-003 wrapper scripts make
# cd/echo navigation and output an artifact of the harness, not a mechanizable action.
_EXCLUDED_SHELL_SHAPES = {"cd", "echo", "pwd", "ls", "true"}
_SHELL_BREAKS = {"|", "||", "&&", ";", "&"}


def _is_excluded_shape(shape: str) -> bool:
    words = shape.split()
    first = words[0] if words else ""
    return (shape in _EXCLUDED_INCIDENT_SHAPES
            or Path(first).name == "alt"
            or shape in _EXCLUDED_SHELL_SHAPES)


def _transcript_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _cwd_slug(path: Path) -> str:
    return str(path.resolve()).replace("/", "-").replace(".", "-")


def _command_words(command: str) -> list[str]:
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    if not words:
        return []
    while words and _ASSIGNMENT.match(words[0]):
        words.pop(0)
    if words and words[0] == "sudo":
        words.pop(0)
        while words and words[0].startswith("-"):
            words.pop(0)
        while words and _ASSIGNMENT.match(words[0]):
            words.pop(0)
    return words


def _arguments(words: list[str], value_flags: set[str] | None = None) -> list[str]:
    out = []
    skip_value = False
    for word in words:
        if word in _SHELL_BREAKS or word.startswith(">") or word.startswith("<"):
            break
        if skip_value:
            skip_value = False
            continue
        if word in (value_flags or set()):
            skip_value = True
            continue
        if not word.startswith("-"):
            out.append(word)
    return out


def _bash_shape(command: str) -> str | None:
    words = _command_words(command)
    if not words:
        return None
    first, rest = Path(words[0]).name or words[0], words[1:]
    args = _arguments(rest, _VALUE_FLAGS.get(first))
    if first in _SUBCOMMANDS and args:
        return f"{first} {args[0]}"
    if first in _TARGET_COMMANDS and args:
        # sed/grep take an expression before the file; without both, there is no safe target to expose.
        if first in {"sed", "grep"}:
            if len(args) < 2:
                return first
            target = args[-1]
        else:
            # head/tail often take a count; cat/ls take their first non-flag target.
            target = args[-1] if first in {"head", "tail"} else args[0]
        return f"{first} {Path(target).name or target}"
    return first


def _tool_shape(block: dict) -> str | None:
    name = block.get("name")
    if not isinstance(name, str) or not name:
        return None
    if name != "Bash":
        return name
    tool_input = block.get("input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return _bash_shape(command) if isinstance(command, str) else None


def _timestamp(value, fallback: float) -> float:
    try:
        if value is None or value == "":
            return fallback
        if isinstance(value, (int, float)):
            return float(value)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return fallback


def _transcripts(project: str, cutoff: float) -> list[tuple[Path, float]]:
    root = config.project_path(project)
    project_slug = _cwd_slug(root)
    worktree_prefix = f"{project_slug}--claude-worktrees-"
    try:
        candidates = list(_transcript_root().iterdir())
    except OSError:
        return []
    found: dict[Path, float] = {}
    for directory in candidates:
        if directory.name != project_slug and not directory.name.startswith(worktree_prefix):
            continue
        try:
            if not directory.is_dir():
                continue
            paths = directory.glob("*.jsonl")
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size < 2 or stat.st_mtime < cutoff:
                    continue
                found[path] = stat.st_mtime
        except OSError:
            continue
    return sorted(found.items())


def _usage_tokens(message: dict) -> int:
    usage = message.get("usage") or {}
    return sum(int(usage.get(field) or 0) for field in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
    ))


def _collect(project: str, now: float) -> list[dict]:
    cutoff = now - WINDOW_SECONDS
    counts: dict[str, dict] = defaultdict(lambda: {"turns": 0, "context_tokens": 0, "sessions": set()})
    for path, mtime in _transcripts(project, cutoff):
        turns: dict[tuple[str, str], dict] = {}
        try:
            with path.open(errors="replace") as transcript:
                for line_number, line in enumerate(transcript):
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict) or obj.get("type") != "assistant":
                        continue
                    if _timestamp(obj.get("timestamp"), mtime) < cutoff:
                        continue
                    message = obj.get("message") or {}
                    if not isinstance(message, dict):
                        continue
                    content = message.get("content") or []
                    if not isinstance(content, list):
                        continue
                    message_id = message.get("id")
                    request_id = obj.get("requestId")
                    if isinstance(message_id, (str, int)) and message_id != "":
                        key = ("message", str(message_id))
                    elif isinstance(request_id, (str, int)) and request_id != "":
                        key = ("request", str(request_id))
                    else:
                        key = ("line", str(line_number))
                    turn = turns.setdefault(key, {"shapes": set(), "tokens": 0})
                    turn["shapes"].update(
                        shape for block in content
                        if isinstance(block, dict) and block.get("type") == "tool_use"
                        if (shape := _tool_shape(block))
                    )
                    # Lines in one turn repeat usage; max counts it once and tolerates a partial zero line.
                    turn["tokens"] = max(turn["tokens"], _usage_tokens(message))
        except OSError:
            continue
        for turn in turns.values():
            for shape in turn["shapes"]:
                counts[shape]["turns"] += 1
                counts[shape]["context_tokens"] += turn["tokens"]
                counts[shape]["sessions"].add(str(path))
    rows = [
        {"shape": shape, "turns": values["turns"], "context_tokens": values["context_tokens"],
         "sessions": len(values["sessions"])}
        for shape, values in counts.items()
    ]
    return sorted(rows, key=lambda row: (-row["turns"], -row["context_tokens"], row["shape"]))


def _recent(iso: str | None, now: float) -> bool:
    return bool(iso) and now - _timestamp(iso, 0) < WINDOW_SECONDS


def _file_incidents(project: str, rows: list[dict], now: float) -> None:
    path = config.MONITOR_DIR / "mechanize-incidents.json"
    stamps = S.read_json(path, {}) or {}
    project_stamps = stamps.setdefault(project, {})
    changed = False
    for row in rows:
        shape = row["shape"]
        if row["turns"] < INCIDENT_TURNS or _is_excluded_shape(shape):
            continue
        previous = project_stamps.get(shape) or {}
        if previous.get("incident") and _recent(previous.get("last"), now):
            # Keep the filing time fixed so the dedup window expires seven days after the actual incident.
            previous["count"] = int(previous.get("count", 0)) + 1
            project_stamps[shape] = previous
            changed = True
            continue
        incident = improve.new_incident(
            project, title=f"mechanize: `{shape}`", task=None,
            what=f"mechanize: `{shape}` ×{row['turns']} turns ≈ {row['context_tokens']} context-tokens / 7d",
            evidence=f"monitor/tool-shapes-{project}.json",
            cause="a recurring manual action that has no `alt` command or skill behind it yet",
            tags=["mechanize", "decision-47"], generalizable="yes", mechanism="skill",
            scope="project", actor="altd",
        )
        project_stamps[shape] = {"last": S.now(), "incident": incident["id"],
                                 "count": int(previous.get("count", 0)) + 1}
        # The incident exists before its dedup stamp does: a failed new_incident can never suppress a retry.
        S.write_json(path, stamps)
        changed = False
        T.fyi(project, None, f"mechanize: `{shape}` recurred in {row['turns']} turns / 7d — incident {incident['id']}.", actor="altd")
    if changed:
        S.write_json(path, stamps)


def run_for(project: str) -> dict:
    """Build the seven-day histogram now, ignoring the daily scheduler stamp."""
    now = time.time()
    result = {"project": project, "generated": S.now(), "window_days": WINDOW_DAYS,
              "shapes": _collect(project, now)}
    S.write_json(config.MONITOR_DIR / f"tool-shapes-{project}.json", result)
    _file_incidents(project, result["shapes"], now)
    return result


def run_due(project: str) -> dict | None:
    """Attempt at most once per project per 24 hours, including failed attempts."""
    path = config.MONITOR_DIR / "mechanize-stamp.json"
    stamps = S.read_json(path, {}) or {}
    now = time.time()
    if project in stamps and now - _timestamp(stamps[project], 0) < RUN_INTERVAL_SECONDS:
        return None
    try:
        return run_for(project)
    finally:
        # The outer tick files the system fault; stamping here prevents a persistent fault retrying every tick.
        stamps[project] = S.now()
        S.write_json(path, stamps)
