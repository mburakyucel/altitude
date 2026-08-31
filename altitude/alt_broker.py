"""Capability broker for sandboxed model coordinators.

The engine receives one pre-created per-generation Unix socket and bearer
token, never a writable broker directory or Altitude state root. This module intentionally has no Altitude state
imports: the trusted wrapper supplies generation validation, checkpoint, and
post-call callbacks.
"""
from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


BROKER_PREFIX = "ALTITUDE_ALT_BROKER_"
SOCKET_ENV = BROKER_PREFIX + "SOCKET"
FD_ENV = BROKER_PREFIX + "FD"
LOCK_FD_ENV = BROKER_PREFIX + "LOCK_FD"
_FD_REQUEST_LOCK = threading.Lock()
TOKEN_ENV = BROKER_PREFIX + "TOKEN"
PROJECT_ENV = BROKER_PREFIX + "PROJECT"
TASK_ENV = BROKER_PREFIX + "TASK"
GENERATION_ENV = BROKER_PREFIX + "GENERATION"
MAX_MESSAGE = 2 * 1024 * 1024
MAX_STDIN = 1024 * 1024
MAX_CHECKPOINT = 1024 * 1024
CHECKPOINTS = {"progress.md", "report.md", "report.json"}


class BrokerDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerAction:
    kind: str
    argv: tuple[str, ...] = ()
    name: str | None = None
    source: str | None = None
    files: tuple[tuple[str, str], ...] = ()


