"""Host voice: setup of the pinned runtime, the recordings the daemon keeps, and the one speech worker.

The worker is replaced at the capability seam by a small fixture process that speaks the same protocol, so the
supervision, sequence rules and failure paths run for real without the model.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import sys
import threading
import time
import urllib.error
import zipfile
from pathlib import Path

from tests.support import AltitudeCase
from altitude import access, config, platform, server, speech, speech_worker, state as S, terminal

#: Answers like the worker: the text is how many samples the recording holds. `argv[1]` picks a failure.
FAKE_WORKER = r'''
import json, sys, base64, time
mode = sys.argv[1]
if mode == "fail-load":
    print(json.dumps({"error": "RuntimeError: no model"}), flush=True); sys.exit(1)
if mode == "hang-load":
    time.sleep(60)
if mode == "slow-load":
    time.sleep(0.8)
print(json.dumps({"ready": True}), flush=True)
held = {}
for line in sys.stdin:
    request = json.loads(line)
    ident, op = request["id"], request["op"]
    if op == "open":
        held[ident] = 0
    elif op == "close":
        held.pop(ident, None)
    elif op == "audio":
        if mode == "crash":
            sys.exit(3)
        if mode == "hang-update":
            time.sleep(60)
        held[ident] += len(base64.b64decode(request["pcm"])) // 2
        print(json.dumps({"id": ident, "text": f"{held[ident]} samples"}), flush=True)
    elif op == "finish":
        if mode == "hang-finish":
            continue
        print(json.dumps({"id": ident, "text": f"{held.pop(ident)} samples.", "final": True}), flush=True)
'''


class SpeechCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(speech, "ROOT", self.tmp / "speech")
        self.patch(speech, "WATCH_S", 0.05)
        self.worker_file = self.tmp / "fake_worker.py"
        self.worker_file.write_text(FAKE_WORKER)
        self.logs: list[str] = []

    def host(self, mode: str = "ok") -> speech.Host:
        host = speech.Host(self.logs.append, command=lambda path: [sys.executable, str(self.worker_file), mode])
        self.addCleanup(host.shutdown)
        return host

    def runtime(self) -> Path:
        """A finished runtime as setup leaves it, for this host's manifest."""
        files, _ = speech.manifest()
        path = speech.ROOT / f"runtime-{speech._digest(files)}"
        path.mkdir(parents=True)
        S.write_json(path / "complete.json", {"digest": speech._digest(files)})
        return path

    def wait(self, condition, seconds: float = 5.0):
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                self.fail("condition never held")
            time.sleep(0.02)

    def text(self, host, ident, seq, samples: int, device=None, final=False) -> dict:
        return host.audio(ident, device, seq, b"\0\0" * samples, final)


