"""Private conversation images. Call mutations and reference reads under the project lock.

The existing conversation/queue/task records own retention; files without one of those
references are staging, never a second attachment registry or a filesystem download API.
"""
from __future__ import annotations

import base64
import binascii
import ctypes.util
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zlib
from functools import lru_cache
from pathlib import Path

from . import config, state as S

MAX_IMAGES = 4
MAX_BYTES = 10 << 20
MAX_TOTAL_BYTES = 20 << 20
MAX_BODY = 28 << 20
MAX_PROFILE_BYTES = 4 << 20
MAX_PIXELS = 25_000_000
MAX_SIDE = 8192
ORPHAN_SECONDS = 24 * 60 * 60
PROCESS_TIMEOUT = 15
_ID = re.compile(r"[0-9a-f]{32}\Z")
_PNG = b"\x89PNG\r\n\x1a\n"


class ImageError(ValueError):
    """A bounded, operator-readable failure with an HTTP status."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@lru_cache(maxsize=8)
def _codecs(binary: str, modified: int) -> bool:
    del modified  # A replaced converter invalidates this capability observation.
    try:
        result = subprocess.run([binary, "-hide_banner", "-codecs"], capture_output=True,
                                text=True, timeout=5)
        rows = {parts[1]: parts[0] for line in result.stdout.splitlines()
                if len(parts := line.split()) >= 2}
        return result.returncode == 0 and all(rows.get(c, "").startswith("DE")
                                             for c in ("png", "mjpeg")) and rows.get("webp", "").startswith("D")
    except (OSError, subprocess.TimeoutExpired):
        return False


def capability() -> dict:
    binary = shutil.which("ffmpeg")
    try:
        available = bool(binary and _codecs(binary, os.stat(binary).st_mtime_ns))
    except OSError:
        available = False
    return {"available": available,
            "reason": None if available else "Image input unavailable: the local image converter is unavailable."}


def _invalid() -> ImageError:
    return ImageError("This image could not be read. Export a PNG, JPEG or static WebP and try again.")


def _color_profile() -> ImageError:
    return ImageError("This image's color profile is unsupported. Export an sRGB image without an embedded color profile.", 415)


def _orientation(raw: bytes) -> int:
    """Read only TIFF's orientation scalar; every metadata block is discarded after decode."""
    raw = raw.removeprefix(b"Exif\x00\x00")
    if len(raw) < 8 or raw[:2] not in (b"II", b"MM"):
        return 1
    endian = "<" if raw[:2] == b"II" else ">"
    if struct.unpack_from(endian + "H", raw, 2)[0] != 42:
        return 1
    start = struct.unpack_from(endian + "I", raw, 4)[0]
    if start + 2 > len(raw):
        return 1
    count = struct.unpack_from(endian + "H", raw, start)[0]
    if start + 2 + 12 * count > len(raw):
        return 1
    for offset in range(start + 2, start + 2 + 12 * count, 12):
        tag, kind, size = struct.unpack_from(endian + "HHI", raw, offset)
        if (tag, kind, size) == (274, 3, 1):
            orientation = struct.unpack_from(endian + "H", raw, offset + 8)[0]
            return orientation if 1 <= orientation <= 8 else 1
    return 1


