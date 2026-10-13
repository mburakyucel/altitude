"""Descriptor-relative runtime storage shared by task documents, fixed briefs and project locks."""
from __future__ import annotations

import os
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
            if create:
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


@contextmanager
def lock_file(directory: int, name: str):
    """Create a fixed regular lock leaf without following links or truncating existing bytes."""
    _component(name)
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFile("not a regular lock file")
        yield fd
    finally:
        os.close(fd)
