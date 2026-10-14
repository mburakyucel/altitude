"""Descriptor-relative runtime storage: every daemon write, lock and fixed-document read in the runtime root."""
from __future__ import annotations

import errno
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

from . import platform


def _component(name: str) -> None:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("invalid relative storage path")


@contextmanager
def directory(root: Path | int, parts: list[str], *, create: bool = False, search: bool = False):
    """Walk without following links; readable creation descriptors retain directory fsync."""
    flags = (platform.directory_search_access() if search else os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
    # An inherited search descriptor cannot be listed or fsynced. Reopen only its pinned directory.
    if isinstance(root, int):
        fd = os.dup(root) if search else os.open(".", flags, dir_fd=root)
    else:
        fd = os.open(root, flags)
    try:
        for part in parts:
            _component(part)
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                # Only a missing folder is made: a task worker walks folders it cannot write.
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    if not search:
                        os.fsync(fd)
                child = os.open(part, flags, dir_fd=fd)
            parent, fd = fd, child
            os.close(parent)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def runtime_directory(parts: list[str], *, create: bool = False, search: bool = False):
    """Use the canonical startup anchor while keeping configured paths for identity and display."""
    from . import config
    with directory(Path("/"), list(config.RUNTIME_ROOT.parts[1:]), create=create, search=True) as root:
        with directory(root, parts, create=create, search=search) as fd:
            yield fd


class NotRegularFile(ValueError):
    pass


def _regular(fd: int) -> None:
    """A runtime leaf is one regular file: a hard link could name a file outside the runtime root."""
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise NotRegularFile("not a singly linked regular file")


@contextmanager
def regular_reader(directory: int, name: str):
    """Never follow a leaf link or block on a substituted special file."""
    _component(name)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        _regular(fd)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def lock_file(directory: int, name: str):
    """A fixed regular lock leaf, created when missing, without following links. It is opened for reading, which is
    all flock needs, so a task worker that cannot write Altitude's home still takes the locks its reads share."""
    _component(name)
    fd = _lock_open(name, directory)
    try:
        _regular(fd)
        yield fd
    finally:
        os.close(fd)


def runtime_parts(path: Path) -> list[str] | None:
    """The path's components below the runtime root, or None outside it."""
    from . import config
    for root in (config.ROOT, config.RUNTIME_ROOT):
        try:
            return list(Path(path).relative_to(root).parts) or None
        except ValueError:
            pass
    return None


def _runtime_parts(path: Path) -> list[str]:
    parts = runtime_parts(path)
    if parts is None:
        raise ValueError("path outside the runtime root")
    return parts


def open_runtime(path: Path, flags: int) -> int:
    """Open a file in the runtime root without following a link at any component; creation also creates its folders.

    Workers can write the runtime root, so a writer that followed a planted link would write for them outside it.
    Truncation happens only after the leaf is known to be this root's own regular file."""
    *parents, name = _runtime_parts(path)
    _component(name)
    with runtime_directory(parents, create=bool(flags & os.O_CREAT), search=True) as directory:
        fd = _open_at(name, flags & ~os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK, directory)
    try:
        _regular(fd)
        if flags & os.O_TRUNC:
            os.ftruncate(fd, 0)
    except BaseException:
        os.close(fd)
        raise
    return fd


def opener(path, flags: int) -> int:
    """An `open()` opener for a file in the runtime root."""
    return open_runtime(Path(path), flags)


def read_open(path: Path) -> int:
    """A read descriptor for an engine's own record. In the runtime root, where a worker can plant links, it opens
    as `open_runtime` does; elsewhere, such as the operator's own engine folder, as an ordinary file."""
    if runtime_parts(path) is None:
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        return open_runtime(path, os.O_RDONLY)
    except NotRegularFile as exc:
        raise OSError(errno.EINVAL, str(exc), str(path)) from exc


def runtime_lock(path: Path):
    """A lock file in the runtime root as an unbuffered binary handle for flock, opened for reading like
    `lock_file`."""
    *parents, name = _runtime_parts(path)
    _component(name)
    with runtime_directory(parents, create=True, search=True) as directory:
        fd = _lock_open(name, directory)
    try:
        _regular(fd)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb", buffering=0)


def _lock_open(name: str, directory: int) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        return os.open(name, flags, dir_fd=directory)
    except FileNotFoundError:
        return _open_at(name, flags | os.O_CREAT, directory)


def _open_at(name: str, flags: int, directory: int) -> int:
    """Open `name` in `directory`. macOS can refuse a create with "no such file" while another thread creates the
    same name in the same folder; the name exists by then, so a create that loses that race is repeated once."""
    try:
        return os.open(name, flags, 0o600, dir_fd=directory)
    except FileNotFoundError:
        if not flags & os.O_CREAT:
            raise
        return os.open(name, flags, 0o600, dir_fd=directory)


def append(path: Path, text: str, *, sync: bool = False) -> None:
    """Append text to a file in the runtime root."""
    with os.fdopen(open_runtime(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT), "ab", buffering=0) as stream:
        stream.write(text.encode())
        if sync:
            os.fsync(stream.fileno())


def replace(path: Path, data: bytes) -> None:
    """Atomically replace a file in the runtime root, durable once this returns."""
    *parents, name = _runtime_parts(path)
    _component(name)
    with runtime_directory(parents, create=True) as directory:
        temporary = f".{name}.{secrets.token_hex(6)}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
            raise
        os.fsync(directory)


def create(folder: int, parts: list[str], data: bytes) -> None:
    """Write a new file below an open runtime folder, creating the folders on the way; nothing is followed or
    replaced, so a link planted while the folder fills makes the write fail."""
    *parents, name = parts
    _component(name)
    with directory(folder, parents, create=True, search=True) as parent:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def read_text(path: Path) -> str:
    """A whole UTF-8 text file in the runtime root, with universal newlines."""
    with os.fdopen(open_runtime(path, os.O_RDONLY), "r", encoding="utf-8") as stream:
        return stream.read()


def tail(path: Path, size: int) -> str:
    """The last bytes of a file in the runtime root, as text."""
    fd = open_runtime(path, os.O_RDONLY)
    try:
        os.lseek(fd, max(0, os.fstat(fd).st_size - size), os.SEEK_SET)
        return os.read(fd, size).decode(errors="replace")
    finally:
        os.close(fd)
