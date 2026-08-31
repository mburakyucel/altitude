#!/usr/bin/env python3
"""Codex PreToolUse hook: fail-closed L2 tool/L1 envelope with atomic counters."""
import fcntl
import json
import os
import re
import shlex
import sys
from pathlib import Path

from guard import _command_word


def fail(message: str, *, monitor: Path | None = None, key: str = "unknown") -> None:
    if monitor is not None:
        try:
            with open(monitor / "hook-faults.log", "a") as stream:
                stream.write(f"codex_l2_cap.py key={key}: {message}\n")
        except OSError:
            pass
    print(f"altitude: Codex L2 envelope hook failed closed: {message}", file=sys.stderr)
    raise SystemExit(2)


_REPORT_FILES = {"report.md", "report.json", "progress.md"}
_CMDPOS = re.compile(
    r"(?:^|[;&|(){}\x60\n])[ \t]*(?:(?:[A-Za-z_]\w*=\S*|env|nohup|time|exec|sudo)[ \t]+)*"
    r"((?:\S*/)?alt[ \t]+l1[ \t]+run\b)"
)
_PY_CMDPOS = re.compile(
    r"(?:^|[;&|(){}\x60\n])[ \t]*((?:\S*/)?python(?:\d+(?:\.\d+)*)?[ \t]+(?:\S*/)?alt[ \t]+l1[ \t]+run\b)"
)
_PROBE = re.compile(r"(^|\s)(--help|-h|--version|-V)(\s|$)")


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|(){}\n`")
    lexer.whitespace = " \t\r"  # newlines are command separators
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _runs_shell(header: str) -> bool:
    try:
        words = _tokens(header)
    except ValueError:
        return False
    index = _command_word(words)
    return index is not None and Path(words[index]).name in ("sh", "bash", "zsh")


def _heredocs(line: str, quote: str | None = None) -> tuple[list[tuple[str, bool, int]], str | None]:
    found = []
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"':
                i += 1
            i += 1
            continue
        if char in "'\"":
            quote = char
            i += 1
            continue
        if char == "\\":
            i += 2
            continue
        if char == "#" and (i == 0 or line[i - 1] in " \t;&|()"):
            break
        if line.startswith("<<<", i):
            i += 3
            continue
        if not line.startswith("<<", i):
            i += 1
            continue
        start = i
        i += 2
        tabs = i < len(line) and line[i] == "-"
        if tabs:
            i += 1
        while i < len(line) and line[i] in " \t":
            i += 1
        if i >= len(line) or line[i] in "\r\n":
            continue
        if line[i] in "'\"":
            delimiter_quote = line[i]
            end = line.find(delimiter_quote, i + 1)
            if end < 0:
                continue
            delimiter = line[i + 1:end]
            i = end + 1
        else:
            end = i
            while end < len(line) and line[end] not in " \t\r\n;&|()<>":
                end += 1
            delimiter = line[i:end]
            i = end
        if delimiter:
            found.append((delimiter, tabs, start))
    return found, quote


def _executable_text(command: str) -> str:
    """Remove data heredoc bodies and recursively retain shell-source heredocs."""
    kept = []
    pending: list[tuple[str, bool, bool, list[str]]] = []
    quote = None
    for line in command.splitlines(keepends=True):
        if pending:
            delimiter, tabs, shell_body, body = pending[0]
            candidate = line.rstrip("\r\n")
            if tabs:
                candidate = candidate.lstrip("\t")
            if candidate == delimiter:
                pending.pop(0)
                if shell_body:
                    kept.append(_executable_text("".join(body)))
            elif shell_body:
                body.append(line)
            continue
        found, quote = _heredocs(line, quote)
        kept.append(line)
        for delimiter, tabs, start in found:
            pending.append((delimiter, tabs, _runs_shell(line[:start]), []))
    for _delimiter, _tabs, shell_body, body in pending:
        if shell_body:
            kept.append(_executable_text("".join(body)))
    return "".join(kept)


def _blank_quoted(text: str, stack=None) -> tuple[str, list[str]]:
    stack, out, i = list(stack or []), [], 0
    tick = chr(96)
    while i < len(text):
        char = text[i]
        top = stack[-1] if stack else None
        if top in ("'", '"'):
            if char == top:
                stack.pop(); out.append(char)
            elif top == '"' and text.startswith("$(", i):
                stack.append("("); out.append("$("); i += 1
            elif top == '"' and char == tick:
                stack.append(tick); out.append(char)
            elif top == '"' and char == "\\" and i + 1 < len(text):
                out.append("  "); i += 1
            else:
                out.append(char if char == "\n" else " ")
        else:
            if char in "'\"":
                stack.append(char); out.append(char)
            elif text.startswith("$(", i):
                stack.append("("); out.append("$("); i += 1
            elif char == tick:
                stack.pop() if top == tick else stack.append(char); out.append(char)
            elif char == ")" and top == "(":
                stack.pop(); out.append(char)
            elif char == "\\" and i + 1 < len(text):
                out.append(" "); i += 1; out.append(text[i])
            else:
                out.append(char)
        i += 1
    return "".join(out), stack


def _simple_commands(command: str):
    """Yield shell simple-command token lists, preserving execution boundaries."""
    try:
        words = _tokens(command)
    except ValueError:
        return
    current = []
    for word in words:
        if word and all(char in ";&|(){}\n`" for char in word):
            if current:
                yield current
                current = []
        else:
            current.append(word)
    if current:
        yield current


