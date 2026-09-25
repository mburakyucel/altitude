"""The operator's terminal: one login shell per task worktree or project folder, run by altd.

A terminal is a shell on a pseudo-terminal owned by altd. It ends when the operator closes it, when the
shell exits, when its task finishes, or when altd stops: nothing keeps it alive beyond altd's own
process. A bounded replay buffer lets a reconnecting page resume where it left off; nothing typed or
printed is stored anywhere. Only opening and closing are recorded, on the task or project log.

Terminal requests from Altitude's own agents are refused (`agent_connection`): the terminal is full
command access as the operator, outside every worker sandbox and the machine-grant approval flow.
"""
from __future__ import annotations

import fcntl
import ipaddress
import os
import pwd
import re
import signal
import struct
import subprocess
import termios
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config, state as S

REPLAY_BYTES = 256 * 1024
READ_BYTES = 65536
INPUT_LIMIT = 65536
CLOSE_GRACE_SECONDS = 2.0
#: This altd process. A page that attached under another boot knows a restart closed its terminal.
BOOT = uuid.uuid4().hex
#: Where process and socket facts are read; tests point these at fixture trees.
PROC = Path("/proc")
#: A cgroup path component naming altd's own service or one of its transient units (workers, reviews,
#: machine commands, restarts).
ALTITUDE_UNIT = re.compile(r"altitude(-[^/]*)?\.service")


class TerminalError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class Terminal:
    project: str
    slug: str | None
    folder: Path
    proc: subprocess.Popen
    fd: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)  # tells a page a replaced terminal from its own
    buffer: bytearray = field(default_factory=bytearray)
    start: int = 0  # absolute output offset of buffer[0]
    exit_code: int | None = None
    reason: str | None = None  # why it ended: exited, closed, task-finished, project-removed
    ended: bool = False
    cond: threading.Condition = field(default_factory=threading.Condition)  # output and ending
    io: threading.Lock = field(default_factory=threading.Lock)  # the descriptor: held to use it or close it

    @property
    def end(self) -> int:
        return self.start + len(self.buffer)


_terminals: dict[tuple[str, str | None], Terminal] = {}
_lock = threading.Lock()


def enabled() -> bool:
    return config.machine_settings().get("terminal") is True


def shell_command() -> list[str]:
    """The operator's login shell."""
    shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    return [shell, "-l"]


def folder(project: str, slug: str | None) -> Path:
    """Where the terminal starts: the task's worktree while the task is active, else the project folder."""
    if not config.is_managed(project):
        raise TerminalError("Project is not managed.", 404)
    if slug is None:
        path = config.project_path(project)
    else:
        try:
            task = S.load_task(project, slug)
        except S.TaskNotFound:
            raise TerminalError("Task is not available.", 404) from None
        if task.get("state") in ("done", "rejected") or not task.get("worktree"):
            raise TerminalError("This task has no worktree to open a terminal in.")
        path = Path(task["worktree"])
    if not path.is_dir():
        raise TerminalError("The folder is missing." if slug is None else "The worktree is missing.")
    return path


def _record(term: Terminal, action: str, **data) -> None:
    fields = {"action": action, "folder": str(term.folder), **data}
    if term.slug is None:
        S.project_log(term.project, "terminal", **fields)
    else:
        S.append_event(term.project, term.slug, "terminal", **fields)


def _controlling_terminal() -> None:  # runs in the child between fork and exec
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _env() -> dict[str, str]:
    return {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}


def open_terminal(project: str, slug: str | None) -> dict:
    """Start the terminal, or return the one already running for this task or project."""
    if not enabled():
        raise TerminalError("Terminal is off. Turn it on in Settings › This machine.", 403)
    path = folder(project, slug)
    with _lock:
        term = _terminals.get((project, slug))
        if term is not None and not term.ended:
            return view(term)
        master, child = os.openpty()
        try:
            _winsize(master, 24, 80)
            proc = subprocess.Popen(shell_command(), stdin=child, stdout=child, stderr=child, cwd=path, env=_env(),
                                    start_new_session=True, preexec_fn=_controlling_terminal)
        except OSError as exc:
            os.close(master)
            raise TerminalError(f"Could not start the shell: {exc}") from exc
        finally:
            os.close(child)
        term = Terminal(project, slug, path, proc, master)
        _terminals[(project, slug)] = term
    _record(term, "opened")
    threading.Thread(target=_read, args=(term,), name=f"terminal:{project}:{slug or ''}", daemon=True).start()
    return view(term)


