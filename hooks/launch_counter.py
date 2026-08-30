#!/usr/bin/env python3
"""Authoritative L1 launch accounting, shared by the envelope hook and `alt l1 run` (decision 31, decision 36, R-006).

Decision 31 gave every dispatched session a launch envelope, and `hooks/subagent_cap.py` billed it per *tool call*:
a launch the guard refused, one that died at exec, and one that never reached its engine all cost exactly what a
session that ran cost. Here the bill follows the evidence instead — the `l1-started` event the launcher appends to
the task's own log once the engine process has started — and two pieces keep that honest across the launch window:

  reservation  a slot held from the pre-launch cap check until the engine either starts (settled into a bill) or
               does not (released, no bill). Live reservations count against the cap, so the check-then-launch
               window cannot be raced past the envelope, and a reservation dies with the process holding it.
  settlement   `counts-<key>.json` rewritten from the distinct `l1-started` names, so a phantom count left behind
               by the old tool-call billing shrinks back to the truth while a genuine count at the cap still blocks.

Decision 36 runs through all of it: data that cannot be read, or cannot be trusted, faults and blocks — it never
quietly becomes zero. `started_count` says `None` for "cannot tell", and every caller must treat that as a refusal.

This module is imported two ways — `from hooks import launch_counter` inside Altitude and `import launch_counter`
from a hook run as a script — so it imports nothing from `altitude` and takes the Altitude root as an argument.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

def note_fault(root, message: str) -> None:
    """Leave the line the server tick raises as a system fault — a hook cannot reach the server itself."""
    try:
        monitor = Path(root) / "monitor"
        monitor.mkdir(parents=True, exist_ok=True)
        with open(monitor / "hook-faults.log", "a") as stream:
            stream.write(f"launch_counter.py: {message}\n")
    except OSError:
        pass


def _text(path: Path) -> str:
    """Decode persisted state strictly so corruption cannot masquerade as a smaller authoritative history."""
    return Path(path).read_bytes().decode("utf-8")


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def task_dirs(root, project, task) -> list[Path]:
    """Every home a task has, active first — `state.task_dir`'s precedence, without importing it."""
    if not project or not task:
        return []
    base = Path(root) / str(project)
    return [d for d in (base / "tasks" / str(task), base / "archive" / str(task)) if d.is_dir()]


# ---- authoritative launches --------------------------------------------------------------------------------

def started_names(root, project, task) -> set | None:
    """The distinct L1 sessions this task actually started, or None when that cannot be determined.

    Both logs are read and unioned, never picked between: a slug archived and re-opened keeps an `events.log` in
    each place, and a name recorded in both is still one launch. A `l1-started` line carrying no name cannot be
    deduped across files, so it is keyed by where it was found — it counts once and never merges with another."""
    dirs = task_dirs(root, project, task)
    if not dirs:
        return None
    names: set = set()
    for d in dirs:
        log = d / "events.log"
        if not log.exists():
            continue
        try:
            text = _text(log)
        except (OSError, UnicodeError) as e:
            note_fault(root, f"{log}: events unreadable, launch count unknown: {e}")
            return None
        for lineno, line in enumerate(text.splitlines(), 1):
            try:
                event = json.loads(line)
            except ValueError as e:
                note_fault(root, f"{log}:{lineno}: malformed event, launch count unknown: {e}")
                return None
            if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
                note_fault(root, f"{log}:{lineno}: event is not an object with a string kind, launch count unknown")
                return None
            if event["kind"] != "l1-started":
                continue
            name = event.get("name")
            if not isinstance(name, str) or not name:
                note_fault(root, f"{log}:{lineno}: l1-started event has no valid name, launch count unknown")
                return None
            names.add(("name", name))
    return names


def launch_ledger(root, project, task) -> tuple[int, set] | None:
    """(launches started, the run names among them) in one pass, or None when that cannot be determined.

    `l1.start` needs both — the count for the envelope, the names to tell a real in-flight run from a record whose
    engine never started — and reading the logs twice for them would be one pass too many."""
    names = started_names(root, project, task)
    return None if names is None else (len(names), {n[1] for n in names if n[0] == "name"})


def started_count(root, project, task) -> int | None:
    """How many L1 sessions this task has started, or None when it cannot be told (never 0 as a guess)."""
    names = started_names(root, project, task)
    return None if names is None else len(names)


# ---- reservations ------------------------------------------------------------------------------------------

def _reservations_path(root, project, task) -> Path | None:
    dirs = task_dirs(root, project, task)
    return (dirs[0] / "l1" / "reservations.json") if dirs else None


@contextmanager
def launch_lock(root, project, task):
    """The one critical section: the cap check and the slot it takes, or the bill and the slot it settles.

    Deliberately per task and not `state.project_lock`: engine routing, `git worktree add`, the prompt write and
    the spawn all happen outside it, so one launch never serialises the whole project's writers. flock is not
    reentrant, so nothing called while this is held may take it again."""
    dirs = task_dirs(root, project, task)
    if not dirs:
        yield None
        return
    d = dirs[0] / "l1"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".launch.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True  # somebody else's process, but it exists
    except (OSError, TypeError, ValueError):
        return False