def _launch_in_words(words: list[str]) -> bool:
    index = _command_word(words)
    if index is None:
        return False
    verb = Path(words[index]).name
    tail = words[index + 1:]
    launch_tail = None
    if verb == "alt" and tail[:2] == ["l1", "run"]:
        launch_tail = tail[2:]
    elif re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", verb) and len(tail) >= 3:
        if Path(tail[0]).name == "alt" and tail[1:3] == ["l1", "run"]:
            launch_tail = tail[3:]
    if launch_tail is not None:
        return not _PROBE.search(" ".join(launch_tail))
    if verb in ("sh", "bash", "zsh"):
        for option_index in range(index + 1, len(words)):
            option = words[option_index]
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", option):
                if option_index + 1 < len(words):
                    return _is_l1_launch(words[option_index + 1])
                return False
    return False


def _is_l1_launch(command: str) -> bool:
    executable = _executable_text(command)
    for words in _simple_commands(executable):
        if _launch_in_words(words):
            return True
    # Expose executable command substitutions while blanking ordinary prose.
    visible, _ = _blank_quoted(executable)
    for pattern in (_CMDPOS, _PY_CMDPOS):
        for match in pattern.finditer(visible):
            segment = re.split(r"[;&|)\x60\n]", visible[match.start(1):], 1)[0]
            if not _PROBE.search(segment):
                return True
    return False


