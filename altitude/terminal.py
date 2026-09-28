"""The operator's terminal: one login shell per task worktree or project folder, started for altd by the user manager.

A terminal is a shell on a pseudo-terminal altd owns, run as a job of its own (`platform.terminal_job`) so that it
does not inherit altd's service hardening and `sudo` works in it. It ends when the operator closes it, when the shell
exits, when its task finishes, or when altd stops; its end stops every process the shell started. A bounded replay
buffer lets a reconnecting page resume where it left off; nothing typed or printed is written anywhere. Only opening
and closing are recorded, on the task or project log.

Terminal requests from Altitude's own agents are refused (`agent_connection`): the terminal is full command access
as the operator, outside every worker sandbox and the machine-grant approval flow. A task's own owner may read its
task terminal's output (`owner_output`, `owner_connection`), never type into or control it; the last output of an
ended task terminal stays readable in memory until a new terminal opens there, the task finishes or altd stops.
"""
from __future__ import annotations

import fcntl
import ipaddress
import os
import pwd
import re
import select
import socket
import struct
import subprocess
import termios
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config, platform, state as S

REPLAY_BYTES = 256 * 1024
READ_BYTES = 65536
INPUT_LIMIT = 65536
#: Seconds the job gives its processes to end after the hang-up before killing them.
CLOSE_GRACE_SECONDS = 2
#: How long Close keeps stopping a job whose launcher has not ended, beyond the grace period, and how often.
STOP_SECONDS = 10
STOP_POLL_SECONDS = 0.5
#: How long input waits for a program that has stopped reading it before the request is refused.
WRITE_SECONDS = 2.0
POLL_SECONDS = 0.2
#: A cgroup path component naming altd's own service or one of its transient units (workers, reviews,
#: machine commands, terminals, restarts).
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
    proc: subprocess.Popen  # the launcher, which runs until the shell's job has ended
    fd: int
    tty: int  # altd's own descriptor for the shell's side, held so the shell can open it whenever its job starts
    id: str  # every request names it: a replaced terminal refuses
    buffer: bytearray = field(default_factory=bytearray)
    start: int = 0  # absolute output offset of buffer[0]
    exit_code: int | None = None
    reason: str | None = None  # why it ended: exited, closed, task-finished, project-removed, failed
    error: str | None = None  # why a shell that failed could not run, in the launcher's words
    ended: bool = False
    cond: threading.Condition = field(default_factory=threading.Condition)  # output and ending
    io: threading.Lock = field(default_factory=threading.Lock)  # the descriptor: held to use it or close it
    closing: bool = False  # Close was asked for: a waiting write gives up

    @property
    def end(self) -> int:
        return self.start + len(self.buffer)

    @property
    def unit(self) -> str:
        return f"altitude-terminal-{self.id}.service"


_terminals: dict[tuple[str, str | None], Terminal] = {}
#: The last ended terminal of each task or project: a page naming it reads how it ended, and a task's owner reads
#: its output, until a new terminal opens there.
_ended: dict[tuple[str, str | None], Terminal] = {}
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


def shell_env() -> dict[str, str]:
    """What the shell adds to its session's environment: the screen's capabilities, and Altitude's settings and
    PATH so that `alt` typed in the terminal reaches this Altitude."""
    env = {key: value for key, value in os.environ.items() if key == "PATH" or key.startswith("ALTITUDE_")}
    return {**env, "TERM": "xterm-256color", "COLORTERM": "truecolor"}


def launch(unit: str, tty: str, path: Path) -> subprocess.Popen:
    """Start the shell's job on `tty`; the launcher's error output says why a job could not start."""
    return subprocess.Popen(platform.terminal_job(unit, tty, shell_command(), shell_env(), grace=CLOSE_GRACE_SECONDS),
                            cwd=path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            env=platform.manager_env(dict(os.environ)), start_new_session=True)


def stop(unit: str) -> None:
    """Stop the shell's job: every process in it is hung up, then killed after the grace period."""
    platform.job_stop(unit, platform.manager_env(dict(os.environ)), timeout=CLOSE_GRACE_SECONDS + 30)


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
            proc = launch(f"altitude-terminal-{ident}.service", os.ttyname(child), path)
        except OSError as exc:
            os.close(master)
            os.close(child)
            raise TerminalError(f"Could not start the shell: {exc}") from exc
        term = Terminal(project, slug, path, proc, master, child, ident)
        _terminals[(project, slug)] = term
        _ended.pop((project, slug), None)
    _record(term, "opened")
    threading.Thread(target=_read, args=(term,), name=f"terminal:{project}:{slug or ''}", daemon=True).start()
    return view(term)


