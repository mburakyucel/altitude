"""Bounded validation inputs and evidence; no repository configuration crosses the boundary.

The wire envelope is SHA-256 plus base64(zlib(JSON)). Git objects retain their exact
bytes, so attributes and checkout filters cannot change the candidate. Only the
selected commit and its ordinary-file tree are carried; its parents are shallow.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
import zlib

COMPRESSED_LIMIT = 64 << 20
EXPANDED_LIMIT = 256 << 20
FILE_LIMIT = 256 << 20
ENTRY_LIMIT = 10000
PATH_LIMIT = 1024
DEPTH_LIMIT = 64
EXPORT_TIMEOUT = 60
_OID = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"[A-Za-z0-9_. @+,-]+\Z")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode(data: object, limit: int) -> bytes:
    if not isinstance(data, str) or len(data) > 4 * ((limit + 2) // 3):
        raise ValueError("Validation payload exceeds its size limit")
    try:
        result = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid validation payload encoding") from None
    if len(result) > limit:
        raise ValueError("Validation payload exceeds its size limit")
    return result


def _pack(value: dict) -> dict:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    if len(raw) > EXPANDED_LIMIT:
        raise ValueError("Validation payload exceeds its expanded size limit")
    data = zlib.compress(raw)
    if len(data) > COMPRESSED_LIMIT:
        raise ValueError("Validation payload exceeds its compressed size limit")
    return {"digest": hashlib.sha256(data).hexdigest(), "data": _b64(data)}


def _unique(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate validation payload entry")
        result[key] = value
    return result


def _unpack(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Invalid validation payload")
    data = _decode(payload.get("data"), COMPRESSED_LIMIT)
    if hashlib.sha256(data).hexdigest() != payload.get("digest"):
        raise ValueError("Validation payload digest mismatch")
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(data, EXPANDED_LIMIT + 1)
        if len(raw) > EXPANDED_LIMIT or not inflater.eof or inflater.unused_data:
            raise ValueError("Invalid or oversized compressed validation payload")
        value = json.loads(raw, object_pairs_hook=_unique)
    except (zlib.error, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Invalid compressed validation payload") from None
    if not isinstance(value, dict):
        raise ValueError("Invalid validation payload document")
    return value


def _path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > PATH_LIMIT:
        raise ValueError("Invalid validation entry path")
    parts = value.split("/")
    if len(parts) > DEPTH_LIMIT or any(
        len(part) > 255 or not _NAME.fullmatch(part) or part in {".", ".."}
        or part.lower() == ".git" or part.endswith((".", " "))
        for part in parts
    ):
        raise ValueError("Unsafe validation entry path")
    return value


def _git(worktree: Path, *args: str, deadline: float | None = None) -> bytes:
    # Never inherit replacement refs, injected config, credentials or lazy fetching.
    env = {"PATH": os.defpath, "HOME": "/dev/null", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
           "GIT_TERMINAL_PROMPT": "0"}
    now = time.monotonic()
    deadline = min(deadline, now + 60) if deadline is not None else now + 60
    if deadline <= now:
        raise ValueError("Candidate Git read timed out")
    try:
        process = subprocess.Popen(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "protocol.allow=never", "-C", str(worktree), *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("Candidate Git read timed out")
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fileobj.fileno(), 1 << 20)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if len(output) + len(chunk) > FILE_LIMIT:
                            raise ValueError("Candidate Git output exceeds its size limit")
                        output.extend(chunk)
                if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                    raise ValueError("Cannot read validation candidate Git objects")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Cannot read validation candidate Git objects") from None
    return bytes(output)


def _identity(kind: str, data: bytes) -> str:
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _tree_entries(data: bytes) -> list[tuple[str, str, str]]:
    entries = []
    position = 0
    previous = b""
    while position < len(data):
        if len(entries) >= ENTRY_LIMIT:
            raise ValueError("Candidate exceeds its tree entry budget")
        try:
            space = data.index(b" ", position)
            end = data.index(b"\0", space)
            mode = data[position:space].decode("ascii")
            name = data[space + 1:end].decode("ascii")
        except (ValueError, UnicodeError):
            raise ValueError("Invalid candidate tree") from None
        if mode not in {"40000", "100644", "100755"} or "/" in name:
            raise ValueError("Candidate contains a link or unsupported tree entry")
        _path(name)
        ordering = name.encode() + (b"/" if mode == "40000" else b"")
        if ordering <= previous or end + 21 > len(data):
            raise ValueError("Invalid candidate tree order or object identity")
        previous = ordering
        entries.append((mode, name, data[end + 1:end + 21].hex()))
        position = end + 21
    return entries


def _candidate(commit: str, tree: str, read) -> tuple[dict, dict]:
    if not isinstance(commit, str) or not _OID.fullmatch(commit):
        raise ValueError("Invalid candidate commit")
    if not isinstance(tree, str) or not _OID.fullmatch(tree):
        raise ValueError("Invalid candidate tree")
    objects: dict[str, tuple[str, bytes]] = {}
    files: dict[str, tuple[str, bytes]] = {}
    total = 0
    file_total = 0
    names: set[str] = set()

    def get(oid: str, kind: str) -> bytes:
        nonlocal total
        if oid not in objects:
            raw = read(oid, kind)
            total += len(raw)
            if len(objects) >= ENTRY_LIMIT or total > EXPANDED_LIMIT:
                raise ValueError("Candidate exceeds its object budget")
            if _identity(kind, raw) != oid:
                raise ValueError("Candidate Git object identity mismatch")
            objects[oid] = (kind, raw)
        actual, raw = objects[oid]
        if actual != kind:
            raise ValueError("Candidate Git object type mismatch")
        return raw

    raw_commit = get(commit, "commit")
    if not raw_commit.startswith(b"tree " + tree.encode() + b"\n"):
        raise ValueError("Candidate commit and tree disagree")

    def walk(oid: str, prefix: str = "") -> None:
        nonlocal file_total
        for mode, name, child in _tree_entries(get(oid, "tree")):
            path = _path(prefix + name)
            folded = path.lower()
            if folded in names or len(names) >= ENTRY_LIMIT:
                raise ValueError("Duplicate or excessive candidate paths")
            names.add(folded)
            if mode == "40000":
                files[path] = (mode, b"")
                walk(child, path + "/")
            else:
                raw = get(child, "blob")
                file_total += len(raw)
                if file_total > EXPANDED_LIMIT:
                    raise ValueError("Candidate exceeds its working file budget")
                files[path] = (mode, raw)

    walk(tree)
    return objects, files


def export(worktree: Path) -> dict:
    """Export committed HEAD only; tracked working edits and other refs stay local."""
    deadline = time.monotonic() + EXPORT_TIMEOUT
    commit = _git(worktree, "rev-parse", "--verify", "HEAD^{commit}", deadline=deadline).decode().strip()
    tree = _git(worktree, "rev-parse", "--verify", commit + "^{tree}", deadline=deadline).decode().strip()

    def read(oid: str, kind: str) -> bytes:
        size = int(_git(worktree, "cat-file", "-s", oid, deadline=deadline))
        if size > FILE_LIMIT:
            raise ValueError("Candidate object exceeds its size limit")
        return _git(worktree, "cat-file", kind, oid, deadline=deadline)

    objects, _ = _candidate(commit, tree, read)
    if time.monotonic() >= deadline:
        raise ValueError("Candidate export timed out")
    result = {"commit": commit, "tree": tree, **_pack({
        "objects": {oid: [kind, _b64(raw)] for oid, (kind, raw) in objects.items()},
    })}
    if time.monotonic() >= deadline:
        raise ValueError("Candidate export timed out")
    return result


def _destination(destination: Path) -> None:
    # Callers choose a private staging directory; refuse even pre-existing empty links.
    if destination.is_symlink():
        raise ValueError("Unsafe validation destination")
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise ValueError("Validation destination must be empty")
    else:
        destination.mkdir(parents=True, mode=0o700)


def restore(payload: dict, destination: Path) -> None:
    """Verify all object identities and the complete tree before writing any files."""
    document = _unpack(payload)
    encoded = document.get("objects")
    if set(document) != {"objects"} or not isinstance(encoded, dict) or len(encoded) > ENTRY_LIMIT:
        raise ValueError("Invalid candidate object inventory")

    def read(oid: str, kind: str) -> bytes:
        value = encoded.get(oid)
        if not isinstance(value, list) or len(value) != 2 or value[0] != kind:
            raise ValueError("Missing or invalid candidate object")
        return _decode(value[1], FILE_LIMIT)

    commit, tree = payload.get("commit"), payload.get("tree")
    objects, files = _candidate(commit, tree, read)
    if set(objects) != set(encoded):
        raise ValueError("Candidate contains unrelated objects")
    try:
        _destination(destination)
        git = destination / ".git"
        (git / "objects").mkdir(parents=True)
        (git / "refs").mkdir()
        (git / "HEAD").write_text(commit + "\n")
        (git / "shallow").write_text(commit + "\n")
        (git / "config").write_text("[core]\n\trepositoryformatversion = 0\n\tbare = false\n")
        for oid, (kind, raw) in objects.items():
            target = git / "objects" / oid[:2] / oid[2:]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(zlib.compress(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw))
        for name, (mode, raw) in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "40000":
                target.mkdir(exist_ok=True)
                continue
            target.write_bytes(raw)
            target.chmod(0o755 if mode == "100755" else 0o644)
        _git(destination, "read-tree", commit)
    except OSError:
        raise ValueError("Cannot restore validation candidate") from None


def collect_results(directory: Path) -> dict:
    """Capture regular evidence through no-follow descriptors, or fail as incomplete."""
    entries: dict[str, list] = {}
    total = 0

    def walk(fd: int, prefix: str = "") -> None:
        nonlocal total
        before_directory = os.fstat(fd)
        for name in sorted(os.listdir(fd)):
            path = _path(prefix + name)
            if len(entries) >= ENTRY_LIMIT:
                raise ValueError("Validation evidence exceeds its entry limit")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                entries[path] = ["dir"]
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, path + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(child, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise ValueError("Unsafe validation evidence file")
                    if before.st_size > FILE_LIMIT or total + before.st_size > EXPANDED_LIMIT:
                        raise ValueError("Validation evidence exceeds its size limit")
                    raw = stream.read(min(FILE_LIMIT, EXPANDED_LIMIT - total) + 1)
                    after = os.fstat(stream.fileno())
                    if (len(raw) != before.st_size or before.st_mtime_ns != after.st_mtime_ns
                            or before.st_ctime_ns != after.st_ctime_ns):
                        raise ValueError("Validation evidence changed during collection")
                total += len(raw)
                entries[path] = ["file", _b64(raw)]
            else:
                raise ValueError("Validation evidence contains a link or special file")
        after_directory = os.fstat(fd)
        if (before_directory.st_mtime_ns != after_directory.st_mtime_ns
                or before_directory.st_ctime_ns != after_directory.st_ctime_ns):
            raise ValueError("Validation evidence directory changed during collection")

    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            walk(fd)
        finally:
            os.close(fd)
    except OSError:
        raise ValueError("Cannot collect complete validation evidence") from None
    return _pack({"entries": entries})


def restore_results(payload: dict, destination: Path) -> None:
    """Validate the entire inventory before restoring it to a private empty directory."""
    document = _unpack(payload)
    entries = document.get("entries")
    if set(document) != {"entries"} or not isinstance(entries, dict) or len(entries) > ENTRY_LIMIT:
        raise ValueError("Invalid validation evidence inventory")
    files = {}
    names = set()
    total = 0
    for name, value in entries.items():
        _path(name)
        if name.lower() in names or not isinstance(value, list):
            raise ValueError("Duplicate or invalid validation evidence entry")
        names.add(name.lower())
        if value == ["dir"]:
            continue
        if len(value) != 2 or value[0] != "file":
            raise ValueError("Invalid validation evidence file")
        raw = _decode(value[1], FILE_LIMIT)
        total += len(raw)
        if total > EXPANDED_LIMIT:
            raise ValueError("Validation evidence exceeds its size limit")
        files[name] = raw
    for name in entries:
        parts = name.split("/")
        for end in range(1, len(parts)):
            if entries.get("/".join(parts[:end])) != ["dir"]:
                raise ValueError("Incomplete validation evidence directory inventory")
    try:
        _destination(destination)
        for name in sorted(entries, key=lambda name: (name.count("/"), name)):
            if name in files:
                (destination / name).write_bytes(files[name])
            else:
                (destination / name).mkdir()
    except OSError:
        raise ValueError("Cannot restore validation evidence") from None
