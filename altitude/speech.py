"""Host speech: live dictation transcribed on this computer, part of the capability seam.

Setup downloads a pinned runtime once — the speech model and the wheels of the libraries that run it — checks
each file against the SHA-256 written here, unpacks it into a staging folder and moves the finished runtime into
place in one step. Only a runtime whose completion record matches this release's manifest is ever used.

A recording streams from the browser as 16 kHz mono 16-bit samples, one numbered request at a time. The daemon
hands the samples to one speech worker process (`speech_worker.py`), shared by every recording, and answers each
request at once with the text so far. The worker starts with the first recording, stops after WORKER_IDLE_S
without one, and is killed when it misses a deadline. Audio lives only in memory, for its recording.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from . import config, platform, state as S

ROOT = config.ROOT / "speech"
WORKER = Path(__file__).resolve().parent / "speech_worker.py"

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
#: The wheels for each runtime `platform.speech_runtime` names: onnxruntime 1.22.0 and numpy 2.5.3.
WHEELS = {
    "linux-x86_64-cp312": [
        ("wheels/onnxruntime-1.22.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "8c/60/16d219b8868cc8e8e51a68519873bdb9f5f24af080b62e917a13fff9989b/"
         "onnxruntime-1.22.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         16406377, "6964a975731afc19dc3418fad8d4e08c48920144ff590149429a5ebe0d15fb3c"),
        ("wheels/numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "65/af/aa78d1a88805456e212b65461354cd943197fb9acecc4c90fd12295123a3/"
         "numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         16717410, "b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3"),
    ],
    "linux-x86_64-cp313": [
        ("wheels/onnxruntime-1.22.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "1e/70/342514ade3a33ad9dd505dcee96ff1f0e7be6d0e6e9c911fe0f1505abf42/"
         "onnxruntime-1.22.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         16406707, "9fe45ee3e756300fccfd8d61b91129a121d3d80e9d38e01f03ff1295badc32b8"),
        ("wheels/numpy-2.5.3-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         _PYPI + "3a/1b/3b16a9bc514a440a7a0883684111dcb1ef1aee960af2ca95da8fc775f124/"
         "numpy-2.5.3-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         16708577, "a5fa86b80fd24bcd1aff83ad23be44ea323de3f787be8f8b15d4a65621e25321"),
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


def runtime() -> Path | None:
    """The finished runtime for this release, or None."""
    files, _ = manifest()
    if files is None:
        return None
    digest = _digest(files)
    path = ROOT / f"runtime-{digest}"
    return path if _complete(path, digest) else None


def _setting_up() -> bool:
    if not (ROOT / ".lock").exists():
        return False
    with open(ROOT / ".lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def status() -> dict:
    """Where host voice stands: unavailable (with why), absent, setting-up (with progress), failed, outdated or
    ready. The download size is always known, so Settings can say what setup costs."""
    files, reason = manifest()
    if files is None:
        return {"state": "unavailable", "reason": reason}
    total = sum(size for _, _, size, _ in files)
    view = {"download_bytes": total}
    if _setting_up():
        progress = S.read_json(ROOT / ".staging" / "progress.json", None) or {}
        return {**view, "state": "setting-up", "done_bytes": min(int(progress.get("done", 0)), total)}
    if runtime() is not None:
        return {**view, "state": "ready"}
    failure = S.read_json(ROOT / "failure.json", None)
    if failure:
        return {**view, "state": "failed", "reason": failure.get("reason", "")}
    return {**view, "state": "outdated" if _runtimes() else "absent"}


def _download(url: str, target: Path, size: int, digest: str, advance: Callable[[int], None],
              cancel: threading.Event) -> None:
    hashed, seen = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as out:
        while block := response.read(BLOCK):
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
    """Unpack one verified wheel with the installer's path rules: no absolute, parent, backslash or link entries."""
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