def _read_reservations(root, path: Path) -> dict | None:
    if not path.exists():
        return {}
    try:
        data = json.loads(_text(path))
    except (OSError, ValueError, UnicodeError) as e:
        note_fault(root, f"{path}: reservations unreadable, held slots unknown: {e}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("reservations"), dict):
        note_fault(root, f"{path}: not a reservation table, held slots unknown")
        return None
    return data["reservations"]


def _at(row: dict) -> float | None:
    try:
        return float(row.get("at") or 0)
    except (TypeError, ValueError):
        return None


def reservations(root, project, task, *, now: float | None = None) -> list[dict] | None:
    """Launch slots whose owner process is still running, or None when the table cannot be trusted.

    The owner PID, not wall-clock age, is authoritative. Setup begins before the engine's full timeout, so expiring
    a live reservation at that same timeout creates an over-cap launch window. `now` remains for call compatibility."""
    path = _reservations_path(root, project, task)
    if path is None:
        return []
    table = _read_reservations(root, path)
    if table is None:
        return None
    live = []
    for token, row in table.items():
        if not isinstance(row, dict) or _at(row) is None:
            note_fault(root, f"{path}: malformed reservation {token!r}, held slots unknown")
            return None
        try:
            int(row.get("pid"))
        except (TypeError, ValueError):
            note_fault(root, f"{path}: reservation {token!r} has no valid owner PID, held slots unknown")
            return None
        if not _alive(row.get("pid")):
            continue
        live.append({"token": str(token), **row})
    return live


def reserve(root, project, task, *, name=None, role=None, pid=None) -> str | None:
    """Take a launch slot and return its token. Call inside `launch_lock`, right after the cap check it belongs to."""
    path = _reservations_path(root, project, task)
    if path is None:
        return None
    token = f"{os.getpid()}-{time.time_ns()}"
    table = _read_reservations(root, path)
    if table is None:
        raise RuntimeError(f"cannot reserve a launch slot from unreadable table {path}")
    table = {t: r for t, r in table.items() if isinstance(r, dict) and _alive(r.get("pid"))}
    table[token] = {"at": time.time(), "pid": int(pid or os.getpid()), "name": name, "role": role}
    _atomic_write(path, json.dumps({"reservations": table}, sort_keys=True))
    return token


def hand_over(root, project, task, token, pid) -> None:
    """Move a slot to the process that now owns it — the detached wrapper, once `Popen` has returned its pid."""
    if not token:
        return
    with launch_lock(root, project, task):
        path = _reservations_path(root, project, task)
        if path is None:
            return
        table = _read_reservations(root, path)
        if table is None:
            return
        row = table.get(token)
        if not isinstance(row, dict):
            return
        row["pid"] = int(pid)
        _atomic_write(path, json.dumps({"reservations": table}, sort_keys=True))


def release(root, project, task, token, *, locked: bool = False) -> None:
    """Give a slot back. A launch that was refused, failed to spawn, or never reached its engine leaves nothing
    behind: no reservation, no event, no bill. `locked` when the caller already holds `launch_lock`."""
    if not token:
        return

    def drop():
        path = _reservations_path(root, project, task)
        if path is None or not path.exists():
            return
        table = _read_reservations(root, path)
        if table is None:
            return
        if table.pop(token, None) is None:
            return
        _atomic_write(path, json.dumps({"reservations": table}, sort_keys=True))

    if locked:
        drop()
    else:
        with launch_lock(root, project, task):
            drop()


# ---- the persisted count -----------------------------------------------------------------------------------

def counts_path(root, key) -> Path:
    return Path(root) / "monitor" / f"counts-{key}.json"


def read_counts(root, path) -> dict:
    """The counts document, or an empty one to rebuild from. A file that is missing, corrupt, or parses to
    something that is not an object (`[]` is valid JSON with no keys) is rebuilt rather than indexed into."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(_text(path))
    except (OSError, ValueError, UnicodeError) as e:
        note_fault(root, f"{path.name}: unreadable, rebuilt from events: {e}")
        return {}
    if not isinstance(data, dict):
        note_fault(root, f"{path.name}: a JSON {type(data).__name__} is not a counts object, rebuilt from events")
        return {}
    return data


@contextmanager
def counts_lock(root, path):
    """The same per-counts-file lock used by edit_count.py; yields whether it was acquired."""
    lock = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(path.with_name(f"{path.name}.lock"), "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
    except OSError as e:
        if lock:
            lock.close()
        note_fault(root, f"{Path(path).name}: counts lock unavailable, settlement refused: {e}")
        yield False
        return
    try:
        yield True
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError as e:
            note_fault(root, f"{Path(path).name}: counts unlock failed: {e}")
        lock.close()


def settle_counts(root, path, project, task, cap, *, locked: bool = False) -> int | None:
    """Rewrite the persisted launch count from the authoritative events; returns it, or None when it is unknown.

    Idempotent, and the only writer of `subagent_launches` inside a managed task: a phantom count left by the old
    per-tool-call billing shrinks to the number of distinct `l1-started` names, and a genuine count at or above the
    cap survives untouched so it keeps blocking. Every other key in the file (edits, files) is left alone."""
    if not locked:
        with counts_lock(root, path) as acquired:
            if not acquired:
                return None
            return settle_counts(root, path, project, task, cap, locked=True)
    started = started_count(root, project, task)
    if started is None:
        note_fault(root, f"{project}/{task}: launch count unknown, {Path(path).name} left untouched")
        return None
    counts = read_counts(root, path)
    counts["subagent_launches"] = started
    counts["cap"] = int(cap)
    _atomic_write(path, json.dumps(counts))
    return started
