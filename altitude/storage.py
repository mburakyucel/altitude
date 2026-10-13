"""Descriptor-relative runtime storage shared by task documents, fixed briefs and project locks."""
from __future__ import annotations

import os
import stat
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from . import platform


class StorageRefused(OSError):
    """A runtime component or leaf cannot be admitted as the requested kind."""


class NotRegularFile(StorageRefused):
    pass


class OrdinaryNonDirectory(NotADirectoryError):
    """An ordinary file occupies a directory component used by a directory predicate."""


def _component(name: str) -> None:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise StorageRefused("invalid relative storage path")


@contextmanager
def directory(root: Path | int, parts: list[str], *, create: bool = False, search: bool = False,
              mode: int = 0o700):
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
            if create:
                try:
                    os.mkdir(part, mode=mode, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    if not search:
                        os.fsync(fd)
            try:
                child = os.open(part, flags, dir_fd=fd)
            except NotADirectoryError as exc:
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    raise OrdinaryNonDirectory(exc.errno, exc.strerror, exc.filename) from exc
                raise StorageRefused("runtime directory component refused") from exc
            parent, fd = fd, child
            os.close(parent)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def runtime_directory(parts: list[str], *, create: bool = False, search: bool = False,
                      mode: int = 0o700):
    """Use the canonical startup anchor while keeping configured paths for identity and display."""
    from . import config
    with ExitStack() as stack:
        try:
            root = stack.enter_context(directory(Path("/"), list(config.RUNTIME_ROOT.parts[1:]),
                                                 create=create, search=True, mode=mode))
        except OrdinaryNonDirectory as exc:
            raise StorageRefused("runtime root anchor refused") from exc
        with directory(root, parts, create=create, search=True, mode=mode) as held:
            if search:
                yield held
            else:
                # Ancestors need search only. Reopen the pinned final directory for listing or fsync.
                with directory(held, [], search=False) as readable:
                    yield readable


def runtime_parts(path: Path) -> list[str]:
    """Classify only an explicitly selected runtime path, without resolving mutable descendants."""
    from . import config
    try:
        parts = list(Path(path).relative_to(config.ROOT).parts)
    except ValueError as exc:
        raise StorageRefused("not a runtime record") from exc
    for part in parts:
        _component(part)
    return parts


@contextmanager
def runtime_parent(path: Path, *, create: bool = False, mode: int = 0o700,
                   search: bool = True):
    parts = runtime_parts(path)
    if not parts:
        raise StorageRefused("runtime root is not a leaf")
    with runtime_directory(parts[:-1], create=create, mode=mode, search=search) as parent:
        yield parent, parts[-1]


@contextmanager
def regular_reader(directory: int, name: str):
    """Never follow a leaf link or block on a substituted special file."""
    _component(name)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFile("not a regular file")
        yield fd
    finally:
        os.close(fd)


def read_text(directory: int, name: str) -> str:
    with regular_reader(directory, name) as fd, os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
        return stream.read()


def runtime_read_text(path: Path) -> str:
    with runtime_parent(path) as (parent, name):
        return read_text(parent, name)


def regular_stat(directory: int, name: str) -> os.stat_result:
    _component(name)
    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise NotRegularFile("not a regular file")
    return info


def runtime_regular_stat(path: Path) -> os.stat_result:
    with runtime_parent(path) as (parent, name):
        return regular_stat(parent, name)


def exists(directory: int, name: str, *, kind: str = "file") -> bool:
    _component(name)
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        raise StorageRefused("runtime link refused")
    if kind == "directory":
        if stat.S_ISDIR(info.st_mode):
            return True
        if stat.S_ISREG(info.st_mode):
            return False
        raise StorageRefused("runtime special entry refused")
    if not stat.S_ISREG(info.st_mode):
        raise NotRegularFile("not a regular file")
    return True


def runtime_exists(path: Path, *, kind: str = "file") -> bool:
    try:
        with runtime_parent(path) as (parent, name):
            return exists(parent, name, kind=kind)
    except (FileNotFoundError, OrdinaryNonDirectory):
        return False


def runtime_entry_exists(path: Path) -> bool:
    """Collision check for any existing nonlink task entry, including an ordinary file."""
    try:
        with runtime_parent(path) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise StorageRefused("runtime link refused")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise StorageRefused("runtime special entry refused")
            return True
    except (FileNotFoundError, OrdinaryNonDirectory):
        return False


@contextmanager
def directory_fd_for_sync(parent: int):
    fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def atomic_write(directory: int, name: str, text: str) -> None:
    """Replace a leaf entry without opening it, and sync the held parent."""
    _component(name)
    temporary = f".{name}.{uuid.uuid4().hex}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        with directory_fd_for_sync(directory) as synced:
            os.fsync(synced)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass
        raise


def initialize(directory: int, name: str, text: str) -> None:
    """Create the first registry leaf with its existing umask-based mode."""
    _component(name)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o666, dir_fd=directory)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


