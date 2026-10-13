"""The voice setting: host voice by default where the speech model can run, browser recognition elsewhere, one
durable operator request to change it, and the removal of a speech-service URL and key saved before."""
from __future__ import annotations

import http.client
import json
import io
import wave
import time
import subprocess
import ssl
import threading
from unittest import mock

from tests.support import AltitudeCase
from altitude import access, config, dispatch, platform, server, speech, terminal, state as S


class VoiceCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "ROOT", self.tmp / "machine")
        self.patch(config, "RUNTIME_ROOT", (self.tmp / "machine").resolve())
        config.ensure_root()

    def runs_here(self, supported: bool):
        self.patch(platform, "speech_runtime",
                   return_value=("linux-x86_64-cp312", "") if supported else (None, "voice runs on Linux x86_64 and on Macs with Apple silicon"))


class TestVoiceSetting(VoiceCase):
    def test_host_voice_is_the_default_where_the_model_can_run(self):
        self.runs_here(True)
        self.assertEqual(config.voice_setting(), {"backend": "host"})
        self.runs_here(False)
        self.assertEqual(config.voice_setting(), {"backend": "browser"})

    def test_a_saved_choice_outranks_the_default(self):
        self.runs_here(True)
        S.write_json(config.ROOT / "settings.json", {"voice": "browser"})
        self.assertEqual(config.voice_setting(), {"backend": "browser"})
        self.runs_here(False)
        S.write_json(config.ROOT / "settings.json", {"voice": "host"})
        self.assertEqual(config.voice_setting(), {"backend": "host"}, "an unavailable host stays host, with its reason")

    def test_validation_accepts_host_browser_and_the_default_only(self):
        for value in (None, "browser", "host"):
            config.validate_voice(value)
        for value in ("local", "", {"url": "https://api.example/v1/audio/transcriptions"}, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.validate_voice(value)

    def test_operator_request_applies_the_choice(self):
        self.runs_here(True)
        with self.assertRaisesRegex(dispatch.T.TransitionError, "host or browser"):
            dispatch.request_setting(None, "voice", {"url": "https://x"}, "test", actor=config.OPERATOR_ACTOR)
        with self.assertRaisesRegex(dispatch.T.TransitionError, "operator"):
            dispatch.request_setting(None, "voice", "browser", "test", actor="l3")
        dispatch.request_setting(None, "voice", "browser", "Use the browser", actor=config.OPERATOR_ACTOR)
        self.assertEqual(config.voice_setting(), {"backend": "host"}, "the request waits for the daemon")
        self.assertEqual(dispatch.run_settings()["voice"]["status"], "done")
        self.assertEqual(config.voice_setting(), {"backend": "browser"})
        recorded = [json.loads(row) for row in (config.ROOT / "events.jsonl").read_text().splitlines()][-1]
        self.assertEqual((recorded["kind"], recorded["voice"]), ("machine-set", "browser"))
        dispatch.request_setting(None, "voice", None, "Default", actor=config.OPERATOR_ACTOR)
        dispatch.run_settings()
        self.assertEqual(config.voice_setting(), {"backend": "host"})

    def test_cli_sets_and_shows_the_backend(self):
        env = {"ALTITUDE_ACTOR": config.OPERATOR_ACTOR}
        result = self.alt("machine", "set", "--voice", "browser", "--reason", "test", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["request"]["voice"], "browser")
        dispatch.run_settings()
        shown = self.alt("machine", "show", env=env)
        self.assertEqual(json.loads(shown.stdout)["voice"], {"backend": "browser"})
        for args in (("--voice", "local"), ("--voice", "https://api.example/v1"), ("--voice", "browser", "--wip", "3"),
                     ("--voice", "browser", "--voice-key-file", "key.txt")):
            with self.subTest(args=args):
                self.assertNotEqual(self.alt("machine", "set", *args, "--reason", "test", env=env).returncode, 0)


class TestSpeechServiceRemoval(VoiceCase):
    SERVICE = {"url": "https://voice.example/v1/audio/transcriptions", "model": "whisper-1", "key": "sk-private"}

    def test_a_saved_service_and_its_receipt_are_deleted_once(self):
        self.runs_here(True)
        S.write_json(config.ROOT / "settings.json", {"voice": self.SERVICE, "wip": 3})
        S.write_json(config.ROOT / "voice-request.json", {"id": "r1", "voice": self.SERVICE, "status": "done",
                                                          "result_voice": self.SERVICE})
        self.assertEqual(config.voice_setting(), {"backend": "host"}, "a saved service is never used")

        dispatch.forget_speech_service()
        dispatch.forget_speech_service()  # the next start finds nothing to do

        self.assertEqual(config.machine_settings(), {"wip": 3})
        self.assertFalse((config.ROOT / "voice-request.json").exists())
        events = (config.ROOT / "events.jsonl").read_text()
        self.assertEqual([json.loads(row)["reason"] for row in events.splitlines()], ["speech-service option removed"])
        for secret in ("sk-private", "voice.example"):
            self.assertNotIn(secret, events)
            self.assertNotIn(secret, "".join(path.read_text() for path in config.ROOT.glob("*.json")))

    def test_machine_show_never_prints_a_service_key_before_the_daemon_removes_it(self):
        S.write_json(config.ROOT / "voice-request.json", {"id": "r1", "voice": self.SERVICE, "status": "done",
                                                          "result_voice": self.SERVICE})
        shown = self.alt("machine", "show", env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR})
        self.assertEqual(shown.returncode, 0, shown.stderr)
        for secret in ("sk-private", "voice.example"):
            self.assertNotIn(secret, shown.stdout)
        self.assertEqual(json.loads(shown.stdout)["voice_request"]["voice"], {"backend": "service"})

    def test_a_pending_service_request_is_never_applied(self):
        S.write_json(config.ROOT / "settings.json", {"voice": "browser"})
        S.write_json(config.ROOT / "voice-request.json", {"id": "r1", "voice": self.SERVICE, "status": "pending",
                                                          "actor": config.OPERATOR_ACTOR, "reason": "old",
                                                          "operation": "machine-set", "project": None})
        dispatch.forget_speech_service()
        dispatch.run_settings()
        self.assertEqual(config.machine_settings(), {"voice": "browser"})
        self.assertNotIn("sk-private", (config.ROOT / "events.jsonl").read_text())

    def test_host_and_browser_choices_are_untouched(self):
        for choice in ("host", "browser"):
            S.write_json(config.ROOT / "settings.json", {"voice": choice})
            dispatch.forget_speech_service()
            self.assertEqual(config.machine_settings(), {"voice": choice})
        self.assertFalse((config.ROOT / "events.jsonl").exists())


