#!/usr/bin/env python3
"""PreToolUse guard for task-owned Bash sessions (decision 39).

Shared services, ports, and live Altitude state belong to altd, not a task.
Judge shell text that will be executed, without treating quoted report text or
heredoc payloads as commands.
"""
import json
import os
import re
import shlex
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


_SHELL_PUNCTUATION = ";&|(){}<>\n"
_SHELL_SEPARATORS = set(";&|\n")
_SHELLS = {"bash", "sh", "zsh"}
_CONTROL_PREFIXES = {"!", "coproc", "do", "elif", "if", "then", "until", "while"}
_GIT_CONFIG_ENV = re.compile(r"^GIT_CONFIG_[A-Z0-9_]*=")
_PROTECTED_BRANCHES = {"main", "master", "refs/heads/main", "refs/heads/master"}
_WRITERS = {
    "chmod", "chown", "cp", "dd", "install", "ln", "mv", "perl", "rm",
    "patch", "rsync", "sed", "shred", "sponge", "tee", "touch", "truncate",
    "unlink",
}
_INTERPRETERS = {"node", "perl", "python", "python3", "ruby"}
def _shell_tokens(source):
    """Tokenize shell source while retaining command separators and empty words."""
    lexer = shlex.shlex(source, posix=True, punctuation_chars=_SHELL_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    tokens = []
    for token in lexer:
        if token and all(char in _SHELL_PUNCTUATION for char in token):
            tokens.extend(token)
        else:
            tokens.append(token)
    return tokens


def _command_chunks(tokens):
    chunk = []
    for token in tokens:
        if token in _SHELL_SEPARATORS:
            if chunk:
                yield chunk
                chunk = []
        else:
            chunk.append(token)
    if chunk:
        yield chunk


def _command_at(words, start=0):
    """Resolve wrappers and assignments to an executed command word."""
    while start < len(words) and (words[start] in "(){}" or words[start] in _CONTROL_PREFIXES):
        start += 1
    relative = _command_word(words[start:])
    if relative is None:
        return None
    return start + relative


def _basename(word):
    return word.rsplit("/", 1)[-1]


def _config_assignment(words):
    return any(_GIT_CONFIG_ENV.match(word) for word in words)


def _sensitive_git_target(words):
    """Whether operands name Git metadata or the deployed tracked hooks."""
    joined = " ".join(words)
    if ".git/" in joined:
        return True
    if re.search(
        r"(?:^|[^A-Za-z0-9_.-])\.git(?:$|[\s/])|"
        r"(?:^|[^A-Za-z0-9_.-])\.git/(?:config|HEAD|packed-refs|hooks(?:/|$)|"
        r"refs/heads/(?:main|master)(?:$|[\s/]))",
        joined,
    ):
        return True
    if re.search(
        r"--git-common-dir[^;&|]*(?:/|\s)(?:config|HEAD|packed-refs|"
        r"refs/heads/(?:main|master))(?:$|[\s/)]|$)",
        joined,
    ):
        return True
    return bool(re.search(
        r"(?:^|[^A-Za-z0-9_.-])hooks(?:/|$)(?:$|[\s/'\"),]|[^\s]*)",
        joined,
    ))


def _expand_static_vars(word, variables):
    """Expand only variables whose value was visibly assigned in this source."""
    def replacement(match):
        name = match.group(1) or match.group(2)
        return variables.get(name, match.group(0))

    previous = None
    expanded = word
    for _ in range(20):
        if expanded == previous:
            break
        previous = expanded
        expanded = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
            replacement,
            expanded,
        )
    return expanded


def _expand_static_words(words, variables):
    """Fail closed by word-splitting visibly assigned multi-word expansions."""
    expanded_words = []
    for word in words:
        expanded = _expand_static_vars(word, variables)
        if expanded != word and re.search(r"\s", expanded):
            try:
                expanded_words.extend(_shell_tokens(expanded))
            except ValueError:
                expanded_words.append(expanded)
        else:
            expanded_words.append(expanded)
    return expanded_words


