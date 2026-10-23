"""Host speech: live dictation transcribed on this computer, part of the capability seam.

Setup downloads a pinned runtime once — the speech model and the wheels of the libraries that run it — checks
each file against the SHA-256 written here, unpacks it into a staging folder and moves the finished runtime into
place in one step. A release explicitly names the completed runtimes it can safely keep serving during updates.

A recording streams from the browser as 16 kHz mono 16-bit samples, one numbered request at a time. The daemon
hands the samples to one speech worker process (`speech_worker.py`), shared by every recording, and answers each
request at once with the text so far. The worker starts with the first recording, stops after WORKER_IDLE_S
without one, and is killed when it misses a deadline. Audio lives only in memory, for its recording.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import http.client
import io
import json
import os
import queue
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import wave
import zipfile
from urllib.parse import urlsplit
from pathlib import Path, PurePosixPath
from typing import Callable

from . import config, platform, state as S

# Beside Altitude's home, outside every folder an agent session writes: this process imports the runtime's code.
ROOT = config.beside_root("speech")
WORKER = Path(__file__).resolve().parent / "speech_worker.py"

SERVICE_PCM_LIMIT = 600 * 16000 * 2
SERVICE_UPLOAD_SECONDS = 120
SERVICE_RESPONSE_SECONDS = 120
SERVICE_RESPONSE_LIMIT = 1 << 20
SERVICE_SLOTS = threading.BoundedSemaphore(2)


class ServiceError(RuntimeError):
    """Only these fixed messages cross the service boundary; provider bodies never do."""

    MESSAGES = {
        "service_auth": "The speech service rejected its key. Update it in Voice settings.",
        "service_unavailable": "Transcription did not finish. Check your connection and service address in Voice settings.",
        "service_response": "The speech service returned no usable transcription. Check its endpoint and model.",
        "service_empty": "No words were returned. Try recording again.",
        "selection_changed": "Voice settings changed. Record again using the current choice.",
    }

    def __init__(self, code: str):
        self.code = code
        super().__init__(self.MESSAGES[code])


def transcribe_service(setting: dict, pcm: bytes) -> str:
    """One direct, certificate-verified request: no proxy discovery, redirect, retry or audio file."""
    if not pcm or len(pcm) > SERVICE_PCM_LIMIT or len(pcm) % 2:
        raise ServiceError("service_response")
    wav = io.BytesIO()
    with wave.open(wav, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("response_format", "json"), ("model", setting.get("model"))):
        if value:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.wav"\r\n'
                  'Content-Type: audio/wav\r\n\r\n'.encode(), wav.getvalue(), f'\r\n--{boundary}--\r\n'.encode()])
    try:
        parsed = urlsplit(setting["url"])
        connection = (http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=SERVICE_RESPONSE_SECONDS,
                                                  context=ssl.create_default_context())
                      if parsed.scheme == "https" else
                      http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=SERVICE_RESPONSE_SECONDS))
    except ValueError:
        raise ServiceError("service_unavailable") from None

    expired = threading.Event()

    def expire() -> None:
        # The socket timeout bounds each read; this bounds a service that answers a little at a time.
        expired.set()
        try:
            socket.socket.shutdown(connection.sock, socket.SHUT_RDWR)
        except (OSError, TypeError):
            pass  # Not connected yet, or already closed.
    deadline = threading.Timer(SERVICE_RESPONSE_SECONDS, expire)
    deadline.start()
    try:
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if setting.get("key"):
            headers["Authorization"] = "Bearer " + setting["key"]
        connection.connect()
        if expired.is_set():  # The deadline passed before there was a socket to close.
            raise ServiceError("service_unavailable")
        connection.request("POST", parsed.path or "/", b"".join(parts), headers)
        response = connection.getresponse()
        if response.status in (401, 403):
            raise ServiceError("service_auth")
        if response.status >= 500 or response.status in (408, 429):
            raise ServiceError("service_unavailable")
        if response.status != 200:
            raise ServiceError("service_response")
        raw = response.read(SERVICE_RESPONSE_LIMIT + 1)
        if len(raw) > SERVICE_RESPONSE_LIMIT:
            raise ServiceError("service_response")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ServiceError("service_response") from None
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ServiceError("service_response")
        text = result["text"].strip()
        if not text:
            raise ServiceError("service_empty")
        return text
    except ServiceError:
        if expired.is_set():
            raise ServiceError("service_unavailable") from None
        raise
    except (OSError, http.client.HTTPException, UnicodeError, ValueError):
        raise ServiceError("service_unavailable") from None
    finally:
        deadline.cancel()
        connection.close()

_MODEL = "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85/"
_PYPI = "https://files.pythonhosted.org/packages/"
#: NVIDIA Parakeet TDT 0.6B v2 (CC-BY-4.0), int8 ONNX export used by onnx-asr.
MODEL_FILES = [
    ("model/config.json", _MODEL + "config.json", 97, "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"),
    ("model/vocab.txt", _MODEL + "vocab.txt", 9384, "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"),
    ("model/nemo128.onnx", _MODEL + "nemo128.onnx", 139764, "a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f"),
    ("model/decoder_joint-model.int8.onnx", _MODEL + "decoder_joint-model.int8.onnx", 8998286,
     "a449f49acd68979d418651dd2dcb737cc0f1bf0225e009e29ee326354edbf7d3"),
    ("model/encoder-model.int8.onnx", _MODEL + "encoder-model.int8.onnx", 652184014,
     "3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155"),
]
_DECODER = ("wheels/onnx_asr-0.12.0-py3-none-any.whl",
            _PYPI + "6a/60/2fa469a2ee674c35ab48821a1039762ae7b9d0b88188ac1012e779477f76/onnx_asr-0.12.0-py3-none-any.whl",
            3980006, "5e7ceca454609819ea7833f61e2302e0c8f6ece4f8a78b66c5daba53cb51de4a")
#: The wheels for each runtime `platform.speech_runtime` names: onnxruntime 1.28.0 and numpy 2.5.3. onnxruntime 1.28
#: is the newest release whose library has no usage-reporting client (1.29 adds one).
WHEELS = {
    "linux-x86_64-cp312": [
        ("wheels/onnxruntime-1.28.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "95/df/5486ab03e9be288d5268867054c8b04bebcf95bfd12e801c05cc67703dab/"
         "onnxruntime-1.28.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         19214257, "0a83bdb70d143cede762b677789bf2a7acca54b3fb82565601d5c30695aa933c"),
        ("wheels/numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "65/af/aa78d1a88805456e212b65461354cd943197fb9acecc4c90fd12295123a3/"
         "numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         16717410, "b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3"),
    ],
    "linux-x86_64-cp313": [
        ("wheels/onnxruntime-1.28.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "78/b6/8c5396e7894e77c5a7d1e026f3acb9dd39c4b5644e412e37a0055eaa3bc5/"
         "onnxruntime-1.28.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         19214329, "54fa221d669282bd8f582708ce4c96010a7e9fb0661f9006b37fe2fedafb73fe"),
        ("wheels/numpy-2.5.3-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "3a/1b/3b16a9bc514a440a7a0883684111dcb1ef1aee960af2ca95da8fc775f124/"
         "numpy-2.5.3-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         16708577, "a5fa86b80fd24bcd1aff83ad23be44ea323de3f787be8f8b15d4a65621e25321"),
    ],
    "macos-arm64-cp312": [
        ("wheels/onnxruntime-1.28.0-cp312-cp312-macosx_14_0_arm64.whl",
         _PYPI + "98/f8/dcbe7700dca82fa540035abd3c868fe5ad0f86af00b9a3db7c2e27d15c7d/"
         "onnxruntime-1.28.0-cp312-cp312-macosx_14_0_arm64.whl",
         19141362, "26ff0fdd06efb6c155bae95387a09db1a2be89c7a03e4d0bffd5a171cc2826da"),
        ("wheels/numpy-2.5.3-cp312-cp312-macosx_14_0_arm64.whl",
         _PYPI + "9c/59/a312e95696e5f601914dd8b6dd844692ba61670807417e24b68e337b5c70/"
         "numpy-2.5.3-cp312-cp312-macosx_14_0_arm64.whl",
         5445405, "a72f874bc9e10e4b8f80426fb49716d5141f64442a0c8418065093ec8017fbb0"),
    ],
    "macos-arm64-cp313": [
        ("wheels/onnxruntime-1.28.0-cp313-cp313-macosx_14_0_arm64.whl",
         _PYPI + "9c/12/3807e2b17d9eb71d3cb78ed2ba76869b05c637c9b9d6112e636098b0c97a/"
         "onnxruntime-1.28.0-cp313-cp313-macosx_14_0_arm64.whl",
         19141759, "31410f544674f534c2f27348af52ef81682ca9c8719154bf4d48f0ef23823b1e"),
        ("wheels/numpy-2.5.3-cp313-cp313-macosx_14_0_arm64.whl",
         _PYPI + "ab/2a/98282aa5b8f58b1157d440bb6282eed47e3632a5de53a714fbab17e659fe/"
         "numpy-2.5.3-cp313-cp313-macosx_14_0_arm64.whl",
         5439695, "f9a2353b37a1a9e78fd82b27ad7e2a32a2d036604d18f02b05e3136c62ca3b09"),
    ],
}
#: The unpacked wheels are about 110 MB; anything far larger is not the pinned content.
UNPACKED_LIMIT = 400 << 20
BLOCK = 1 << 20

RATE_BYTES = 32000  # 16 kHz, 16-bit mono
CHUNK_LIMIT = 10 * RATE_BYTES
RECORDING_LIMIT = 600 * RATE_BYTES
MAX_RECORDINGS = 2
RECORDING_IDLE_S = 30
WORKER_IDLE_S = 900
LOAD_S = 30
UPDATE_S = 10
FINISH_S = 15
WATCH_S = 1.0
#: What a loaded worker needs: the measured peak is 1.4 GB.
MEMORY_NEEDED = 2_500_000_000


class SpeechError(RuntimeError):
    """A safe, useful voice error that may cross the HTTP boundary."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def manifest() -> tuple[list[tuple[str, str, int, str]] | None, str]:
    """This computer's pinned runtime files, or None and why voice cannot run here."""
    key, reason = platform.speech_runtime()
    if key is None:
        return None, reason
    if key not in WHEELS:
        return None, "voice needs Python 3.12 or 3.13"
    return [*MODEL_FILES, _DECODER, *WHEELS[key]], ""