def _read(term: Terminal) -> None:
    while True:
        try:
            chunk = os.read(term.fd, READ_BYTES)
        except OSError:  # EIO: every process holding the terminal has gone
            chunk = b""
        if not chunk:
            break
        with term.cond:
            term.buffer += chunk
            if len(term.buffer) > REPLAY_BYTES:
                drop = len(term.buffer) - REPLAY_BYTES
                del term.buffer[:drop]
                term.start += drop
            term.cond.notify_all()
    code = term.proc.wait()
    with term.io:
        os.close(term.fd)
        term.fd = -1
    with term.cond:
        term.exit_code = code
        term.reason = term.reason or "exited"
        term.ended = True
        term.cond.notify_all()
    _record(term, "closed", reason=term.reason, exit_code=code)


def _get(project: str, slug: str | None) -> Terminal:
    term = _terminals.get((project, slug))
    if term is None:
        raise TerminalError("No terminal is open here.", 404)
    return term


def _running(project: str, slug: str | None) -> Terminal:
    term = _get(project, slug)
    if term.ended:
        raise TerminalError("The terminal has closed.", 410)
    return term


def view(term: Terminal | None) -> dict:
    if term is None:
        return {"state": "none", "boot": BOOT, "enabled": enabled()}
    with term.cond:
        return {"state": "exited" if term.ended else "running", "id": term.id, "boot": BOOT, "enabled": enabled(),
                "folder": str(term.folder), "offset": term.end, "exit_code": term.exit_code,
                "reason": term.reason, "busy": None if term.ended else _busy(term)}


def status(project: str, slug: str | None) -> dict:
    return view(_terminals.get((project, slug)))


def _busy(term: Terminal) -> str | None:
    """The command in the foreground, when it is not the shell itself: what Close would stop."""
    with term.io:
        try:
            group = os.tcgetpgrp(term.fd)
        except OSError:
            return None
    if group in (term.proc.pid, -1):
        return None
    try:
        return (PROC / str(group) / "comm").read_text().strip() or "A command"
    except OSError:
        return "A command"


def write(project: str, slug: str | None, data: str) -> None:
    if not isinstance(data, str) or len(data) > INPUT_LIMIT:
        raise TerminalError("Send terminal input as text.", 400)
    term = _running(project, slug)
    raw = data.encode()
    with term.io:
        while raw:
            try:
                raw = raw[os.write(term.fd, raw):]
            except OSError as exc:
                raise TerminalError("The terminal has closed.", 410) from exc


def _winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def resize(project: str, slug: str | None, cols, rows) -> None:
    if not all(isinstance(n, int) and not isinstance(n, bool) and 2 <= n <= 1000 for n in (cols, rows)):
        raise TerminalError("Terminal size must be whole columns and rows.", 400)
    term = _running(project, slug)
    try:
        with term.io:
            _winsize(term.fd, rows, cols)
    except OSError as exc:
        raise TerminalError("The terminal has closed.", 410) from exc