class TestVoiceSettingsRoute(VoiceCase):
    def setUp(self):
        super().setUp()
        self.runs_here(True)
        self.patch(terminal, "agent_connection", return_value=False)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(2)))

    def request(self, method: str, path: str, body: dict | None = None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        connection.request(method, path, body=None if body is None else json.dumps(body),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def save(self, **body):
        return self.request("POST", "/api/voice", {"selection": self.request("GET", "/api/voice")[1]["selection"], **body})

    def test_reports_the_backend_and_host_state(self):
        status, view = self.request("GET", "/api/voice")
        self.assertEqual(status, 200)
        self.assertEqual(set(view), {"backend", "selection", "host"})
        self.assertEqual((view["backend"], view["host"]["state"]), ("host", "absent"))

    def test_saves_through_the_durable_operator_request(self):
        status, view = self.save(backend="browser")
        self.assertEqual((status, view["backend"]), (200, "browser"))
        request = S.read_json(config.ROOT / "voice-request.json", {})
        self.assertEqual((request["actor"], request["status"], request["voice"]), (config.OPERATOR_ACTOR, "done", "browser"))
        self.assertEqual(self.save(backend="host")[1]["backend"], "host")

    def test_invalid_or_stale_saves_change_nothing(self):
        for body in ({"backend": "endpoint", "url": "https://voice.example"}, {"backend": "local"},
                     {"backend": "browser", "key": "unwanted"}):
            with self.subTest(body=body):
                self.assertEqual(self.save(**body)[0], 400)
        stale = self.request("GET", "/api/voice")[1]["selection"]
        self.assertEqual(self.save(backend="browser")[0], 200)
        status, payload = self.request("POST", "/api/voice", {"backend": "host", "selection": stale})
        self.assertEqual(status, 409)
        self.assertIn("Reload settings", payload["error"])
        self.assertEqual(config.voice_setting(), {"backend": "browser"})

    def test_recordings_are_no_longer_uploaded(self):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        connection.request("POST", "/api/transcribe", body=b"audio", headers={"Content-Type": "audio/webm"})
        self.assertEqual(connection.getresponse().status, 404)
        connection.close()


class TestServiceConfiguration(TestVoiceSettingsRoute):
    def setUp(self):
        super().setUp()
        self.patch(access, "device", return_value={"id": "fictional-device"})
        self.patch(access, "renew", return_value=False)
        self.patch(access, "known", return_value=True)

    def configure(self, **extra):
        return self.save(backend="service", url="https://speech.example.test/v1/audio/transcriptions",
                         **extra)

    def test_invalid_saved_service_is_recoverable_without_fallback_or_secret_exposure(self):
        for invalid in ({"url": "not-a-url", "selection": "old", "key": "fixture-secret"},
                        {"url": "https://speech.example.test/transcribe", "key": "fixture-secret"},
                        {"url": "https://speech.example.test/transcribe", "selection": "old", "model": []}):
            S.write_json(config.ROOT / "settings.json", {"voice": {"backend": "service", **invalid}})
            status, view = self.request("GET", "/api/voice")
            self.assertEqual(status, 200)
            self.assertEqual((view["backend"], view["selection"], view["error"]),
                             ("service", "invalid-service", "service_response"))
            self.assertNotIn("service", view)
            self.assertNotIn("fixture-secret", json.dumps(view))
            outgoing = mock.Mock()
            with mock.patch.object(speech, "transcribe_service", outgoing):
                self.assertEqual(self.audio()[1]["code"], "service_response")
            outgoing.assert_not_called()
            shown = self.alt("machine", "show", env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR})
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertNotIn("fixture-secret", shown.stdout)
            self.assertNotIn("speech.example.test", shown.stdout)
            self.assertEqual(self.configure()[0], 200)
            self.assertNotIn("error", self.request("GET", "/api/voice")[1])
            self.assertNotIn("key", config.voice_setting())
        S.write_json(config.ROOT / "settings.json", {"voice": {"backend": "service"}})
        self.assertEqual(self.save(backend="browser")[0], 200)
        self.assertEqual(config.voice_setting(), {"backend": "browser"})

    def test_reports_the_backend_and_host_state(self):
        view = self.request("GET", "/api/voice")[1]
        self.assertEqual(view["owner"], access.voice_owner("fictional-device"))

    def test_service_save_persists_without_secret_in_reads_events_or_receipts(self):
        status, view = self.configure(key="fictional-secret", model="fictional-model")
        self.assertEqual(status, 200, view)
        self.assertEqual(view["service"]["key_present"], True)
        self.assertNotIn("fictional-secret", json.dumps(view))
        saved = config.voice_setting()
        dispatch.forget_speech_service()
        self.assertEqual(config.voice_setting(), saved)
        for path in ("voice-request.json", "events.jsonl"):
            content = (config.ROOT / path).read_text()
            for private in ("fictional-secret", "fictional-model", "speech.example.test"):
                self.assertNotIn(private, content)
        shown = self.alt("machine", "show", env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR})
        self.assertEqual(shown.returncode, 0, shown.stderr)
        for private in ("fictional-secret", "fictional-model", "speech.example.test"):
            self.assertNotIn(private, shown.stdout)

    def test_keep_remove_key_and_address_changes_never_reuse_old_key(self):
        self.configure(key="fictional-secret")
        first = config.voice_setting()["selection"]
        self.configure(key="", model="model")
        self.assertEqual(config.voice_setting()["key"], "fictional-secret")
        self.assertNotEqual(config.voice_setting()["selection"], first)
        self.save(backend="service", url="https://speech.example.test/v1/audio/transcriptions/")
        self.assertNotIn("key", config.voice_setting())
        self.configure(key="new-secret")
        self.configure(remove_key=True)
        self.assertNotIn("key", config.voice_setting())
        self.configure(key="last-secret")
        self.save(backend="browser")
        self.assertEqual(config.machine_settings()["voice"], "browser")
        self.assertNotIn("last-secret", (config.ROOT / "voice-request.json").read_text())

    def test_complete_url_validation_does_not_touch_destination(self):
        outgoing = self.patch(speech, "transcribe_service")
        for url in ("ftp://example.test/a", "https://user:pass@example.test/a", "https://example.test/a?key=x",
                    "https://example.test/a#fragment", "https://example.test/a?", "https://example.test/a#",
                    "https://example.test:bad/a", "https://example.test/\na"):
            with self.subTest(url=url):
                self.assertEqual(self.save(backend="service", url=url)[0], 400)
        self.assertEqual(self.save(backend="service", url="http://speech.example.test/transcribe")[0], 200)
        outgoing.assert_not_called()

    def test_machine_and_agent_clients_cannot_configure_service(self):
        with mock.patch.object(access, "device", return_value=None):
            self.assertEqual(self.configure()[0], 403)
        with mock.patch.object(terminal, "agent_connection", return_value=True):
            self.assertEqual(self.configure()[0], 403)

    def test_browser_and_service_work_when_native_host_is_unavailable(self):
        with mock.patch.object(platform, "containerized", return_value=True):
            self.assertEqual(self.save(backend="browser")[0], 200)
            self.assertEqual(self.configure()[0], 200)
            with mock.patch.object(speech, "transcribe_service", return_value="fixture"):
                self.assertEqual(self.audio()[1], {"text": "fixture"})
            self.assertEqual(self.request("POST", "/api/voice/host", {"action": "setup"})[0], 403)

    def audio(self, pcm=b"\0\0", **headers):
        view = self.request("GET", "/api/voice")[1]
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        connection.request("POST", "/api/voice/service", pcm, headers={
            "X-Voice-Selection": view["selection"], "X-Voice-Owner": view["owner"], **headers})
        response = connection.getresponse()
        answer = response.status, json.loads(response.read())
        connection.close()
        return answer

    def test_audio_owner_selection_and_cap_before_forwarding(self):
        self.configure()
        outgoing = self.patch(speech, "transcribe_service", return_value="hello")
        self.assertEqual(self.audio()[1], {"text": "hello"})
        outgoing.reset_mock()
        for headers in ({"X-Voice-Owner": "other"}, {"X-Voice-Selection": "old"}):
            self.assertEqual(self.audio(**headers)[1]["code"], "selection_changed")
        self.assertEqual(self.audio(b"odd")[1]["code"], "service_response")
        outgoing.assert_not_called()
        self.assertEqual(self.audio(b"\0" * speech.SERVICE_PCM_LIMIT)[1], {"text": "hello"})
        self.assertEqual(len(outgoing.call_args.args[1]), speech.SERVICE_PCM_LIMIT)

    def test_late_results_and_repairing_are_rejected(self):
        self.configure()
        def changed(*args):
            S.write_json(config.ROOT / "settings.json", {"voice": "browser"})
            return "must not arrive"
        self.patch(speech, "transcribe_service", side_effect=changed)
        self.assertEqual(self.audio()[1]["code"], "selection_changed")
        self.configure()
        with mock.patch.object(access, "known", return_value=False):
            self.assertEqual(self.audio()[1]["code"], "selection_changed")

    def test_service_errors_are_structured_and_never_pairing_401(self):
        self.configure()
        for code in speech.ServiceError.MESSAGES:
            with mock.patch.object(speech, "transcribe_service", side_effect=speech.ServiceError(code)):
                status, result = self.audio()
                self.assertNotEqual(status, 401)
                self.assertEqual(result, {"error": speech.ServiceError.MESSAGES[code], "code": code})

    def test_host_runtime_actions_keep_service_selection_and_private_view(self):
        self.configure(key="fixture-secret")
        original = self.request("GET", "/api/voice")[1]
        with mock.patch.object(server.SPEECH, "start_setup") as setup:
            status, result = self.request("POST", "/api/voice/host", {"action": "setup"})
        setup.assert_called_once()
        self.assertEqual(status, 200)
        for field in ("backend", "selection", "owner", "service"):
            self.assertEqual(result[field], original[field])

    def test_concurrency_limit_releases_after_failure_and_oversized_upload_never_forwards(self):
        self.configure()
        outgoing = self.patch(speech, "transcribe_service", return_value="hello")
        speech.SERVICE_SLOTS.acquire()
        speech.SERVICE_SLOTS.acquire()
        try:
            self.assertEqual(self.audio()[1]["code"], "service_unavailable")
        finally:
            speech.SERVICE_SLOTS.release()
            speech.SERVICE_SLOTS.release()
        self.assertEqual(self.audio(**{"Content-Length": str(speech.SERVICE_PCM_LIMIT + 2)})[1]["code"],
                         "service_response")
        outgoing.assert_not_called()
        self.assertEqual(self.audio()[1], {"text": "hello"})

    def test_upload_phase_deadline_releases_slot_without_forwarding(self):
        self.configure()
        outgoing = self.patch(speech, "transcribe_service")
        view = self.request("GET", "/api/voice")[1]
        with mock.patch.object(speech, "SERVICE_UPLOAD_SECONDS", 0.05):
            connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=2)
            connection.putrequest("POST", "/api/voice/service")
            for field, value in {"Content-Length": "4", "X-Voice-Selection": view["selection"],
                                 "X-Voice-Owner": view["owner"]}.items():
                connection.putheader(field, value)
            connection.endheaders(b"\0\0")
            response = connection.getresponse()
            self.assertEqual(json.loads(response.read())["code"], "service_unavailable")
            connection.close()
        outgoing.assert_not_called()
        self.assertTrue(speech.SERVICE_SLOTS.acquire(blocking=False))
        self.assertTrue(speech.SERVICE_SLOTS.acquire(blocking=False))
        speech.SERVICE_SLOTS.release()
        speech.SERVICE_SLOTS.release()

    def test_exhausted_total_budget_does_not_open_another_response_window(self):
        self.configure()
        def finish_after_deadline(*args):
            time.sleep(0.03)
            return "late result"
        with mock.patch.object(speech, "SERVICE_UPLOAD_SECONDS", 0.01), \
                mock.patch.object(speech, "SERVICE_RESPONSE_SECONDS", 0.01), \
                mock.patch.object(speech, "transcribe_service", side_effect=finish_after_deadline):
            with self.assertRaises(http.client.RemoteDisconnected):
                self.audio()
        self.assertTrue(speech.SERVICE_SLOTS.acquire(blocking=False))
        self.assertTrue(speech.SERVICE_SLOTS.acquire(blocking=False))
        speech.SERVICE_SLOTS.release()
        speech.SERVICE_SLOTS.release()

    def test_response_header_and_body_writes_share_absolute_deadline(self):
        handler = object.__new__(server.Handler)
        handler.headers = {}
        handler._keep_open = False
        handler.connection = mock.Mock()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.wfile = mock.Mock()
        with mock.patch.object(server.time, "monotonic", side_effect=[9.0, 9.75]):
            handler._json({"text": "words"}, deadline=10.0)
        self.assertEqual(handler.connection.settimeout.call_args_list, [mock.call(1.0), mock.call(0.25)])
        handler.wfile.write.assert_called_once()
        handler.wfile.reset_mock()
        handler.end_headers.reset_mock()
        with mock.patch.object(server.time, "monotonic", return_value=10.0):
            handler._json({"text": "late"}, deadline=10.0)
        handler.end_headers.assert_not_called()
        handler.wfile.write.assert_not_called()
        self.assertTrue(handler.close_connection)

    def test_refused_receipt_and_crash_replay_project_before_writing(self):
        self.configure(key="old-secret")
        stored = config.voice_setting()
        value = {**stored, "key": "new-secret", "selection": "new-identity"}
        dispatch.request_setting(None, "voice", value, "fixture", actor=config.OPERATOR_ACTOR, expected="wrong")
        receipt = dispatch._run_setting(None, "voice")
        self.assertTrue(receipt["changed"])
        self.assertNotIn("secret", json.dumps(receipt))
        dispatch.request_setting(None, "voice", value, "fixture", actor=config.OPERATOR_ACTOR,
                                 expected=stored["selection"])
        real_write = S.atomic_write
        def fail_event(path, data, *args, **kwargs):
            if path.name == "events.jsonl":
                self.assertNotIn("secret", data)
                raise OSError("fixture crash before event write")
            return real_write(path, data, *args, **kwargs)
        with mock.patch.object(S, "atomic_write", side_effect=fail_event):
            with self.assertRaises(OSError):
                dispatch._run_setting(None, "voice")
        self.assertEqual(config.voice_setting()["selection"], "new-identity")
        dispatch.forget_speech_service()
        self.assertEqual(dispatch._run_setting(None, "voice")["status"], "done")
        self.assertNotIn("secret", (config.ROOT / "events.jsonl").read_text())
        self.assertNotIn("secret", (config.ROOT / "voice-request.json").read_text())


