"""Capability broker for sandboxed Codex coordinators.

The engine receives a per-generation FIFO endpoint and bearer token, never a
writable Altitude state root. This module intentionally has no Altitude state
imports: the trusted wrapper supplies generation validation, checkpoint, and
post-call callbacks.
"""
from __future__ import annotations

import base64
import fcntl
import json
import os
import secrets
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


def _read_exact_fd(descriptor: int, size: int) -> bytes:
    chunks = []
    left = size
    while left:
        chunk = os.read(descriptor, left)
        if not chunk:
            raise BrokerDenied("truncated broker request")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def _recv_fd(descriptor: int) -> dict:
    size = struct.unpack("!I", _read_exact_fd(descriptor, 4))[0]
    if size < 2 or size > MAX_MESSAGE:
        raise BrokerDenied("broker message size is invalid")
    try:
        value = json.loads(_read_exact_fd(descriptor, size))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise BrokerDenied(f"invalid broker JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BrokerDenied("broker request is not an object")
    return value


def _send_fd(descriptor: int, value: dict) -> None:
    raw = json.dumps(value, sort_keys=True).encode()
    if len(raw) > MAX_MESSAGE:
        raw = json.dumps({"returncode": 2, "stdout": "", "stderr": "alt broker: response too large\n"}).encode()
    pending = memoryview(struct.pack("!I", len(raw)) + raw)
    while pending:
        written = os.write(descriptor, pending)
        pending = pending[written:]


def _open_fifo(path: Path, flags: int, deadline: float) -> int:
    while True:
        try:
            descriptor = os.open(path, flags | os.O_NONBLOCK)
            os.set_blocking(descriptor, True)
            return descriptor
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise BrokerDenied(f"broker endpoint is unavailable: {exc}") from exc


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
            "--name": True, "--cwd": True,
        })
        if "--brief" not in opts or len(opts["--brief"]) != 1:
            raise BrokerDenied("l1 run requires exactly one --brief")
        brief = str(opts["--brief"][0])
        _regular_worktree_file(brief, worktree)
        for singular in ("--role", "--engine", "--model", "--name", "--cwd"):
            if len(opts.get(singular, [])) > 1:
                raise BrokerDenied(f"{singular} may be specified only once")
        return BrokerAction("cli", tuple(argv), files=(("--brief", brief),))
    if argv[:1] == ["verify"]:
        if argv != ["verify", slug]:
            raise BrokerDenied("verify is restricted to the current task")
        return BrokerAction("cli", tuple(argv))
    if argv[:1] == ["land"]:
        opts = _option_values(argv[1:], {
            "--message": True, "--pr-title": True, "--pr-body-file": True,
            "--merge": False, "--wait": True, "--base": True,
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
    "show", "events", "status", "resume", "size", "paths", "hold-merge", "done", "list",
}
L3_INCIDENT = {"new", "amend", "list"}
L3_RULE = {"propose", "list", "audit-input"}


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
        if len(argv) < 2:
            raise BrokerDenied("fyi requires text")
        return BrokerAction("cli", tuple(argv))
    if len(argv) >= 2 and argv[0] == "task" and argv[1] in L3_TASK:
        return BrokerAction("cli", tuple(argv))
    if len(argv) >= 2 and argv[0] == "incident" and argv[1] in L3_INCIDENT:
        return BrokerAction("cli", tuple(argv))
    if len(argv) >= 2 and argv[0] == "rule" and argv[1] in L3_RULE:
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
        self.lock_path = self.socket_path.with_name("broker.lock")
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()

    def env(self) -> dict[str, str]:
        return {SOCKET_ENV: str(self.socket_path), TOKEN_ENV: self.token, PROJECT_ENV: self.project,
                TASK_ENV: self.slug, GENERATION_ENV: self.generation}

    def start(self) -> "AltBroker":
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(path.exists() or path.is_symlink() for path in (self.socket_path, self.lock_path)):
            raise BrokerDenied("broker endpoint already exists")
        os.mkfifo(self.socket_path, 0o600)
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(lock_fd)
        self._thread = threading.Thread(target=self._serve, name=f"alt-broker-{self.slug}", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed.set()
        try:
            descriptor = os.open(self.socket_path, os.O_WRONLY | os.O_NONBLOCK)
            _send_fd(descriptor, {})
            os.close(descriptor)
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        for path in (self.socket_path, self.lock_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        for response in self.socket_path.parent.glob("response-*"):
            try:
                response.unlink()
            except FileNotFoundError:
                pass
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
            descriptor = None
            try:
                descriptor = os.open(self.socket_path, os.O_RDONLY)
                request = _recv_fd(descriptor)
            except (OSError, BrokerDenied):
                continue
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            if self._closed.is_set():
                break
            response_id = request.get("response_id")
            if not isinstance(response_id, str) or len(response_id) != 32 or not response_id.isalnum():
                continue
            response_path = self.socket_path.parent / f"response-{response_id}"
            try:
                os.mkfifo(response_path, 0o600)
                try:
                    response = self._process(request)
                except BrokerDenied as exc:
                    response = {"returncode": 2, "stdout": "", "stderr": f"alt broker: {exc}\n"}
                except Exception as exc:
                    response = {"returncode": 2, "stdout": "",
                                "stderr": f"alt broker: internal {type(exc).__name__}: {exc}\n"}
                try:
                    writer = _open_fifo(response_path, os.O_WRONLY, time.monotonic() + 5)
                    try:
                        _send_fd(writer, response)
                    finally:
                        os.close(writer)
                finally:
                    response_path.unlink(missing_ok=True)

            except OSError:
                continue
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
    deadline = time.monotonic() + timeout
    lock_path = endpoint.with_name("broker.lock")
    try:
        endpoint_info = endpoint.lstat()
        lock_info = lock_path.lstat()
    except OSError as exc:
        raise BrokerDenied(f"broker endpoint is unavailable: {exc}") from exc
    if (not stat.S_ISFIFO(endpoint_info.st_mode) or not stat.S_ISREG(lock_info.st_mode)
            or endpoint_info.st_uid != os.getuid() or lock_info.st_uid != os.getuid()):
        raise BrokerDenied("broker endpoint identity is invalid")
    lock_fd = os.open(lock_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BrokerDenied("broker request lock timed out")
                time.sleep(0.01)
        response_id = secrets.token_hex(16)
        request = {**request, "response_id": response_id}
        writer = _open_fifo(endpoint, os.O_WRONLY, deadline)
        try:
            _send_fd(writer, request)
        finally:
            os.close(writer)
        response_path = endpoint.parent / f"response-{response_id}"
        while not response_path.exists():
            if time.monotonic() >= deadline:
                raise BrokerDenied("broker response timed out")
            time.sleep(0.01)
        response_info = response_path.lstat()
        if not stat.S_ISFIFO(response_info.st_mode) or response_info.st_uid != os.getuid():
            raise BrokerDenied("broker response endpoint identity is invalid")
        reader = os.open(response_path, os.O_RDONLY)
        try:
            return _recv_fd(reader)
        finally:
            os.close(reader)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def forward_hook_if_configured(payload: dict) -> dict | None:
    """Forward one PreToolUse payload to the generation's trusted host callback."""
    present = {name: os.environ.get(name) for name in
               (SOCKET_ENV, TOKEN_ENV, PROJECT_ENV, TASK_ENV, GENERATION_ENV)}
    if not any(present.values()):
        return None
    if not all(present.values()):
        raise BrokerDenied("incomplete broker capability")
    request = {"kind": "hook", "token": present[TOKEN_ENV], "project": present[PROJECT_ENV],
               "slug": present[TASK_ENV], "generation": present[GENERATION_ENV],
               "cwd": os.getcwd(), "payload": payload}
    return _fifo_request(Path(str(present[SOCKET_ENV])), request, timeout=30)


def forward_if_configured(argv: list[str]) -> int | None:
    """Forward one bin/alt invocation before importing stateful CLI modules."""
    present = {name: os.environ.get(name) for name in (SOCKET_ENV, TOKEN_ENV, PROJECT_ENV, TASK_ENV, GENERATION_ENV)}
    if not any(present.values()):
        return None
    if not all(present.values()):
        print("alt broker: incomplete broker capability", file=sys.stderr)
        return 2
    raw_stdin = sys.stdin.buffer.read(MAX_STDIN + 1)
    if len(raw_stdin) > MAX_STDIN:
        print("alt broker: stdin exceeds the broker limit", file=sys.stderr)
        return 2
    request = {"token": present[TOKEN_ENV], "project": present[PROJECT_ENV], "slug": present[TASK_ENV],
               "generation": present[GENERATION_ENV], "argv": list(argv), "cwd": os.getcwd(),
               "stdin": base64.b64encode(raw_stdin).decode()}

    try:
        response = _fifo_request(Path(str(present[SOCKET_ENV])), request)
    except (OSError, BrokerDenied) as exc:
        print(f"alt broker: unavailable: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(str(response.get("stdout") or ""))
    sys.stderr.write(str(response.get("stderr") or ""))
    try:
        return int(response.get("returncode", 2))
    except (TypeError, ValueError):
        return 2