class TestRecordings(SpeechCase):
    def setUp(self):
        super().setUp()
        self.patch(platform, "speech_runtime", return_value=("linux-x86_64-cp312", ""))
        self.patch(platform, "available_memory", return_value=8_000_000_000)
        self.runtime()

    def test_text_follows_the_samples_and_the_final_answer_waits_for_all_of_them(self):
        host = self.host()
        ident = host.open("phone")["id"]
        self.assertEqual(self.text(host, ident, 0, 800, "phone")["text"], "")
        self.text(host, ident, 1, 400, "phone")
        seq = iter(range(2, 1000))
        self.wait(lambda: self.text(host, ident, next(seq), 0, "phone")["text"] == "1200 samples")
        self.assertEqual(self.text(host, ident, next(seq), 100, "phone", final=True), {"text": "1300 samples.", "final": True})

    def test_a_repeated_chunk_is_answered_again_and_never_added_twice(self):
        host = self.host()
        ident = host.open(None)["id"]
        first = self.text(host, ident, 0, 800)
        self.assertEqual(self.text(host, ident, 0, 800), first)
        final = self.text(host, ident, 1, 0, final=True)
        self.assertEqual(final, {"text": "800 samples.", "final": True})
        self.assertEqual(self.text(host, ident, 1, 0, final=True), final)

    def test_a_final_repeating_the_last_chunk_finishes_without_adding_it(self):
        host = self.host()
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 800)
        self.assertEqual(self.text(host, ident, 0, 800, final=True), {"text": "800 samples.", "final": True})

    def test_a_skipped_chunk_ends_the_recording(self):
        host = self.host()
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 800)
        with self.assertRaisesRegex(speech.SpeechError, "part of the recording was lost") as caught:
            self.text(host, ident, 2, 800)
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaisesRegex(speech.SpeechError, "lost"):
            self.text(host, ident, 1, 0, final=True)

    def test_a_recording_belongs_to_the_device_that_opened_it(self):
        host = self.host()
        ident = host.open("phone")["id"]
        with self.assertRaises(speech.SpeechError) as caught:
            self.text(host, ident, 0, 800, "laptop")
        self.assertEqual(caught.exception.status, 403)
        host.close(ident, "laptop")
        self.text(host, ident, 0, 800, "phone")

    def test_a_cancelled_recording_is_gone(self):
        host = self.host()
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 800)
        host.close(ident, None)
        with self.assertRaisesRegex(speech.SpeechError, "has ended"):
            self.text(host, ident, 1, 800)

    def test_malformed_and_oversized_chunks_are_refused(self):
        host = self.host()
        ident = host.open(None)["id"]
        for pcm in (b"\0", b"\0" * (speech.CHUNK_LIMIT + 2)):
            with self.assertRaises(speech.SpeechError) as caught:
                host.audio(ident, None, 0, pcm, False)
            self.assertEqual(caught.exception.status, 400)

    def test_the_recording_stops_at_ten_minutes(self):
        self.patch(speech, "RECORDING_LIMIT", 4000)
        host = self.host()
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 1500)
        with self.assertRaises(speech.SpeechError) as caught:
            self.text(host, ident, 1, 1500)
        self.assertEqual(caught.exception.status, 413)

    def test_a_third_recording_is_busy_and_a_finished_one_frees_its_place(self):
        host = self.host()
        first = host.open("a")["id"]
        host.open("b")
        with self.assertRaisesRegex(speech.SpeechError, "busy"):
            host.open("c")
        self.text(host, first, 0, 0, "a", final=True)
        host.open("c")

    def test_one_worker_serves_every_recording(self):
        host = self.host()
        host.open("a")
        worker = host.worker
        host.open("b")
        self.assertIs(host.worker, worker)
        self.assertEqual(sum("worker started" in line for line in self.logs), 1)

    def test_an_idle_recording_is_dropped(self):
        self.patch(speech, "RECORDING_IDLE_S", 0.1)
        host = self.host()
        ident = host.open(None)["id"]
        self.wait(lambda: ident not in host.recordings)
        with self.assertRaisesRegex(speech.SpeechError, "has ended"):
            self.text(host, ident, 0, 800)

    def test_an_unused_worker_exits(self):
        self.patch(speech, "WORKER_IDLE_S", 0.1)
        host = self.host()
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 0, final=True)
        host.close(ident, None)
        self.wait(lambda: host.worker is None)

    def test_a_crashed_worker_stops_its_recordings_and_the_next_one_starts_it_again(self):
        host = self.host("crash")
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 800)
        self.wait(lambda: host.worker is None)
        with self.assertRaisesRegex(speech.SpeechError, "Voice stopped: the speech process stopped"):
            self.text(host, ident, 1, 800)
        host.open(None)
        self.assertIsNotNone(host.worker)

    def test_a_worker_that_cannot_load_stops_voice_with_a_fixed_reason(self):
        host = self.host("fail-load")
        ident = host.open(None)["id"]
        self.wait(lambda: host.worker is None)
        with self.assertRaisesRegex(speech.SpeechError, "could not run"):
            self.text(host, ident, 0, 800)
        self.assertTrue(any("no model" in line for line in self.logs))

    def test_missed_deadlines_kill_the_worker(self):
        for mode, deadline in (("hang-load", "LOAD_S"), ("hang-update", "UPDATE_S")):
            with self.subTest(mode=mode):
                self.patch(speech, deadline, 0.3)
                host = self.host(mode)
                ident = host.open(None)["id"]
                started = time.monotonic()
                self.text(host, ident, 0, 800)  # answered at once: the reply never waits for the worker
                self.assertLess(time.monotonic() - started, 0.5)
                worker = host.worker
                self.wait(lambda: host.worker is None)
                self.wait(lambda: worker.poll() is not None)
                with self.assertRaisesRegex(speech.SpeechError, "Voice stopped"):
                    self.text(host, ident, 1, 800)

    def test_a_slow_load_is_not_mistaken_for_a_hung_update(self):
        self.patch(speech, "UPDATE_S", 0.3)
        host = self.host("slow-load")
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 800)
        worker = host.worker
        seq = iter(range(1, 1000))
        self.wait(lambda: self.text(host, ident, next(seq), 0)["text"] == "800 samples")
        self.assertIs(host.worker, worker)

    def test_cancelling_a_stop_that_waits_ends_only_that_recording(self):
        host = self.host("hang-finish")
        stopping, other = host.open(None)["id"], host.open("laptop")["id"]
        self.text(host, stopping, 0, 800)
        worker, outcome = host.worker, []

        def stop():
            try:
                self.text(host, stopping, 1, 0, final=True)
            except speech.SpeechError as exc:
                outcome.append(exc)

        waiting = threading.Thread(target=stop)
        waiting.start()
        time.sleep(0.1)
        host.close(stopping, None)
        waiting.join(2)
        self.assertFalse(waiting.is_alive())
        self.assertEqual(outcome[0].status, 410)
        self.assertIs(host.worker, worker)
        self.text(host, other, 0, 800, "laptop")

    def test_a_final_answer_that_never_comes_stops_voice(self):
        self.patch(speech, "FINISH_S", 0.3)
        host = self.host("hang-finish")
        ident = host.open(None)["id"]
        with self.assertRaisesRegex(speech.SpeechError, "did not finish in time"):
            self.text(host, ident, 0, 800, final=True)
        self.assertIsNone(host.worker)

    def test_removing_voice_stops_the_recordings_first(self):
        host = self.host()
        ident = host.open(None)["id"]
        self.text(host, ident, 0, 800)
        host.remove()
        with self.assertRaisesRegex(speech.SpeechError, "removed"):
            self.text(host, ident, 1, 800)
        self.assertEqual(speech.status()["state"], "absent")
        with self.assertRaisesRegex(speech.SpeechError, "one-time download"):
            host.open(None)

    def test_a_runtime_removed_from_the_command_line_stops_the_worker(self):
        host = self.host()
        ident = host.open(None)["id"]
        speech.remove_runtime()
        self.wait(lambda: host.worker is None)
        with self.assertRaisesRegex(speech.SpeechError, "removed"):
            self.text(host, ident, 0, 800)

    def test_shutdown_stops_the_worker(self):
        host = self.host()
        host.open(None)
        worker = host.worker
        host.shutdown()
        self.wait(lambda: worker.poll() is not None)

    def test_too_little_memory_refuses_to_start_the_worker(self):
        platform.available_memory.return_value = 1_000_000_000
        with self.assertRaisesRegex(speech.SpeechError, "1.0 GB of free memory") as caught:
            self.host().open(None)
        self.assertEqual(caught.exception.status, 503)


