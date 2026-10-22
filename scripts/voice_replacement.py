#!/usr/bin/env python3
"""Host voice through a runtime replacement on this computer, with the real speech worker and model.

Run it in a validation run, where it has a throwaway home of its own:

    alt task validate -- python3.12 scripts/voice_replacement.py

It downloads this computer's pinned runtime and an earlier one from their public sources, checking every file
against its SHA-256, then serves them from a loopback fixture that can fail or hold a download. The earlier runtime
is the current pins with onnxruntime 1.22.0. On Linux that is the runtime this release already supports; on a Mac,
where no earlier runtime has shipped, it is a real earlier wheel that only this script's own processes admit to
the support set. The release's setup code installs it, and `speech.Host`, the daemon's own voice, drives the real
worker through the update checks: an automatic replacement at startup and the page's Retry, each with a recording
open across it; a failed and a cancelled update; reuse of checked model files; deletion of the earlier runtime only
after its last reader exits; and each restart, a fresh process starting voice as the daemon does. Recordings are
synthetic silence: this shows the runtime works, not microphone capture or word accuracy.

Writes voice-replacement.json, voice-replacement.log and the speech worker's own logs to $VALIDATION_RESULTS,
removes everything else it made, and exits non-zero when a check fails.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __name__ == "__main__" and sys.argv[1:2] != ["--restart"]:
    # Altitude reads its home at import; a restart inherits its parent's.
    os.environ["ALTITUDE_HOME"] = str(Path(tempfile.mkdtemp(prefix="voice-replacement-")) / ".altitude")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from altitude import config, platform, server, speech  # noqa: E402

_PYPI = "https://files.pythonhosted.org/packages/"
#: onnxruntime 1.22.0 for each runtime: the Linux entries are the pins of the runtime before the current one.
EARLIER = {
    "linux-x86_64-cp312": (
        "wheels/onnxruntime-1.22.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        _PYPI + "8c/60/16d219b8868cc8e8e51a68519873bdb9f5f24af080b62e917a13fff9989b/"
        "onnxruntime-1.22.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        16406377, "6964a975731afc19dc3418fad8d4e08c48920144ff590149429a5ebe0d15fb3c"),
    "linux-x86_64-cp313": (
        "wheels/onnxruntime-1.22.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        _PYPI + "1e/70/342514ade3a33ad9dd505dcee96ff1f0e7be6d0e6e9c911fe0f1505abf42/"
        "onnxruntime-1.22.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        16406707, "9fe45ee3e756300fccfd8d61b91129a121d3d80e9d38e01f03ff1295badc32b8"),
    "macos-arm64-cp312": (
        "wheels/onnxruntime-1.22.0-cp312-cp312-macosx_13_0_universal2.whl",
        _PYPI + "4d/de/9162872c6e502e9ac8c99a98a8738b2fab408123d11de55022ac4f92562a/"
        "onnxruntime-1.22.0-cp312-cp312-macosx_13_0_universal2.whl",
        34298046, "f3c0380f53c1e72a41b3f4d6af2ccc01df2c17844072233442c3a7e74851ab97"),
    "macos-arm64-cp313": (
        "wheels/onnxruntime-1.22.0-cp313-cp313-macosx_13_0_universal2.whl",
        _PYPI + "a9/65/5cb5018d5b0b7cba820d2c4a1d1b02d40df538d49138ba36a509457e4df6/"
        "onnxruntime-1.22.0-cp313-cp313-macosx_13_0_universal2.whl",
        34298715, "fe7c051236aae16d8e2e9ffbfc1e115a0cc2450e873a9c4cb75c0cc96c1dae07"),
}
DEVICE = "validation"
SILENCE = bytes(speech.RATE_BYTES)
WAIT_S = 300
LIMITS = ["Recordings are synthetic silence: the runtime runs, but microphone capture and word accuracy are not shown.",
          "Downloads during the checks come from a loopback fixture serving the checked public files.",
          "Voice is driven through speech.Host as the daemon drives it; the page, its draft and Send are not.",
          "Restarts are fresh processes starting voice as the daemon does; no installed service restarts."]


def earlier(key: str, files: list) -> list:
    """The current pins with the earlier onnxruntime in its place."""
    return [EARLIER[key] if entry[0].startswith("wheels/onnxruntime-") else entry for entry in files]


def wait(predicate, what: str, seconds: float = WAIT_S) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {what}: {speech.status()}")
        time.sleep(0.05)


def in_use(pid: int) -> list[str]:
    """The runtimes whose files the process has mapped or open."""
    if sys.platform == "linux":
        proc = Path(f"/proc/{pid}")
        names = [(proc / "maps").read_text()]
        for fd in (proc / "fd").iterdir():
            try:
                names.append(os.readlink(fd))
            except OSError:
                pass  # closed meanwhile
        text = "\n".join(names)
    else:
        listed = subprocess.run(["/usr/sbin/lsof", "-p", str(pid), "-Fn"], capture_output=True, text=True, timeout=30)
        if listed.returncode or not listed.stdout:
            raise AssertionError(f"lsof could not list the worker's files (exit {listed.returncode}): {listed.stderr}")
        text = listed.stdout
    return sorted(set(re.findall(re.escape(speech.ROOT.name) + r"/(runtime-[0-9a-f]+)/", text)))


def admit(key: str, digest: str) -> bool:
    """Admit `digest` to this process's support set; whether the release itself did not."""
    supported = speech.SUPPORTED.get(key, ())
    if digest in supported:
        return False
    speech.SUPPORTED = {**speech.SUPPORTED, key: (*supported, digest)}
    return True


