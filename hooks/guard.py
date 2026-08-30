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
    """Discard data heredocs, but retain source fed to a shell interpreter."""
    kept = []
    pending = []
    quote = None
    for line in command.splitlines(keepends=True):
        if pending:
            delimiter, strips_tabs, is_shell, body = pending[0]
            candidate = line.rstrip("\r\n")
            if strips_tabs:
                candidate = candidate.lstrip("\t")
            if candidate == delimiter:
                pending.pop(0)
                if is_shell:
                    kept.append(_without_heredoc_bodies("".join(body)))
            elif is_shell:
                body.append(line)
            continue
        kept.append(line)
        found, quote = _heredocs_on_line(line, quote)
        is_shell = _line_runs_shell(line)
        pending.extend((delimiter, strips_tabs, is_shell, []) for delimiter, strips_tabs in found)
    for _delimiter, _strips_tabs, is_shell, body in pending:
        if is_shell:
            kept.append(_without_heredoc_bodies("".join(body)))
    return "".join(kept)


def _finish_word(words, current):
    if current:
        words.append("".join(current))
        current.clear()


_WRAPPER_ARGUMENT_OPTIONS = {
    "command": set(),
    "exec": {"-a"},
    "nohup": set(),
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-p", "--prompt"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "timeout": {"-k", "--kill-after", "-s", "--signal"},
    "time": {"-f", "--format", "-o", "--output"},
    "xargs": {
        "-a", "--arg-file", "-d", "--delimiter", "-E", "-e", "--eof", "-I", "-i",
        "--replace", "-L", "-l", "--max-lines", "-n", "--max-args", "-P", "--max-procs",
        "-s", "--max-chars",
    },
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "setsid": set(),
    "script": {"-c", "--command", "-E", "--echo", "-I", "--log-in", "-O", "--log-out", "-B", "--log-io", "-T", "--log-timing", "-m", "--logging-format"},
}


def _skip_wrapper_options(words, index, options_with_arguments):
    while index < len(words) and words[index].startswith("-"):
        option = words[index]
        index += 1
        if option == "--":
            break
        name = option.split("=", 1)[0]
        short_name = option[:2] if option.startswith("-") and not option.startswith("--") else name
        takes_argument = name in options_with_arguments or short_name in options_with_arguments
        has_attached_argument = "=" in option or (short_name in options_with_arguments and len(option) > 2)
        if takes_argument and not has_attached_argument and index < len(words):
            index += 1
    return index


def _command_word(words, stop_at=()):
    """Locate a simple command verb, including common command wrappers."""
    i = 0
    while i < len(words):
        while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i]):
            i += 1
        if i >= len(words):
            return None
        word = words[i].rsplit("/", 1)[-1]
        if word in stop_at:
            return i
        if word in _WRAPPER_ARGUMENT_OPTIONS:
            i += 1
            i = _skip_wrapper_options(words, i, _WRAPPER_ARGUMENT_OPTIONS[word])
            if word == "timeout" and i < len(words):
                i += 1
            continue
        return i
    return None


def _words_before_heredoc(line):
    """Tokenize the simple command before a heredoc redirection."""
    words = []
    current = []
    quote = None
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"' and i + 1 < len(line):
                i += 1
                current.append(line[i])
            else:
                current.append(char)
            i += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "\\" and i + 1 < len(line):
            i += 1
            current.append(line[i])
        elif char == "#" and (i == 0 or line[i - 1] in " \t;&|()"):
            break
        elif line.startswith("<<", i):
            break
        elif char in " \t\r\n":
            _finish_word(words, current)
        elif char in ";|&()":
            _finish_word(words, current)
            words.clear()
        elif char in "<>":
            _finish_word(words, current)
        else:
            current.append(char)
        i += 1
    _finish_word(words, current)
    return words


def _line_runs_shell(line):
    words = _words_before_heredoc(line)
    command_index = _command_word(words)
    if command_index is None:
        return False
    return words[command_index].rsplit("/", 1)[-1] in ("sh", "bash", "zsh")


def _is_code_argument(words, current):
    """Whether the word being scanned is evaluated as shell source text."""
    all_words = words + (["".join(current)] if current else [])
    word_index = len(words)
    script_index = _command_word(all_words, stop_at=("script",))
    if script_index is not None and all_words[script_index].rsplit("/", 1)[-1] == "script":
        if word_index > script_index and all_words[word_index - 1] in ("-c", "--command"):
            return True
        if current and "".join(current) == "--command=":
            return True
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


def _backtick_end(command, start):
    i = start + 1
    while i < len(command):
        if command[i] == "`":
            return i
        if command[i] == "\\":
            i += 1
        i += 1
    return None