def _dynamic_shell_word(word):
    return bool(re.search(r"\$|`|\*|\?|\[|\]|\{|\}", word))


def _metadata_mutation(words, command_index, verb):
    """Distinguish metadata writes from harmless reads of protected files."""
    args = words[command_index + 1:]
    for i, token in enumerate(words[:-1]):
        if token in {">", ">>"} and _sensitive_git_target(words[i + 1:]):
            return True

    if verb in {"rm", "unlink", "shred", "mv", "chmod", "chown", "touch", "truncate", "patch", "sponge", "tee"}:
        return _sensitive_git_target(args)
    if verb in {"cp", "install", "ln", "rsync"}:
        for i, token in enumerate(args[:-1]):
            if token in {"-t", "--target-directory"} and _sensitive_git_target([args[i + 1]]):
                return True
            if token.startswith("--target-directory=") and _sensitive_git_target([token.split("=", 1)[1]]):
                return True
        operands = [word for word in args if not word.startswith("-")]
        return bool(operands) and _sensitive_git_target([operands[-1]])
    if verb == "dd":
        return any(token.startswith("of=") and _sensitive_git_target([token[3:]]) for token in args)
    if verb == "sed":
        writes = any(
            (token.startswith("-") and not token.startswith("--") and "i" in token[1:])
            or token.startswith("--in-place")
            for token in args
        )
        return writes and _sensitive_git_target(args)
    if verb == "perl":
        writes = any(
            token.startswith("-") and not token.startswith("--") and "i" in token[1:]
            for token in args
        )
        return writes and _sensitive_git_target(args)
    if verb in _INTERPRETERS and "-c" in args and _sensitive_git_target(args):
        code = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
        return bool(re.search(
            r"(?i)(?:\.write(?:_text|_bytes)?\s*\(|\.unlink\s*\(|\.remove\s*\(|"
            r"\.rename\s*\(|\.replace\s*\(|\.truncate\s*\(|\.chmod\s*\(|"
            r"\bopen\s*\([^)]*,\s*['\"][wax+]|\b(?:unlink|remove|rename|truncate|chmod|chown)\s*\()",
            code,
        ))
    return False


def _executable_substitutions(source):
    """Yield command/process substitutions, respecting shell quote semantics."""
    i = 0
    quote = None
    while i < len(source):
        if quote == "'":
            if source[i] == "'":
                quote = None
            i += 1
            continue
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == '"':
            quote = None if quote == '"' else '"'
            i += 1
            continue
        if quote is None and source[i] == "'":
            quote = "'"
            i += 1
            continue
        is_process_substitution = quote is None and source.startswith(("<(", ">("), i)
        if source.startswith("$(", i) or is_process_substitution:
            end = _command_substitution_end(source, i)
            if end is None:
                yield source[i + 2:]
                return
            yield source[i + 2:end]
            i = end + 1
            continue
        if source[i] == "`":
            end = _backtick_end(source, i)
            if end is None:
                yield source[i + 1:]
                return
            yield source[i + 1:end]
            i = end + 1
            continue
        i += 1