def _patch_targets(patch: str) -> list[str]:
    targets = re.findall(r"^(?:\+\+\+|---)\s+(?:[ab]/)?([^\s]+)", patch, re.M)
    targets += re.findall(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", patch, re.M)
    targets += re.findall(r"^\*\*\* Move to:\s*(.+?)\s*$", patch, re.M)
    return [path for path in targets if path != "/dev/null"]


def _checkpoint_tool(root: Path, tool: str, tool_input: dict, command: str) -> bool:
    """Permit only exact task-owned checkpoint files or one standalone alt task status/block."""
    project = os.environ.get("ALTITUDE_PROJECT")
    task = os.environ.get("ALTITUDE_TASK")
    task_dir = (root / project / "tasks" / task).resolve() if project and task else None

    if task_dir is None:
        return False

    def owned(value) -> bool:
        if not value:
            return False
        path = Path(str(value))
        return path.is_absolute() and path.resolve().parent == task_dir and path.name in _REPORT_FILES

    low = tool.lower()
    if low in ("edit", "write", "multiedit"):
        return owned(tool_input.get("file_path") or tool_input.get("path"))
    if low == "apply_patch":
        patch = str(tool_input.get("patch") or tool_input.get("input") or "")
        targets = _patch_targets(patch)
        return bool(targets) and all(owned(path) for path in targets)
    if low in ("exec_command", "shell", "bash"):
        stripped = command.strip()
        if not stripped or re.search(r"[\n;&|<>\x60$()]", stripped):
            return False
        try:
            words = shlex.split(stripped)
        except ValueError:
            return False
        if len(words) < 3 or Path(words[0]).name != "alt" or words[1] != "task":
            return False
        if words[2] == "status":
            return words[3:] in ([], [task])
        if words[2] == "block":
            return (len(words) == 6 and words[3] == task
                    and words[4] == "--reason" and bool(words[5]))
        return False
    return False


try:
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
except (OSError, ValueError, TypeError) as exc:
    fail(f"invalid hook input: {exc}")
if not isinstance(payload, dict):
    fail("hook input is not a JSON object")

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from altitude.alt_broker import BrokerDenied, forward_hook_if_configured
    broker_response = forward_hook_if_configured(payload)
except (OSError, BrokerDenied) as exc:
    fail(f"host broker unavailable: {exc}")
if broker_response is not None:
    sys.stdout.write(str(broker_response.get("stdout") or ""))
    sys.stderr.write(str(broker_response.get("stderr") or ""))
    try:
        raise SystemExit(int(broker_response.get("returncode", 2)))
    except (TypeError, ValueError):
        fail("host broker returned an invalid exit code")
if os.environ.get("ALTITUDE_L2_CAP_TEST_LOCAL") != "1":
    fail("generation-scoped host broker capability is missing")

root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
monitor = root / "monitor"
try:
    monitor.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    fail(f"monitor directory unavailable: {exc}")
key = str(os.environ.get("ALTITUDE_SESSION_KEY") or payload.get("session_id") or "")
if not key:
    fail("ALTITUDE_SESSION_KEY and session_id are both missing", monitor=monitor)

counts_path = monitor / f"counts-{key}.json"
envelope_path = monitor / f"envelope-{key}.json"
tool = str(payload.get("tool_name") or "")
tool_input = payload.get("tool_input") or {}
if not tool or not isinstance(tool_input, dict):
    fail("tool_name/tool_input missing or malformed", monitor=monitor, key=key)
tool_id = payload.get("tool_use_id")
if not tool_id:
    fail("tool_use_id is required for unambiguous billing", monitor=monitor, key=key)
dedupe_key = str(tool_id)
command = str(tool_input.get("command") or tool_input.get("cmd") or "")
launch_attempt = _is_l1_launch(command)
checkpoint_tool = _checkpoint_tool(root, tool, tool_input, command)


def persist(counts: dict) -> None:
    temp = counts_path.with_name(f"{counts_path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(counts))
    os.replace(temp, counts_path)


lock_file = None
try:
    lock_file = open(counts_path.with_name(counts_path.name + ".lock"), "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    try:
        envelope = json.loads(envelope_path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        fail(f"envelope file unreadable: {exc}", monitor=monitor, key=key)
    if not isinstance(envelope, dict):
        fail("envelope is not a JSON object", monitor=monitor, key=key)
    try:
        launch_cap = int(envelope["subagent_launches"])
        call_cap = int(envelope["max_turns"])
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"envelope caps missing or invalid: {exc}", monitor=monitor, key=key)
    if launch_cap < 0 or call_cap < 1:
        fail("envelope caps are out of range", monitor=monitor, key=key)

    try:
        counts = json.loads(counts_path.read_text()) if counts_path.exists() else {}
    except (OSError, ValueError, TypeError) as exc:
        fail(f"counts file unreadable: {exc}", monitor=monitor, key=key)
    if not isinstance(counts, dict):
        fail("counts file is not a JSON object", monitor=monitor, key=key)
    seen = counts.get("codex_tool_seen") or []
    if not isinstance(seen, list):
        fail("counts dedupe ledger is malformed", monitor=monitor, key=key)
    try:
        calls = int(counts.get("tool_calls", 0))
        launches = int(counts.get("subagent_launches", 0))
        edits = int(counts.get("edits", 0))
    except (TypeError, ValueError) as exc:
        fail(f"counts values are malformed: {exc}", monitor=monitor, key=key)

    stop_reason = counts.get("envelope_stop")
    if dedupe_key in seen:
        if stop_reason and not checkpoint_tool:
            print(f"altitude: envelope checkpoint-only mode ({stop_reason})", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(0)

    if stop_reason:
        if checkpoint_tool:
            raise SystemExit(0)
        print(f"altitude: envelope checkpoint-only mode ({stop_reason})", file=sys.stderr)
        raise SystemExit(2)

    if launch_attempt and launches >= launch_cap:
        counts["denied_launches"] = int(counts.get("denied_launches", 0)) + 1
        counts["envelope_stop"] = "L1 launch cap"
        counts["codex_tool_seen"] = (seen + [dedupe_key])[-500:]
        persist(counts)
        print(f"altitude: envelope reached ({launches}/{launch_cap} L1 launches); checkpoint, report, and stop", file=sys.stderr)
        raise SystemExit(2)

    if calls >= call_cap:
        counts["denied_tool_calls"] = int(counts.get("denied_tool_calls", 0)) + (0 if checkpoint_tool else 1)
        counts["envelope_stop"] = "tool-call cap"
        counts["codex_tool_seen"] = (seen + [dedupe_key])[-500:]
        persist(counts)
        if checkpoint_tool:
            raise SystemExit(0)
        print(f"altitude: Codex L2 tool-call envelope reached ({calls}/{call_cap}); checkpoint, report, and stop", file=sys.stderr)
        raise SystemExit(2)

    counts["tool_calls"] = calls + 1
    counts["subagent_launches"] = launches + (1 if launch_attempt else 0)
    counts["edits"] = edits
    if tool.lower() in ("apply_patch", "edit", "write", "multiedit"):
        counts["edits"] += 1
        files = set(counts.get("files") or [])
        path = tool_input.get("file_path") or tool_input.get("path")
        if path:
            files.add(str(path))
        counts["files"] = sorted(files)[:200]
    counts["codex_tool_seen"] = (seen + [dedupe_key])[-500:]
    persist(counts)
except OSError as exc:
    fail(f"hook lock/write failure: {exc}", monitor=monitor, key=key)
finally:
    if lock_file is not None:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()