class TestServiceTransport(VoiceCase):
    def setUp(self):
        super().setUp()
        self.received = []
        self.answer = (200, b'{"text":" fixture words "}')
        owner = self
        class Fixture(server.http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.received.append((self.path, self.headers, body))
                status, data = owner.answer
                self.send_response(status)
                self.send_header("Location", "/must-not-follow")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass
            def log_message(self, *args):
                pass
        self.fixture = server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        self.fixture.daemon_threads = True
        thread = threading.Thread(target=self.fixture.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.fixture.shutdown(), self.fixture.server_close(), thread.join(2)))
        self.setting = {"backend": "service", "url": f"http://127.0.0.1:{self.fixture.server_port}/transcribe",
                        "key": "fixture-secret", "model": "fixture-model", "selection": "fixture"}

    def test_real_disposable_transport_wav_multipart_and_no_proxy(self):
        with mock.patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}):
            self.assertEqual(speech.transcribe_service(self.setting, b"\1\0" * 160), "fixture words")
        path, headers, body = self.received[0]
        self.assertEqual(path, "/transcribe")
        self.assertEqual(headers["Authorization"], "Bearer fixture-secret")
        self.assertIn(b'name="model"\r\n\r\nfixture-model', body)
        self.assertIn(b'name="response_format"\r\n\r\njson', body)
        with wave.open(io.BytesIO(body[body.index(b"RIFF"):]), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            self.assertEqual(wav.readframes(160), b"\1\0" * 160)
        self.assertEqual(len(self.received), 1)

    def test_paired_route_storage_child_and_compatible_endpoint_integration(self):
        self.patch(access, "is_machine", return_value=False)
        self.patch(access, "DIR", self.tmp / "access")
        self.patch(terminal, "agent_connection", return_value=False)
        access.prepare()
        cookie, device = access.redeem(access.issue_code()["code"], "Fictional browser")
        altitude = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        altitude.daemon_threads = True
        thread = threading.Thread(target=altitude.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (altitude.shutdown(), altitude.server_close(), thread.join(2)))
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(*altitude.server_address, timeout=5)
            connection.request(method, path, body, headers={"Cookie": f"{access.COOKIE}={cookie}",
                               "Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            answer = response.status, json.loads(response.read())
            connection.close()
            return answer
        status, view = request("GET", "/api/voice")
        self.assertEqual(status, 200)
        status, view = request("POST", "/api/voice", json.dumps({
            "backend": "service", "selection": view["selection"], "url": self.setting["url"],
            "key": self.setting["key"], "model": self.setting["model"]}))
        self.assertEqual(status, 200, view)
        self.assertEqual(view["owner"], access.voice_owner(device["id"]))
        self.assertNotIn("fixture-secret", json.dumps(view))
        headers = {"X-Voice-Owner": view["owner"], "X-Voice-Selection": view["selection"],
                   "Content-Type": "application/octet-stream"}
        self.assertEqual(request("POST", "/api/voice/service", b"\1\0" * 160, headers),
                         (200, {"text": "fixture words"}))
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0][1]["Authorization"], "Bearer fixture-secret")
        self.assertIn(b"RIFF", self.received[0][2])
        self.assertEqual(config.voice_setting()["key"], "fixture-secret")
        for path in ("events.jsonl", "voice-request.json"):
            self.assertNotIn("fixture-secret", (config.ROOT / path).read_text())
        # Actual pairing revocation prevents a second child/endpoint request, not merely a mocked owner check.
        access.revoke(device["id"])
        self.assertEqual(request("POST", "/api/voice/service", b"\0\0", headers)[0], 401)
        self.assertEqual(len(self.received), 1)

    def test_real_child_rejects_untrusted_https_without_sending_audio(self):
        key, cert = self.tmp / "fixture.key", self.tmp / "fixture.crt"
        made = subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                               "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", str(key),
                               "-out", str(cert), "-days", "1", "-subj", "/CN=localhost"],
                              capture_output=True, timeout=10)
        self.assertEqual(made.returncode, 0, made.stderr.decode())
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        https = server.ThreadingHTTPServer(("127.0.0.1", 0), self.fixture.RequestHandlerClass)
        https.socket = context.wrap_socket(https.socket, server_side=True)
        https.daemon_threads = True
        thread = threading.Thread(target=https.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (https.shutdown(), https.server_close(), thread.join(2)))
        setting = {**self.setting, "url": f"https://127.0.0.1:{https.server_port}/transcribe"}
        with self.assertRaises(speech.ServiceError) as caught:
            speech.transcribe_service(setting, b"\0\0")
        self.assertEqual(caught.exception.code, "service_unavailable")
        self.assertEqual(self.received, [], "TLS authentication must finish before audio or key are transmitted")

    def test_failures_are_sanitized_no_redirect_retry_or_provider_body(self):
        for status, data, code in ((302, b"private", "service_response"), (401, b"private", "service_auth"),
                                  (503, b"private", "service_unavailable"), (200, b"private", "service_response"),
                                  (200, b'{"text":""}', "service_empty"),
                                  (200, b"x" * (speech.SERVICE_RESPONSE_LIMIT + 1), "service_response")):
            self.answer = status, data
            before = len(self.received)
            with self.assertRaises(speech.ServiceError) as caught:
                speech.transcribe_service(self.setting, b"\0\0")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private", str(caught.exception))
            self.assertEqual(len(self.received), before + 1)

    def test_hard_deadline_kills_and_reaps_a_stalled_child(self):
        # Replace only the child's work, retaining real run(timeout)'s kill/communicate/reap behavior.
        real_popen = subprocess.Popen
        children = []
        def stalled(args, **kwargs):
            child = real_popen([args[0], "-I", "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            return child
        with mock.patch.object(speech, "SERVICE_RESPONSE_SECONDS", 0.1), mock.patch.object(subprocess, "Popen", stalled):
            start = time.monotonic()
            with self.assertRaises(speech.ServiceError) as caught:
                speech.transcribe_service(self.setting, b"\0\0")
        self.assertEqual(caught.exception.code, "service_unavailable")
        self.assertLess(time.monotonic() - start, 3)
        self.assertIsNotNone(children[0].returncode)


if __name__ == "__main__":
    import unittest
    unittest.main()
