"""Altitude's own sign-in on an engine whose runs take a token from the operator (`TOKEN_SETUP`), held by altd.

altd runs the engine's setup command on a pseudo-terminal of its own, very wide so nothing wraps, and reads that
screen: the sign-in link goes to the operator's browser, the code the engine's page then shows is typed into the
screen, and the token the command prints is saved to Altitude's private folder (`access.save_engine_token`). Nothing
read from the screen is logged, recorded or returned except the link, and a failure is one of a few fixed sentences.
The command is the session leader of that terminal, so it is hung up when altd ends; cancelling, failing or the ten
minutes running out end its whole process group. One sign-in per engine is open at a time; starting one replaces it.
"""
from __future__ import annotations

import codecs
import fcntl
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid

from .. import access, config
from . import common

DEADLINE = 600   # seconds an open sign-in lasts
LINK_WAIT = 30   # seconds for the setup command to show its link
FINISH_WAIT = 90  # seconds for the engine to turn the code into a token
SHOWN = 600      # seconds a failure stays on the page
QUIET = 0.05     # a pause in output long enough to read the screen as drawn
COLUMNS, ROWS = 4000, 200

# The command's own process, run as the session leader of its terminal (preexec_fn is unsafe in altd's threads).
_LEADER = "import fcntl, os, sys, termios\nfcntl.ioctl(0, termios.TIOCSCTTY, 0)\nos.execvp(sys.argv[1], sys.argv[1:])\n"
_CODE = re.compile(r"[\x21-\x7e]{1,2048}")

_lock = threading.Lock()
_changed = threading.Condition(_lock)
_open: dict[str, "_Attempt"] = {}


def failures(engine: str) -> dict[str, str]:
    label = config.ENGINE_LABELS[engine]
    return {"rejected": f"{label} didn't accept that code.",
            "expired": "The sign-in expired after ten minutes.",
            "interrupted": "The sign-in was interrupted.",
            "unfollowed": f"Altitude couldn't follow {label}'s sign-in. If this repeats, Altitude needs an update "
                          f"for this {label} version."}


class SignInError(ValueError):
    """A request that does not fit the open sign-in; its text is fixed."""