def record(host: speech.Host) -> dict:
    """One recording of silence; the runtimes its worker uses once it has answered."""
    ident = host.open(DEVICE)["id"]
    host.audio(ident, DEVICE, 0, SILENCE, False)
    wait(lambda: host.ready, "the speech worker to load")
    answer = host.audio(ident, DEVICE, 1, b"", True)
    assert answer == {"text": "", "final": True}, answer
    used = in_use(host.worker.pid)
    host.close(ident, DEVICE)
    return {"answer": answer, "runtimes": used}


def restart(settings: dict) -> dict:
    """A fresh process starting voice as the daemon does, then one recording."""
    speech.ROOT = Path(settings["root"])
    files = [tuple(entry) for entry in settings["current"]]
    speech.manifest = lambda: (files, "")
    if settings["admit"]:
        admit(platform.speech_runtime()[0], settings["admit"])
    if settings.get("worker"):  # the suite's fixture worker
        speech.WORKER = Path(settings["worker"])
        platform.available_memory = lambda: None
    log: list[str] = []
    host = speech.Host(log.append)
    try:
        host.update_installed()
        started = host.setup_attempt is not None
        wait(lambda: host.setup_attempt is None, "the automatic update to end")
        return {"started": started, "view": server.voice_view(), "recording": record(host), "log": log}
    finally:
        host.shutdown()


class Fixture(ThreadingHTTPServer):
    """Serves checked files by SHA-256. `fail` answers 503; while `release` is clear, files trickle out a few bytes
    at a time, so a download stays open for the check's next step and still sees cancellation between reads."""

    def __init__(self, assets: Path):
        super().__init__(("127.0.0.1", 0), Handler)
        self.assets, self.fail, self.release = assets, False, threading.Event()
        self.release.set()
        self.requests: list[str] = []
        threading.Thread(target=self.serve_forever, name="fixture", daemon=True).start()

    def local(self, files: list) -> list:
        return [(name, f"http://127.0.0.1:{self.server_port}/{sha}", size, sha) for name, _, size, sha in files]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        sha = self.path.removeprefix("/")
        path = self.server.assets / sha
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or not path.is_file():
            self.send_error(404)
            return
        self.server.requests.append(sha)
        if self.server.fail:
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        try:
            with path.open("rb") as source:
                while data := source.read(1 << 20 if self.server.release.is_set() else 64):
                    self.wfile.write(data)
                    if not self.server.release.is_set():
                        time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError):
            pass  # a cancelled download