def _inspect(raw: bytes) -> tuple[str, int, int, int, bytes]:
    """Bound dimensions and reject animation/truncation before invoking a raster decoder."""
    width = height = 0
    orientation = 1
    profile, profile_parts, profile_count = b"", {}, 0
    needs_profile = False
    if raw.startswith(_PNG):
        mime, offset, seen = "image/png", 8, []
        while offset + 12 <= len(raw):
            size = int.from_bytes(raw[offset:offset + 4], "big")
            kind = raw[offset + 4:offset + 8]
            end = offset + 12 + size
            if end > len(raw) or zlib.crc32(raw[offset + 4:end - 4]) != int.from_bytes(raw[end - 4:end], "big"):
                raise _invalid()
            data = raw[offset + 8:end - 4]
            if kind in (b"acTL", b"fcTL", b"fdAT"):
                raise ImageError("Animated images are not supported. Export a still PNG, JPEG or WebP.", 415)
            if kind == b"iCCP":
                name, separator, compressed = data.partition(b"\0")
                if profile or not separator or not 1 <= len(name) <= 79 or compressed[:1] != b"\0":
                    raise _invalid()
                try:
                    decoder = zlib.decompressobj()
                    profile = decoder.decompress(compressed[1:], MAX_PROFILE_BYTES + 1)
                    if len(profile) > MAX_PROFILE_BYTES or not decoder.eof or decoder.unused_data:
                        raise _color_profile()
                except zlib.error as exc:
                    raise _invalid() from exc
            if ((kind == b"gAMA" and data != struct.pack(">I", 45455))
                    or (kind == b"cHRM" and data != struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000))):
                needs_profile = True
            if kind in (b"mDCv", b"cLLi") or (kind == b"cICP" and data != bytes((1, 13, 0, 1))):
                raise _color_profile()
            if kind == b"IHDR":
                if seen or size != 13:
                    raise _invalid()
                width, height = struct.unpack_from(">II", data)
            if kind == b"eXIf":
                orientation = _orientation(data)
            seen.append(kind)
            offset = end
            if kind == b"IEND":
                if size or offset != len(raw):
                    raise _invalid()
                break
        if not seen or seen[0] != b"IHDR" or seen[-1] != b"IEND" or b"IDAT" not in seen:
            raise _invalid()
    elif raw.startswith(b"\xff\xd8"):
        mime, offset, ended, scans = "image/jpeg", 2, False, 0
        while offset < len(raw):
            if raw[offset] != 255:
                raise _invalid()
            while offset < len(raw) and raw[offset] == 255:
                offset += 1
            if offset >= len(raw):
                raise _invalid()
            marker, offset = raw[offset], offset + 1
            if marker == 0xD9:
                ended = offset == len(raw)
                break
            if marker in (0, 0xD8) or 0xD0 <= marker <= 0xD7 or offset + 2 > len(raw):
                raise _invalid()
            size = int.from_bytes(raw[offset:offset + 2], "big")
            if size < 2 or offset + size > len(raw):
                raise _invalid()
            data, offset = raw[offset + 2:offset + size], offset + size
            if marker in (0xC0, 0xC1, 0xC2):
                if len(data) < 6 or width:
                    raise _invalid()
                height, width = struct.unpack_from(">HH", data, 1)
            if marker == 0xE1 and data.startswith(b"Exif\x00\x00"):
                orientation = _orientation(data)
            if marker == 0xE2 and data.startswith(b"MPF\x00"):
                raise ImageError("Multiple-picture images are not supported. Export a single still image.", 415)
            if marker == 0xE2 and data.startswith(b"ICC_PROFILE\x00"):
                if len(data) < 14 or not 1 <= data[12] <= data[13] or data[12] in profile_parts:
                    raise _invalid()
                if profile_count and profile_count != data[13]:
                    raise _invalid()
                profile_count = data[13]
                profile_parts[data[12]] = data[14:]
            if marker == 0xDA:
                scans += 1
                while offset < len(raw):
                    if raw[offset] == 255:
                        if offset + 1 >= len(raw):
                            raise _invalid()
                        if raw[offset + 1] == 0 or 0xD0 <= raw[offset + 1] <= 0xD7:
                            offset += 2
                            continue
                        break
                    offset += 1
        if not ended or not scans:
            raise _invalid()
        if profile_count:
            if set(profile_parts) != set(range(1, profile_count + 1)):
                raise _invalid()
            profile = b"".join(profile_parts[i] for i in range(1, profile_count + 1))
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime, offset, frames, canvas = "image/webp", 12, 0, None
        if int.from_bytes(raw[4:8], "little") + 8 != len(raw):
            raise _invalid()
        while offset + 8 <= len(raw):
            kind, size = raw[offset:offset + 4], int.from_bytes(raw[offset + 4:offset + 8], "little")
            end = offset + 8 + size
            if end + size % 2 > len(raw):
                raise _invalid()
            data, offset = raw[offset + 8:end], end + size % 2
            if kind in (b"ANIM", b"ANMF") or (kind == b"VP8X" and data and data[0] & 2):
                raise ImageError("Animated images are not supported. Export a still PNG, JPEG or WebP.", 415)
            if kind == b"ICCP":
                if profile:
                    raise _invalid()
                profile = data
            if kind == b"EXIF":
                orientation = _orientation(data)
            if kind == b"VP8X":
                if size != 10 or canvas is not None or frames:
                    raise _invalid()
                canvas = (int.from_bytes(data[4:7], "little") + 1, int.from_bytes(data[7:10], "little") + 1)
            if kind in (b"VP8 ", b"VP8L"):
                frames += 1
                if kind == b"VP8 " and len(data) >= 10 and data[3:6] == b"\x9d\x01\x2a":
                    width, height = (n & 0x3fff for n in struct.unpack_from("<HH", data, 6))
                elif kind == b"VP8L" and len(data) >= 5 and data[0] == 0x2F:
                    packed = int.from_bytes(data[1:5], "little")
                    width, height = (packed & 0x3FFF) + 1, ((packed >> 14) & 0x3FFF) + 1
                else:
                    raise _invalid()
        if offset != len(raw) or frames != 1 or (canvas is not None and canvas != (width, height)):
            raise _invalid()
    else:
        raise ImageError("Use PNG, JPEG or static WebP images. Other file types are not supported.", 415)
    if width <= 0 or height <= 0:
        raise _invalid()
    if max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ImageError("Images must be at most 25 megapixels and 8192 pixels per side. Choose a smaller image.", 413)
    if (needs_profile and not profile) or len(profile) > MAX_PROFILE_BYTES:
        raise _color_profile()
    if profile and (len(profile) < 128 or profile[16:20] != b"RGB " or profile[36:40] != b"acsp"):
        raise _color_profile()
    return mime, width, height, orientation, profile