def _digest(files) -> str:
    return hashlib.sha256(json.dumps([[name, digest] for name, _, _, digest in files]).encode()).hexdigest()[:16]


def _runtimes() -> list[Path]:
    return [path for path in ROOT.glob("runtime-*") if path.is_dir()] if ROOT.is_dir() else []


def _complete(path: Path, digest: str) -> bool:
    try:
        return S.read_json(path / "complete.json", {}).get("digest") == digest
    except (OSError, ValueError):
        return False


def _state() -> dict:
    return S.read_json(ROOT / "installation.json", {})


def _write_state(value: dict) -> None:
    S.write_json(ROOT / "installation.json", value)


def _guard():
    ROOT.mkdir(parents=True, exist_ok=True)
    handle = open(ROOT / ".control", "a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


# Release-owned support, including the worker/model protocol. Never infer support from folder age.
# The preceding Linux 1.22 runtimes use the same pinned model and current worker protocol.
SUPPORTED = {
    "linux-x86_64-cp312": ("a99b5413cbe7fbf9",),
    "linux-x86_64-cp313": ("ecfca085ff6ad628",),
}


def _installed() -> list[Path]:
    return [p for p in _runtimes() if _complete(p, p.name.removeprefix("runtime-"))]


def runtime() -> Path | None:
    """A completed runtime explicitly supported by this release; prefer its target."""
    files, _ = manifest()
    if files is None or _state().get("removed"):
        return None
    key, _ = platform.speech_runtime()
    for digest in dict.fromkeys([_digest(files), *SUPPORTED.get(key, ())]):
        path = ROOT / f"runtime-{digest}"
        if _complete(path, digest):
            return path
    return None


def installation_id() -> str:
    """Stable across supported replacements; removal/reinstallation invalidates retained audio."""
    state = _state()
    if state.get("generation"):
        return state["generation"]
    installed = _installed()
    return installed[0].name if installed else ""


def remove_home_runtime() -> bool:
    """Delete what voice kept in Altitude's home in earlier releases, where task workers could write it; nothing of
    it is reused. Returns whether a finished runtime was there, so host voice is set up again beside the home.

    A worker may still be writing there, so the folder is held open and never followed through a link, only voice's
    own entries go (a project named `speech` keeps its state and lock), and no failure keeps the daemon from
    starting: nothing reads what is left."""
    names = {".staging", "installation.json", ".control", ".use", "worker.log", "verification.log"}
    try:
        if not any(name.lower() == "speech" for name in config.load_projects()):
            names.add(".lock")
    except Exception:  # noqa: BLE001 — workers can write the registry; an unreadable one keeps the lock
        pass
    try:
        home = os.open(config.ROOT, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return False
    finished = False
    try:
        folder = os.open("speech", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home)
        try:
            for entry in list(os.scandir(folder)):
                if not (entry.name.startswith("runtime-") or entry.name in names):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.startswith("runtime-") and not finished:
                            try:
                                runtime = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
                                try:
                                    complete = os.stat("complete.json", dir_fd=runtime, follow_symlinks=False)
                                    finished = stat.S_ISREG(complete.st_mode)
                                finally:
                                    os.close(runtime)
                            except OSError:
                                pass
                        shutil.rmtree(entry.name, dir_fd=folder, ignore_errors=True)
                    else:
                        os.unlink(entry.name, dir_fd=folder)
                except OSError:
                    pass
        finally:
            os.close(folder)
        os.rmdir("speech", dir_fd=home)  # fails while a project's state shares it
    except OSError:
        pass
    finally:
        os.close(home)
    return finished


def _setting_up() -> bool:
    if not (ROOT / ".lock").exists():
        return False
    with _guard(), open(ROOT / ".lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def status() -> dict:
    """Serving availability and replacement progress are independent."""
    files, reason = manifest()
    if files is None:
        return {"state": "unavailable", "reason": reason}
    total = sum(size for _, _, size, _ in files)
    state = _state()
    update = state.get("update", {})
    if update.get("target") != _digest(files) or state.get("removed"):
        update = {}
    running = _setting_up()
    if update and update["state"] not in ("failed", "cancelled") and not running:
        update = {**update, "state": "failed", "reason": "The update was interrupted. Try again."}
    view = {"download_bytes": total}
    if runtime() is not None:
        view["state"] = "ready"
    elif not state.get("removed") and _installed():
        view["state"] = "outdated"
    elif running:
        return {**view, "state": "setting-up", "done_bytes": update.get("done_bytes", 0), "update": update}
    elif update.get("state") == "failed":
        return {**view, "state": "failed", "reason": update.get("reason", "")}
    else:
        view["state"] = "absent"
    if update and view["state"] != "absent":
        view["update"] = {k: v for k, v in update.items() if k not in ("id", "target", "cancel")}
    return view


class _Attempt:
    def __init__(self, ident: str, automatic: bool = False):
        self.ident, self.automatic = ident, automatic

    def is_set(self) -> bool:
        state = _state()
        update = state.get("update", {})
        return (state.get("removed", False) or update.get("id") != self.ident
                or bool(update.get("cancel"))
                or (self.automatic and config.voice_setting()["backend"] != "host"))

    def check(self) -> None:
        if self.is_set():
            raise SpeechError("Setup was cancelled.", 409)

    def progress(self, phase: str, **fields) -> None:
        with _guard():
            self.check()
            state = _state()
            state["update"].update(state=phase, **fields)
            _write_state(state)


def _begin(automatic: bool = False, reinstall: bool = False) -> _Attempt:
    files, reason = manifest()
    if files is None:
        raise SpeechError(f"Voice isn't available on this computer: {reason}.", 409)
    with _guard():
        state = _state()
        target = _digest(files)
        if automatic and (state.get("removed") or not (reinstall or _installed())
                          or config.voice_setting()["backend"] != "host"
                          or state.get("update", {}).get("target") == target
                          or _complete(ROOT / f"runtime-{target}", target)):
            raise SpeechError("No automatic voice update is needed.", 409)
        ident = uuid.uuid4().hex
        generation = uuid.uuid4().hex if state.get("removed") else installation_id() or uuid.uuid4().hex
        state.update(generation=generation, removed=False,
                     update={"id": ident, "target": target, "state": "preparing"})
        _write_state(state)
        return _Attempt(ident, automatic)


def _download(url: str, target: Path, size: int, digest: str, advance: Callable[[int], None],
              cancel) -> None:
    hashed, seen = hashlib.sha256(), 0
    if cancel.is_set():
        raise SpeechError("Setup was cancelled.", 409)
    with urllib.request.urlopen(url, timeout=10) as response, open(target, "wb") as out:
        # One underlying read lets cancellation run even when a server trickles a large block.
        while block := response.read1(BLOCK):
            if cancel.is_set():
                raise SpeechError("Setup was cancelled.", 409)
            seen += len(block)
            if seen > size:
                raise SpeechError(f"Setup stopped: {target.name} is larger than expected.")
            hashed.update(block)
            out.write(block)
            advance(len(block))
    if seen != size or hashed.hexdigest() != digest:
        raise SpeechError(f"Setup stopped: {target.name} did not match its checksum.")


def _unpack(wheel: Path, site: Path) -> int:
    """Unpack one verified wheel with the installer's path rules."""
    total = 0
    with zipfile.ZipFile(wheel) as bundle:
        for member in bundle.infolist():
            path = PurePosixPath(member.filename)
            kind = stat.S_IFMT(member.external_attr >> 16)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise SpeechError(f"Setup stopped: {wheel.name} contains an unsafe entry.")
            total += member.file_size
        if total > UNPACKED_LIMIT:
            raise SpeechError(f"Setup stopped: {wheel.name} unpacks larger than expected.")
        bundle.extractall(site)
    return total


def _lock(wait: float = 0):
    """The setup lock. It is taken, like `_setting_up` probes it, only under the control lock, so a status probe's
    momentary hold never reads as a setup already running."""
    ROOT.mkdir(parents=True, exist_ok=True)
    handle = open(ROOT / ".lock", "a")
    deadline = time.monotonic() + wait
    while True:
        with _guard():
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                pass
        if time.monotonic() >= deadline:
            handle.close()
            raise SpeechError("Voice is already being set up.", 409)
        time.sleep(0.05)


def _lease(exclusive: bool, cancel=None, timeout: float | None = None):
    """Workers inherit shared use; verification, promotion and deletion need exclusive use."""
    handle = open(ROOT / ".use", "a")
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        while True:
            if cancel is not None:
                cancel.check()
            try:
                fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise SpeechError("Voice is checking an update. Try again shortly.", 425)
                time.sleep(0.05)
    except BaseException:
        handle.close()
        raise


def _environment() -> dict:
    return {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "TMPDIR") if name in os.environ}


def _verify(path: Path, cancel: _Attempt) -> None:
    """Load and warm the real model in isolation, bounded and cancellable, before promotion."""
    memory = platform.available_memory()
    if memory is not None and memory < MEMORY_NEEDED:
        raise SpeechError("Voice needs 2.5 GB of free memory to check the speech model.")
    with open(ROOT / "verification.log", "w") as errors:
        worker = subprocess.Popen([sys.executable, "-I", str(WORKER), str(path)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=errors, text=True, env=_environment())
        try:
            deadline = time.monotonic() + LOAD_S
            while True:
                cancel.check()
                try:
                    # EOF after model warmup makes this the same executable path as a real recording.
                    output, _ = worker.communicate(input="", timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise SpeechError("The speech model did not finish checking in time.") from None
            if worker.returncode or json.loads(output.strip() or "{}") != {"ready": True}:
                raise SpeechError("The speech model could not run. Try again.")
        finally:
            if worker.poll() is None:
                worker.kill()
            worker.wait()
            if worker.stdin:
                worker.stdin.close()
            if worker.stdout:
                worker.stdout.close()


def setup(lock=None, attempt: _Attempt | None = None) -> Path:
    """Stage alongside working voice, then verify/switch/clean only with exclusive worker use."""
    with lock or _lock():
        attempt = attempt or _begin()
        files, _ = manifest()
        digest = _digest(files)
        staging, target = ROOT / ".staging", ROOT / f"runtime-{digest}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            if not _complete(target, digest):
                _stage(files, staging, digest, attempt)
                attempt.progress("waiting")
                with _lease(True, attempt):
                    attempt.progress("checking")
                    _verify(staging, attempt)
                    with _guard():
                        attempt.check()
                        S.write_json(staging / "complete.json", {"digest": digest, "at": S.now()})
                        shutil.rmtree(target, ignore_errors=True)
                        os.replace(staging, target)
                        _finish_setup(target)
            elif any(old != target for old in _runtimes()):
                attempt.progress("waiting")
                with _lease(True, attempt), _guard():
                    attempt.check()
                    _finish_setup(target)
            else:
                # A no-op setup need not drain working dictation merely to clean up old files.
                with _guard():
                    attempt.check()
                    state = _state()
                    state.pop("update", None)
                    _write_state(state)
            return target
        except BaseException as exc:
            with _guard():
                state = _state()
                if state.get("update", {}).get("id") == attempt.ident:
                    interrupted = state["update"].get("cancel") == "interrupted"
                    cancelled = not interrupted and (attempt.is_set() or isinstance(exc, KeyboardInterrupt))
                    reason = str(exc) if isinstance(exc, SpeechError) else "A download failed. Try again."
                    if interrupted:
                        reason = "The update was interrupted. Try again."
                    state["update"].update(state="cancelled" if cancelled else "failed", reason="" if cancelled else reason)
                    _write_state(state)
            if isinstance(exc, (OSError, ValueError, zipfile.BadZipFile)) and not isinstance(exc, SpeechError):
                raise SpeechError("Setup stopped: a download failed. Try again.") from exc
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _finish_setup(target: Path) -> None:
    # Callers hold exclusive worker use and the control lock; no reader or remover can cross promotion.
    # Leftovers that cannot be removed now go with the next setup; the update itself has finished.
    for old in _runtimes():
        if old != target:
            # Without its marker, a partly removed runtime never reads as installed.
            (old / "complete.json").unlink(missing_ok=True)
            shutil.rmtree(old, ignore_errors=True)
    state = _state()
    state.pop("update", None)
    _write_state(state)


def _stage(files, staging: Path, digest: str, cancel: _Attempt) -> None:
    (staging / "model").mkdir(parents=True)
    (staging / "wheels").mkdir()
    missing = []
    for name, url, size, sha in files:
        cancel.check()
        reused = False
        if name.startswith("model/"):
            # A second link keeps the serving runtime's file in place and costs no disk space.
            for old in _installed():
                source = old / name
                if not source.is_file() or source.is_symlink() or source.stat().st_size != size:
                    continue
                try:
                    os.link(source, staging / name)
                except OSError:
                    break  # This volume refuses hard links: download the file.
                hashed = hashlib.sha256()
                with (staging / name).open("rb") as inp:
                    while block := inp.read(BLOCK):
                        cancel.check()
                        hashed.update(block)
                if hashed.hexdigest() == sha:
                    reused = True
                    break
                (staging / name).unlink()
        if not reused:
            missing.append((name, url, size, sha))
    total, done, reported = sum(size for _, _, size, _ in missing), 0, 0
    needed = total + UNPACKED_LIMIT
    if shutil.disk_usage(ROOT).free < needed:
        raise SpeechError(f"Voice needs {needed / 1e9:.1f} GB of free disk space.")

    def advance(count: int) -> None:
        nonlocal done, reported
        done += count
        if done - reported >= 8 * BLOCK or done == total:
            reported = done
            cancel.progress("downloading", done_bytes=done, download_bytes=total)

    cancel.progress("downloading", done_bytes=0, download_bytes=total)
    for name, url, size, sha in missing:
        _download(url, staging / name, size, sha, advance, cancel)
    for wheel in sorted((staging / "wheels").iterdir()):
        cancel.check()
        _unpack(wheel, staging / "site")
    shutil.rmtree(staging / "wheels")


def _cancel_setup(reason: str) -> None:
    with _guard():
        state = _state()
        if (state.get("update", {}).get("state") not in (None, "failed", "cancelled")
                and not state["update"].get("cancel")):
            state["update"]["cancel"] = reason
            _write_state(state)


def cancel_setup() -> None:
    """Cancellation is shared by browser, CLI and the automatic updater."""
    _cancel_setup("cancelled")
    try:
        with _lock(wait=70):
            pass
    except SpeechError:
        raise SpeechError("Cancellation is requested; the update is still stopping. Try again shortly.", 409) from None


def remove_runtime() -> None:
    """Invalidate recording identity first; cancel and drain all users before deleting files."""
    unavailable = platform.container_unavailable("Voice")
    if unavailable:
        raise SpeechError(unavailable, 403)
    with _guard():
        state = _state()
        was_removed = state.get("removed", False)
        generation = uuid.uuid4().hex
        state.update(removed=True, generation=generation)
        if state.get("update"):
            state["update"]["cancel"] = "cancelled"
        _write_state(state)
    try:
        with _lock(wait=70), _lease(True, timeout=10), _guard():
            if _state().get("generation") != generation:
                # A later explicit setup owns the installation now; never delete its result.
                return
            for path in _runtimes():
                (path / "complete.json").unlink(missing_ok=True)
                shutil.rmtree(path)
            shutil.rmtree(ROOT / ".staging", ignore_errors=True)
            _write_state({"removed": True, "generation": generation})
    except SpeechError:
        # Lock deadlines precede deletion. Restore availability, but never revive old recording identity.
        with _guard():
            state = _state()
            if state.get("generation") == generation:
                state["removed"] = was_removed
                _write_state(state)
        raise SpeechError("Voice removal did not finish: another process is still using it. Try Remove voice again.", 409) from None


class _Recording:
    def __init__(self, device: str | None):
        self.device = device
        self.seq = -1
        self.bytes = 0
        self.text = ""
        self.final: str | None = None
        self.error: SpeechError | None = None
        self.answer: dict = {"text": ""}
        self.seen = time.monotonic()
        self.waiting_since: float | None = None


class Host:
    """The daemon's recordings and its one speech worker."""

    def __init__(self, log: Callable[[str], None], command: Callable[[Path], list[str]] | None = None):
        self.log = log
        self.command = command or (lambda path: [sys.executable, "-I", str(WORKER), str(path)])
        self.changed = threading.Condition()
        self.recordings: dict[str, _Recording] = {}
        self.worker: subprocess.Popen | None = None
        self.outbox: queue.SimpleQueue | None = None
        self.ready = False
        self.started = 0.0
        self.used = 0.0
        self.watching = False
        self.path: Path | None = None
        self.setup_attempt: _Attempt | None = None

    # --- recordings -------------------------------------------------------------------------------------

    def open(self, device: str | None) -> dict:
        path = runtime()
        if path is None:
            state = status()
            if state["state"] == "unavailable":
                raise SpeechError(f"Voice isn't available on this computer: {state['reason']}.", 409)
            raise SpeechError("Voice needs a one-time download on this computer. Set it up in Settings.", 409)
        with self.changed:
            if sum(r.final is None and r.error is None for r in self.recordings.values()) >= MAX_RECORDINGS:
                raise SpeechError("Voice is busy on another device.", 429)
            if self.worker is None:
                self._start(path)
            ident = uuid.uuid4().hex
            self.recordings[ident] = _Recording(device)
            self._send({"op": "open", "id": ident})
            self.used = time.monotonic()
            self._watch()
            return {"id": ident, "starting": not self.ready}

    def audio(self, ident: str, device: str | None, seq: int, pcm: bytes, final: bool) -> dict:
        """Add chunk `seq` (0, 1, …) and answer the text so far; `final` answers the finished text. A repeated
        chunk gets its first answer again; a skipped one fails the recording."""
        if len(pcm) % 2 or len(pcm) > CHUNK_LIMIT:
            raise SpeechError("Voice received a malformed recording.", 400)
        with self.changed:
            recording = self.recordings.get(ident)
            if recording is None:
                # The page still holds the audio and replays it into a new recording.
                raise SpeechError("Voice stopped: this recording has ended.", 410)
            if recording.device != device:
                raise SpeechError("Voice stopped: this recording belongs to another device.", 403)
            if recording.error is not None:
                raise recording.error
            if runtime() is None:
                raise SpeechError("Voice needs an update on this computer.", 409)
            recording.seen = time.monotonic()
            if seq == recording.seq:
                if final and recording.final is None and recording.error is None:
                    return self._finish(ident, recording)
                return recording.answer
            if seq != recording.seq + 1 or recording.final is not None:
                recording.error = SpeechError("Voice stopped: part of the recording was lost.", 409)
                self._send({"op": "close", "id": ident})
                raise recording.error
            if recording.bytes + len(pcm) > RECORDING_LIMIT:
                raise SpeechError("Voice stopped: the recording reached ten minutes.", 413)
            recording.seq, recording.bytes = seq, recording.bytes + len(pcm)
            if pcm:
                self._send({"op": "audio", "id": ident, "pcm": base64.b64encode(pcm).decode()})
                recording.waiting_since = recording.waiting_since or time.monotonic()
            self.used = time.monotonic()
            if final:
                return self._finish(ident, recording)
            recording.answer = {"text": recording.text, "starting": not self.ready}
            return recording.answer

    def _finish(self, ident: str, recording: _Recording) -> dict:
        self._send({"op": "finish", "id": ident})
        deadline = time.monotonic() + FINISH_S
        while recording.final is None and recording.error is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop("Voice stopped: the speech process did not finish in time.")
                break
            self.changed.wait(remaining)
        if recording.error is not None:
            raise recording.error
        recording.answer = {"text": recording.final, "final": True}
        return recording.answer

    def close(self, ident: str, device: str | None) -> None:
        with self.changed:
            recording = self.recordings.get(ident)
            if recording is None or recording.device != device:
                return
            del self.recordings[ident]
            if recording.final is None and recording.error is None:
                # A Stop still waiting for its final words ends here, not at the finish deadline.
                recording.error = SpeechError("Voice stopped: this recording has ended.", 410)
                if self.worker is not None:
                    self._send({"op": "close", "id": ident})
            self.changed.notify_all()

    # --- setup and removal --------------------------------------------------------------------------------

    def start_setup(self, automatic: bool = False, reinstall: bool = False) -> None:
        """Set up in the background; Settings follows `status()`."""
        with self.changed:
            if self.setup_attempt is not None:
                raise SpeechError("Voice is already being set up.", 409)
            lock = _lock()  # held before answering, so Settings reads setting-up at once
            try:
                attempt = _begin(automatic, reinstall)
            except BaseException:
                lock.close()
                raise
            self.setup_attempt = attempt

        def run() -> None:
            try:
                setup(lock, attempt)
                self.log("voice: host runtime set up")
            except SpeechError as exc:
                self.log(f"voice: setup ended: {exc}")
            except Exception as exc:  # noqa: BLE001 — the reason stays in the private log; setup() recorded a failure
                self.log(f"voice: setup failed: {exc!r}")
            finally:
                with self.changed:
                    self.setup_attempt = None

        threading.Thread(target=run, name="speech-setup", daemon=True).start()

    def update_installed(self, reinstall: bool = False) -> None:
        """One durable automatic attempt for this target, only at daemon startup. `reinstall`: host voice was set up
        in the folder `remove_home_runtime` deleted, so it is set up here again without an installed runtime."""
        if not (reinstall or _installed()):
            return
        try:
            self.start_setup(automatic=True, reinstall=reinstall)
        except SpeechError as exc:
            if exc.status != 409:
                self.log(f"voice: automatic update unavailable: {exc}")
        except OSError as exc:  # the folder beside the home cannot be made; Settings still offers setup
            self.log(f"voice: automatic update unavailable: {exc}")
        else:
            self.log("voice: setting up again beside Altitude's home" if reinstall
                     else "voice: automatic runtime update started at release startup")

    def cancel_setup(self) -> None:
        cancel_setup()

    def remove(self) -> None:
        with self.changed:
            self._stop("Voice stopped: voice was removed from this computer.")
        remove_runtime()

    def shutdown(self) -> None:
        with self.changed:
            if self.setup_attempt is not None:
                _cancel_setup("interrupted")
            self._stop("Voice stopped: Altitude is restarting.")

    # --- the worker -------------------------------------------------------------------------------------------

    def _start(self, path: Path) -> None:
        # Answer before the browser's request deadline; it retains samples and retries a busy handoff.
        with _lease(False, timeout=0.5) as lease, _guard():
            memory = platform.available_memory()
            if memory is not None and memory < MEMORY_NEEDED:
                raise SpeechError(f"Voice isn't available right now: this computer has {memory / 1e9:.1f} GB of free "
                                  "memory and voice needs 2.5 GB.", 503)
            path = runtime()
            if path is None:
                raise SpeechError("Voice needs an update on this computer.", 409)
            with open(ROOT / "worker.log", "w") as errors:
                self.worker = subprocess.Popen(self.command(path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                               stderr=errors, text=True, env=_environment(), close_fds=True,
                                               pass_fds=(lease.fileno(),))
        self.ready, self.started, self.path = False, time.monotonic(), path
        self.outbox = queue.SimpleQueue()
        threading.Thread(target=self._read, args=(self.worker,), name="speech-reader", daemon=True).start()
        threading.Thread(target=self._write, args=(self.worker, self.outbox), name="speech-writer", daemon=True).start()
        self.log("voice: speech worker started")

    def _send(self, message: dict) -> None:
        """Queue a request: the writer thread delivers it, so a worker that is still loading or has hung never
        holds up a request or the watchdog."""
        if self.outbox is not None:
            self.outbox.put(json.dumps(message) + "\n")

    def _write(self, worker: subprocess.Popen, outbox: queue.SimpleQueue) -> None:
        assert worker.stdin is not None
        try:
            while (line := outbox.get()) is not None:
                worker.stdin.write(line)
                worker.stdin.flush()
        except (OSError, ValueError):
            with self.changed:
                if worker is self.worker:
                    self._stop("Voice stopped: the speech process stopped.")
        finally:
            try:
                worker.stdin.close()
            except (OSError, ValueError):
                pass

    def _read(self, worker: subprocess.Popen) -> None:
        assert worker.stdout is not None
        with worker.stdout:
            self._answers(worker)
        with self.changed:
            if worker is self.worker:
                self._stop("Voice stopped: the speech process stopped.")

    def _answers(self, worker: subprocess.Popen) -> None:
        for line in iter(lambda: worker.stdout.readline(1 << 20), ""):
            try:
                answer = json.loads(line)
            except ValueError:
                answer = {"error": "unreadable answer"}
            with self.changed:
                if worker is not self.worker:
                    return
                if "error" in answer:
                    self.log(f"voice: speech worker failed: {str(answer['error'])[:300]}")
                    self._stop("Voice stopped: the speech process could not run.")
                    return
                if answer.get("ready"):
                    self.ready = True
                    now = time.monotonic()
                    for waiting in self.recordings.values():  # the update deadline counts from the load
                        waiting.waiting_since = waiting.waiting_since and now
                recording = self.recordings.get(answer.get("id", ""))
                if recording is not None and isinstance(answer.get("text"), str):
                    recording.text, recording.waiting_since = answer["text"], None
                    if answer.get("final"):
                        recording.final = answer["text"]
                self.changed.notify_all()

    def _stop(self, reason: str) -> None:
        """End the worker and fail every unfinished recording with `reason`. Callers hold `changed`."""
        worker, self.worker, self.ready = self.worker, None, False
        if self.outbox is not None:
            self.outbox.put(None)
            self.outbox = None
        for recording in self.recordings.values():
            if recording.final is None and recording.error is None:
                recording.error = SpeechError(reason)
        self.changed.notify_all()
        if worker is None:
            return
        self.log(f"voice: speech worker stopped ({reason})")
        worker.terminate()
        threading.Thread(target=_reap, args=(worker,), name="speech-reap", daemon=True).start()

    def _watch(self) -> None:
        if not self.watching:
            self.watching = True
            threading.Thread(target=self._watch_loop, name="speech-watch", daemon=True).start()

    def _watch_loop(self) -> None:
        while True:
            time.sleep(WATCH_S)
            with self.changed:
                now = time.monotonic()
                for ident, recording in list(self.recordings.items()):
                    if now - recording.seen > RECORDING_IDLE_S:
                        del self.recordings[ident]
                        if self.worker is not None and recording.final is None:
                            self._send({"op": "close", "id": ident})
                if self.worker is not None:
                    if _state().get("removed") or (self.path is not None and not (self.path / "complete.json").exists()):
                        self._stop("Voice stopped: voice was removed from this computer.")
                    elif (_state().get("update", {}).get("state") == "waiting"
                          and _setting_up()
                          and not any(r.final is None and r.error is None for r in self.recordings.values())):
                        self._stop("voice update ready to check")
                    elif not self.ready and now - self.started > LOAD_S:
                        self._stop("Voice stopped: the speech process did not start in time.")
                    elif self.ready and any(r.waiting_since and now - r.waiting_since > UPDATE_S
                                            for r in self.recordings.values()):
                        self._stop("Voice stopped: the speech process stopped answering.")
                    elif not self.recordings and now - self.used > WORKER_IDLE_S:
                        self._stop("voice unused")
                if self.worker is None and not self.recordings:
                    self.watching = False
                    return


def _reap(worker: subprocess.Popen) -> None:
    try:
        worker.wait(5)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait()