class Check:
    def __init__(self, assets: Path, current: list, prior: list, results: Path, worker: Path | None = None,
                 say=print):
        self.fixture, self.say = Fixture(assets), say
        self.current, self.prior = self.fixture.local(current), self.fixture.local(prior)
        self.results, self.worker = results, worker
        self.key = platform.speech_runtime()[0]
        self.earlier = speech.ROOT / f"runtime-{speech._digest(prior)}"
        self.target = speech.ROOT / f"runtime-{speech._digest(current)}"
        self.wheels = sorted(sha for name, _, _, sha in current if name.startswith("wheels/"))
        self.checks: list[str] = []
        self.log: list[str] = []
        self.hosts: list[speech.Host] = []
        self.report: dict = {"passed": False, "runtime": self.key, "earlier": self.earlier.name,
                             "replacement": self.target.name, "checks": self.checks, "limits": LIMITS}

    def run(self) -> dict:
        try:
            self._run()
            self.report["passed"] = True
        except BaseException as exc:
            self.report["failure"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            for host in self.hosts:  # also after a failed check: no worker outlives the run
                host.shutdown()
            self.fixture.release.set()
            self.fixture.shutdown()
            self.fixture.server_close()
            self.results.mkdir(parents=True, exist_ok=True)
            (self.results / "voice-replacement.log").write_text("\n".join(self.log) + "\n")
            for name in ("worker.log", "verification.log"):  # the speech worker's own errors
                if (speech.ROOT / name).is_file():
                    shutil.copyfile(speech.ROOT / name, self.results / f"speech-{name}")
        return self.report

    def check(self, text: str) -> None:
        self.checks.append(text)
        self.log.append("PASS " + text)
        self.say("PASS " + text)

    def host(self) -> speech.Host:
        host = speech.Host(self.log.append)
        self.hosts.append(host)
        return host

    def kept(self, view: dict, identity: str) -> None:
        """Host voice is still selected with the same identity, so pages' replay stays valid."""
        current = server.voice_view()
        assert current["backend"] == "host" and current["selection"] == view["selection"], current
        assert speech.installation_id() == identity

    def install_earlier(self) -> None:
        speech.manifest = lambda: (self.prior, "")
        assert speech.setup() == self.earlier
        speech.manifest = lambda: (self.current, "")
        assert not self.target.exists()

    def restart(self, label: str) -> dict:
        """A fresh process starting voice as the daemon does; it must start no update."""
        handle, name = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        settings = Path(name)
        try:
            settings.write_text(json.dumps({"root": str(speech.ROOT), "current": self.current,
                                            "admit": self.admitted and speech._digest(self.prior),
                                            "worker": str(self.worker) if self.worker else None}))
            done = subprocess.run([sys.executable, __file__, "--restart", str(settings)], capture_output=True,
                                  text=True, timeout=WAIT_S)
        finally:
            settings.unlink()
        self.log.append(f"--- restart {label} (exit {done.returncode})\n{done.stdout}{done.stderr}")
        assert done.returncode == 0, done.stderr[-2000:]
        result = json.loads(done.stdout.splitlines()[-1])
        assert result["started"] is False, result
        assert not any("automatic runtime update started" in line for line in result["log"]), result["log"]
        return result

    def replace(self, label: str, start, *, open_first: bool) -> None:
        """Replace the earlier runtime with a recording open across the update; `start` begins the update."""
        identity, view = speech.installation_id(), server.voice_view()
        assert view["backend"] == "host" and view["host"]["state"] == "ready", view
        host, mark, samples = self.host(), len(self.fixture.requests), []
        stop, order = threading.Event(), {}

        def watch():
            while not stop.is_set():
                current = server.voice_view()
                if (current["backend"], current["selection"], current["host"]["state"]) != (
                        "host", view["selection"], "ready"):
                    samples.append(current)
                time.sleep(0.02)

        def follow(worker):
            while worker.poll() is None and not stop.is_set():
                time.sleep(0.005)
            order["earlier_when_worker_exited"] = self.earlier.exists()

        def begin():
            ident = host.open(DEVICE)["id"]
            host.audio(ident, DEVICE, 0, SILENCE, False)
            wait(lambda: host.ready, "the speech worker to load")
            threading.Thread(target=follow, args=(host.worker,), name="voice-reader", daemon=True).start()
            return ident, host.worker

        watcher = threading.Thread(target=watch, name="voice-view", daemon=True)
        try:
            self.fixture.release.clear()
            if open_first:
                ident, worker = begin()
            watcher.start()
            start(host)
            assert host.setup_attempt is not None
            wait(lambda: len(self.fixture.requests) > mark, "the update to download")
            if not open_first:
                ident, worker = begin()
            assert in_use(worker.pid) == [self.earlier.name], in_use(worker.pid)
            assert host.audio(ident, DEVICE, 1, SILENCE, False)["text"] == ""
            self.fixture.release.set()
            wait(lambda: speech.status().get("update", {}).get("state") == "waiting", "the update to wait for readers")
            time.sleep(2)
            assert speech.status()["update"]["state"] == "waiting", speech.status()
            assert self.earlier.exists() and not self.target.exists() and worker.poll() is None
            final = host.audio(ident, DEVICE, 2, b"", True)
            assert final == {"text": "", "final": True}, final
            wait(lambda: host.setup_attempt is None, "the update to finish")
        finally:
            stop.set()
            if watcher.is_alive():
                watcher.join()
        assert order == {"earlier_when_worker_exited": True}, order
        assert speech.runtime() == self.target and not self.earlier.exists(), speech.status()
        assert not (speech.ROOT / ".staging").exists()
        assert not samples, samples[:3]
        self.kept(view, identity)
        assert host.audio(ident, DEVICE, 2, b"", True) == final
        requested = self.fixture.requests[mark:]
        assert sorted(requested) == self.wheels, (requested, self.wheels)
        self.report[f"{label}_transfer_bytes"] = sum(size for _, _, size, sha in self.current if sha in requested)
        self.check(f"{label}: host voice stays selected and ready while the update downloads, waits and checks; "
                   "the recording open across it keeps its final answer on the earlier runtime, and replays it after")
        self.check(f"{label}: the earlier runtime is deleted only after its last worker exits; only the "
                   "replacement's wheels are downloaded, the checked model files are reused, and no staging remains")
        assert record(host)["runtimes"] == [self.target.name]
        host.shutdown()
        result = self.restart(f"after-{label}")
        assert result["recording"]["runtimes"] == [self.target.name], result
        assert len(self.fixture.requests) == mark + len(requested)
        assert result["view"]["selection"] == view["selection"]
        self.check(f"{label}: the next restart starts no update and downloads nothing; its worker loads the replacement")

    def _run(self) -> None:
        self.install_earlier()
        serves = speech.runtime() == self.earlier
        self.admitted = admit(self.key, speech._digest(self.prior))
        assert serves is not self.admitted and speech.runtime() == self.earlier
        self.report["admitted_for_this_run"] = self.admitted
        self.check("the release's setup code installs the earlier runtime"
                   + (", which the release does not serve; only this run admits it to the support set"
                      if self.admitted else ", which the release supports"))

        # 1. A restart's automatic replacement, a recording started while it downloads.
        self.replace("automatic", lambda host: host.update_installed(), open_first=False)

        # 2. A failed automatic replacement, then the next restart.
        self.install_earlier()
        assert speech.runtime() == self.earlier
        view, identity = server.voice_view(), speech.installation_id()
        mark = len(self.fixture.requests)
        self.fixture.fail = True
        host = self.host()
        host.update_installed()
        assert host.setup_attempt is not None
        wait(lambda: host.setup_attempt is None, "the update to fail")
        self.fixture.fail = False
        assert len(self.fixture.requests) > mark
        status = speech.status()
        assert status["state"] == "ready" and status["update"]["state"] == "failed", status
        assert self.earlier.exists() and not self.target.exists() and not (speech.ROOT / ".staging").exists()
        assert record(host)["runtimes"] == [self.earlier.name]
        self.kept(view, identity)
        host.shutdown()
        self.check("a failed automatic replacement keeps host voice selected and the earlier runtime serving, and "
                   "removes its staging")
        mark = len(self.fixture.requests)
        result = self.restart("after-failure")
        assert len(self.fixture.requests) == mark and result["view"]["host"]["update"]["state"] == "failed", result
        assert result["recording"]["runtimes"] == [self.earlier.name], result
        assert result["view"]["selection"] == view["selection"], result
        self.check("the next restart does not repeat the failed update; dictation stays on the earlier runtime")

        # 3. The page's Retry, cancelled while it downloads, then the next restart.
        self.fixture.release.clear()
        host = self.host()
        mark = len(self.fixture.requests)
        host.start_setup()
        wait(lambda: len(self.fixture.requests) > mark, "the update to download")
        host.cancel_setup()
        self.fixture.release.set()
        wait(lambda: host.setup_attempt is None, "the update to stop")
        status = speech.status()
        assert status["state"] == "ready" and status["update"]["state"] == "cancelled", status
        assert self.earlier.exists() and not self.target.exists() and not (speech.ROOT / ".staging").exists()
        assert record(host)["runtimes"] == [self.earlier.name]
        self.kept(view, identity)
        host.shutdown()
        self.check("cancelling Retry keeps host voice selected and the earlier runtime serving, and removes its staging")
        mark = len(self.fixture.requests)
        result = self.restart("after-cancel")
        assert len(self.fixture.requests) == mark and result["view"]["host"]["update"]["state"] == "cancelled", result
        assert result["recording"]["runtimes"] == [self.earlier.name], result
        assert result["view"]["selection"] == view["selection"], result
        self.check("the next restart does not repeat the cancelled update; dictation stays on the earlier runtime")

        # 4. The page's Retry with a recording already open.
        self.replace("retry", lambda host: host.start_setup(), open_first=True)


def download(files: list, assets: Path) -> int:
    """Fetch each pinned file once from its public source, refusing any that does not match its pin."""
    total = 0
    for name, url, size, sha in files:
        target = assets / sha
        if target.exists():
            continue
        print(f"downloading {name} ({size} bytes)", flush=True)
        hashed, seen = hashlib.sha256(), 0
        with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as out:
            while block := response.read(1 << 20):
                seen += len(block)
                if seen > size:
                    raise AssertionError(f"{name} is larger than pinned")
                hashed.update(block)
                out.write(block)
        if seen != size or hashed.hexdigest() != sha:
            raise AssertionError(f"{name} does not match its pin")
        total += seen
    return total


def main() -> int:
    work = config.ROOT.parent  # the run's own home, beside which voice keeps its runtimes
    results = Path(os.environ.get("VALIDATION_RESULTS") or work.with_name(work.name + "-results"))
    report: dict = {"passed": False, "limits": LIMITS}
    try:
        current, reason = speech.manifest()
        if current is None:
            raise AssertionError(f"voice cannot run here: {reason}")
        prior = earlier(platform.speech_runtime()[0], current)
        assets = work / "assets"
        assets.mkdir()
        downloaded = download(current + prior, assets)
        check = Check(assets, current, prior, results)
        report = check.report
        report.update(host=platform.host_identity(), python=sys.version.split()[0], downloaded_bytes=downloaded)
        check.run()
    except BaseException as exc:
        report.setdefault("failure", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        report["cleanup"] = "the run's home, speech runtimes and downloaded files removed" + (
            "" if not work.exists() else f"; {work} remains")
        results.mkdir(parents=True, exist_ok=True)
        (results / "voice-replacement.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--restart"]:
        print(json.dumps(restart(json.loads(Path(sys.argv[2]).read_text()))), flush=True)
    else:
        sys.exit(main())
