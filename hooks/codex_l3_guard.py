#!/usr/bin/env python3
"""Fail-closed Codex L3 tool allowlist.

The L3 may inspect evidence and mutate Altitude state only through the trusted
``alt`` CLI through a host broker. Its read-only sandbox is not permission to
edit files, access the network, or run a general-purpose shell.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path


READ_TOOLS = {"read", "read_file", "grep", "glob"}
SHELL_TOOLS = {"exec_command", "shell", "bash"}
WRITE_TOOLS = {"apply_patch", "edit", "write", "multiedit", "write_file", "create_file"}
READ_COMMANDS = {"cat", "head", "tail", "wc", "ls", "pwd", "stat", "readlink", "realpath", "grep", "rg", "jq"}
ALT_SINGLE = {"state", "agents", "decisions", "digest", "monitor", "backlog"}
ALT_TASK = {
    "new", "propose", "auto-approve", "reject", "park", "needs-user", "block",
    "unpark", "show", "events", "status", "resume", "size", "paths",
    "hold-merge", "done", "list",
}
ALT_INCIDENT = {"new", "amend", "list"}
ALT_RULE = {"propose", "list", "audit-input"}
GH_READ = {
    "pr": {"view", "list", "checks", "status"},
    "issue": {"view", "list", "status"},
    "run": {"view", "list", "watch"},
}
SEPARATORS = set(";&|<>(){}\n`")


def deny(message: str) -> None:
    print(f"altitude Codex L3 guard: blocked — {message}", file=sys.stderr)
    raise SystemExit(2)


def tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>(){}\n`")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = "#"
        words = list(lexer)
    except ValueError as exc:
        deny(f"malformed shell command: {exc}")
    if not words:
        deny("empty shell command")
    if any(word and all(char in SEPARATORS for char in word) for word in words):
        deny("shell composition, redirection, and substitution are not L3 tools")
    if any("$(" in word or "`" in word for word in words):
        deny("command substitution is not an L3 inspection tool")
    return words


def trusted_alt(word: str) -> bool:
    trusted = (Path(__file__).resolve().parent.parent / "bin" / "alt").resolve()
    if word == "alt":
        return True
    path = Path(word)
    return path.is_absolute() and path.resolve() == trusted


def allow_alt(words: list[str]) -> bool:
    if not trusted_alt(words[0]):
        return False
    args = words[1:]
    project = os.environ.get("ALTITUDE_PROJECT")
    if args[:1] in (["--project"], ["-p"]):
        if len(args) < 3 or not project or args[1] != project:
            return False
        args = args[2:]
    if not args:
        return False
    if args[0] in ALT_SINGLE:
        return len(args) == 1 or args[0] == "monitor"
    if args[0] == "fyi":
        return len(args) >= 2
    if args[0] == "task":
        return len(args) >= 2 and args[1] in ALT_TASK
    if args[0] == "incident":
        return len(args) >= 2 and args[1] in ALT_INCIDENT
    if args[0] == "rule":
        return len(args) >= 2 and args[1] in ALT_RULE
    return False


def trusted_system_binary(word: str, verb: str) -> bool:
    return word == verb or word in (f"/usr/bin/{verb}", f"/bin/{verb}")


def allow_read_command(words: list[str]) -> bool:
    verb = Path(words[0]).name
    if verb not in READ_COMMANDS or not trusted_system_binary(words[0], verb):
        return False
    if verb == "rg" and any(
        arg == "--pre" or arg.startswith("--pre=") or arg.startswith("--pre-glob")
        or arg == "--hostname-bin" or arg.startswith("--hostname-bin=")
        for arg in words[1:]
    ):
        return False
    if verb == "tail" and any(arg in ("-f", "--follow") or arg.startswith("--follow=") for arg in words[1:]):
        return False
    return True


def allow_git(words: list[str]) -> bool:
    if not trusted_system_binary(words[0], "git"):
        return False
    args = words[1:]
    while args[:1] == ["-C"]:
        if len(args) < 3:
            return False
        args = args[2:]
    if not args:
        return False
    dangerous = {"--ext-diff", "--textconv", "--no-index"}
    if any(arg in dangerous or arg == "--output" or arg.startswith("--output=") for arg in args):
        return False
    if args[0] == "log":
        return True
    if args[0] == "diff":
        return any(arg == "--stat" or arg.startswith("--stat=") for arg in args[1:])
    return False


def allow_gh(words: list[str]) -> bool:
    if not trusted_system_binary(words[0], "gh"):
        return False
    if len(words) < 3 or words[1] not in GH_READ or words[2] not in GH_READ[words[1]]:
        return False
    return "--web" not in words[3:]


def allow_shell(command: str) -> bool:
    words = tokens(command)
    if any(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word) for word in words[:1]):
        return False
    return allow_alt(words) or allow_read_command(words) or allow_git(words) or allow_gh(words)


def main() -> int:
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (OSError, TypeError, ValueError) as exc:
        deny(f"invalid hook input: {exc}")
    if not isinstance(payload, dict):
        deny("hook input is not an object")
    tool = str(payload.get("tool_name") or "").lower()
    tool_input = payload.get("tool_input") or {}
    if not tool or not isinstance(tool_input, dict):
        deny("tool_name/tool_input is missing or malformed")
    if tool in WRITE_TOOLS:
        deny("L3 cannot edit or patch files")
    if tool in READ_TOOLS:
        return 0
    if tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        if allow_shell(command):
            return 0
        deny("command is outside the L3 inspection/orchestration allowlist")
    deny(f"tool {tool!r} is not available to L3")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
