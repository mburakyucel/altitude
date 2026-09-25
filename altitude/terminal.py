"""The operator's terminal: one login shell per task worktree or project folder, run by altd.

A terminal is a shell on a pseudo-terminal owned by altd, in a session of its own. It ends when the
operator closes it, when the shell exits, when its task finishes, or when altd stops: nothing keeps it
alive beyond altd's own process, and its end stops every process still in its session. A bounded replay buffer lets a reconnecting page resume where it left off; nothing typed or
printed is stored anywhere, and an ended terminal is dropped at once: its open streams read how it ended.
Only opening and closing are recorded, on the task or project log.

Terminal requests from Altitude's own agents are refused (`agent_connection`): the terminal is full
command access as the operator, outside every worker sandbox and the machine-grant approval flow.
"""
from __future__ import annotations

import fcntl
import ipaddress
import os
import pwd
import re
import select
import signal
import socket
import struct
import subprocess
import termios
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config, state as S

REPLAY_BYTES = 256 * 1024
READ_BYTES = 65536
INPUT_LIMIT = 65536
CLOSE_GRACE_SECONDS = 2.0
#: How long input waits for a program that has stopped reading it before the request is refused.
WRITE_SECONDS = 2.0
POLL_SECONDS = 0.2
#: The environment variable every process a terminal starts inherits, so its end finds those that left its session.
MARK = "ALTITUDE_TERMINAL"
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
    id: str = field(default_factory=lambda: uuid.uuid4().hex)  # every request names it: a replaced terminal refuses
    buffer: bytearray = field(default_factory=bytearray)
    start: int = 0  # absolute output offset of buffer[0]
    exit_code: int | None = None
    reason: str | None = None  # why it ended: exited, closed, task-finished, project-removed
    ended: bool = False
    cond: threading.Condition = field(default_factory=threading.Condition)  # output and ending
    io: threading.Lock = field(default_factory=threading.Lock)  # the descriptor: held to use it or close it
    closing: bool = False  # Close was asked for: a waiting write gives up

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


