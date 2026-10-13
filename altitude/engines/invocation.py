"""Request-local readers for task-owned invocation records and their stdout."""
from __future__ import annotations

import errno
import json
import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .. import config, storage


class InvocationRefused(ValueError):
    """A task invocation entry no longer belongs to the inspected namespace."""


class RuntimeSources(list):
    def __init__(self, items: list, job_identity: tuple[int, int] | None):
        super().__init__(items)
        self.job_identity = job_identity


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _linked(directory: int, name: str) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return False


@contextmanager
def job_directory(project: str, slug: str):
    """Select the logical live or archive invocation, holding its task and job directories."""
    storage._component(slug)
    with ExitStack() as stack:
        try:
            root = stack.enter_context(storage.runtime_directory([project], search=False))
        except OSError as exc:
            raise InvocationRefused("invocation ancestry refused") from exc
        try:
            live_entry = os.stat("tasks", dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            live_entry = None
        except OSError as exc:
            raise InvocationRefused("live tasks entry refused") from exc
        if live_entry and stat.S_ISLNK(live_entry.st_mode):
            raise InvocationRefused("linked tasks entry")
        live_parent = None
        entry = None
        if live_entry and stat.S_ISDIR(live_entry.st_mode):
            try:
                live_parent = stack.enter_context(storage.directory(root, ["tasks"], search=False))
                if _identity(os.fstat(live_parent)) != _identity(live_entry):
                    raise InvocationRefused("live tasks directory replaced")
                try:
                    entry = os.stat(slug, dir_fd=live_parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
            except OSError as exc:
                raise InvocationRefused("live tasks directory refused") from exc
        if entry and stat.S_ISLNK(entry.st_mode):
            raise InvocationRefused("linked task entry")
        location = "tasks" if entry and stat.S_ISDIR(entry.st_mode) else "archive"
        display = config.project_dir(project) / location / slug / "l2-engine"
        parent = live_parent if location == "tasks" else None
        try:
            if parent is None:
                parent = stack.enter_context(storage.directory(root, [location], search=False))
            task = stack.enter_context(storage.directory(parent, [slug], search=False))
            if location == "tasks" and entry is not None and _identity(os.fstat(task)) != _identity(entry):
                raise InvocationRefused("live task directory replaced")
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ENOTDIR):
                raise InvocationRefused("task directory refused") from exc
            if location == "tasks":
                raise InvocationRefused("live task directory changed") from exc
            if _linked(root, location) or (parent is not None and _linked(parent, slug)):
                raise InvocationRefused("task directory refused") from exc
            yield None, display
            return
        try:
            job = stack.enter_context(storage.directory(task, ["l2-engine"], search=False))
        except OSError as exc:
            if _linked(task, "l2-engine") or exc.errno not in (errno.ENOENT, errno.ENOTDIR):
                raise InvocationRefused("invocation directory refused") from exc
            yield None, display
            return
        yield job, display


@dataclass
class RuntimeInvocation:
    job: int
    root: Path
    worker: str
    record: dict
    record_identity: tuple[int, int]
    stdout_identity: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self.root / f"{self.worker}.stdout.jsonl"

    def __str__(self) -> str:
        return str(self.path)

    def _open(self, name: str, expected: tuple[int, int] | None = None):
        try:
            reader = storage.regular_reader(self.job, name)
            fd = reader.__enter__()
        except (OSError, storage.NotRegularFile) as exc:
            if _linked(self.job, name) or isinstance(exc, storage.NotRegularFile):
                raise InvocationRefused("invocation leaf refused") from exc
            raise
        try:
            info = os.fstat(fd)
        except BaseException:
            reader.__exit__(None, None, None)
            raise
        if expected is not None and _identity(info) != expected:
            reader.__exit__(None, None, None)
            raise InvocationRefused("invocation leaf replaced")
        return reader, fd, info

    def _record_check(self) -> None:
        reader, _, _ = self._open(f"{self.worker}.json", self.record_identity)
        reader.__exit__(None, None, None)

    def discover_stdout(self, *, header: bool) -> str | None:
        """Read only the historical initialization header; save the opened identity."""
        try:
            reader, fd, info = self._open(f"{self.worker}.stdout.jsonl")
        except OSError:
            return None
        self.stdout_identity = _identity(info)
        thread = None
        try:
            if header:
                with os.fdopen(os.dup(fd), "r", encoding="utf-8", errors="replace", newline=None) as stream:
                    for line in stream:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and event.get("type") == "thread.started" and event.get("thread_id"):
                            thread = str(event["thread_id"])
                            break
                        if isinstance(event, dict) and str(event.get("type", "")).startswith(("item.", "turn.")):
                            break
        finally:
            reader.__exit__(None, None, None)
        return thread

    def stat(self) -> tuple:
        if self.stdout_identity is None:
            return ()
        try:
            reader, _, info = self._open(f"{self.worker}.stdout.jsonl", self.stdout_identity)
        except OSError:
            return ()
        try:
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        finally:
            reader.__exit__(None, None, None)

    def read(self) -> tuple[bytes, os.stat_result]:
        if self.stdout_identity is None:
            raise FileNotFoundError("invocation stdout unavailable at discovery")
        self._record_check()
        reader, fd, info = self._open(f"{self.worker}.stdout.jsonl", self.stdout_identity)
        try:
            with os.fdopen(os.dup(fd), "rb") as stream:
                return stream.read(), info
        finally:
            reader.__exit__(None, None, None)


@dataclass
class NativeSession:
    """Existing external adapter selection, with no new provenance assertion."""
    path: Path
    record: dict

    def __str__(self) -> str:
        return str(self.path)

    def stat(self) -> tuple:
        try:
            info = self.path.stat()
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        except OSError:
            return ()

    def read(self) -> tuple[bytes, None]:
        data = self.path.read_bytes()
        return data, None


def invocation(job: int, root: Path, worker: str) -> RuntimeInvocation | None:
    """Parse a flat enumerated record exactly once through a regular no-follow descriptor."""
    try:
        storage._component(worker)
        with storage.regular_reader(job, f"{worker}.json") as fd:
            info = os.fstat(fd)
            with os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
                text = stream.read()
    except FileNotFoundError:
        return None
    except (OSError, storage.NotRegularFile) as exc:
        if _linked(job, f"{worker}.json") or isinstance(exc, storage.NotRegularFile):
            raise InvocationRefused("invocation record refused") from exc
        raise
    try:
        record = json.loads(text) if text.strip() else None
    except ValueError as exc:
        raise ValueError(f"corrupt JSON in {root / (worker + '.json')}: {exc}") from exc
    if not isinstance(record, dict):
        return None
    return RuntimeInvocation(job, root, worker, record, _identity(info))


def replaced_after_projection(before: list, after: list) -> bool:
    """A fresh fingerprint may observe appends, but not publish bytes from a replaced runtime leaf."""
    if (isinstance(before, RuntimeSources) and isinstance(after, RuntimeSources)
            and before.job_identity is not None and after.job_identity != before.job_identity):
        return True
    current = {str(source): source for source, _ in after if isinstance(source, RuntimeInvocation)}
    for source, _ in before:
        if not isinstance(source, RuntimeInvocation):
            continue
        try:
            source._record_check()
        except OSError:
            pass  # An ordinary disappearance is observed by the next request.
        source.stat()
        fresh = current.get(str(source))
        if fresh is None:
            continue
        if fresh.record_identity != source.record_identity:
            return True
        if (source.stdout_identity is not None and fresh.stdout_identity is not None
                and fresh.stdout_identity != source.stdout_identity):
            return True
    return False
