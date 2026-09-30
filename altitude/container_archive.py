"""Private two-volume backups, executed only by the Linux image's explicit helper.

This is a bounded PAX archive of trusted, self-created data, not an authenticity
format. The final member authenticates neither the operator nor executable data:
it detects incomplete/corrupt transport before a restore can be marked complete.
Host runtime, volume locking and helper confinement belong to platform.py.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile

MARKER = ".altitude-restore.json"
END = "ALTITUDE-COMPLETE.json"
MAX_HEADER = 1024 * 1024
MAX_ENTRIES = 1_000_000
MAX_BYTES = 64 * 1024**3
CHUNK = 1024 * 1024
XATTR = "ALTITUDE.xattrs"


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class BoundedInfo(tarfile.TarInfo):
    # tarfile reads PAX/longname payloads before yielding a member. Bound those
    # allocations too, not just the sizes seen by the extractor (issue543/F8).
    def _proc_pax(self, archive):
        if self.size > MAX_HEADER:
            raise ValueError("Backup metadata exceeds its limit")
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        raise ValueError("Only the backup's PAX format is supported")

    def _proc_sparse(self, archive):
        raise ValueError("Sparse archive records are unsupported")


def _attributes(path: Path) -> dict[str, str]:
    result = {}
    for key in os.listxattr(path, follow_symlinks=False):
        if not (key.startswith("user.") or key in ("system.posix_acl_access", "system.posix_acl_default")):
            raise ValueError(f"Unsupported file attribute {key!r} on {path}")
        result[key] = base64.b64encode(os.getxattr(path, key, follow_symlinks=False)).decode()
    return result


def _metadata(info: tarfile.TarInfo) -> dict:
    return {"name": info.name, "kind": info.type.decode("ascii"), "size": info.size,
            "mode": info.mode, "uid": info.uid, "gid": info.gid,
            "mtime_ns": info.pax_headers.get("ALTITUDE.mtime_ns"),
            "atime_ns": info.pax_headers.get("ALTITUDE.atime_ns"),
            "link": info.linkname, "attrs": info.pax_headers.get(XATTR, "{}")}


class Contents:
    def __init__(self, max_bytes: int, max_entries: int):
        self.digest = hashlib.sha256()
        self.bytes = 0
        self.entries = 0
        self.max_bytes = max_bytes
        self.max_entries = max_entries

    def member(self, info):
        self.entries += 1
        self.bytes += info.size
        if self.entries > self.max_entries or self.bytes > self.max_bytes:
            raise ValueError("Backup exceeds its file or byte limit")
        value = _json(_metadata(info))
        if len(value) > MAX_HEADER:
            raise ValueError("Backup metadata exceeds its limit")
        self.digest.update(len(value).to_bytes(8, "big"))
        self.digest.update(value)

    def result(self):
        return {"format": 1, "entries": self.entries, "bytes": self.bytes,
                "sha256": self.digest.hexdigest()}


class DigestReader:
    def __init__(self, stream, digest):
        self.stream, self.digest = stream, digest

    def read(self, size):
        value = self.stream.read(size)
        self.digest.update(value)
        return value


def export(stream, home: Path, projects: Path, *, max_bytes=MAX_BYTES, max_entries=MAX_ENTRIES) -> dict:
    """Caller holds both directory locks and has proved the controller stopped."""
    contents = Contents(max_bytes, max_entries)
    with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive:
        for root_name, root in (("home", home), ("projects", projects)):
            links = {}

            def visit(path, relative):
                current = path.lstat()
                mode = current.st_mode
                if current.st_uid not in (0, 1000) or current.st_gid not in (0, 1000):
                    raise ValueError(f"Unsupported namespace ownership on {relative}")
                if mode & (stat.S_ISUID | stat.S_ISGID):
                    raise ValueError(f"Set-ID metadata is unsupported: {relative}")
                info = tarfile.TarInfo(relative)
                info.mode = stat.S_IMODE(mode)
                info.uid, info.gid = current.st_uid, current.st_gid
                info.mtime = current.st_mtime_ns // 1_000_000_000
                info.pax_headers = {"ALTITUDE.mtime_ns": str(current.st_mtime_ns),
                    "ALTITUDE.atime_ns": str(current.st_atime_ns), XATTR: _json(_attributes(path)).decode()}
                if stat.S_ISDIR(mode):
                    info.type = tarfile.DIRTYPE
                elif stat.S_ISLNK(mode):
                    if relative == 'home/Projects':
                        raise ValueError('Home Projects mountpoint must be an empty directory')
                    info.type, info.linkname = tarfile.SYMTYPE, os.readlink(path)
                elif stat.S_ISREG(mode):
                    identity = (current.st_dev, current.st_ino)
                    if identity in links:
                        info.type, info.linkname = tarfile.LNKTYPE, links[identity]
                    else:
                        links[identity] = relative
                        info.size = current.st_size
                else:
                    raise ValueError(f"Unsupported file type: {relative}")
                contents.member(info)
                if info.isreg():
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, "rb") as source:
                        observed = os.fstat(source.fileno())
                        if (observed.st_dev, observed.st_ino, observed.st_size) != (current.st_dev, current.st_ino, current.st_size):
                            raise ValueError("Backup source changed while reading")
                        archive.addfile(info, DigestReader(source, contents.digest))
                else:
                    archive.addfile(info)
                if info.isdir():
                    children = sorted(path.iterdir())
                    # Home contains a nested mountpoint in a running controller, but
                    # these helpers mount the two volumes at SEPARATE paths. Hidden
                    # content must not be silently lost or duplicated (review F3).
                    if relative == "home/Projects" and children:
                        raise ValueError("Home volume contains hidden Projects data; backup refused")
                    for child in children:
                        if child.name == MARKER and path == root:
                            continue  # restore authority is never inherited from archive contents
                        visit(child, relative + "/" + child.name)

            visit(root, root_name)
        payload = _json(contents.result())
        final = tarfile.TarInfo(END)
        final.size, final.mode = len(payload), 0o600
        archive.addfile(final, io.BytesIO(payload))
    return contents.result()


def _validate(info: tarfile.TarInfo, seen: dict[str, bytes]):
    parts = PurePosixPath(info.name).parts
    if (not parts or parts[0] not in ("home", "projects") or info.name != "/".join(parts)
            or any(part in (".", "..", "") for part in parts) or "\0" in info.name
            or len(parts) > 128 or info.name in seen or parts[-1] == MARKER):
        raise ValueError("Invalid, duplicate or reserved backup path")
    if len(parts) > 1 and seen.get("/".join(parts[:-1])) != tarfile.DIRTYPE:
        raise ValueError("Backup parent must be an earlier directory")
    if info.uid not in (0, 1000) or info.gid not in (0, 1000) or info.mode & ~0o1777:
        raise ValueError("Unsupported ownership or mode")
    if info.type not in (tarfile.REGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE) or info.sparse:
        raise ValueError("Unsupported archive file type")
    if info.size < 0 or (not info.isreg() and info.size):
        raise ValueError("Invalid archive size")
    if len(parts) == 1 and not info.isdir():
        raise ValueError("Volume root must be a directory")
    if info.name == "home/Projects" and not info.isdir() or info.name.startswith("home/Projects/"):
        raise ValueError("Nested Projects data is not part of the home archive")
    if info.islnk() and (info.linkname.split("/")[0] != parts[0] or seen.get(info.linkname) != tarfile.REGTYPE):
        raise ValueError("Hardlinks require an earlier regular file in the same volume")
    if "\0" in info.linkname or len(info.linkname) > 4096:
        raise ValueError("Invalid link target")
    expected = {"ALTITUDE.mtime_ns", "ALTITUDE.atime_ns", XATTR}
    if not expected <= info.pax_headers.keys() or info.pax_headers.keys() - expected - {"path", "linkpath", "size", "mtime"}:
        raise ValueError("Unknown or missing backup metadata")
    attrs = json.loads(info.pax_headers[XATTR])
    if not isinstance(attrs, dict):
        raise ValueError("Invalid file attributes")
    decoded = {}
    for key, value in attrs.items():
        if not (key.startswith("user.") or key in ("system.posix_acl_access", "system.posix_acl_default")):
            raise ValueError("Unsupported file attribute")
        decoded[key] = base64.b64decode(value, validate=True)
    times = tuple(int(info.pax_headers["ALTITUDE." + key + "_ns"]) for key in ("atime", "mtime"))
    if any(abs(value) >= 2**63 for value in times):
        raise ValueError("Timestamp is outside the supported range")
    seen[info.name] = info.type
    return parts, decoded, times


def _apply(path: Path, info, attrs, times):
    os.chown(path, info.uid, info.gid, follow_symlinks=False)
    if not info.issym():
        os.chmod(path, info.mode, follow_symlinks=False)
    for key, value in attrs.items():
        os.setxattr(path, key, value, follow_symlinks=False)
    os.utime(path, ns=times, follow_symlinks=False)


def restore(stream, home: Path, projects: Path, *, max_bytes=MAX_BYTES, max_entries=MAX_ENTRIES) -> dict:
    """Extract into fresh locked image volumes; never execute restored configuration."""
    roots = {"home": home, "projects": projects}
    for root in roots.values():
        if not stat.S_ISDIR(root.lstat().st_mode) or any(root.iterdir()):
            raise ValueError("Restore requires empty volume directories")
    contents = Contents(max_bytes, max_entries)
    seen, directories, links = {}, [], []
    completed = False
    with tarfile.open(fileobj=stream, mode="r|", tarinfo=BoundedInfo) as archive:
        for info in archive:
            if completed:
                raise ValueError("Data follows the completion record")
            if info.name == END:
                if not info.isreg() or not 0 < info.size <= MAX_HEADER:
                    raise ValueError("Invalid completion record")
                if json.load(archive.extractfile(info)) != contents.result():
                    raise ValueError("Backup content digest or counts do not match")
                completed = True
                continue
            parts, attrs, times = _validate(info, seen)
            contents.member(info)
            target = roots[parts[0]].joinpath(*parts[1:])
            if info.isdir():
                if len(parts) > 1:
                    target.mkdir(mode=0o700)
                directories.append((target, info, attrs, times))
            elif info.issym() or info.islnk():
                links.append((target, info, attrs, times))
            else:
                with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as output:
                    source = archive.extractfile(info)
                    while value := source.read(CHUNK):
                        contents.digest.update(value)
                        output.write(value)
                    output.flush()
                    os.fsync(output.fileno())
                _apply(target, info, attrs, times)
        if not completed or seen.get("home") != tarfile.DIRTYPE or seen.get("projects") != tarfile.DIRTYPE:
            raise ValueError("Backup stream is incomplete")
        # Tar stops at its end marker. Consume and bound the remaining zero padding
        # from its buffered stream, refusing hidden/concatenated archive contents.
        tail = archive.fileobj.read(tarfile.RECORDSIZE * 2)
        if any(tail) or len(tail) >= tarfile.RECORDSIZE * 2:
            raise ValueError("Unexpected data after the backup archive")
    # No symlink exists while regular paths are written. Parents must have been
    # declared directories; hardlinks point only to existing same-volume files.
    for target, info, attrs, times in links:
        if info.islnk():
            parts = PurePosixPath(info.linkname).parts
            os.link(roots[parts[0]].joinpath(*parts[1:]), target, follow_symlinks=False)
        else:
            target.symlink_to(info.linkname)
        _apply(target, info, attrs, times)
    for arguments in reversed(directories):
        _apply(*arguments)
    return contents.result()


def helper(action: str, descriptor: dict):
    """Fixed image entrypoint; stdout is binary for export and silent for restore."""
    from altitude import platform
    import re
    if action not in ('export', 'restore') or os.getuid() != 0 or not platform.containerized():
        raise ValueError('Backup helper requires the explicit container-root image entrypoint')
    for key, length in (('lineage', 32), ('pair', 32), ('archive', 64)):
        if not re.fullmatch('[0-9a-f]{'+str(length)+'}', descriptor.get(key, '')):
            raise ValueError('Invalid backup operation identity')
    home, projects = Path('/backup/home'), Path('/backup/projects')
    status = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    group = Path('/sys/fs/cgroup')
    quota, period = (group/'cpu.max').read_text().split()
    if (status.get('NoNewPrivs','').strip()!='1' or status.get('Seccomp','').strip()!='2'
            or int(status['CapEff'].strip(),16) & ~0xb
            or (group/'memory.max').read_text().strip()!=str(1024**3)
            or (group/'pids.max').read_text().strip()!='64'
            or not quota.isdigit() or int(quota)!=int(period)
            or not os.statvfs('/').f_flag & os.ST_RDONLY):
        raise RuntimeError('Backup helper protections or effective limits are unavailable')
    mounts = {line.split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines()}
    if not {str(home), str(projects)} <= mounts:
        raise ValueError('Mount the two separate named volumes for the backup helper')
    with platform.container_volume_locks(home, projects):
        if action == 'export':
            export(sys.stdout.buffer, home, projects)
            sys.stdout.buffer.flush()
        else:
            digest = hashlib.sha256()
            result = restore(DigestReader(sys.stdin.buffer, digest), home, projects)
            if digest.hexdigest() != descriptor['archive']:
                raise ValueError('The complete input archive differs from its manifest')
            marker = _json({'format': 1, **descriptor})
            for root in (home, projects):
                with (root/MARKER).open('xb') as target:
                    os.fchmod(target.fileno(), 0o600)
                    target.write(marker); target.flush(); os.fsync(target.fileno())
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(fd)
                finally: os.close(fd)


if __name__ == '__main__':
    try:
        helper(sys.argv[1], json.loads(sys.argv[2]))
    except Exception:
        # Never include restored names/content or source exceptions in runtime logs.
        print('Private backup helper refused or failed; no completed restore is admitted', file=sys.stderr)
        raise SystemExit(1) from None