def _command_substitution_end(command, start):
    depth = 1
    quote = None
    i = start + 2
    while i < len(command):
        char = command[i]
        if quote:
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"':
                i += 1
        elif char in "'\"":
            quote = char
        elif char == "\\":
            i += 1
        elif char == "`":
            end = _backtick_end(command, i)
            if end is None:
                return None
            i = end
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _quoted_end(command, start, quote):
    i = start + 1
    while i < len(command):
        if command[i] == quote:
            return i
        if command[i] == "\\" and quote == '"':
            i += 1
        elif quote == '"' and command.startswith("$(", i):
            end = _command_substitution_end(command, i)
            if end is None:
                return None
            i = end
        elif quote == '"' and command[i] == "`":
            end = _backtick_end(command, i)
            if end is None:
                return None
            i = end
        i += 1
    return None


def _double_quoted_views(content, depth):
    """Expose executable substitutions while blanking double-quoted prose."""
    bare = []
    full = []
    i = 0
    while i < len(content):
        if content[i] == "\\" and i + 1 < len(content):
            escaped = content[i:i + 2]
            full.append(escaped)
            bare.append(" " * len(escaped))
            i += 2
            continue
        if content.startswith("$(", i):
            end = _command_substitution_end(content, i)
            inner_end = end if end is not None else len(content)
            inner = _without_heredoc_bodies(content[i + 2:inner_end])
            if depth < 100:
                nested_bare, nested_full = _command_views(inner, depth + 1)
            else:
                nested_bare = nested_full = inner.replace("'", "").replace('"', "")
            full.append("$(" + nested_full)
            bare.append("  " + nested_bare)
            if end is not None:
                full.append(")")
                bare.append(" ")
                i = end + 1
            else:
                i = len(content)
            continue
        if content[i] == "`":
            end = _backtick_end(content, i)
            inner_end = end if end is not None else len(content)
            inner = _without_heredoc_bodies(content[i + 1:inner_end])
            if depth < 100:
                nested_bare, nested_full = _command_views(inner, depth + 1)
            else:
                nested_bare = nested_full = inner.replace("'", "").replace('"', "")
            full.append("`" + nested_full)
            bare.append(" " + nested_bare)
            if end is not None:
                full.append("`")
                bare.append(" ")
                i = end + 1
            else:
                i = len(content)
            continue
        full.append(content[i])
        bare.append(" ")
        i += 1
    return "".join(bare), "".join(full)


def _command_views(command, depth=0):
    """Return the bare (unquoted) and full (unquoted-content) command views."""
    bare = []
    full = []
    words = []
    current = []
    here_string_word = None
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
            word_index = len(words)
            is_code = _is_code_argument(words, current) or here_string_word == word_index
            if is_code:
                content = _without_heredoc_bodies(content)
            if char == '"' and not is_code:
                nested_bare, nested_full = _double_quoted_views(content, depth)
            elif depth < 100:
                nested_bare, nested_full = _command_views(content, depth + 1)
            else:
                # Excessive nesting also fails closed instead of hiding text.
                nested_bare = nested_full = content.replace("'", "").replace('"', "")
            full.append(nested_full)
            possible_words = words + ["".join(current) + nested_full]
            is_command = _command_word(possible_words) == word_index
            if is_code or (char == '"' and not is_command):
                bare.append(nested_bare)
            elif is_command:
                bare.append(nested_full)
            else:
                bare.append(" " * len(nested_full))
            current.append(nested_full)
            i = end + 1
            continue
        if char == "\\" and i + 1 < len(command):
            escaped = command[i:i + 2]
            bare.append(escaped)
            full.append(escaped)
            current.append(escaped)
            i += 2
            continue
        if command.startswith("<<<", i):
            _finish_word(words, current)
            command_index = _command_word(words)
            if command_index is not None and words[command_index].rsplit("/", 1)[-1] in ("sh", "bash", "zsh"):
                here_string_word = len(words)
            bare.append("<<<")
            full.append("<<<")
            i += 3
            continue
        bare.append(char)
        full.append(char)
        if char in " \t\r":
            _finish_word(words, current)
        elif char == "\n" or char in ";|&()":
            _finish_word(words, current)
            words.clear()
            here_string_word = None
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
    if not re.search(anchor, bare):
        continue
    for match in re.finditer(pattern, full):
        if re.search(anchor, bare[match.start():match.end()]):
            fragment = _matched_fragment(match)
            print(f"altitude guard (decision 39): blocked — {why}. Matched: {fragment}", file=sys.stderr)
            sys.exit(2)
sys.exit(0)