def _read_exact_socket(connection: socket.socket, size: int) -> bytes:
    chunks = []
    left = size
    while left:
        chunk = connection.recv(left)
        if not chunk:
            raise BrokerDenied("truncated broker request")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def _recv_socket(connection: socket.socket) -> dict:
    size = struct.unpack("!I", _read_exact_socket(connection, 4))[0]
    if size < 2 or size > MAX_MESSAGE:
        raise BrokerDenied("broker message size is invalid")
    try:
        value = json.loads(_read_exact_socket(connection, size))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise BrokerDenied(f"invalid broker JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BrokerDenied("broker request is not an object")
    return value


def _send_socket(connection: socket.socket, value: dict) -> None:
    raw = json.dumps(value, sort_keys=True).encode()
    if len(raw) > MAX_MESSAGE:
        raw = json.dumps({"returncode": 2, "stdout": "", "stderr": "alt broker: response too large\n"}).encode()
    connection.sendall(struct.pack("!I", len(raw)) + raw)


def _read_exact_fd(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    left = size
    while left:
        chunk = os.read(descriptor, left)
        if not chunk:
            raise BrokerDenied("truncated inherited broker response")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def _recv_fd(descriptor: int) -> dict:
    size = struct.unpack("!I", _read_exact_fd(descriptor, 4))[0]
    if size < 2 or size > MAX_MESSAGE:
        raise BrokerDenied("inherited broker response size is invalid")
    try:
        value = json.loads(_read_exact_fd(descriptor, size))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise BrokerDenied(f"invalid inherited broker JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BrokerDenied("inherited broker response is not an object")
    return value


def _send_fd(descriptor: int, value: dict) -> None:
    raw = json.dumps(value, sort_keys=True).encode()
    if len(raw) > MAX_MESSAGE:
        raise BrokerDenied("inherited broker request is too large")
    framed = struct.pack("!I", len(raw)) + raw
    written = 0
    while written < len(framed):
        count = os.write(descriptor, framed[written:])
        if count <= 0:
            raise BrokerDenied("inherited broker request write was truncated")
        written += count


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _option_values(args: list[str], allowed: dict[str, bool]) -> dict[str, list[str | bool]]:
    """Parse only long/short options named by policy; reject positional surprises."""
    values: dict[str, list[str | bool]] = {}
    i = 0
    while i < len(args):
        option = args[i]
        if option not in allowed:
            raise BrokerDenied(f"option or operand {option!r} is not brokered")
        takes_value = allowed[option]
        if takes_value:
            if i + 1 >= len(args):
                raise BrokerDenied(f"{option} requires a value")
            values.setdefault(option, []).append(args[i + 1])
            i += 2
        else:
            values.setdefault(option, []).append(True)
            i += 1
    return values


def _regular_worktree_file(value: str, worktree: Path, *, limit: int = MAX_CHECKPOINT) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = worktree / path
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as exc:
        raise BrokerDenied(f"source file is unavailable: {exc}") from exc
    if not _inside(resolved, worktree) or not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise BrokerDenied("source must be a regular, non-symlink file inside the exact task worktree")
    if info.st_size > limit:
        raise BrokerDenied(f"source exceeds the {limit}-byte broker limit")
    return path


def _read_regular_worktree_file(value: str, worktree: Path, *, limit: int = MAX_CHECKPOINT) -> bytes:
    """Open once with O_NOFOLLOW, then verify the opened inode and exact containment."""
    path = _regular_worktree_file(value, worktree, limit=limit)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerDenied(f"source file cannot be opened safely: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise BrokerDenied("opened source is not a bounded regular file")
        opened = Path(f"/proc/self/fd/{descriptor}")
        if opened.exists() and not _inside(opened.resolve(strict=True), worktree.resolve()):
            raise BrokerDenied("opened source escaped the exact task worktree")
        data = b""
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not chunk:
                return data
            data += chunk
        raise BrokerDenied(f"source exceeds the {limit}-byte broker limit")
    finally:
        os.close(descriptor)





def l2_policy(argv: list[str], *, project: str, slug: str, worktree: Path) -> BrokerAction:
    """Strict current-task L2 CLI surface. The trusted CLI validates semantics again."""
    if not argv or argv[0] in ("--project", "-p"):
        raise BrokerDenied("project selection is fixed by the broker capability")
    if argv[:2] == ["task", "status"]:
        if len(argv) not in (2, 3) or (len(argv) == 3 and argv[2] != slug):
            raise BrokerDenied("task status is restricted to the current task")
        return BrokerAction("cli", tuple(argv))
    if argv[:2] == ["task", "checkpoint"]:
        if len(argv) != 7 or argv[2] != slug:
            raise BrokerDenied("checkpoint is restricted to the current task")
        opts = _option_values(argv[3:], {"--name": True, "--source": True})
        if set(opts) != {"--name", "--source"} or len(opts["--name"]) != 1 or len(opts["--source"]) != 1:
            raise BrokerDenied("checkpoint requires one --name and one --source")
        name, source = str(opts["--name"][0]), str(opts["--source"][0])
        if name not in CHECKPOINTS:
            raise BrokerDenied("checkpoint name is not progress.md, report.md, or report.json")
        _regular_worktree_file(source, worktree)
        return BrokerAction("checkpoint", name=name, source=source)
    if len(argv) >= 3 and argv[:2] == ["task", "block"]:
        if argv[2] != slug:
            raise BrokerDenied("task transition is restricted to the current task")
        opts = _option_values(argv[3:], {"--reason": True})
        if set(opts) != {"--reason"} or len(opts["--reason"]) != 1:
            raise BrokerDenied("task transition requires exactly one --reason")
        return BrokerAction("cli", tuple(argv))
    if argv[:2] == ["task", "request-merge"]:
        if len(argv) != 5 or argv[2] != slug:
            raise BrokerDenied("merge requests are restricted to the current task")
        opts = _option_values(argv[3:], {"--pr": True})
        if set(opts) != {"--pr"} or len(opts["--pr"]) != 1:
            raise BrokerDenied("request-merge requires exactly one --pr")
        try:
            number = int(str(opts["--pr"][0]))
        except ValueError as exc:
            raise BrokerDenied("request-merge PR is not an integer") from exc
        if number < 1:
            raise BrokerDenied("request-merge PR must be positive")
        return BrokerAction("cli", tuple(argv))
    if argv[:2] == ["l1", "status"]:
        if len(argv) != 2:
            raise BrokerDenied("l1 status takes no brokered operands")
        return BrokerAction("cli", tuple(argv))
    if argv[:2] == ["l1", "wait"]:
        rest = argv[2:]
        name = None
        if rest and not rest[0].startswith("-"):
            name, rest = rest[0], rest[1:]
        opts = _option_values(rest, {"--timeout": True})
        if "--timeout" in opts:
            try:
                timeout = int(str(opts["--timeout"][0]))
            except ValueError as exc:
                raise BrokerDenied("l1 wait timeout is not an integer") from exc
            if timeout < 1 or timeout > 540:
                raise BrokerDenied("l1 wait timeout must be between 1 and 540 seconds")
        if name is not None and (not name or "/" in name or ".." in name):
            raise BrokerDenied("l1 run name is invalid")
        return BrokerAction("cli", tuple(argv))
    if argv[:2] == ["l1", "run"]:
        opts = _option_values(argv[2:], {
            "--brief": True, "--role": True, "--engine": True, "--model": True,
            "--name": True, "--cwd": True, "--pr": True,
        })
        if "--brief" not in opts or len(opts["--brief"]) != 1:
            raise BrokerDenied("l1 run requires exactly one --brief")
        brief = str(opts["--brief"][0])
        _regular_worktree_file(brief, worktree)
        for singular in ("--role", "--engine", "--model", "--name", "--cwd", "--pr"):
            if len(opts.get(singular, [])) > 1:
                raise BrokerDenied(f"{singular} may be specified only once")
        return BrokerAction("cli", tuple(argv), files=(("--brief", brief),))
    if argv[:1] == ["verify"]:
        if argv != ["verify", slug]:
            raise BrokerDenied("verify is restricted to the current task")
        return BrokerAction("cli", tuple(argv))
    if argv[:1] == ["land"]:
        if "--merge" in argv:
            raise BrokerDenied(
                "model workers may publish a PR but may not merge it; use `alt task request-merge <slug> --pr <n>`"
            )
        opts = _option_values(argv[1:], {
            "--message": True, "--pr-title": True, "--pr-body-file": True,
            "--wait": True, "--base": True,
            "--dry-run": False,
        })
        if "--message" not in opts or len(opts["--message"]) != 1:
            raise BrokerDenied("land requires exactly one --message")
        for singular in ("--pr-title", "--pr-body-file", "--wait", "--base"):
            if len(opts.get(singular, [])) > 1:
                raise BrokerDenied(f"{singular} may be specified only once")
        if "--base" in opts and opts["--base"] != ["main"]:
            raise BrokerDenied("the brokered land base is fixed to main")
        if "--pr-body-file" in opts:
            body = str(opts["--pr-body-file"][0])
            _regular_worktree_file(body, worktree)
            return BrokerAction("cli", tuple(argv), files=(("--pr-body-file", body),))
        return BrokerAction("cli", tuple(argv))
    raise BrokerDenied("command is outside the current-task L2 broker allowlist")

L3_SINGLE = {"state", "agents", "decisions", "digest", "monitor", "backlog"}
L3_TASK = {
    "new", "propose", "auto-approve", "reject", "park", "needs-user", "block", "unpark",
    "show", "events", "status", "resume", "size", "paths", "hold-merge", "list",
}
L3_INCIDENT = {"new", "amend", "list"}
L3_RULE = {"propose", "list", "audit-input"}

_BROKER_IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_BROKER_RECORD_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")


def _l3_identifier(value: str, label: str, *, max_length: int = 80) -> str:
    if (not isinstance(value, str) or not value or len(value) > max_length
            or _BROKER_IDENTIFIER.fullmatch(value) is None):
        raise BrokerDenied(f"{label} is not a canonical Altitude identifier")
    return value


def _l3_record_id(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) > 80 or _BROKER_RECORD_ID.fullmatch(value) is None:
        raise BrokerDenied(f"{label} is not a safe record identifier")
    return value


def _l3_parse(args: list[str], allowed: dict[str, bool], *, min_pos: int, max_pos: int,
              required: tuple[str, ...] = (), repeatable: tuple[str, ...] = (),
              allow_dash: bool = False) -> tuple[list[str], dict[str, list[str | bool]]]:
    """Parse one brokered CLI verb without delegating operand meaning to argparse."""
    positionals: list[str] = []
    options: dict[str, list[str | bool]] = {}
    index = 0
    while index < len(args):
        token = args[index]
        if allow_dash and token == "-":
            positionals.append(token)
            index += 1
            continue
        if token.startswith("-"):
            if token not in allowed:
                raise BrokerDenied(f"option {token!r} is not brokered for this command")
            takes_value = allowed[token]
            if takes_value:
                if index + 1 >= len(args):
                    raise BrokerDenied(f"{token} requires a value")
                options.setdefault(token, []).append(args[index + 1])
                index += 2
            else:
                options.setdefault(token, []).append(True)
                index += 1
            continue
        positionals.append(token)
        index += 1
    if not min_pos <= len(positionals) <= max_pos:
        raise BrokerDenied(f"command requires {min_pos}-{max_pos} positional operands")
    missing = [name for name in required if name not in options]
    if missing:
        raise BrokerDenied(f"command requires {', '.join(missing)}")
    for name, values in options.items():
        if name not in repeatable and len(values) != 1:
            raise BrokerDenied(f"{name} may be specified only once")
    return positionals, options


def _l3_task(argv: list[str]) -> None:
    verb, args = argv[1], argv[2:]
    if verb == "new":
        pos, opts = _l3_parse(
            args,
            {"--title": True, "--engine": True, "--hold-merge": True, "--class": True,
             "--source": True, "--model": True, "--paths": True},
            min_pos=0, max_pos=1, required=("--title",), allow_dash=True,
        )
        if pos and pos[0] == "-" and len(pos) != 1:
            raise BrokerDenied("stdin marker must be the only task request operand")
        if "--engine" in opts and opts["--engine"][0] not in ("claude", "codex"):
            raise BrokerDenied("task engine is invalid")
        if "--class" in opts and opts["--class"][0] not in ("auto", "S", "M", "L"):
            raise BrokerDenied("task class is invalid")
        if "--model" in opts and opts["--model"][0] not in ("opus", "sonnet", "haiku", "fable"):
            raise BrokerDenied("task model is invalid")
        return
    if verb == "propose":
        pos, opts = _l3_parse(
            args,
            {"--existing": False, "--question": True, "--context": True, "--detail": True, "--option": True},
            min_pos=1, max_pos=2, repeatable=("--option",),
        )
        _l3_identifier(pos[0], "task slug")
        if "--existing" in opts and len(pos) != 1:
            raise BrokerDenied("--existing cannot be combined with engine-supplied proposal text")
        return
    if verb in ("auto-approve", "reject", "park", "needs-user"):
        pos, _ = _l3_parse(args, {"--reason": True}, min_pos=1, max_pos=1, required=("--reason",))
        _l3_identifier(pos[0], "task slug")
        return
    if verb == "block":
        pos, opts = _l3_parse(
            args, {"--reason": True, "--recovery-batch": True},
            min_pos=1, max_pos=1, required=("--reason",),
        )
        _l3_identifier(pos[0], "task slug")
        if "--recovery-batch" in opts:
            _l3_record_id(str(opts["--recovery-batch"][0]), "recovery batch")
        return
    if verb in ("unpark", "show", "events"):
        pos, _ = _l3_parse(args, {}, min_pos=1, max_pos=1)
        _l3_identifier(pos[0], "task slug")
        return
    if verb == "status":
        pos, _ = _l3_parse(args, {}, min_pos=0, max_pos=1)
        if pos:
            _l3_identifier(pos[0], "task slug")
        return
    if verb == "resume":
        pos, opts = _l3_parse(
            args, {"--launches": True, "--turns": True, "--answer": True}, min_pos=1, max_pos=1,
        )
        _l3_identifier(pos[0], "task slug")
        for name in ("--launches", "--turns"):
            if name in opts and not str(opts[name][0]).isdigit():
                raise BrokerDenied(f"{name} must be a non-negative integer")
        return
    if verb == "size":
        pos, _ = _l3_parse(
            args, {"--why": True, "--paths": True}, min_pos=2, max_pos=2, required=("--why",),
        )
        _l3_identifier(pos[0], "task slug")
        if pos[1] not in ("S", "M", "L"):
            raise BrokerDenied("task class is invalid")
        return
    if verb == "paths":
        pos, _ = _l3_parse(args, {}, min_pos=2, max_pos=2)
        _l3_identifier(pos[0], "task slug")
        return
    if verb == "hold-merge":
        pos, _ = _l3_parse(args, {"--why": True, "--off": False}, min_pos=1, max_pos=1)
        _l3_identifier(pos[0], "task slug")
        return
    if verb == "list":
        _l3_parse(args, {"--all": False}, min_pos=0, max_pos=0)
        return
    raise BrokerDenied("task command is outside the L3 broker allowlist")


def _l3_incident(argv: list[str]) -> None:
    verb, args = argv[1], argv[2:]
    if verb == "list":
        _l3_parse(args, {}, min_pos=0, max_pos=0)
        return
    if verb == "new":
        _, opts = _l3_parse(
            args,
            {"--title": True, "--task": True, "--what": True, "--evidence": True, "--cause": True,
             "--tag": True, "--generalizable": True, "--mechanism": True, "--scope": True},
            min_pos=0, max_pos=0, required=("--title", "--what", "--evidence", "--cause"),
            repeatable=("--tag",),
        )
        if "--task" in opts:
            _l3_identifier(str(opts["--task"][0]), "task slug")
        return
    if verb == "amend":
        pos, _ = _l3_parse(
            args,
            {"--what": True, "--evidence": True, "--cause": True, "--status": True, "--reason": True},
            min_pos=1, max_pos=1, required=("--reason",),
        )
        _l3_record_id(pos[0], "incident id")
        return
    raise BrokerDenied("incident command is outside the L3 broker allowlist")


def _l3_rule(argv: list[str]) -> None:
    verb, args = argv[1], argv[2:]
    if verb in ("list", "audit-input"):
        _l3_parse(args, {}, min_pos=0, max_pos=0)
        return
    if verb == "propose":
        _, opts = _l3_parse(
            args,
            {"--incident": True, "--title": True, "--text": True, "--mechanism": True,
             "--scope": True, "--stack": True, "--where": True, "--prevents": True, "--effect": True},
            min_pos=0, max_pos=0, required=("--incident", "--title", "--text"),
        )
        _l3_record_id(str(opts["--incident"][0]), "incident id")
        return
    raise BrokerDenied("rule command is outside the L3 broker allowlist")


def l3_policy(argv: list[str], *, project: str, slug: str, worktree: Path) -> BrokerAction:
    """Strict current-project L3 surface; file reads and engine-launch commands stay outside it."""
    if not argv:
        raise BrokerDenied("brokered L3 command is empty")
    if argv[0] in ("--project", "-p"):
        if len(argv) < 3 or argv[1] != project:
            raise BrokerDenied("project selection does not match the L3 broker capability")
        argv = argv[2:]
    if "--file" in argv or any(arg.startswith("--file=") for arg in argv):
        raise BrokerDenied("brokered L3 commands cannot make the host read an engine-chosen file")
    if argv[0] in L3_SINGLE:
        if len(argv) != 1:
            raise BrokerDenied(f"{argv[0]} takes no brokered operands")
        return BrokerAction("cli", tuple(argv))
    if argv[0] == "fyi":
        pos, _ = _l3_parse(argv[1:], {}, min_pos=1, max_pos=2)
        if len(pos) == 2:
            _l3_identifier(pos[0], "task slug")
        return BrokerAction("cli", tuple(argv))
    if len(argv) >= 2 and argv[0] == "task" and argv[1] in L3_TASK:
        _l3_task(argv)
        return BrokerAction("cli", tuple(argv))
    if len(argv) >= 2 and argv[0] == "incident" and argv[1] in L3_INCIDENT:
        _l3_incident(argv)
        return BrokerAction("cli", tuple(argv))
    if len(argv) >= 2 and argv[0] == "rule" and argv[1] in L3_RULE:
        _l3_rule(argv)
        return BrokerAction("cli", tuple(argv))
    raise BrokerDenied("command is outside the current-project L3 broker allowlist")



class AltBroker:
    """One serialized, generation-fenced host broker."""

    def __init__(self, *, socket_path: Path, token: str, project: str, slug: str, generation: str,
                 worktree: Path, trusted_alt: Path, policy: Callable[..., BrokerAction],
                 validate_generation: Callable[[], bool], checkpoint: Callable[[str, bytes], dict] | None = None,
                 validate_cwd: Callable[[Path], bool] | None = None,
                 hook: Callable[[dict], dict] | None = None,
                 before_cli: Callable[[list[str], Path], dict[str, str] | None] | None = None,
                 after_cli: Callable[[list[str], int], None] | None = None, timeout: int = 1200,
                 actor: str = "l2", host_env: dict[str, str] | None = None):
        self.socket_path = socket_path
        self.token = token
        self.project = project
        self.slug = slug
        self.generation = generation
        self.worktree = worktree.resolve()
        self.trusted_alt = trusted_alt.resolve()
        self.policy = policy
        self.validate_generation = validate_generation
        self.checkpoint = checkpoint
        self.validate_cwd = validate_cwd or (lambda candidate: candidate == self.worktree)
        self.hook = hook
        self.before_cli = before_cli
        self.after_cli = after_cli
        self.timeout = timeout
        self.actor = actor
        self.host_env = dict(host_env or {})
        self._listener: socket.socket | None = None
        self._inherited_connection: socket.socket | None = None
        self._inherited_lock_fd: int | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()

    def env(self) -> dict[str, str]:
        """Path transport for Claude's restricted sandbox."""
        return {SOCKET_ENV: str(self.socket_path), TOKEN_ENV: self.token, PROJECT_ENV: self.project,
                TASK_ENV: self.slug, GENERATION_ENV: self.generation}

    def codex_capability(self) -> tuple[dict[str, str], tuple[int, int]]:
        """Preconnect one anonymous, inherited Codex capability.

        Codex 0.151 denies AF_UNIX connect without broad filesystem/network
        grants. The trusted host therefore connects before launch and passes
        only the connected socket plus an anonymous lock inode. No endpoint
        path crosses the sandbox boundary.
        """
        if self._listener is None or self._thread is None:
            raise BrokerDenied("broker must be started before creating a Codex capability")
        if self._inherited_connection is not None or self._inherited_lock_fd is not None:
            raise BrokerDenied("Codex broker capability already exists")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        lock_fd = None
        try:
            connection.connect(str(self.socket_path))
            if not hasattr(os, "memfd_create"):
                raise BrokerDenied("Codex broker serialization requires Linux memfd support")
            lock_fd = os.memfd_create("altitude-broker-lock", getattr(os, "MFD_CLOEXEC", 0))
            os.ftruncate(lock_fd, 1)
            info = os.fstat(lock_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 0:
                raise BrokerDenied("Codex broker lock is not an anonymous regular inode")
        except BaseException:
            connection.close()
            if lock_fd is not None:
                os.close(lock_fd)
            raise
        self._inherited_connection = connection
        self._inherited_lock_fd = lock_fd
        env = {FD_ENV: str(connection.fileno()), LOCK_FD_ENV: str(lock_fd),
               TOKEN_ENV: self.token, PROJECT_ENV: self.project,
               TASK_ENV: self.slug, GENERATION_ENV: self.generation}
        return env, (connection.fileno(), lock_fd)

    def start(self) -> "AltBroker":
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise BrokerDenied("broker endpoint already exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            listener.listen(16)
        except BaseException:
            listener.close()
            self.socket_path.unlink(missing_ok=True)
            raise
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, name=f"alt-broker-{self.slug}", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed.set()
        if self._inherited_connection is not None:
            try:
                self._inherited_connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._inherited_connection.close()
            self._inherited_connection = None
        if self._inherited_lock_fd is not None:
            try:
                os.close(self._inherited_lock_fd)
            except OSError:
                pass
            self._inherited_lock_fd = None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(1)
                connection.connect(str(self.socket_path))
                _send_socket(connection, {})
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        self.socket_path.unlink(missing_ok=True)
        try:
            self.socket_path.parent.rmdir()
        except OSError:
            pass

    def __enter__(self) -> "AltBroker":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()

    def _serve(self) -> None:
        while True:
            try:
                if self._listener is None:
                    return
                connection, _ = self._listener.accept()
            except OSError:
                if self._closed.is_set():
                    return
                continue
            with connection:
                try:
                    connection.settimeout(5)
                    first = True
                    while not self._closed.is_set():
                        request = _recv_socket(connection)
                        try:
                            response = self._process(request)
                        except BrokerDenied as exc:
                            response = {"returncode": 2, "stdout": "", "stderr": f"alt broker: {exc}\n"}
                        except Exception as exc:
                            response = {"returncode": 2, "stdout": "",
                                        "stderr": f"alt broker: internal {type(exc).__name__}: {exc}\n"}
                        _send_socket(connection, response)
                        if first:
                            # A preconnected Codex stream persists for the
                            # entire engine turn and may be idle for minutes.
                            connection.settimeout(None)
                            first = False
                except (OSError, BrokerDenied):
                    if self._closed.is_set():
                        return
                    continue
            if self._closed.is_set():
                return

    def _process(self, request: dict) -> dict:
        for key, expected in (("project", self.project), ("slug", self.slug), ("generation", self.generation)):
            if request.get(key) != expected:
                raise BrokerDenied(f"{key} does not match this capability")
        if not isinstance(request.get("token"), str) or not secrets.compare_digest(request["token"], self.token):
            raise BrokerDenied("invalid broker token")
        if not self.validate_generation():
            raise BrokerDenied("worker generation is no longer current and running")
        if request.get("kind") == "hook":
            if self.hook is None or not isinstance(request.get("payload"), dict):
                raise BrokerDenied("this capability does not accept hook decisions")
            raw_cwd = request.get("cwd")
            try:
                request_cwd = Path(str(raw_cwd)).resolve(strict=True)
            except OSError as exc:
                raise BrokerDenied(f"hook cwd is unavailable: {exc}") from exc
            if not request_cwd.is_dir() or not self.validate_cwd(request_cwd):
                raise BrokerDenied("hook cwd is not an exact persisted worktree owned by this task")
            verdict = self.hook(dict(request["payload"]))
            if not isinstance(verdict, dict) or not isinstance(verdict.get("allowed"), bool):
                raise BrokerDenied("host hook callback returned no decision")
            allowed = verdict["allowed"]
            message = str(verdict.get("message") or "")
            return {"returncode": 0 if allowed else 2, "stdout": "",
                    "stderr": (message.rstrip() + "\n") if message else ""}
        argv = request.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise BrokerDenied("argv is missing or malformed")
        raw_cwd = request.get("cwd")
        if not isinstance(raw_cwd, str):
            raise BrokerDenied("cwd is missing or malformed")
        try:
            request_cwd = Path(raw_cwd).resolve(strict=True)
        except OSError as exc:
            raise BrokerDenied(f"cwd is unavailable: {exc}") from exc
        if not request_cwd.is_dir() or not self.validate_cwd(request_cwd):
            raise BrokerDenied("cwd is not an exact persisted worktree owned by this task")
        action = self.policy(argv, project=self.project, slug=self.slug, worktree=request_cwd)
        if action.kind == "checkpoint":
            if self.checkpoint is None:
                raise BrokerDenied("this capability does not permit checkpoints")
            data = _read_regular_worktree_file(str(action.source), request_cwd)
            result = self.checkpoint(str(action.name), data)
            return {"returncode": 0, "stdout": json.dumps(result, indent=2) + "\n", "stderr": ""}
        try:
            stdin = base64.b64decode(str(request.get("stdin") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise BrokerDenied("stdin is not valid base64") from exc
        if len(stdin) > MAX_STDIN:
            raise BrokerDenied("stdin exceeds the broker limit")
        argv = list(action.argv)
        pinned_fds: list[int] = []
        try:
            for option, source in action.files:
                data = _read_regular_worktree_file(source, request_cwd)
                if hasattr(os, "memfd_create"):
                    descriptor = os.memfd_create("altitude-broker-input", getattr(os, "MFD_CLOEXEC", 0))
                else:
                    descriptor, fallback = tempfile.mkstemp(prefix="pinned-", dir=self.socket_path.parent)
                    os.unlink(fallback)
                pending = memoryview(data)
                while pending:
                    written = os.write(descriptor, pending)
                    pending = pending[written:]
                os.lseek(descriptor, 0, os.SEEK_SET)
                pinned_fds.append(descriptor)
                matches = [index for index, value in enumerate(argv[:-1])
                           if value == option and argv[index + 1] == source]
                if len(matches) != 1:
                    raise BrokerDenied(f"{option} source could not be pinned unambiguously")
                argv[matches[0] + 1] = f"/proc/self/fd/{descriptor}"
        except Exception:
            for descriptor in pinned_fds:
                os.close(descriptor)
            raise
        try:
            prepared_env = self.before_cli(argv, request_cwd) if self.before_cli else {}
            env = {key: value for key, value in os.environ.items() if not key.startswith(BROKER_PREFIX)}
            env.update(self.host_env)
            env.update(prepared_env or {})
            # Broker identity is authoritative. Callers may add context such as
            # ALTITUDE_TRIGGER, but cannot impersonate another project/task/actor.
            env.update({"ALTITUDE_PROJECT": self.project, "ALTITUDE_TASK": self.slug, "ALTITUDE_ACTOR": self.actor})
            completed = subprocess.run(
                [sys.executable, str(self.trusted_alt), "--project", self.project, *argv],
                input=stdin, capture_output=True, cwd=request_cwd, env=env, timeout=self.timeout,
                pass_fds=tuple(pinned_fds),
            )
            stdout = completed.stdout[-MAX_MESSAGE:].decode(errors="replace")
            stderr = completed.stderr[-MAX_MESSAGE:].decode(errors="replace")
            if self.after_cli:
                self.after_cli(argv, completed.returncode)
            return {"returncode": completed.returncode, "stdout": stdout, "stderr": stderr}
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "", "stderr": "alt broker: trusted command timed out\n"}
        finally:
            for descriptor in pinned_fds:
                os.close(descriptor)


def _fifo_request(endpoint: Path, request: dict, *, timeout: float = 1220) -> dict:
    """Exchange one request on the pre-created exact Unix socket.

    The historical function name is retained for callers, but there are no
    model-writable response paths or directory locks. One connected stream is
    both the request and response capability.
    """
    try:
        endpoint_info = endpoint.lstat()
    except OSError as exc:
        raise BrokerDenied(f"broker endpoint metadata is unavailable: {exc}") from exc
    if not stat.S_ISSOCK(endpoint_info.st_mode) or endpoint_info.st_uid != os.getuid():
        raise BrokerDenied("broker endpoint identity is invalid")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(endpoint))
            _send_socket(connection, request)
            return _recv_socket(connection)
    except (OSError, TimeoutError) as exc:
        raise BrokerDenied(f"broker socket connection is unavailable: {exc}") from exc


def _inherited_fd(raw: str | None, name: str, *, socket_fd: bool) -> int:
    if not raw or not raw.isdigit() or int(raw) < 3:
        raise BrokerDenied(f"inherited broker {name} is invalid")
    descriptor = int(raw)
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        raise BrokerDenied(f"inherited broker {name} is unavailable: {exc}") from exc
    if socket_fd:
        if not stat.S_ISSOCK(info.st_mode):
            raise BrokerDenied("inherited broker connection is not a socket")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 0:
        raise BrokerDenied("inherited broker lock is not an anonymous regular inode")
    return descriptor


def _fd_request(connection_fd: int, lock_fd: int, request: dict, *, timeout: float = 1220) -> dict:
    """Serialize one framed exchange on the inherited persistent connection."""
    try:
        # POSIX record locks are process-owned, so separate ``bin/alt``
        # children serialize even though they inherited the same memfd open
        # description. A process-local guard covers threads. Codex's sandbox
        # intentionally denies reopening /proc/self/fd; the inherited
        # descriptor itself is the complete lock capability.
        with _FD_REQUEST_LOCK:
            try:
                fcntl.lockf(lock_fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise BrokerDenied(f"inherited broker lock is unavailable: {exc}") from exc
            try:
                # Codex 0.151's seccomp profile denies both dup(2) and the
                # socket introspection performed by ``socket.socket(fileno=)``.
                # Plain read/write on the preconnected descriptor is the
                # narrow inherited capability that remains allowed.
                _send_fd(connection_fd, request)
                return _recv_fd(connection_fd)
            finally:
                fcntl.lockf(lock_fd, fcntl.LOCK_UN)
    except BrokerDenied:
        raise
    except (OSError, TimeoutError) as exc:
        raise BrokerDenied(f"inherited broker connection is unavailable: {exc}") from exc


def _configured_transport() -> tuple[dict[str, str], tuple[str, str | int, int | None]] | None:
    identity = {name: os.environ.get(name) for name in
                (TOKEN_ENV, PROJECT_ENV, TASK_ENV, GENERATION_ENV)}
    path = os.environ.get(SOCKET_ENV)
    raw_fd, raw_lock = os.environ.get(FD_ENV), os.environ.get(LOCK_FD_ENV)
    if not any((*identity.values(), path, raw_fd, raw_lock)):
        return None
    if not all(identity.values()):
        raise BrokerDenied("incomplete broker identity capability")
    if path and (raw_fd or raw_lock):
        raise BrokerDenied("broker path and inherited transports may not be combined")
    if path:
        return identity, ("path", path, None)
    if not raw_fd or not raw_lock:
        raise BrokerDenied("incomplete inherited broker transport")
    connection_fd = _inherited_fd(raw_fd, "connection", socket_fd=True)
    lock_fd = _inherited_fd(raw_lock, "lock", socket_fd=False)
    if connection_fd == lock_fd:
        raise BrokerDenied("inherited broker descriptors must be distinct")
    return identity, ("fd", connection_fd, lock_fd)


def _configured_request(request: dict, *, timeout: float) -> dict:
    configured = _configured_transport()
    if configured is None:
        raise BrokerDenied("broker capability is not configured")
    _identity, (kind, endpoint, lock_fd) = configured
    if kind == "path":
        return _fifo_request(Path(str(endpoint)), request, timeout=timeout)
    return _fd_request(int(endpoint), int(lock_fd), request, timeout=timeout)


def forward_hook_if_configured(payload: dict) -> dict | None:
    """Forward one PreToolUse payload to the generation's trusted host callback."""
    try:
        configured = _configured_transport()
    except BrokerDenied:
        raise
    if configured is None:
        return None
    present, _transport = configured
    request = {"kind": "hook", "token": present[TOKEN_ENV], "project": present[PROJECT_ENV],
               "slug": present[TASK_ENV], "generation": present[GENERATION_ENV],
               "cwd": os.getcwd(), "payload": payload}
    return _configured_request(request, timeout=30)


def forward_if_configured(argv: list[str]) -> int | None:
    """Forward one bin/alt invocation before importing stateful CLI modules."""
    try:
        configured = _configured_transport()
    except BrokerDenied as exc:
        print(f"alt broker: unavailable: {exc}", file=sys.stderr)
        return 2
    if configured is None:
        return None
    present, _transport = configured
    raw_stdin = sys.stdin.buffer.read(MAX_STDIN + 1)
    if len(raw_stdin) > MAX_STDIN:
        print("alt broker: stdin exceeds the broker limit", file=sys.stderr)
        return 2
    request = {"token": present[TOKEN_ENV], "project": present[PROJECT_ENV], "slug": present[TASK_ENV],
               "generation": present[GENERATION_ENV], "argv": list(argv), "cwd": os.getcwd(),
               "stdin": base64.b64encode(raw_stdin).decode()}

    try:
        response = _configured_request(request, timeout=1220)
    except (OSError, BrokerDenied) as exc:
        print(f"alt broker: unavailable: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(str(response.get("stdout") or ""))
    sys.stderr.write(str(response.get("stderr") or ""))
    try:
        return int(response.get("returncode", 2))
    except (TypeError, ValueError):
        return 2