# Set limits in a fresh helper process: preexec_fn is unsafe in the threaded HTTP server.
_LIMITED_EXEC = """import os, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (1073741824, 1073741824))
resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[1]), int(sys.argv[1])))
os.execv(sys.argv[2], sys.argv[2:])
"""


# The native API is used only by an isolated, bounded process, never the server's address space.
# RGBA8 and COPY_ALPHA follow https://github.com/mm2/Little-CMS/blob/master/include/lcms2.h.
_ICC_EXEC = """import ctypes as C, pathlib, sys
library, profile_path, pixel_path = sys.argv[1:]
try:
    lib = C.CDLL(library)
except OSError:
    sys.exit(3)
pointer, integer = C.c_void_p, C.c_uint32
for name, result, arguments in (
    ('cmsOpenProfileFromMem', pointer, [pointer, integer]),
    ('cmsCreate_sRGBProfile', pointer, []),
    ('cmsCreateTransform', pointer, [pointer, integer, pointer, integer, integer, integer]),
    ('cmsDoTransform', None, [pointer, pointer, pointer, integer]),
    ('cmsDeleteTransform', None, [pointer]),
    ('cmsCloseProfile', C.c_int, [pointer]),
):
    function = getattr(lib, name)
    function.restype, function.argtypes = result, arguments
profile_bytes = pathlib.Path(profile_path).read_bytes()
buffer = C.create_string_buffer(profile_bytes)
source = lib.cmsOpenProfileFromMem(buffer, len(profile_bytes))
target = lib.cmsCreate_sRGBProfile()
rgba8 = (4 << 16) | (1 << 7) | (3 << 3) | 1
transform = lib.cmsCreateTransform(source, rgba8, target, rgba8, 0, 0x04000000) if source and target else None
if not transform:
    sys.exit(2)
pixels = bytearray(pathlib.Path(pixel_path).read_bytes())
array = (C.c_ubyte * len(pixels)).from_buffer(pixels)
lib.cmsDoTransform(transform, array, array, len(pixels) // 4)
pathlib.Path(pixel_path).write_bytes(pixels)
lib.cmsDeleteTransform(transform)
lib.cmsCloseProfile(source)
lib.cmsCloseProfile(target)
"""