def _read(term: Terminal) -> None:
    """Collect output until the shell's job has ended, then its last output. A launcher that could not start the
    job ends the terminal as `failed`, with its reason."""
    while term.proc.poll() is None:
        _keep(term, _drain(term, POLL_SECONDS))
    stop(term.unit)  # nothing of the job outlives a launcher that ended for any other reason
    for _ in range(REPLAY_BYTES // READ_BYTES):  # what was written before the end, bounded
        chunk = _drain(term, 0)
        if not chunk:
            break
        _keep(term, chunk)
    code = term.proc.wait()
    with term.proc.stderr:
        failure = term.proc.stderr.read().decode(errors="replace").strip()
    with term.io:
        os.close(term.fd)
        os.close(term.tty)
        term.fd = -1
    with term.cond:
        term.exit_code = code
        if failure and not term.reason:
            term.reason, term.error = "failed", failure[-500:]
        term.reason = term.reason or "exited"
        term.ended = True
        term.cond.notify_all()
    with _lock:
        if _terminals.get((term.project, term.slug)) is term:
            del _terminals[(term.project, term.slug)]
            _ended[(term.project, term.slug)] = term
    _record(term, "closed", reason=term.reason, exit_code=code)


def _drain(term: Terminal, wait: float) -> bytes:
    """Output ready within `wait` seconds, b"" when there is none. altd holds the shell's side open, so the
    terminal never reads as hung up while its job runs."""
    if not select.select([term.fd], [], [], wait)[0]:
        return b""
    try:
        return os.read(term.fd, READ_BYTES)
    except (BlockingIOError, OSError):
        return b""


def _keep(term: Terminal, chunk: bytes) -> None:
    if not chunk:
        return
    with term.cond:
        term.buffer += chunk
        if len(term.buffer) > REPLAY_BYTES:
            drop = len(term.buffer) - REPLAY_BYTES
            del term.buffer[:drop]
            term.start += drop
        term.cond.notify_all()


def _named(project: str, slug: str | None, ident) -> Terminal:
    """The terminal a request names. A page still showing a terminal that was replaced must not type into,
    resize, close or read its successor. A page naming the terminal that last ended here reads how it ended,
    even when it ended before the page attached (a shell that could not start)."""
    ended = _ended.get((project, slug))
    term = _terminals.get((project, slug)) or (ended if ended is not None and ended.id == ident else None)
    if term is None:
        raise TerminalError("No terminal is open here.", 404)
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
                "reason": term.reason, "error": term.error, "busy": None if term.ended else _busy(term)}


def status(project: str, slug: str | None) -> dict:
    return view(_terminals.get((project, slug)))


def _busy(term: Terminal) -> str | None:
    """The command in the foreground, when it is not the shell itself: what Close would stop."""
    with term.io:
        try:
            group = os.tcgetpgrp(term.fd)
            shell = platform.terminal_session(term.fd)
        except OSError:
            return None
    if group in (shell, -1, 0) or shell <= 0:
        return None
    return platform.process_name(group) or "A command"


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
    """End the terminal by stopping its job; the reader records the close once the shell has gone. Close returns once
    the job's launcher has ended, and says so when it has not. `ident`, when a page asks, names the terminal it shows."""
    term = _terminals.get((project, slug))
    if term is None or term.ended or ident is not None and ident != term.id:
        return
    with term.cond:
        term.reason = reason
        term.closing = True
    deadline = time.monotonic() + CLOSE_GRACE_SECONDS + STOP_SECONDS
    while True:  # a Close right after opening can reach the manager before the job exists: the next stop finds it
        stop(term.unit)
        try:
            term.proc.wait(STOP_POLL_SECONDS)
            return
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise TerminalError("The terminal did not stop. Close it again, or stop it from a desktop terminal.",
                                    500) from None


def _finished(project: str, slug: str | None) -> str | None:
    """Why a terminal here must end: its project is no longer managed or its task has finished."""
    if not config.is_managed(project):
        return "project-removed"
    if slug is not None:
        try:
            state = S.load_task(project, slug).get("state")
        except S.TaskNotFound:
            state = None
        if state in (None, "done", "rejected"):
            return "task-finished"
    return None


def sweep() -> None:
    """Close terminals whose task finished or whose project is no longer managed, and forget their output."""
    for (project, slug), term in list(_terminals.items()):
        if not term.ended and (reason := _finished(project, slug)):
            try:
                close(project, slug, reason)
            except TerminalError:
                continue  # the next tick tries again
    with _lock:
        for key in [key for key in _ended if _finished(*key)]:
            del _ended[key]