def close(project: str, slug: str | None, reason: str = "closed") -> None:
    """End the shell and its foreground command; the reader records the close once they have gone."""
    term = _terminals.get((project, slug))
    if term is None or term.ended:
        return
    with term.cond:
        term.reason = reason
    groups = {term.proc.pid}
    with term.io:
        try:
            groups.add(os.tcgetpgrp(term.fd))
        except OSError:
            pass
    _signal(groups, signal.SIGHUP)
    try:
        term.proc.wait(CLOSE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal(groups, signal.SIGKILL)


def _signal(groups: set[int], sig: int) -> None:
    for group in groups:
        if group > 0:
            try:
                os.killpg(group, sig)
            except OSError:
                pass


def forget(project: str, slug: str | None) -> None:
    """Drop an ended terminal's replay once the page has read how it ended."""
    with _lock:
        term = _terminals.get((project, slug))
        if term is not None and term.ended:
            del _terminals[(project, slug)]


def sweep() -> None:
    """Close terminals whose task finished or whose project is no longer managed."""
    for (project, slug), term in list(_terminals.items()):
        if term.ended:
            continue
        if not config.is_managed(project):
            close(project, slug, "project-removed")
        elif slug is not None:
            try:
                state = S.load_task(project, slug).get("state")
            except S.TaskNotFound:
                state = None
            if state in (None, "done", "rejected"):
                close(project, slug, "task-finished")


def close_all() -> None:
    for project, slug in list(_terminals):
        close(project, slug, "closed")


def read(project: str, slug: str | None, offset: int, wait: float) -> tuple[bytes, int, bool, bool]:
    """Output after `offset`, waiting up to `wait` seconds for some: (data, next offset, missed, ended).
    `missed` says output before the replay buffer's start is gone."""
    term = _get(project, slug)
    with term.cond:
        if offset >= term.end and not term.ended:
            term.cond.wait(wait)
        missed = offset < term.start
        begin = max(offset, term.start)
        data = bytes(term.buffer[begin - term.start:])
        return data, term.end, missed, term.ended and begin + len(data) >= term.end


# --- Refusing Altitude's own agents -------------------------------------------------------------------


def _hex_address(address: str, port: int) -> tuple[str, str]:
    """An address as /proc/net/tcp{,6} spells it: host-order 32-bit words in hex, then the port."""
    ip = ipaddress.ip_address(address)
    packed = ip.packed
    words = struct.unpack(f"{len(packed) // 4}I", packed)
    return "".join(f"{word:08X}" for word in words) + f":{port:04X}", "6" if ip.version == 6 else ""


def _socket_inode(peer: tuple, local: tuple) -> int | None:
    """The inode of the client end of this connection, when that end lives in this host's network namespace."""
    want, family = _hex_address(peer[0], peer[1])
    ours, _ = _hex_address(local[0], local[1])
    for line in (PROC / "net" / f"tcp{family}").read_text().splitlines()[1:]:
        cols = line.split()
        if len(cols) > 9 and cols[1] == want and cols[2] == ours:
            return int(cols[9])
    return None


def _altitude_processes() -> set[int]:
    """altd, its descendants (L3 turns, terminal shells) and every process in an Altitude service unit."""
    parents: dict[int, int] = {}
    owned: set[int] = set()
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text()
            cgroup = (entry / "cgroup").read_text()
        except OSError:  # the process exited while being read
            continue
        parents[pid] = int(stat.rsplit(")", 1)[1].split()[1])
        if any(ALTITUDE_UNIT.fullmatch(part) for line in cgroup.splitlines() for part in line.split("/")):
            owned.add(pid)
    children: dict[int, list[int]] = {}
    for pid, parent in parents.items():
        children.setdefault(parent, []).append(pid)
    frontier = [os.getpid()]
    descendants = {os.getpid()}
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in descendants:
                descendants.add(child)
                frontier.append(child)
    owned |= descendants
    return owned


def _owns(pid: int, inode: int) -> bool:
    target = f"socket:[{inode}]"
    try:
        for fd in (PROC / str(pid) / "fd").iterdir():
            try:
                if os.readlink(fd) == target:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def agent_connection(peer: tuple, local: tuple) -> bool:
    """Whether this connection comes from Altitude itself rather than the operator's browser.

    A connection from another host is the operator's (Altitude has no login: reaching it is the access). On
    this host, the client socket's owning process decides: altd, anything it started, and anything in an
    Altitude service unit is refused. A loopback connection whose client cannot be identified is refused.
    Limits: a process an agent starts outside these units, through the user service manager or a scheduler,
    is not recognised, and a worker whose engine runs without an OS sandbox can already change the
    operator's files directly. Reading this host's process table is Linux-specific."""
    try:
        inode = _socket_inode(peer, local)
        if inode is None:
            return ipaddress.ip_address(peer[0]).is_loopback
        return any(_owns(pid, inode) for pid in _altitude_processes())
    except (OSError, ValueError):
        return True