def _git_invocation(words, git_index, config_env_active):
    """Return a policy violation for one parsed Git invocation, if any."""
    args = words[git_index + 1:]
    override = False
    i = 0
    subcommand = None
    rest = []
    global_arg_options = {
        "-C", "--exec-path", "--git-dir", "--namespace", "--super-prefix",
        "--work-tree",
    }
    while i < len(args):
        token = args[i]
        if token in "(){}":
            i += 1
            continue
        if token == "-c" or token == "--config-env":
            override = True
            value = args[i + 1] if i + 1 < len(args) else ""
            if re.match(r"(?i)^core\.hookspath(?:=|$)", value):
                return "tracked Git hooks may not be overridden"
            if re.match(r"(?i)^(?:alias\.|include(?:if)?\.)", value):
                return "Git aliases and config includes may not be supplied as overrides"
            i += 2
            continue
        if token.startswith("-c=") or token.startswith("--config-env="):
            override = True
            value = token.split("=", 1)[1]
            if re.match(r"(?i)^core\.hookspath(?:=|$)", value):
                return "tracked Git hooks may not be overridden"
            if re.match(r"(?i)^(?:alias\.|include(?:if)?\.)", value):
                return "Git aliases and config includes may not be supplied as overrides"
            i += 1
            continue
        if token in global_arg_options:
            i += 2
            continue
        if any(token.startswith(option + "=") for option in global_arg_options if option.startswith("--")):
            i += 1
            continue
        if token.startswith("-C") and token != "-C":
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        subcommand = _basename(token)
        rest = args[i + 1:]
        break

    if subcommand is None:
        return None
    if config_env_active:
        return "Git config environment overrides may not accompany Git commands"
    if override and subcommand in {"push", "send-pack"}:
        return "Git config overrides may not accompany network writes"
    if subcommand in {"commit", "merge", "push"} and "--no-verify" in rest:
        return "Git verification hooks may not be skipped"
    if subcommand == "commit" and any(
        token.startswith("-") and not token.startswith("--") and "n" in token[1:]
        for token in rest
    ):
        return "Git verification hooks may not be skipped"
    if subcommand == "send-pack":
        return "git send-pack bypasses the protected-branch pre-push hook"
    if subcommand == "push":
        return _push_violation(rest)
    if subcommand == "config":
        return _config_violation(rest)
    if subcommand == "update-ref":
        if "--stdin" in rest:
            return "update-ref standard input cannot prove a non-protected destination"
        if any(_dynamic_shell_word(token) for token in rest if not token.startswith("-")):
            return "dynamic update-ref destinations cannot prove a non-protected ref"
        if any(token.lstrip("+") in _PROTECTED_BRANCHES for token in rest):
            return "protected local branches may move only to the fetched origin commit"
    return None


def _push_violation(args):
    """Validate that an explicit push can only name literal topic destinations."""
    positionals = []
    remote_from_option = False
    options_with_arguments = {
        "--exec", "--push-option", "--receive-pack", "--repo", "-o",
    }
    i = 0
    while i < len(args):
        token = args[i]
        if token in "(){}":
            i += 1
            continue
        if token.startswith("+"):
            return "no force-pushes (never-list)"
        if token in {"--force", "--force-if-includes", "--force-with-lease"} or token.startswith("--force-with-lease="):
            return "no force-pushes (never-list)"
        if token.startswith("-o") and token != "-o":
            i += 1
            continue
        if token.startswith("-") and not token.startswith("--") and "f" in token[1:]:
            return "no force-pushes (never-list)"
        if token in {"--all", "--mirror"}:
            return "bulk pushes cannot prove a topic-only destination"
        if token == "--no-verify":
            return "Git verification hooks may not be skipped"
        if token in options_with_arguments:
            if token == "--repo":
                remote_from_option = True
            i += 2
            continue
        if any(token.startswith(option + "=") for option in options_with_arguments if option.startswith("--")):
            if token.startswith("--repo="):
                remote_from_option = True
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        positionals.append(token)
        i += 1

    refspecs = positionals if remote_from_option else positionals[1:]
    for refspec in refspecs:
        destination = refspec.split(":", 1)[1] if ":" in refspec else refspec
        if any(char in destination for char in "$`*?[]{}"):
            return "dynamic push destinations cannot prove a topic-only ref"
        if destination in _PROTECTED_BRANCHES:
            return "protected branches move only through merged PRs"
    return None


