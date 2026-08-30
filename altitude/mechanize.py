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
_EXCLUDED_INCIDENT_SHAPES = {"Read", "Edit", "Write"}
_SHELL_BREAKS = {"|", "||", "&&", ";", "&"}


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


def _arguments(words: list[str]) -> list[str]:
    out = []
    for word in words:
        if word in _SHELL_BREAKS or word.startswith(">") or word.startswith("<"):
            break
        if not word.startswith("-"):
            out.append(word)
    return out


def _bash_shape(command: str) -> str | None:
    words = _command_words(command)
    if not words:
        return None
    first, rest = words[0], words[1:]
    args = _arguments(rest)
    if first in _SUBCOMMANDS and args:
        return f"{first} {args[0]}"
    if first in _TARGET_COMMANDS and args:
        # sed/grep take an expression before the file; head/tail often take a count.
        target = args[-1] if first in {"sed", "grep", "head", "tail"} else args[0]
        return f"{first} {Path(target).name}"
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
    if value is None or value == "":
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _transcripts(project: str, cutoff: float) -> list[tuple[Path, float]]:
    root = config.project_path(project)
    cwds = [root]
    worktrees = root / ".claude" / "worktrees"
    try:
        cwds.extend(p for p in worktrees.iterdir() if p.is_dir())
    except OSError:
        pass
    found: dict[Path, float] = {}
    for cwd in cwds:
        directory = _transcript_root() / _cwd_slug(cwd)
        try:
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
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            try:
                if _timestamp(obj.get("timestamp"), mtime) < cutoff:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            message = obj.get("message") or {}
            if not isinstance(message, dict):
                continue
            content = message.get("content") or []
            if not isinstance(content, list):
                continue
            shapes = {
                shape for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
                if (shape := _tool_shape(block))
            }
            if not shapes:
                continue
            tokens = _usage_tokens(message)
            for shape in shapes:
                counts[shape]["turns"] += 1
                counts[shape]["context_tokens"] += tokens
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
        if row["turns"] < INCIDENT_TURNS or shape in _EXCLUDED_INCIDENT_SHAPES or shape.split()[0] == "alt":
            continue
        previous = project_stamps.get(shape) or {}
        if previous.get("incident") and _recent(previous.get("last"), now):
            project_stamps[shape] = {"last": S.now(), "incident": previous["incident"],
                                     "count": int(previous.get("count", 0)) + 1}
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
    """Run at most once per project per 24 hours."""
    path = config.MONITOR_DIR / "mechanize-stamp.json"
    stamps = S.read_json(path, {}) or {}
    now = time.time()
    if project in stamps and now - _timestamp(stamps[project], 0) < RUN_INTERVAL_SECONDS:
        return None
    result = run_for(project)
    stamps[project] = S.now()
    S.write_json(path, stamps)
    return result