def _env(ident: str) -> dict[str, str]:
    return {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", MARK: ident}


def open_terminal(project: str, slug: str | None) -> dict:
    """Start the terminal, or return the one already running for this task or project."""
    path = folder(project, slug)
    with _lock:  # held while registering, so turning the terminal off either sees this one or refuses it
        if not enabled():
            raise TerminalError("Terminal is off. Turn it on in Settings › This machine.", 403)
        term = _terminals.get((project, slug))
        if term is not None and not term.ended:
            return view(term)
        master, child, ident = *os.openpty(), uuid.uuid4().hex
        try:
            _winsize(master, 24, 80)
            os.set_blocking(master, False)
            proc = subprocess.Popen(shell_command(), stdin=child, stdout=child, stderr=child, cwd=path, env=_env(ident),
                                    start_new_session=True, preexec_fn=_controlling_terminal)
        except OSError as exc:
            os.close(master)
            raise TerminalError(f"Could not start the shell: {exc}") from exc
        finally:
            os.close(child)
        term = Terminal(project, slug, path, proc, master, ident)
        _terminals[(project, slug)] = term
    _record(term, "opened")
    threading.Thread(target=_read, args=(term,), name=f"terminal:{project}:{slug or ''}", daemon=True).start()
    return view(term)


def _read(term: Terminal) -> None:
    """Collect output until the shell has exited and its last output is read. The shell's exit ends the
    terminal even while a process that left the session still holds the pseudo-terminal open."""
    while term.proc.poll() is None:
        chunk = _drain(term, POLL_SECONDS)
        if chunk is None:
            break
        _keep(term, chunk)
    _stop_session(term, signal.SIGKILL)
    for _ in range(REPLAY_BYTES // READ_BYTES):  # what was written before the end, bounded
        chunk = _drain(term, 0)
        if not chunk:
            break
        _keep(term, chunk)
    code = term.proc.wait()
    with term.io:
        os.close(term.fd)
        term.fd = -1
    with term.cond:
        term.exit_code = code
        term.reason = term.reason or "exited"
        term.ended = True
        term.cond.notify_all()
    with _lock:
        if _terminals.get((term.project, term.slug)) is term:
            del _terminals[(term.project, term.slug)]
    _record(term, "closed", reason=term.reason, exit_code=code)


def _drain(term: Terminal, wait: float) -> bytes | None:
    """Output ready within `wait` seconds: b"" when there is none, None once nothing holds the terminal."""
    if not select.select([term.fd], [], [], wait)[0]:
        return b""
    try:
        return os.read(term.fd, READ_BYTES) or None
    except BlockingIOError:
        return b""
    except OSError:  # EIO: every process holding the terminal has gone
        return None


def _keep(term: Terminal, chunk: bytes) -> None:
    with term.cond:
        term.buffer += chunk
        if len(term.buffer) > REPLAY_BYTES:
            drop = len(term.buffer) - REPLAY_BYTES
            del term.buffer[:drop]
            term.start += drop
        term.cond.notify_all()


def _get(project: str, slug: str | None) -> Terminal:
    term = _terminals.get((project, slug))
    if term is None:
        raise TerminalError("No terminal is open here.", 404)
    return term


def _named(project: str, slug: str | None, ident) -> Terminal:
    """The terminal a request names. A page still showing a terminal that was replaced must not type into,
    resize, close or read its successor."""
    term = _get(project, slug)
    if ident != term.id:
        raise TerminalError("This terminal was replaced.", 410)
    return term


def _running(project: str, slug: str | None, ident) -> Terminal:
    term = _named(project, slug, ident)
    if term.ended:
        raise TerminalError("The terminal has closed.", 410)
    return term


def view(term: Terminal | None) -> dict:
    if term is None:
        return {"state": "none", "enabled": enabled()}
    with term.cond:
        return {"state": "exited" if term.ended else "running", "id": term.id, "enabled": enabled(),
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


def write(project: str, slug: str | None, ident, data: str) -> None:
    """Type `data`. A program that stops reading its input fills the terminal's queue; the write then gives
    up after WRITE_SECONDS, or as soon as Close is asked for, rather than holding the terminal."""
    if not isinstance(data, str) or len(data) > INPUT_LIMIT:
        raise TerminalError("Send terminal input as text.", 400)
    term = _running(project, slug, ident)
    raw = data.encode()
    deadline = time.monotonic() + WRITE_SECONDS
    with term.io:
        while raw:
            if term.fd < 0 or term.closing:
                raise TerminalError("The terminal has closed.", 410)
            try:
                raw = raw[os.write(term.fd, raw):]
                continue
            except BlockingIOError:
                pass
            except OSError as exc:
                raise TerminalError("The terminal has closed.", 410) from exc
            if time.monotonic() >= deadline:
                raise TerminalError("The terminal is not reading input. Press Ctrl+C or close it.")
            select.select([], [term.fd], [], POLL_SECONDS)


def _winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def resize(project: str, slug: str | None, ident, cols, rows) -> None:
    if not all(isinstance(n, int) and not isinstance(n, bool) and 2 <= n <= 1000 for n in (cols, rows)):
        raise TerminalError("Terminal size must be whole columns and rows.", 400)
    term = _running(project, slug, ident)
    try:
        with term.io:
            if term.fd < 0:  # the shell exited after the check above
                raise OSError
            _winsize(term.fd, rows, cols)
    except OSError as exc:
        raise TerminalError("The terminal has closed.", 410) from exc


def close(project: str, slug: str | None, reason: str = "closed", ident=None) -> None:
    """End the terminal: hang up every process in its session, then kill whatever outlasts the grace
    period. The reader records the close once the shell has gone. `ident`, when a page asks, names the
    terminal it shows."""
    term = _terminals.get((project, slug))
    if term is None or term.ended or ident is not None and ident != term.id:
        return
    with term.cond:
        term.reason = reason
        term.closing = True
    _stop_session(term, signal.SIGHUP)
    try:
        term.proc.wait(CLOSE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    _stop_session(term, signal.SIGKILL)


def _stop_session(term: Terminal, sig: int) -> None:
    """Signal every process the terminal started: those in its session (the shell, its foreground command
    and its jobs, including those that ignore a hang-up) and those that left it (`setsid`, daemons) but
    still carry its mark. Each process is held by a pidfd before it is checked, so a pid reused by an
    unrelated process in between is never signalled."""
    session, mark = term.proc.pid, f"{MARK}={term.id}".encode()
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            handle = os.pidfd_open(int(entry.name))
        except OSError:  # it has exited
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[3]) == session or mark in (entry / "environ").read_bytes().split(b"\0"):
                signal.pidfd_send_signal(handle, sig)
        except (OSError, IndexError, ValueError):
            pass
        finally:
            os.close(handle)


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
    with _lock:
        keys = list(_terminals)
    for project, slug in keys:
        close(project, slug, "closed")


def stream(project: str, slug: str | None, ident) -> Terminal:
    """The terminal a page reads output from; a stream stays with it even if another page replaces it."""
    return _named(project, slug, ident)


def read(term: Terminal, offset: int, wait: float) -> tuple[bytes, int, bool, bool]:
    """Output after `offset`, waiting up to `wait` seconds for some: (data, next offset, missed, ended).
    `missed` says output before the replay buffer's start is gone."""
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


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """An address with an IPv4-mapped IPv6 form reduced to its IPv4 address."""
    ip = ipaddress.ip_address(text.split("%", 1)[0])
    return getattr(ip, "ipv4_mapped", None) or ip


def _spellings(ip) -> list[str]:
    """Every way this host's tables can spell an address: an IPv4 client of an IPv6 socket appears
    IPv4-mapped in tcp6, and an IPv6 socket's client can reach an IPv4 address the same way."""
    return [str(ip), f"::ffff:{ip}"] if ip.version == 4 else [str(ip)]


def _socket_inode(peer: tuple, local: tuple) -> int | None:
    """The inode of the client end of this connection, when that end lives in this host's network namespace."""
    for client in _spellings(_address(peer[0])):
        want, family = _hex_address(client, peer[1])
        for server_ in _spellings(_address(local[0])):
            ours, server_family = _hex_address(server_, local[1])
            if server_family != family:
                continue
            for line in (PROC / "net" / f"tcp{family}").read_text().splitlines()[1:]:
                cols = line.split()
                if len(cols) > 9 and cols[1] == want and cols[2] == ours:
                    return int(cols[9])
    return None


def _this_host(ip) -> bool:
    """Whether the address belongs to this host: only a local address can be bound."""
    with socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((str(ip), 0))
        except OSError:
            return False
    return True


def _processes() -> tuple[set[int], set[int]]:
    """(every process whose parent and unit could be read, the Altitude ones among them): altd, its
    descendants (L3 turns, terminal shells) and every process in an Altitude service unit."""
    parents: dict[int, int] = {}
    owned: set[int] = set()
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text()
            cgroup = (entry / "cgroup").read_text()
        except OSError:  # the process exited while being read: it cannot vouch for a connection
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
    return set(parents), owned


def _holds(pid: int, target: str) -> bool:
    """Whether the process visibly holds the socket. Unreadable descriptors (another user's process, or
    one made undumpable) prove nothing either way."""
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

    A connection from another host is the operator's (Altitude has no login: reaching it is the access). A
    client address that belongs to this host is local however it is spelled, and its socket must be found
    in a process outside Altitude and in none of altd, anything it started, or anything in an Altitude
    service unit. A client whose descriptors cannot be read is not identified, so an agent process that
    hides its descriptors is refused.
    Limits: a process an agent starts outside these units, through the user service manager or a scheduler,
    is not recognised, and a worker whose engine runs without an OS sandbox can already change the
    operator's files directly. Reading this host's process table is Linux-specific."""
    try:
        inode = _socket_inode(peer, local)
        if inode is None:
            return _this_host(_address(peer[0]))
        target = f"socket:[{inode}]"
        readable, owned = _processes()
        if any(_holds(pid, target) for pid in owned):
            return True
        return not any(_holds(pid, target) for pid in readable - owned)
    except (OSError, ValueError):
        return True