def _config_violation(args):
    """Allow explicit inspection forms but reject Git config mutation."""
    mutation_flags = {
        "--add", "--edit", "--remove-section", "--rename-section",
        "--replace-all", "--unset", "--unset-all", "-e",
    }
    read_flags = {
        "--get", "--get-all", "--get-color", "--get-colorbool",
        "--get-regexp", "--get-urlmatch", "--list", "-l",
    }
    mutation_actions = {"remove-section", "rename-section", "set", "unset", "unset-all"}
    read_actions = {"get", "get-all", "get-color", "get-colorbool", "get-regexp", "get-urlmatch", "list"}
    if any(token in mutation_flags for token in args):
        return "agents may inspect but not mutate Git configuration"
    if any(token in read_flags for token in args):
        return None

    options_with_arguments = {"--file", "--type", "--blob", "-f", "-t"}
    positionals = []
    i = 0
    while i < len(args):
        token = args[i]
        if token in options_with_arguments:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        positionals.append(token)
        i += 1
    if not positionals:
        return None
    action = positionals[0]
    if action in read_actions:
        return None
    if action in mutation_actions:
        return "agents may inspect but not mutate Git configuration"
    if len(positionals) == 1 and "=" not in action:
        return None
    key = action.split("=", 1)[0]
    if re.match(r"(?i)^(?:core\.hookspath|alias\.|include(?:if)?\.)", key):
        return "agents may not install hook bypasses, aliases, or config includes"
    if "=" in action or len(positionals) > 1:
        return "agents may inspect but not mutate Git configuration"
    return None


def _chunk_violation(words, config_env_active, variables, depth):
    if depth > 30:
        return "excessively nested shell source cannot prove Git policy compliance"
    words = _expand_static_words(words, variables)
    start = 0
    while start < len(words) and (words[start] in "(){}" or words[start] in _CONTROL_PREFIXES):
        start += 1
    script_relative = _command_word(words[start:], stop_at=("script",))
    script_index = start + script_relative if script_relative is not None else None
    if script_index is not None and _basename(words[script_index]) == "script":
        for i, token in enumerate(words[script_index + 1:], script_index + 1):
            if token in {"-c", "--command"} and i + 1 < len(words):
                return _git_hardening_violation(words[i + 1], config_env_active, depth + 1)
            if token.startswith("--command="):
                return _git_hardening_violation(token.split("=", 1)[1], config_env_active, depth + 1)

    env_relative = _command_word(words[start:], stop_at=("env",))
    env_index = start + env_relative if env_relative is not None else None
    if env_index is not None and _basename(words[env_index]) == "env":
        for i, token in enumerate(words[env_index + 1:], env_index + 1):
            if token in {"-S", "--split-string"} and i + 1 < len(words):
                return _git_hardening_violation(words[i + 1], config_env_active, depth + 1)
            if token.startswith("--split-string="):
                return _git_hardening_violation(token.split("=", 1)[1], config_env_active, depth + 1)

    command_index = _command_at(words)
    if command_index is None:
        return None
    verb = _basename(words[command_index])

    for opener in ("{", "(", ")"):
        for i, token in enumerate(words[:-1]):
            if token == opener:
                violation = _tokens_violation(words[i + 1:], config_env_active, variables, depth + 1)
                if violation:
                    return violation

    if verb in _SHELLS:
        for i, token in enumerate(words[command_index + 1:], command_index + 1):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", token) and i + 1 < len(words):
                return _git_hardening_violation(words[i + 1], config_env_active, depth + 1)
        for i in range(command_index + 1, len(words) - 3):
            if words[i:i + 3] == ["<", "<", "<"]:
                return _git_hardening_violation(words[i + 3], config_env_active, depth + 1)
    if verb == "eval":
        return _git_hardening_violation(" ".join(words[command_index + 1:]), config_env_active, depth + 1)
    if verb == "alias":
        for definition in words[command_index + 1:]:
            if "=" in definition:
                violation = _git_hardening_violation(definition.split("=", 1)[1], config_env_active, depth + 1)
                if violation:
                    return "shell aliases may not define Git policy bypasses"

    if verb == "git":
        return _git_invocation(words, command_index, config_env_active or _config_assignment(words))
    if verb == "gh" and command_index + 1 < len(words) and words[command_index + 1] == "api":
        api = " ".join(words[command_index + 2:])
        if re.search(r"(?:^|/)merges(?:$|[/?\s])", api):
            return "the GitHub merges API bypasses the reviewed PR merge route"
        if re.search(r"/git/refs(?:$|[/?\s])|\b(?:createRef|deleteRef|updateRef)\b", api) and re.search(
            r"(?:\s-X(?:\s+)?(?:POST|PATCH|PUT|DELETE)\b|\s--method(?:=|\s)|(?:^|\s)-(?:f|F)\s|"
            r"(?:^|\s)--(?:field|raw-field)(?:=|\s))",
            " " + api,
            re.IGNORECASE,
        ):
            return "GitHub ref mutations must use topic pushes and reviewed PRs"

    if _metadata_mutation(words, command_index, verb):
        return "direct writes to protected Git metadata are blocked"
    return None