def _process(command: list[str], directory: Path, deadline: float, limit: int) -> int:
    command = [sys.executable, "-c", _LIMITED_EXEC, str(limit), *command]
    with open(directory / "diagnostic", "wb") as errors:
        return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=errors,
                              timeout=max(0.01, deadline - time.monotonic())).returncode


def _normalize(raw: bytes, directory: Path) -> tuple[bytes, str, int, int]:
    mime, width, height, orientation, profile = _inspect(raw)
    state = capability()
    if not state["available"]:
        raise ImageError(state["reason"], 422)
    binary = shutil.which("ffmpeg")
    if not binary:
        raise ImageError("Image input unavailable: the local image converter is unavailable.", 422)
    source, target = directory / "source", directory / "canonical"
    source.write_bytes(raw)
    source.chmod(0o600)
    jpeg = mime == "image/jpeg"
    transforms = {2: "hflip", 3: "hflip,vflip", 4: "vflip", 5: "transpose=clock,hflip",
                  6: "transpose=clock", 7: "transpose=clock,vflip", 8: "transpose=cclock"}
    command = [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
               "-max_alloc", str(256 << 20), "-threads", "1", "-filter_threads", "1",
               "-protocol_whitelist", "file,pipe", "-noautorotate", "-err_detect", "explode",
               "-i", str(source), "-map", "0:v:0", "-map_metadata", "-1", "-frames:v", "1",
               "-threads", "1", "-flags:v", "+bitexact", "-fflags", "+bitexact"]
    if orientation in transforms:
        command += ["-vf", transforms[orientation]]
    encoding = (["-c:v", "mjpeg", "-q:v", "2"] if jpeg else ["-c:v", "png"])
    encoding += ["-f", "image2", "-update", "1", str(target)]
    expected = (height, width) if orientation >= 5 else (width, height)
    deadline = time.monotonic() + PROCESS_TIMEOUT
    try:
        if profile:
            library = ctypes.util.find_library("lcms2")
            if not library:
                raise ImageError("Image input unavailable for this color profile: the local color converter is unavailable.", 422)
            pixels, icc = directory / "pixels.rgba", directory / "profile.icc"
            icc.write_bytes(profile)
            pixel_bytes = width * height * 4
            if (_process(command + ["-pix_fmt", "rgba", "-f", "rawvideo", str(pixels)], directory, deadline, pixel_bytes + 1)
                    or not pixels.is_file() or pixels.stat().st_size != pixel_bytes):
                raise _invalid()
            result = _process([sys.executable, "-c", _ICC_EXEC, library, str(icc), str(pixels)], directory, deadline, pixel_bytes + 1)
            if result == 3:
                raise ImageError("Image input unavailable for this color profile: the local color converter is unavailable.", 422)
            if result:
                raise _color_profile()
            command = [binary, "-nostdin", "-loglevel", "error", "-y", "-threads", "1", "-filter_threads", "1",
                       "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{expected[0]}x{expected[1]}",
                       "-i", str(pixels), "-frames:v", "1", "-threads", "1", "-flags:v", "+bitexact",
                       "-fflags", "+bitexact", "-map_metadata", "-1"]
        result = _process(command + encoding, directory, deadline, MAX_BYTES + 1)
        if target.exists() and target.stat().st_size > MAX_BYTES:
            raise ImageError("The prepared image exceeds 10 MiB. Choose a smaller image.", 413)
        if result != 0 or not target.is_file():
            raise _invalid()
        canonical = target.read_bytes()
        canonical_mime, actual_width, actual_height, _, _ = _inspect(canonical)
        if (actual_width, actual_height) != expected:
            raise _invalid()
        return canonical, canonical_mime, actual_width, actual_height
    except FileNotFoundError as exc:
        raise ImageError("Image input unavailable: the local image converter is unavailable.", 422) from exc
    except subprocess.TimeoutExpired as exc:
        raise ImageError("This image took too long to prepare. Choose a smaller image.", 422) from exc
    finally:
        for name in ("source", "pixels.rgba", "profile.icc"):
            (directory / name).unlink(missing_ok=True)