class _Screen:
    """Enough of a terminal to read what a full-screen program draws: text, cursor moves and erasing."""
    _SEQUENCE = re.compile(r"\x1b\[([0-?]*)[ -/]*([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[ -/]*[0-Z\\^-~]"
                           r"|[\x00-\x1a\x1c-\x1f\x7f]|[^\x00-\x1f\x7f]+")

    def __init__(self, cleared):
        self.rows: dict[int, list[str]] = {}
        self.y = self.x = 0
        self.saved = (0, 0)
        self.pending = ""
        self.cleared = cleared  # sees the screen before it is wiped

    def text(self) -> str:
        return "\n".join("".join(self.rows.get(y, [])) for y in range(max(self.rows, default=-1) + 1))

    def _row(self) -> list[str]:
        return self.rows.setdefault(self.y, [])

    def _erase(self, start: int, end: int) -> None:
        row = self._row()
        for x in range(start, min(end, len(row))):
            row[x] = " "

    def _wipe(self) -> None:
        self.cleared(self.text())
        self.rows.clear()

    def feed(self, text: str) -> None:
        text, at = self.pending + text, 0
        while at < len(text):
            found = self._SEQUENCE.match(text, at)
            if found is None and len(text) - at > 4096:
                at += 1  # an escape that never ends: drop it, keep reading
                continue
            if found is None:  # a sequence the next read completes
                break
            at = found.end()
            part = found.group(0)
            if part[0] != "\x1b" and part >= " ":
                for char in part:
                    if self.x >= COLUMNS:
                        self.x, self.y = 0, self.y + 1
                    row = self._row()
                    row.extend(" " * (self.x + 1 - len(row)))
                    row[self.x] = char
                    self.x += 1
            elif part == "\r":
                self.x = 0
            elif part in ("\n", "\x0b", "\x0c"):
                self.y += 1
            elif part == "\b":
                self.x = max(0, self.x - 1)
            elif part == "\t":
                self.x = (self.x // 8 + 1) * 8
            elif part in ("\x1b7", "\x1b8"):
                self.saved, (self.y, self.x) = ((self.y, self.x), self.saved) if part == "\x1b7" else (self.saved, self.saved)
            elif found.group(2):
                self._control(found.group(1), found.group(2))
        self.pending = text[at:]

    def _control(self, raw: str, final: str) -> None:
        private = raw.startswith("?")
        numbers = [int(n) if n.isdigit() else 0 for n in raw.lstrip("?").split(";")]
        n = numbers[0] or 1
        if private:
            if final in "hl" and {47, 1047, 1049} & set(numbers):
                self._wipe()
                self.y = self.x = 0
            return
        if final in "Hf":
            self.y, self.x = max(n, 1) - 1, max(numbers[1] if len(numbers) > 1 else 1, 1) - 1
        elif final == "A":
            self.y = max(0, self.y - n)
        elif final in "Be":
            self.y += n
        elif final in "Ca":
            self.x += n
        elif final == "D":
            self.x = max(0, self.x - n)
        elif final in "EF":
            self.y, self.x = (self.y + n if final == "E" else max(0, self.y - n)), 0
        elif final in "G`":
            self.x = n - 1
        elif final == "d":
            self.y = n - 1
        elif final == "X":
            self._erase(self.x, self.x + n)
        elif final == "K":
            mode = numbers[0]
            self._erase(0 if mode else self.x, self.x + 1 if mode == 1 else COLUMNS)
        elif final == "J":
            mode = numbers[0]
            if mode >= 2:
                self._wipe()
                return
            row = self.y
            self._erase(0 if mode else self.x, self.x + 1 if mode == 1 else COLUMNS)
            for y in list(self.rows):
                if (y > row) if mode == 0 else (y < row):
                    del self.rows[y]
        elif final == "s":
            self.saved = (self.y, self.x)
        elif final == "u":
            self.y, self.x = self.saved


class _Attempt:
    def __init__(self, engine: str):
        self.engine, self.id = engine, uuid.uuid4().hex
        self.started = time.time()
        self.deadline = self.started + DEADLINE
        self.state, self.link, self.failure, self.failed_at = "opening", None, None, None
        self.links: list[str] = []
        self.tokens: set[str] = set()
        self.code_sent = False
        self.process: subprocess.Popen | None = None
        self.fd: int | None = None
        self.home = access.DIR / f"{engine}.setup.{self.id}"

    def view(self) -> dict:
        return {"attempt": self.id, "state": self.state, "link": self.link if self.state == "waiting" else None,
                "started": self.started, "error": failures(self.engine)[self.failure] if self.failure else None}

    def read(self, screen: str) -> None:
        """What the screen shows now: the current link, and every token standing alone on its line."""
        native = common.implementation(self.engine)
        for line in screen.split("\n"):
            if native.SETUP_TOKEN.fullmatch(line.strip()):
                self.tokens.add(line.strip())
            for link in native.SETUP_LINK.findall(line):
                if link in self.links:
                    continue
                self.links.append(link)
                if self.code_sent:  # the engine started over: it did not take the code
                    self.fail("rejected")
                elif self.state == "opening":
                    self.state, self.link = "waiting", link
                    _changed.notify_all()

    def fail(self, failure: str) -> None:
        if self.state in ("failed", "cancelled", "done"):
            return
        self.state, self.failure, self.failed_at = "failed", failure, time.time()
        self.end()
        _changed.notify_all()

    def end(self) -> None:
        """Stop the command and everything it started; the reader closes the terminal and removes the folder."""
        if self.process is not None:  # its group outlives it while anything it started still runs
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.tokens.clear()


def _settings_folder(attempt: _Attempt) -> None:
    """A new settings folder for the command, after removing any an earlier sign-in left behind."""
    access.DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for earlier in access.DIR.glob(f"{attempt.engine}.setup.*"):
        shutil.rmtree(earlier, ignore_errors=True)
    attempt.home.mkdir(mode=0o700)


def _run(attempt: _Attempt) -> None:
    """Read the command's screen until it ends, then decide: save the one token, or fail without saving."""
    snapshots: list[str] = []
    screen = _Screen(snapshots.append)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    drawn = True
    try:
        while True:
            with _lock:
                if attempt.state in ("failed", "cancelled"):
                    break
                if time.time() > attempt.deadline:
                    attempt.fail("expired")
                    break
            if select.select([attempt.fd], [], [], QUIET)[0]:
                try:
                    data = os.read(attempt.fd, 65536)
                except OSError:  # the command and its children closed the terminal
                    data = b""
                if not data:
                    break
                screen.feed(decoder.decode(data))
                drawn = False
                continue
            if not drawn or snapshots:
                with _lock:
                    for text in [*snapshots, screen.text()]:
                        attempt.read(text)
                snapshots.clear()
                drawn = True
            elif attempt.process.poll() is not None:  # ended, while something it started holds the terminal
                break
        returncode = attempt.process.wait(timeout=10) if attempt.state not in ("failed", "cancelled") else None
        with _lock:
            for text in [*snapshots, screen.text()]:
                attempt.read(text)
            if attempt.state in ("failed", "cancelled"):
                return
            if not attempt.code_sent:  # ended before the code: after showing its link, or without one
                attempt.fail("interrupted" if attempt.state == "waiting" else "unfollowed")
            elif returncode == 0 and len(attempt.tokens) == 1:
                access.save_engine_token(attempt.engine, attempt.tokens.pop())
                from .. import route
                route.signed_in(attempt.engine)
                attempt.state = "done"
                _changed.notify_all()
            else:
                attempt.fail("rejected" if returncode else "unfollowed")
    except Exception as exc:  # noqa: BLE001 - never the screen's text: the type alone
        print(f"[altitude] sign-in reader stopped: {type(exc).__name__}", file=sys.stderr, flush=True)
        with _lock:
            attempt.fail("unfollowed")
    finally:
        with _lock:
            attempt.end()
        if attempt.process is not None:
            try:
                attempt.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        os.close(attempt.fd)
        shutil.rmtree(attempt.home, ignore_errors=True)
        snapshots.clear()
        screen.rows.clear()


def _check(engine: str) -> None:
    if engine not in config.ENGINES or not common.implementation(engine).TOKEN_SETUP:
        raise SignInError("This engine does not sign in here.")


def view(engine: str) -> dict:
    """The engine's open sign-in, a recent failure, or idle; and when its saved sign-in was saved."""
    _check(engine)
    with _lock:
        attempt = _open.get(engine)
        shown = attempt is not None and (attempt.state in ("opening", "waiting", "finishing")
                                         or attempt.state == "failed" and time.time() - attempt.failed_at < SHOWN)
        state = attempt.view() if shown else {"attempt": None, "state": "idle", "link": None, "started": None,
                                               "error": None}
    return {**state, "engine": engine, "saved": access.engine_token_saved(engine)}


def start(engine: str) -> dict:
    """Open a sign-in, replacing any open one, and wait for the engine's link."""
    _check(engine)
    native = common.implementation(engine)
    with _lock:
        if previous := _open.get(engine):
            previous.state = "cancelled"
            previous.end()
        attempt = _open[engine] = _Attempt(engine)
    main = None
    try:
        _settings_folder(attempt)
        main, child = os.openpty()
        fcntl.ioctl(child, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
        env = native.token_setup_env(common.clean_env(), attempt.home)
        try:
            attempt.process = subprocess.Popen([sys.executable, "-I", "-c", _LEADER, native.binary(),
                                                *native.TOKEN_SETUP], stdin=child, stdout=child, stderr=child,
                                               env={**env, "TERM": "xterm-256color"}, cwd=attempt.home,
                                               start_new_session=True)
        finally:
            os.close(child)
    except OSError:
        if main is not None:
            os.close(main)
        shutil.rmtree(attempt.home, ignore_errors=True)
        with _lock:
            attempt.fail("unfollowed")
        return view(engine)
    attempt.fd = main
    threading.Thread(target=_run, args=(attempt,), name=f"sign-in-{engine}", daemon=True).start()
    with _lock:
        _changed.wait_for(lambda: attempt.state != "opening", timeout=LINK_WAIT)
        if attempt.state == "opening":
            attempt.fail("unfollowed")
    return view(engine)


def finish(engine: str, attempt_id: str, code: str) -> dict:
    """Type the code the engine's page showed into the open sign-in and wait for the outcome."""
    _check(engine)
    with _lock:
        attempt = _open.get(engine)
        if attempt is None or attempt.id != attempt_id or attempt.state != "waiting":
            raise SignInError("This sign-in is no longer open.")
        code = code.strip() if isinstance(code, str) else ""
        if not _CODE.fullmatch(code):
            attempt.fail("rejected")
            return {**attempt.view(), "engine": engine, "saved": access.engine_token_saved(engine)}
        attempt.state, attempt.code_sent = "finishing", True
        try:
            os.write(attempt.fd, code.encode())
            time.sleep(0.2)  # typed, then Enter on its own, as a person would
            os.write(attempt.fd, b"\r")
        except OSError:
            attempt.fail("interrupted")
        _changed.wait_for(lambda: attempt.state != "finishing", timeout=FINISH_WAIT)
        if attempt.state == "finishing":
            attempt.fail("unfollowed")
        done = attempt.state == "done"
    return {**view(engine), **({"state": "done"} if done else {})}


def cancel(engine: str, attempt_id: str | None = None) -> dict:
    """Close the open sign-in (or the named one), or dismiss a failure."""
    _check(engine)
    with _lock:
        attempt = _open.get(engine)
        if attempt is not None and attempt_id in (None, attempt.id):
            attempt.state = "cancelled"
            attempt.end()
            del _open[engine]
            _changed.notify_all()
    return view(engine)
