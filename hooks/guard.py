#!/usr/bin/env python3
"""PreToolUse guard for task-owned Bash sessions (decision 39).

Shared services, ports, and live Altitude state belong to altd, not a task.
Judge shell text that will be executed, without treating quoted report text or
heredoc payloads as commands.
"""
import json
import os
import re
import sys


def _after_arithmetic(line, start):
    """Return the first index after a $((...)) or ((...)) expression."""
    opening = 3 if line.startswith("$((", start) else 2
    depth = 2
    quote = None
    i = start + opening
    while i < len(line):
        char = line[i]
        if quote:
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"':
                i += 1
        elif char in "'\"":
            quote = char
        elif char == "\\":
            i += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(line)


def _heredocs_on_line(line, quote=None):
    """Return redirections and the open-quote state after a shell line."""
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
        if line.startswith("$((", i) or line.startswith("((", i):
            i = _after_arithmetic(line, i)
            continue
        if line.startswith("<<<", i):
            i += 3
            continue
        if line.startswith("<<", i):
            j = i + 2
            strips_tabs = j < len(line) and line[j] == "-"
            if strips_tabs:
                j += 1
            while j < len(line) and line[j] in " \t":
                j += 1
            if j >= len(line) or line[j] in "\r\n":
                i += 2
                continue
            if line[j] in "'\"":
                delimiter_quote = line[j]
                end = line.find(delimiter_quote, j + 1)
                if end < 0:
                    i += 2
                    continue
                delimiter = line[j + 1:end]
                j = end + 1
            else:
                end = j
                while end < len(line) and line[end] not in " \t\r\n;&|()<>":
                    end += 1
                delimiter = line[j:end]
                j = end
            if delimiter:
                found.append((delimiter, strips_tabs))
            i = j
            continue
        i += 1
    return found, quote


def _without_heredoc_bodies(command):
    """Keep command lines and discard each heredoc body and terminator."""
    kept = []
    pending = []
    quote = None
    for line in command.splitlines(keepends=True):
        if pending:
            delimiter, strips_tabs = pending[0]
            candidate = line.rstrip("\r\n")
            if strips_tabs:
                candidate = candidate.lstrip("\t")
            if candidate == delimiter:
                pending.pop(0)
            continue
        kept.append(line)
        found, quote = _heredocs_on_line(line, quote)
        pending.extend(found)
    return "".join(kept)


def _finish_word(words, current):
    if current:
        words.append("".join(current))
        current.clear()


def _command_word(words):
    """Locate a simple command verb, including common command wrappers."""
    i = 0
    while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i]):
        i += 1
    while i < len(words):
        word = words[i].rsplit("/", 1)[-1]
        if word in ("command", "exec", "nohup"):
            i += 1
            continue
        if word == "sudo":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
            continue
        if word == "env":
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i])):
                i += 1
            continue
        return i
    return None


def _is_code_argument(words, current):
    """Whether the word being scanned is evaluated as shell source text."""
    all_words = words + (["".join(current)] if current else [])
    word_index = len(words)
    command_index = _command_word(all_words)
    if command_index is None or command_index >= word_index:
        return False
    verb = all_words[command_index].rsplit("/", 1)[-1]
    if verb == "eval":
        return True
    if verb not in ("sh", "bash", "zsh"):
        return False
    for index in range(command_index + 1, word_index):
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", all_words[index]):
            return word_index == index + 1
    return False


def _quoted_end(command, start, quote):
    i = start + 1
    while i < len(command):
        if command[i] == quote:
            return i
        if command[i] == "\\" and quote == '"':
            i += 1
        i += 1
    return None


def _command_views(command, depth=0):
    """Return the bare (unquoted) and full (unquoted-content) command views."""
    bare = []
    full = []
    words = []
    current = []
    i = 0
    while i < len(command):
        char = command[i]
        if char in "'\"":
            end = _quoted_end(command, i, char)
            if end is None:
                # Malformed quoting must expose, rather than conceal, the tail.
                remainder = command[i + 1:]
                bare.append(remainder)
                full.append(remainder)
                current.append(remainder)
                break
            content = command[i + 1:end]
            is_code = _is_code_argument(words, current)
            if is_code:
                content = _without_heredoc_bodies(content)
            if depth < 100:
                nested_bare, nested_full = _command_views(content, depth + 1)
            else:
                # Excessive nesting also fails closed instead of hiding text.
                nested_bare = nested_full = content.replace("'", "").replace('"', "")
            full.append(nested_full)
            bare.append(nested_bare if is_code else " " * len(nested_full))
            # Retain word position without making quoted content an anchor.
            current.append("\0")
            i = end + 1
            continue
        if char == "\\" and i + 1 < len(command):
            escaped = command[i:i + 2]
            bare.append(escaped)
            full.append(escaped)
            current.append(escaped)
            i += 2
            continue
        bare.append(char)
        full.append(char)
        if char in " \t\r":
            _finish_word(words, current)
        elif char == "\n" or char in ";|&()":
            _finish_word(words, current)
            words.clear()
        elif char in "<>":
            _finish_word(words, current)
        else:
            current.append(char)
        i += 1
    return "".join(bare), "".join(full)


def _matched_fragment(match, limit=160):
    fragment = match.group(0).replace("\n", "\\n")
    if len(fragment) > limit:
        return fragment[:limit - 3] + "..."
    return fragment


inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
if inp.get("tool_name") != "Bash":
    sys.exit(0)
cmd = str((inp.get("tool_input") or {}).get("command") or "")
executed = _without_heredoc_bodies(cmd)
bare, full = _command_views(executed)
ports = os.environ.get("ALTITUDE_SERVICE_PORTS", "8890,8080,8443").replace(",", "|")
RULES = [
    (r"\bsystemctl\b", r"\bsystemctl\b.*\b(restart|stop|kill|disable|mask)\b.*\b(altitude|tutor|wg-quick)", "service units belong to altd/Burak — report 'needs restart' instead"),
    (r"\b(ufw|wg-quick|iptables|nft)\b", r"\b(ufw|wg-quick|iptables|nft)\b", "firewall / tunnel changes are never a task's"),
    (r"(:|--port[= ]|PORT=|port\s+)", r"(:|--port[= ]|PORT=|port\s+)(%s)\b" % ports, "service ports are taken — use ALTITUDE_TIMERS=0 on an ephemeral port for smoke tests"),
    (r"\brm\b", r"\brm\b.*(\.altitude|ALTITUDE_HOME)", "the Altitude home is live state"),
    (r"\bgit\s+push\b", r"\bgit\s+push\b.*(\s-f\b|--force)", "no force-pushes (never-list)"),
    (r"\bkill(all)?\b", r"\bkill(all)?\b.*\b(altd|altitude)\b", "altd is not yours to kill"),
]
for anchor, pattern, why in RULES:
    for match in re.finditer(pattern, full):
        if re.search(anchor, bare[match.start():match.end()]):
            fragment = _matched_fragment(match)
            print(f"altitude guard (decision 39): blocked — {why}. Matched: {fragment}", file=sys.stderr)
            sys.exit(2)
sys.exit(0)