def _lock():
    """Take the setup lock, which `status()` reads as setting-up; a second setup is refused."""
    ROOT.mkdir(parents=True, exist_ok=True)
    handle = open(ROOT / ".lock", "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SpeechError("Voice is already being set up.", 409) from None
    return handle


def setup(cancel: threading.Event | None = None, lock=None) -> Path:
    """Download, verify and activate this release's runtime under `lock` (taken here when not given). One setup
    runs at a time across the daemon and the CLI; a second one is refused. Cancel or any failure leaves no staging
    behind and the previous state unchanged, and a failure (not a cancel) is remembered for Settings until the
    next setup."""
    cancel = cancel or threading.Event()
    with lock or _lock():
        files, reason = manifest()
        if files is None:
            raise SpeechError(f"Voice isn't available on this computer: {reason}.", 409)
        staging = ROOT / ".staging"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            digest = _digest(files)
            target = ROOT / f"runtime-{digest}"
            if not _complete(target, digest):
                _stage(files, staging, digest, cancel)
                shutil.rmtree(target, ignore_errors=True)
                os.replace(staging, target)
            for old in _runtimes():
                if old != target:
                    shutil.rmtree(old, ignore_errors=True)
            (ROOT / "failure.json").unlink(missing_ok=True)
            return target
        except BaseException as exc:
            if not cancel.is_set():
                reason = str(exc) if isinstance(exc, SpeechError) else "Setup stopped: a download failed. Try again."
                S.write_json(ROOT / "failure.json", {"reason": reason, "at": S.now()})
            if isinstance(exc, (OSError, ValueError, zipfile.BadZipFile)) and not isinstance(exc, SpeechError):
                raise SpeechError("Setup stopped: a download failed. Try again.") from exc
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _stage(files, staging: Path, digest: str, cancel: threading.Event) -> None:
    (staging / "model").mkdir(parents=True)
    (staging / "wheels").mkdir()
    total, done, reported = sum(size for _, _, size, _ in files), 0, 0

    def advance(count: int) -> None:
        nonlocal done, reported
        done += count
        if done - reported >= 8 * BLOCK or done == total:
            reported = done
            S.write_json(staging / "progress.json", {"done": done, "total": total})

    advance(0)
    for name, url, size, sha in files:
        _download(url, staging / name, size, sha, advance, cancel)
    for wheel in sorted((staging / "wheels").iterdir()):
        _unpack(wheel, staging / "site")
    shutil.rmtree(staging / "wheels")
    (staging / "progress.json").unlink()
    S.write_json(staging / "complete.json", {"digest": digest, "at": S.now()})


def remove_runtime() -> None:
    """Delete every runtime and the remembered failure; a setup in progress must be stopped first."""
    if _setting_up():
        raise SpeechError("Voice is being set up. Cancel setup first.", 409)
    for path in _runtimes():
        shutil.rmtree(path, ignore_errors=True)
    (ROOT / "failure.json").unlink(missing_ok=True)


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
        self.setup_cancel: threading.Event | None = None

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
            recording.seen = time.monotonic()
            if seq == recording.seq:
                if final and recording.final is None and recording.error is None:
                    return self._finish(ident, recording)
                return recording.answer
            if recording.error is not None:
                raise recording.error
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

    def start_setup(self) -> None:
        """Set up in the background; Settings follows `status()`."""
        with self.changed:
            if self.setup_cancel is not None:
                raise SpeechError("Voice is already being set up.", 409)
            lock = _lock()  # held before answering, so Settings reads setting-up at once
            self.setup_cancel = cancel = threading.Event()

        def run() -> None:
            try:
                setup(cancel, lock)
                self.log("voice: host runtime set up")
            except SpeechError as exc:
                self.log(f"voice: setup ended: {exc}")
            except Exception as exc:  # noqa: BLE001 — the reason stays in the private log; setup() recorded a failure
                self.log(f"voice: setup failed: {exc!r}")
            finally:
                with self.changed:
                    self.setup_cancel = None

        threading.Thread(target=run, name="speech-setup", daemon=True).start()

    def cancel_setup(self) -> None:
        with self.changed:
            if self.setup_cancel is not None:
                self.setup_cancel.set()

    def remove(self) -> None:
        with self.changed:
            if self.setup_cancel is not None:
                raise SpeechError("Voice is being set up. Cancel setup first.", 409)
            self._stop("Voice stopped: voice was removed from this computer.")
        remove_runtime()

    def shutdown(self) -> None:
        with self.changed:
            if self.setup_cancel is not None:
                self.setup_cancel.set()
            self._stop("Voice stopped: Altitude is restarting.")

    # --- the worker -------------------------------------------------------------------------------------------

    def _start(self, path: Path) -> None:
        memory = platform.available_memory()
        if memory is not None and memory < MEMORY_NEEDED:
            raise SpeechError(f"Voice isn't available right now: this computer has {memory / 1e9:.1f} GB of free "
                              "memory and voice needs 2.5 GB.", 503)
        environment = {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "TMPDIR") if name in os.environ}
        with open(ROOT / "worker.log", "w") as errors:
            self.worker = subprocess.Popen(self.command(path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=errors, text=True, env=environment, close_fds=True)
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
                    if self.path is not None and not (self.path / "complete.json").exists():
                        self._stop("Voice stopped: voice was removed from this computer.")
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