def _tokens_violation(tokens, config_env_active=False, inherited_variables=None, depth=0):
    active_names = {"<inherited>"} if config_env_active else set()
    variables = dict(inherited_variables or {})
    for words in _command_chunks(tokens):
        command_index = _command_at(words)
        verb = _basename(words[command_index]) if command_index is not None else ""
        assignments = {}
        for word in words:
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", word, re.DOTALL)
            if match:
                assignments[match.group(1)] = _expand_static_vars(match.group(2), variables)
        local_variables = {**variables, **assignments}
        local_config = set(active_names)
        local_config.update(name for name in assignments if name.startswith("GIT_CONFIG_"))

        if verb == "unset":
            for name in words[command_index + 1:]:
                variables.pop(name, None)
                active_names.discard(name)
            continue
        violation = _chunk_violation(words, bool(local_config), local_variables, depth)
        if violation:
            return violation
        if command_index is None or verb in {"export", "readonly"}:
            variables.update(assignments)
            active_names.update(name for name in assignments if name.startswith("GIT_CONFIG_"))
    return None


def _git_hardening_violation(source, config_env_active=False, depth=0):
    for substitution in _executable_substitutions(source):
        violation = _git_hardening_violation(substitution, config_env_active, depth + 1)
        if violation:
            return violation
    try:
        tokens = _shell_tokens(source)
    except ValueError:
        if re.search(r"\b(?:git|gh)\b", source):
            return "unparseable shell source cannot prove Git policy compliance"
        return None
    return _tokens_violation(tokens, config_env_active, None, depth)


inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
if inp.get("tool_name") != "Bash":
    sys.exit(0)
cmd = str((inp.get("tool_input") or {}).get("command") or "")
executed = _without_heredoc_bodies(cmd)
bare, full = _command_views(executed)
ports = os.environ.get("ALTITUDE_SERVICE_PORTS", "8890,8080,8443").replace(",", "|")
git_violation = _git_hardening_violation(executed)
if git_violation:
    fragment = executed.replace("\n", "\\n")
    if len(fragment) > 157:
        fragment = fragment[:157] + "..."
    print(
        f"altitude guard (decision 39): blocked — {git_violation}. Matched: {fragment}",
        file=sys.stderr,
    )
    sys.exit(2)

RULES = [
    (r"\bsystemctl\b", r"\bsystemctl\b.*\b(restart|stop|kill|disable|mask)\b.*\b(altitude|tutor|wg-quick)", "service units belong to altd/Burak — report 'needs restart' instead"),
    (r"\b(ufw|wg-quick|iptables|nft)\b", r"\b(ufw|wg-quick|iptables|nft)\b", "firewall / tunnel changes are never a task's"),
    (r"(:|--port[= ]|PORT=|port\s+)", r"(:|--port[= ]|PORT=|port\s+)(%s)\b" % ports, "service ports are taken — use ALTITUDE_TIMERS=0 on an ephemeral port for smoke tests"),
    (r"\brm\b", r"\brm\b.*(\.altitude|ALTITUDE_HOME)", "the Altitude home is live state"),
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
