"""The recorder of a repair started from the app (docs/OPERATIONS.md#repairs).

The service copies this file into the repair's folder and starts it as the repair's own job, outside the service, so
nothing here depends on the service or on the installation a repair may replace: it uses Python's standard library only
and reads everything else from the folder. It holds the one-repair lock while it runs, runs the coding agent one turn
at a time with the operator's queued messages and answers, writes each line the engine reports with the time it
arrived, and ends the session when the agent has reported, the operator stops it or its time is up. When it exits, the
service manager ends its job and every process still in it.

    python3 -I -B recorder.py <repair folder>
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

POLL = 0.5
#: How long the agent has to end after SIGTERM before SIGKILL.
GRACE = 5
#: A line the engine writes that is longer than this is kept as a note, not its text.
LINE_LIMIT = 1024 * 1024


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _passed(moment: str | None) -> bool:
    return bool(moment) and datetime.fromisoformat(moment) <= datetime.now(timezone.utc)


class Record:
    """repair.json under its folder's record.lock, as `altitude.repair` changes it."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.path = folder / "repair.json"

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    @contextmanager
    def changing(self):
        fd = os.open(self.folder / "record.lock", os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            row = self.read()
            yield row
            handle, temporary = tempfile.mkstemp(prefix=".repair.json.", dir=self.folder)
            with os.fdopen(handle, "w") as stream:
                stream.write(json.dumps(row, indent=2, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            os.close(fd)


class Recorder:
    def __init__(self, folder: Path):
        self.folder = folder
        self.record = Record(folder)
        self.spec = json.loads((folder / "session.json").read_text())
        self.agent: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.session: str | None = self.record.read().get("session")
        self.signalled = False

    # ---- the lock --------------------------------------------------------------------------------------------------

    def hold(self) -> int | None:
        """The one-repair lock, held by this process for as long as it runs, and released by the system when it ends,
        however it ends; None when another repair holds it."""
        lock = self.folder.parent / "lock"
        fd = os.open(lock, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        for _ in range(20):  # a moment's read of the lock holds it shared
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.05)
        else:
            os.close(fd)
            return None
        handle, temporary = tempfile.mkstemp(prefix=".running.json.", dir=self.folder.parent)
        with os.fdopen(handle, "w") as stream:
            stream.write(json.dumps({"id": self.folder.name, "route": "app"}) + "\n")
        os.replace(temporary, self.folder.parent / "running.json")
        return fd

    # ---- the agent -------------------------------------------------------------------------------------------------

    def _environment(self) -> dict:
        env = dict(os.environ)
        sign_in = self.spec.get("sign_in")
        if sign_in:  # read at each turn, so a sign-in renewed meanwhile is the one used
            try:
                token = Path(sign_in["file"]).read_text().strip()
            except OSError:
                token = ""
            if token:
                env[sign_in["variable"]] = token
        return env

    def _read(self, stream) -> None:
        """Every line the engine writes, with the time it arrived; the session id from the event that names it."""
        with open(self.folder / "steps.jsonl", "a", encoding="utf-8") as steps:
            for line in stream:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                entry: dict = {"at": now()}
                if len(line) > LINE_LIMIT:
                    entry["note"] = "a line longer than the recorder keeps"
                else:
                    try:
                        entry["event"] = json.loads(line)
                    except ValueError:
                        entry["text"] = line
                event = entry.get("event")
                if (isinstance(event, dict) and self.session is None and event.get("type") == self.spec["session_type"]
                        and isinstance(event.get(self.spec["session_key"]), str)):
                    self.session = event[self.spec["session_key"]]
                    with self.record.changing() as row:
                        row["session"] = self.session
                steps.write(json.dumps(entry, ensure_ascii=False) + "\n")
                steps.flush()

    def _start_turn(self, prompt: str) -> None:
        argv = (self.spec["first"] if self.session is None
                else [self.session if part == "{session}" else part for part in self.spec["resume"]])
        log = open(self.folder / "engine.log", "ab")
        try:
            self.agent = subprocess.Popen(argv, cwd=self.folder, env=self._environment(), stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=log, text=True, start_new_session=True)
        finally:
            log.close()
        self.reader = threading.Thread(target=self._read, args=(self.agent.stdout,), daemon=True)
        self.reader.start()
        try:
            self.agent.stdin.write(prompt)
            self.agent.stdin.close()
        except OSError:
            pass

    def _stop_agent(self) -> None:
        """SIGTERM the agent's process group, then SIGKILL what remains; the job's end takes anything that left it."""
        if self.agent is None:
            return
        for sign, wait in ((signal.SIGTERM, GRACE), (signal.SIGKILL, GRACE)):
            try:
                os.killpg(self.agent.pid, sign)
            except ProcessLookupError:
                break
            try:
                self.agent.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        self._finish_turn()

    def _finish_turn(self) -> int | None:
        code = self.agent.wait() if self.agent is not None else None
        if self.reader is not None:
            self.reader.join(timeout=10)
        self.agent = self.reader = None
        return code

    def _failure(self) -> str:
        """The last line the engine wrote to its error output, with the home folder shortened to ~."""
        try:
            lines = (self.folder / "engine.log").read_text(errors="replace").strip().splitlines()
        except OSError:
            lines = []
        text = (lines[-1] if lines else "").replace(str(Path.home()), "~")[:300]
        return text or "it ended with an error"

    # ---- the session -----------------------------------------------------------------------------------------------

    def _pending(self, row: dict) -> list[dict]:
        return [item for item in row.get("inputs", []) if not item.get("delivered")]

    def end(self, reason: str, detail: str | None = None) -> None:
        """End the session: stop the agent, expire its open questions and record why it ended."""
        self._stop_agent()
        with self.record.changing() as row:
            if row.get("ended"):
                return
            row["ended"] = now()
            row["end"] = reason
            row["state"] = "ended"
            for question in row.get("questions", []):
                if question.get("answer") is None:
                    question.update(answer="expired", answered=row["ended"])
            if reason == "failed":
                row.setdefault("events", []).append({"at": row["ended"], "kind": "failed", "text": detail})

    def run(self) -> int:
        lock = self.hold()
        if lock is None:
            with self.record.changing() as row:
                row.update(state="refused", ended=now(), end="refused")
            return 1
        signal.signal(signal.SIGTERM, self._terminated)
        signal.signal(signal.SIGHUP, self._terminated)
        with self.record.changing() as row:
            row["state"] = "working"
            row["recorder"] = os.getpid()
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001 — the record says why the session ended, never a silent stop
            self.end("failed", f"the repair's recorder stopped: {exc}"[:300])
            raise
        finally:
            os.close(lock)
        return 0

    def _terminated(self, *_):
        self.signalled = True

    def _loop(self) -> None:
        while True:
            row = self.record.read()
            if row.get("ended"):
                return self._stop_agent()
            if self.signalled or row.get("stop"):
                return self.end("stop" if row.get("stop") else "interrupted")
            if _passed(row.get("deadline")):
                return self.end("time")
            if self.agent is not None:
                if self.agent.poll() is None:
                    time.sleep(POLL)
                    continue
                code = self._finish_turn()
                row = self.record.read()
                if row.get("report"):  # the report ends the session, whatever the engine's own exit says
                    return self.end("report")
                if code:
                    return self.end("failed", self._failure())
                if self.session is None:
                    return self.end("failed", "the engine named no session to continue")
            if not self._pending(row):
                if row.get("state") != "waiting":
                    with self.record.changing() as current:
                        current["state"] = "waiting"
                time.sleep(POLL)
                continue
            with self.record.changing() as current:
                pending, delivered = self._pending(current), now()
                for item in pending:
                    item["delivered"] = delivered
                current["state"] = "working"
            self._start_turn("\n\n".join(item["prompt"] for item in pending))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: recorder.py <repair folder>", file=sys.stderr)
        return 2
    return Recorder(Path(sys.argv[1]).resolve()).run()


if __name__ == "__main__":
    sys.exit(main())