def runtime_atomic_write(path: Path, text: str, *, create: bool = True, mode: int = 0o777) -> None:
    with runtime_parent(path, create=create, mode=mode) as (parent, name):
        atomic_write(parent, name, text)


def append(directory: int, name: str, text: str, *, sync: bool = False) -> None:
    _component(name)
    fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                 0o666, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFile("not a regular file")
        with os.fdopen(fd, "a", encoding="utf-8", closefd=False) as stream:
            stream.write(text)
            stream.flush()
            if sync:
                os.fsync(fd)
    finally:
        os.close(fd)


def runtime_append(path: Path, text: str, *, sync: bool = False, mode: int = 0o777) -> None:
    with runtime_parent(path, create=True, mode=mode) as (parent, name):
        append(parent, name, text, sync=sync)


def runtime_unlink(path: Path, *, missing_ok: bool = False) -> None:
    try:
        with runtime_parent(path) as (parent, name):
            unlink(parent, name, missing_ok=missing_ok)
    except FileNotFoundError:
        if not missing_ok:
            raise


def unlink(parent: int, name: str, *, missing_ok: bool = False) -> None:
    _component(name)
    try:
        if not exists(parent, name):
            raise FileNotFoundError(name)
        os.unlink(name, dir_fd=parent)
    except FileNotFoundError:
        if not missing_ok:
            raise


def runtime_mkdir(path: Path, *, mode: int = 0o777) -> None:
    with runtime_parent(path, create=True, mode=mode) as (parent, name):
        os.mkdir(name, mode=mode, dir_fd=parent)


def runtime_rename_dir(source: Path, destination: Path, *, mode: int = 0o777) -> None:
    if not runtime_exists(source, kind="directory"):
        return
    with runtime_parent(source) as (src_parent, src_name), \
            runtime_parent(destination, create=True, mode=mode) as (dst_parent, dst_name):
        os.rename(src_name, dst_name, src_dir_fd=src_parent, dst_dir_fd=dst_parent)


def runtime_directory_stat(path: Path) -> os.stat_result:
    with runtime_parent(path) as (parent, name):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise StorageRefused("not a runtime directory")
        return info


def runtime_listdir(path: Path) -> list[str]:
    with runtime_directory(runtime_parts(path), search=False) as directory_fd:
        return os.listdir(directory_fd)


def create_absent(directory: int, name: str, text: str) -> None:
    """Publish a complete file only when the destination remains absent."""
    _component(name)
    temporary = f".{name}.{uuid.uuid4().hex}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary, dir_fd=directory)


@contextmanager
def lock_file(directory: int, name: str, *, truncate: bool = False, mode: int = 0o600):
    """Create a fixed regular lock leaf without following links or truncating existing bytes."""
    _component(name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | (0 if truncate else os.O_APPEND)
    fd = os.open(name, flags, mode, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFile("not a regular lock file")
        if truncate:
            os.ftruncate(fd, 0)
        yield fd
    finally:
        os.close(fd)