class TestAvailability(SpeechCase):
    def test_an_unsupported_host_is_unavailable_with_its_reason(self):
        self.patch(platform, "speech_runtime", return_value=(None, "voice runs on Linux x86_64 only for now"))
        self.assertEqual(speech.status(), {"state": "unavailable", "reason": "voice runs on Linux x86_64 only for now"})
        with self.assertRaisesRegex(speech.SpeechError, "isn't available on this computer: voice runs on Linux"):
            self.host().open(None)

    def test_an_unpinned_python_is_unavailable(self):
        self.patch(platform, "speech_runtime", return_value=("linux-x86_64-cp399", ""))
        self.assertEqual(speech.status()["state"], "unavailable")

    def test_a_runtime_from_another_release_is_outdated(self):
        self.patch(platform, "speech_runtime", return_value=("linux-x86_64-cp312", ""))
        (speech.ROOT / "runtime-0000").mkdir(parents=True)
        S.write_json(speech.ROOT / "runtime-0000" / "complete.json", {"digest": "0000"})
        self.assertEqual(speech.status()["state"], "outdated")

    def test_the_platform_seam_names_this_interpreter(self):
        key, reason = platform.speech_runtime()
        if key is not None:
            self.assertEqual(key.rsplit("-", 1)[1], f"cp{sys.version_info.major}{sys.version_info.minor}")
        else:
            self.assertTrue(reason)


def _wheel(entries: dict[str, bytes], link: str | None = None) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
        if link:
            info = zipfile.ZipInfo(link)
            info.external_attr = 0o120777 << 16
            bundle.writestr(info, "/etc/passwd")
    return data.getvalue()