def _root(project: str) -> Path:
    try:
        config.project(project)
    except (KeyError, ValueError) as exc:
        raise ImageError("Image unavailable in this project.", 404) from exc
    root = config.project_dir(project) / "images"
    if root.is_symlink():
        raise ImageError("Image access denied.", 403)
    return root


def store(project: str, uploads: list[dict], *, message_id: str, task: str | None = None) -> list[dict]:
    """Prepare one whole submission; its durable message must be written before releasing the lock."""
    if not isinstance(uploads, list) or not 1 <= len(uploads) <= MAX_IMAGES:
        raise ImageError("Attach between one and four images per message.", 413)
    decoded = []
    for upload in uploads:
        if not isinstance(upload, dict) or not isinstance(upload.get("data"), str):
            raise ImageError("Invalid image upload.", 400)
        if len(upload["data"]) > 4 * ((MAX_BYTES + 2) // 3):
            raise ImageError("Each image must be at most 10 MiB. Choose a smaller image.", 413)
        try:
            raw = base64.b64decode(upload["data"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageError("Invalid image upload.", 400) from exc
        if not raw or len(raw) > MAX_BYTES:
            raise ImageError("Each image must be nonempty and at most 10 MiB.", 413)
        decoded.append(raw)
    if sum(map(len, decoded)) > MAX_TOTAL_BYTES:
        raise ImageError("Images in one message must total at most 20 MiB. Remove an image or choose smaller images.", 413)
    root = _root(project)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    moved: list[Path] = []
    refs: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=root) as temporary:
            staging = Path(temporary)
            for upload, raw in zip(uploads, decoded):
                image_id = uuid.uuid4().hex
                directory = staging / image_id
                directory.mkdir(mode=0o700)
                data, mime, width, height = _normalize(raw, directory)
                if sum(row["size"] for row in refs) + len(data) > MAX_TOTAL_BYTES:
                    raise ImageError("Prepared images exceed 20 MiB total. Remove an image or choose smaller images.", 413)
                name = re.sub(r"[\x00-\x1f\x7f]", "", str(upload.get("name") or "Image"))[:128] or "Image"
                ref = {"id": image_id, "name": name, "project": project, "mime_type": mime,
                       "size": len(data), "width": width, "height": height,
                       "sha256": hashlib.sha256(data).hexdigest(), "source_message_id": message_id,
                       "source_task": task}
                path = directory / ("image.jpg" if mime == "image/jpeg" else "image.png")
                with open(path, "xb") as stream:
                    os.chmod(path, 0o600)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                S.write_json(directory / "metadata.json", ref)
                (directory / "canonical").unlink(missing_ok=True)
                (directory / "diagnostic").unlink(missing_ok=True)
                refs.append(ref)
            for ref in refs:
                destination = root / ref["id"]
                os.rename(staging / ref["id"], destination)
                moved.append(destination)
            S._fsync_directory(root)
        return refs
    except BaseException:
        for directory in moved:
            shutil.rmtree(directory)
        raise


def _record_ids(record) -> set[str]:
    if isinstance(record, dict):
        found = {ref["id"] for ref in record.get("images", [])
                 if isinstance(ref, dict) and isinstance(ref.get("id"), str)}
        return found | set().union(*(_record_ids(value) for key, value in record.items() if key != "images"))
    if isinstance(record, list):
        return set().union(*(_record_ids(value) for value in record))
    return set()


def _committed(project: str, task: str | None = None) -> set[str]:
    _root(project)  # Registry lookup precedes all paths, including reference-ledger reads.
    root = config.project_dir(project)
    if task is not None:
        directories = [S.task_dir(project, task)]
        files = []
    else:
        directories = [directory for group in (root / "tasks", root / "archive") if group.is_dir()
                       for directory in group.iterdir() if directory.is_dir() and not directory.is_symlink()]
        files = [root / "chat.jsonl", root / "l3-queue.jsonl"]
    files += [directory / name for directory in directories for name in ("status.json", "conversation.jsonl")]
    found: set[str] = set()
    for path in files:
        try:
            if path.is_symlink() or path.parent.is_symlink():
                raise ImageError("Image access denied.", 403)
            with path.open() as stream:
                if path.suffix == ".jsonl":
                    for line in stream:
                        if line.strip():
                            found.update(_record_ids(json.loads(line)))
                else:
                    found.update(_record_ids(json.load(stream)))
        except FileNotFoundError:
            continue
        except ImageError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ImageError("Image references are temporarily unavailable.", 503) from exc
    return found


def _load(project: str, image_id: str) -> tuple[bytes, dict, Path]:
    root = _root(project)
    if not isinstance(image_id, str) or not _ID.fullmatch(image_id):
        raise ImageError("Image unavailable in this project.", 404)
    directory = root / image_id
    try:
        if directory.is_symlink():
            raise ImageError("Image access denied.", 403)
        metadata = directory / "metadata.json"
        with os.fdopen(os.open(metadata, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ImageError("Image access denied.", 403)
            ref = json.loads(stream.read(64 * 1024))
        if not isinstance(ref, dict):
            raise ImageError("Image unavailable in this project.", 404)
        if ref.get("id") != image_id or ref.get("project") != project or ref.get("mime_type") not in ("image/png", "image/jpeg"):
            raise ImageError("Image unavailable in this project.", 404)
        path = directory / ("image.jpg" if ref["mime_type"] == "image/jpeg" else "image.png")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ImageError("Image access denied.", 403)
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or len(raw) != ref["size"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ImageError("Image content is unavailable. The saved file is missing or damaged.", 404)
        return raw, ref, path
    except ImageError:
        raise
    except PermissionError as exc:
        raise ImageError("Image access denied.", 403) from exc
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ImageError("Image unavailable in this project.", 404) from exc


def resolve(project: str, refs: list[dict], *, task: str | None = None, committed: bool = True) -> list[dict]:
    """Resolve canonical metadata to readable local bytes; callers cannot supply filesystem paths."""
    if not refs:
        return []
    allowed = _committed(project, task) if committed else None
    resolved = []
    for ref in refs:
        if not isinstance(ref, dict) or (allowed is not None and ref.get("id") not in allowed):
            raise ImageError("Image unavailable in this conversation.", 404)
        _, stored, path = _load(project, ref.get("id"))
        if any(ref.get(key) != value for key, value in stored.items()):
            raise ImageError("Image reference does not match its original message.", 404)
        resolved.append({**stored, "path": str(path.absolute())})
    return resolved


def lookup(project: str, ids: list[str]) -> list[dict]:
    if not isinstance(ids, list) or not ids or len(ids) > MAX_IMAGES:
        raise ImageError("Choose between one and four images per message.")
    if any(not isinstance(image_id, str) for image_id in ids):
        raise ImageError("Choose committed image IDs from this project.")
    allowed = _committed(project)
    refs = []
    for image_id in ids:
        if image_id not in allowed:
            raise ImageError("Image unavailable in this project.", 404)
        refs.append(_load(project, image_id)[1])
    if sum(ref["size"] for ref in refs) > MAX_TOTAL_BYTES:
        raise ImageError("Images exceed 20 MiB total. Choose fewer or smaller images.", 413)
    return refs


def read(project: str, image_id: str) -> tuple[bytes, dict]:
    if image_id not in _committed(project):
        raise ImageError("Image unavailable in this project.", 404)
    raw, ref, _ = _load(project, image_id)
    return raw, ref


def collect(project: str) -> int:
    """Remove old staging, taking the same project lock used for message admission."""
    with S.project_lock(project):
        return _collect(project)


def _collect(project: str) -> int:
    root = _root(project)
    if not root.is_dir():
        return 0
    protected = _committed(project)
    cutoff, removed = time.time() - ORPHAN_SECONDS, 0
    for directory in root.iterdir():
        if (directory.name not in protected and (_ID.fullmatch(directory.name) or directory.name.startswith(".stage-"))
                and not directory.is_symlink() and directory.is_dir() and directory.stat().st_mtime < cutoff):
            shutil.rmtree(directory)
            removed += 1
    return removed