def close_all() -> None:
    """Close every terminal; one that does not stop is reported after the others have been closed."""
    with _lock:
        keys = list(_terminals)
    failed = None
    for project, slug in keys:
        try:
            close(project, slug, "closed")
        except TerminalError as exc:
            failed = failed or exc
    if failed:
        raise failed


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


# --- The owner's read-only view -----------------------------------------------------------------------------------

#: Escape sequences: operating-system commands (titles, links), control sequences (colour, cursor) and the rest.
_ESCAPES = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[ -/]*[0-~]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def plain(data: bytes) -> str:
    """Terminal output as text: escape sequences removed, and each line as its last carriage return left it."""
    text = _ESCAPES.sub(b"", data).decode(errors="replace").replace("\r\n", "\n")
    lines = []
    for line in text.split("\n"):
        parts = [part for part in line.split("\r") if part]
        lines.append(_CONTROLS.sub("", parts[-1] if parts else ""))
    return "\n".join(lines)


def owner_output(project: str, slug: str) -> dict:
    """The task terminal's output as its owner reads it: the running terminal's, else the last ended one's. Project
    terminals have no owner reader: `slug` names a task."""
    term = _terminals.get((project, slug)) or _ended.get((project, slug))
    if term is None:
        return {"state": "none", "text": "", "missed": False}
    with term.cond:
        data, missed = bytes(term.buffer), term.start > 0
        state = "exited" if term.ended else "running"
        exit_code, reason, error = term.exit_code, term.reason, term.error
    return {"state": state, "text": plain(data), "missed": missed, "exit_code": exit_code, "reason": reason,
            "error": error}


def owner_connection(peer: tuple, local: tuple, unit: str) -> bool:
    """Whether this connection's client end is held by a process in `unit`, the task's current worker job. The
    process and socket facts come from the platform seam; a connection that cannot be traced is not the owner's."""
    try:
        target = platform.client_socket(_spellings(_address(peer[0])), peer[1], _spellings(_address(local[0])), local[1])
        if target is None:
            return False
        table = platform.process_table()
        return any(unit in groups and platform.holds(pid, target) for pid, (_, groups) in table.items())
    except (OSError, ValueError):
        return False


# --- Refusing Altitude's own agents -------------------------------------------------------------------


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """An address with an IPv4-mapped IPv6 form reduced to its IPv4 address."""
    ip = ipaddress.ip_address(text.split("%", 1)[0])
    return getattr(ip, "ipv4_mapped", None) or ip


def _spellings(ip) -> list[str]:
    """Every way this host's tables can spell an address: an IPv4 client of an IPv6 socket appears
    IPv4-mapped in tcp6, and an IPv6 socket's client can reach an IPv4 address the same way."""
    return [str(ip), f"::ffff:{ip}"] if ip.version == 4 else [str(ip)]


def _this_host(ip) -> bool:
    """Whether the address belongs to this host: only a local address can be bound."""
    with socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((str(ip), 0))
        except OSError:
            return False
    return True


def _owned(table: dict[int, tuple[int, list[str]]]) -> set[int]:
    """The Altitude processes: altd, its descendants (L3 turns) and every process in an Altitude service unit
    (workers, terminal shells)."""
    owned = {pid for pid, (_, groups) in table.items() if any(ALTITUDE_UNIT.fullmatch(part) for part in groups)}
    children: dict[int, list[int]] = {}
    for pid, (parent, _) in table.items():
        children.setdefault(parent, []).append(pid)
    frontier = [os.getpid()]
    descendants = {os.getpid()}
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in descendants:
                descendants.add(child)
                frontier.append(child)
    return owned | descendants


def agent_connection(peer: tuple, local: tuple) -> bool:
    """Whether this connection comes from Altitude itself rather than the operator's browser.

    A connection from another host is the operator's (Altitude has no login: reaching it is the access). A
    client address that belongs to this host is local however it is spelled, and its socket must be found
    in a process outside Altitude and in none of altd, anything it started, or anything in an Altitude
    service unit. A client whose descriptors cannot be read is not identified, so an agent process that
    hides its descriptors is refused.
    Limits: a process an agent starts outside these units, through the user service manager or a scheduler,
    is not recognised, and a worker whose engine runs without an OS sandbox can already change the
    operator's files directly. The process and socket facts come from the platform seam."""
    try:
        target = platform.client_socket(_spellings(_address(peer[0])), peer[1], _spellings(_address(local[0])), local[1])
        if target is None:
            return _this_host(_address(peer[0]))
        table = platform.process_table()
        owned = _owned(table)
        if any(platform.holds(pid, target) for pid in owned):
            return True
        return not any(platform.holds(pid, target) for pid in set(table) - owned)
    except (OSError, ValueError):
        return True