class TestSetup(SpeechCase):
    def setUp(self):
        super().setUp()
        self.served: dict[str, bytes] = {}
        self.fetched: list[str] = []
        self.patch(speech.urllib.request, "urlopen", new=self.urlopen)
        self.install({"model/encoder.onnx": b"weights" * 100,
                      "wheels/lib.whl": _wheel({"lib/__init__.py": b"VALUE = 1\n"})})

    def install(self, files: dict[str, bytes], pinned: dict[str, bytes] | None = None):
        """Serve `files` and pin the manifest to `pinned` (default: the same content)."""
        manifest = []
        for name, content in files.items():
            url = f"https://files.example/{name}"
            self.served[url] = content
            expected = (pinned or files)[name]
            manifest.append((name, url, len(expected), hashlib.sha256(expected).hexdigest()))
        self.patch(speech, "manifest", return_value=(manifest, ""))

    def urlopen(self, url, timeout):
        self.fetched.append(url)
        if url not in self.served:
            raise urllib.error.URLError("offline")
        return io.BytesIO(self.served[url])

    def test_setup_verifies_unpacks_and_activates_the_runtime(self):
        self.assertEqual(speech.status()["state"], "absent")
        path = speech.setup()
        self.assertEqual(speech.runtime(), path)
        self.assertEqual((path / "site/lib/__init__.py").read_text(), "VALUE = 1\n")
        self.assertEqual((path / "model/encoder.onnx").read_bytes(), b"weights" * 100)
        self.assertFalse((path / "wheels").exists())
        self.assertFalse((speech.ROOT / ".staging").exists())
        self.assertEqual(speech.status(), {"state": "ready", "download_bytes": 700 + len(self.served["https://files.example/wheels/lib.whl"])})

    def test_a_ready_runtime_is_not_downloaded_again(self):
        speech.setup()
        self.fetched.clear()
        speech.setup()
        self.assertEqual(self.fetched, [])

    def test_a_file_that_does_not_match_its_checksum_is_refused_and_remembered(self):
        self.install({"model/encoder.onnx": b"tampered" * 100, "wheels/lib.whl": self.served["https://files.example/wheels/lib.whl"]},
                     pinned={"model/encoder.onnx": b"weights!" * 100, "wheels/lib.whl": self.served["https://files.example/wheels/lib.whl"]})
        with self.assertRaisesRegex(speech.SpeechError, "encoder.onnx did not match its checksum"):
            speech.setup()
        self.assertIsNone(speech.runtime())
        self.assertFalse((speech.ROOT / ".staging").exists())
        self.assertEqual(speech.status()["state"], "failed")
        self.assertIn("checksum", speech.status()["reason"])

    def test_a_larger_download_stops_at_its_pinned_size(self):
        self.install({"model/encoder.onnx": b"weights" * 200, "wheels/lib.whl": self.served["https://files.example/wheels/lib.whl"]},
                     pinned={"model/encoder.onnx": b"weights" * 100, "wheels/lib.whl": self.served["https://files.example/wheels/lib.whl"]})
        with self.assertRaisesRegex(speech.SpeechError, "larger than expected"):
            speech.setup()

    def test_an_unsafe_wheel_is_never_unpacked(self):
        for name, wheel in (("parent", _wheel({"../escape.py": b"x"})), ("link", _wheel({}, link="lib/link"))):
            with self.subTest(name):
                self.install({"wheels/bad.whl": wheel})
                with self.assertRaisesRegex(speech.SpeechError, "unsafe entry"):
                    speech.setup()
                self.assertFalse((speech.ROOT / "escape.py").exists())
                self.assertIsNone(speech.runtime())

    def test_a_network_failure_is_remembered_and_retry_succeeds(self):
        served = self.served.pop("https://files.example/model/encoder.onnx")
        with self.assertRaisesRegex(speech.SpeechError, "a download failed"):
            speech.setup()
        self.assertEqual(speech.status()["state"], "failed")
        self.served["https://files.example/model/encoder.onnx"] = served
        speech.setup()
        self.assertEqual(speech.status()["state"], "ready")

    def test_cancel_leaves_nothing_behind_and_no_failure(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(speech.SpeechError, "cancelled"):
            speech.setup(cancel)
        self.assertFalse((speech.ROOT / ".staging").exists())
        self.assertEqual(speech.status()["state"], "absent")

    def test_one_setup_at_a_time_and_remove_waits_for_it(self):
        started, release = threading.Event(), threading.Event()

        def slow(url, timeout):
            started.set()
            release.wait(5)
            return io.BytesIO(self.served[url])

        self.patch(speech.urllib.request, "urlopen", new=slow)
        worker = threading.Thread(target=speech.setup)
        worker.start()
        started.wait(5)
        self.assertEqual(speech.status()["state"], "setting-up")
        with self.assertRaisesRegex(speech.SpeechError, "already being set up"):
            speech.setup()
        with self.assertRaisesRegex(speech.SpeechError, "Cancel setup first"):
            speech.remove_runtime()
        release.set()
        worker.join(5)
        self.assertEqual(speech.status()["state"], "ready")

    def test_setup_replaces_an_outdated_runtime(self):
        (speech.ROOT / "runtime-0000").mkdir(parents=True)
        speech.setup()
        self.assertEqual([path.name for path in speech._runtimes()], [speech.runtime().name])

    def test_the_daemon_reads_setting_up_as_soon_as_it_accepts_setup(self):
        release = threading.Event()

        def slow(url, timeout):
            release.wait(5)
            return io.BytesIO(self.served[url])

        self.patch(speech.urllib.request, "urlopen", new=slow)
        host = self.host()
        host.start_setup()
        self.assertEqual(speech.status()["state"], "setting-up")
        with self.assertRaisesRegex(speech.SpeechError, "already being set up"):
            speech.setup()
        release.set()
        self.wait(lambda: host.setup_cancel is None)
        self.assertEqual(speech.status()["state"], "ready")

    def test_the_daemon_sets_up_in_the_background_and_can_cancel(self):
        host = self.host()
        host.start_setup()
        self.wait(lambda: host.setup_cancel is None)
        self.assertEqual(speech.status()["state"], "ready")
        self.assertTrue(any("set up" in line for line in self.logs))


class TestWorkerCuts(AltitudeCase):
    def test_a_pause_cut_lands_in_the_middle_of_the_last_quiet_run_after_the_lead(self):
        levels = [0.2] * 10 + [0.0] * 6 + [0.2] * 10 + [0.0] * 4 + [0.2] * 3
        self.assertEqual(speech_worker.pause_cut(levels, 2, 4), 28)
        self.assertEqual(speech_worker.pause_cut(levels, 2, 5), 13)
        self.assertIsNone(speech_worker.pause_cut(levels, 2, 7))
        self.assertIsNone(speech_worker.pause_cut(levels[:5], 10, 2))

    def test_quiet_is_relative_to_the_microphone_level(self):
        loud = [0.02] * 20 + [0.001] * 5 + [0.02] * 20
        self.assertEqual(speech_worker.pause_cut(loud, 2, 5), 22)

    def test_without_a_pause_the_settled_words_are_kept_except_the_last(self):
        tokens = [" Hel", "lo", " there", " my", " friend"]
        starts = [0.0, 0.2, 0.5, 11.0, 11.5]
        self.assertEqual(speech_worker.word_cut(tokens, starts, 12.0), (3, 10.96))
        self.assertIsNone(speech_worker.word_cut([" one", " two"], [11.5, 11.8], 12.0))


class TestHostVoiceRoutes(SpeechCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "ROOT", self.tmp / "machine")
        config.ensure_root()
        S.write_json(config.ROOT / "settings.json", {"voice": "host"})
        self.patch(platform, "speech_runtime", return_value=("linux-x86_64-cp312", ""))
        self.patch(platform, "available_memory", return_value=None)
        self.patch(terminal, "agent_connection", return_value=False)
        self.patch(server, "SPEECH", self.host())
        self.patch(server, "log", new=self.logs.append)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(2)))

    def post(self, path: str, body: bytes = b"{}", content_type="application/json", **headers):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        headers = {"Content-Type": content_type, "X-Voice-Selection": server.voice_view()["selection"], **headers}
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def start(self):
        return self.post("/api/voice/live")

    def test_settings_reports_host_voice_and_its_state(self):
        view = server.voice_view()
        self.assertEqual((view["backend"], view["host"]["state"]), ("host", "absent"))
        self.assertGreater(view["host"]["download_bytes"], 600_000_000)

    def test_a_recording_streams_and_finishes_over_http(self):
        self.runtime()
        status, opened = self.start()
        self.assertEqual(status, 200)
        base = f"/api/voice/live/{opened['id']}/audio"
        self.assertEqual(self.post(f"{base}?seq=0", b"\0\0" * 800, "application/octet-stream")[0], 200)
        self.assertEqual(self.post(f"{base}?seq=1&final=1", b"\0\0" * 200, "application/octet-stream"),
                         (200, {"text": "1000 samples.", "final": True}))
        self.assertEqual(self.post(f"{base}?seq=3", b"", "application/octet-stream")[0], 409)
        self.assertEqual(self.post(f"/api/voice/live/{opened['id']}/cancel"), (200, {"ok": True}))
        self.assertEqual(self.post(f"{base}?seq=2", b"", "application/octet-stream")[0], 410)

    def test_audio_needs_the_selection_it_started_with(self):
        self.runtime()
        selection = server.voice_view()["selection"]
        ident = self.start()[1]["id"]
        chunk = {"X-Voice-Selection": selection}
        self.assertEqual(self.post(f"/api/voice/live/{ident}/audio?seq=0", b"\0\0" * 800, "application/octet-stream",
                                   **chunk)[0], 200)
        S.write_json(config.ROOT / "settings.json", {"voice": "browser"})
        status, payload = self.post(f"/api/voice/live/{ident}/audio?seq=1", b"\0\0" * 800, "application/octet-stream",
                                    **chunk)
        self.assertEqual((status, payload["error"]), (409, "Voice settings changed. Record again with the new setting."))
        S.write_json(config.ROOT / "settings.json", {"voice": "host"})
        self.patch(speech, "runtime", return_value=speech.ROOT / "runtime-updated")
        self.assertNotEqual(server.voice_view()["selection"], selection, "a new runtime is a new selection")
        self.assertEqual(self.post(f"/api/voice/live/{ident}/audio?seq=1", b"", "application/octet-stream",
                                   **chunk)[0], 409)

    def test_a_replay_stays_with_the_device_that_opened_the_first_recording(self):
        self.runtime()
        status, opened = self.start()
        self.assertEqual(status, 200)
        owner = opened["owner"]
        self.assertEqual(owner, access.voice_owner(None))
        self.assertNotEqual(access.voice_owner("another-device"), owner, "each paired device has its own name")
        self.post(f"/api/voice/live/{opened['id']}/cancel")
        self.assertEqual(self.post("/api/voice/live", **{"X-Voice-Owner": owner})[0], 200)
        status, payload = self.post("/api/voice/live", **{"X-Voice-Owner": access.voice_owner("another-device")})
        self.assertEqual((status, payload["error"]), (403, "Voice stopped: this recording belongs to another device."))

    def test_starting_needs_the_current_selection_and_a_set_up_runtime(self):
        self.assertEqual(self.post("/api/voice/live", **{"X-Voice-Selection": "stale"})[0], 409)
        status, payload = self.start()
        self.assertEqual(status, 409)
        self.assertIn("one-time download", payload["error"])

    def test_malformed_chunks_are_refused(self):
        self.runtime()
        ident = self.start()[1]["id"]
        for query in ("seq=x", "seq=-1"):
            self.assertEqual(self.post(f"/api/voice/live/{ident}/audio?{query}", b"", "application/octet-stream")[0], 400)

    def test_agents_and_other_sites_are_refused(self):
        self.assertEqual(self.post("/api/voice/host", json.dumps({"action": "setup"}).encode(),
                                   Origin="https://elsewhere.example")[0], 403)
        terminal.agent_connection.return_value = True
        status, payload = self.post("/api/voice/host", json.dumps({"action": "remove"}).encode())
        self.assertEqual(status, 403)
        self.assertIn("agents are refused", payload["error"])

    def test_remove_through_settings(self):
        self.runtime()
        status, view = self.post("/api/voice/host", json.dumps({"action": "remove"}).encode())
        self.assertEqual((status, view["host"]["state"]), (200, "absent"))
        self.assertEqual(self.post("/api/voice/host", json.dumps({"action": "other"}).encode())[0], 400)

    def test_settings_selects_host_voice(self):
        S.write_json(config.ROOT / "settings.json", {})
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        body = json.dumps({"backend": "host", "selection": server.voice_view()["selection"]})
        connection.request("POST", "/api/voice", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual((response.status, json.loads(response.read())["backend"]), (200, "host"))
        self.assertEqual(config.voice_setting(), {"backend": "host"})


if __name__ == "__main__":
    import unittest
    unittest.main()
