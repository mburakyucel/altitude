"""The browser-recording adapter forwards to one standard speech service and fails without exposing internals."""
from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.support import AltitudeCase
from altitude import config, dispatch, server, state as S


class _Endpoint(BaseHTTPRequestHandler):
    """A loopback OpenAI-compatible transcriptions endpoint: records the request, answers as configured."""
    answer: tuple[int, bytes] = (200, b'{"text": " the endpoint heard this "}')
    seen: list[dict] = []
    redirect_to: str | None = None

    def do_GET(self):
        # Where a followed redirect would land: nothing may ever arrive here.
        _Endpoint.seen.append({"path": self.path, "authorization": self.headers.get("Authorization"), "fields": {}, "method": "GET"})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if _Endpoint.redirect_to:
            _Endpoint.seen.append({"path": self.path, "authorization": self.headers.get("Authorization"), "fields": {}})
            self.send_response(302)
            self.send_header("Location", _Endpoint.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        boundary = self.headers["Content-Type"].split("boundary=", 1)[1].encode()
        fields = {}
        for part in body.split(b"--" + boundary)[1:-1]:
            head, _, value = part.strip(b"\r\n").partition(b"\r\n\r\n")
            name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
            fields[name] = {"head": head.decode(), "value": value}
        _Endpoint.seen.append({"path": self.path, "authorization": self.headers.get("Authorization"), "fields": fields})
        status, payload = _Endpoint.answer
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class TestVoiceBackends(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        _Endpoint.seen = []
        _Endpoint.answer = (200, b'{"text": " the endpoint heard this "}')
        _Endpoint.redirect_to = None
        self.endpoint = ThreadingHTTPServer(("127.0.0.1", 0), _Endpoint)
        thread = threading.Thread(target=self.endpoint.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.endpoint.server_close)
        self.addCleanup(self.endpoint.shutdown)
        self.url = f"http://127.0.0.1:{self.endpoint.server_port}/v1/audio/transcriptions"

    def use(self, voice):
        self.patch(config, "machine_settings", return_value={"voice": voice})

    def test_browser_recognition_is_the_default_and_refuses_uploads(self):
        self.patch(config, "machine_settings", return_value={})
        self.assertEqual(server.voice_view()["backend"], "browser")
        with self.assertRaisesRegex(server.VoiceInputError, "runs in the browser") as raised:
            server.transcribe_voice(b"aac", "audio/mp4")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(_Endpoint.seen, [])

    def test_service_receives_the_unchanged_recording_as_one_openai_compatible_request(self):
        self.use({"url": self.url, "key": "sk-private", "model": "whisper-large-v3"})
        self.assertEqual(server.voice_view()["backend"], "endpoint")

        text = server.transcribe_voice(b"opus bytes", "audio/webm;codecs=opus")

        self.assertEqual(text, "the endpoint heard this")
        [request] = _Endpoint.seen
        self.assertEqual(request["path"], "/v1/audio/transcriptions")
        self.assertEqual(request["authorization"], "Bearer sk-private")
        self.assertEqual(request["fields"]["model"]["value"], b"whisper-large-v3")
        self.assertEqual(request["fields"]["file"]["value"], b"opus bytes")
        self.assertIn('filename="recording.webm"', request["fields"]["file"]["head"])
        self.assertIn("Content-Type: audio/webm", request["fields"]["file"]["head"])

    def test_endpoint_without_a_key_uses_the_default_model_and_sends_no_authorization(self):
        self.use({"url": self.url})
        server.transcribe_voice(b"aac bytes", "audio/mp4")
        [request] = _Endpoint.seen
        self.assertIsNone(request["authorization"])
        self.assertEqual(request["fields"]["model"]["value"], config.VOICE_DEFAULT_MODEL.encode())

    def test_endpoint_failures_become_safe_client_errors(self):
        self.use({"url": self.url, "key": "sk-private"})
        for answer, status, pattern in (
            ((401, b'{"error": "bad key sk-private"}'), 503, "answered HTTP 401. Check its URL, model and key"),
            ((200, b"not json"), 502, "invalid response"),
            ((200, b'{"result": "no text field"}'), 502, "invalid response"),
            ((200, b'{"text": "   "}'), 422, "No speech was detected"),
        ):
            with self.subTest(answer=answer):
                _Endpoint.answer = answer
                with self.assertRaisesRegex(server.VoiceInputError, pattern) as raised:
                    server.transcribe_voice(b"aac", "audio/mp4")
                self.assertEqual(raised.exception.status, status)
                self.assertNotIn("sk-private", str(raised.exception))
                if status != 422:
                    self.assertIn(self.url, str(raised.exception))
        self.assertIn("voice service refused the recording: HTTP 401", self.logs)
        self.assertNotIn("sk-private", "\n".join(self.logs))

    def test_a_redirecting_endpoint_is_refused_so_the_key_never_follows_it(self):
        _Endpoint.redirect_to = f"http://127.0.0.1:{self.endpoint.server_port}/elsewhere"
        self.use({"url": self.url, "key": "sk-private"})
        with self.assertRaisesRegex(server.VoiceInputError, "answered HTTP 302") as raised:
            server.transcribe_voice(b"aac", "audio/mp4")
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual([seen["path"] for seen in _Endpoint.seen], ["/v1/audio/transcriptions"])
        self.assertIn("voice service refused the recording: HTTP 302", self.logs)
        self.assertNotIn("sk-private", "\n".join(self.logs))

    def test_unreachable_service_names_its_url_not_a_traceback(self):
        self.endpoint.shutdown()
        self.endpoint.server_close()
        self.use({"url": self.url, "key": "sk-private"})
        with self.assertRaises(server.VoiceInputError) as raised:
            server.transcribe_voice(b"aac", "audio/mp4")
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(str(raised.exception),
                         f"Couldn't reach your speech service at {self.url}. Check that it's running. Your draft is unchanged.")
        self.assertNotIn("sk-private", str(raised.exception))

    def test_a_stored_value_that_is_no_service_url_means_browser_recognition(self):
        self.use("local")
        self.assertEqual(config.voice_setting(), {"backend": "browser"})


class TestVoiceSetting(AltitudeCase):
    def setUp(self):
        super().setUp()
        settings = config.ROOT / "settings.json"
        request = config.ROOT / "voice-request.json"
        events = config.ROOT / "events.jsonl"
        saved = {path: path.read_text() if path.exists() else None for path in (settings, request, events)}

        def restore():
            for path, text in saved.items():
                if text is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(text)
        self.addCleanup(restore)
        request.unlink(missing_ok=True)

    def test_validation_accepts_browser_and_an_http_service_url_only(self):
        for value in (None, "browser", {"url": "https://api.example/v1/audio/transcriptions"},
                      {"url": "http://10.0.0.5:8000/v1/audio/transcriptions", "model": "Systran/faster-whisper-small", "key": "k"}):
            config.validate_voice(value)
        for value in ("local", "whisper", "", {"url": "ftp://x"}, {"url": "api.example"}, {"url": "https://x", "extra": 1},
                      {"url": "https://x", "key": " "}, {"model": "m"}, 3,
                      {"url": "https://user:secret@api.example/v1"}, {"url": "https://api.example/v1?api_key=secret"},
                      {"url": "https://api.example/v1#frag"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.validate_voice(value)

    def test_operator_request_applies_the_endpoint_and_records_it_without_the_key(self):
        value = {"url": "https://api.example/v1/audio/transcriptions", "key": "sk-private"}
        with self.assertRaisesRegex(dispatch.T.TransitionError, "speech service"):
            dispatch.request_setting(None, "voice", {"url": "nowhere"}, "test", actor=config.OPERATOR_ACTOR)
        with self.assertRaisesRegex(dispatch.T.TransitionError, "operator"):
            dispatch.request_setting(None, "voice", value, "test", actor="l3")
        dispatch.request_setting(None, "voice", value, "Use the hosted model", actor=config.OPERATOR_ACTOR)
        self.assertEqual(config.voice_setting()["backend"], "browser", "CLI only persists the daemon request")

        result = dispatch.run_settings()["voice"]

        self.assertEqual(result["status"], "done")
        self.assertEqual(config.voice_setting(), {"backend": "endpoint", "model": config.VOICE_DEFAULT_MODEL, **value})
        events = (config.ROOT / "events.jsonl").read_text()
        recorded = [json.loads(row) for row in events.splitlines() if '"machine-set"' in row][-1]
        self.assertEqual(recorded["voice"], {"url": "https://api.example/v1/audio/transcriptions", "key": "set"})
        self.assertNotIn("sk-private", events)

        dispatch.request_setting(None, "voice", "browser", "Back to the browser", actor=config.OPERATOR_ACTOR)
        dispatch.run_settings()
        self.assertEqual(config.voice_setting(), {"backend": "browser"})
        dispatch.request_setting(None, "voice", None, "Default", actor=config.OPERATOR_ACTOR)
        dispatch.run_settings()
        self.assertEqual(config.voice_setting(), {"backend": "browser"})

    def test_cli_sets_and_shows_the_backend_without_printing_the_key(self):
        key_file = config.ROOT / "voice-key.txt"
        key_file.write_text("sk-private\n")
        result = self.alt("machine", "set", "--voice", "https://api.example/v1/audio/transcriptions",
                          "--voice-key-file", str(key_file), "--voice-model", "gpt-4o-transcribe", "--reason", "test",
                          env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sk-private", result.stdout)
        self.assertEqual(json.loads(result.stdout)["request"]["voice"],
                         {"url": "https://api.example/v1/audio/transcriptions", "model": "gpt-4o-transcribe", "key": "set"})
        self.assertEqual(S.read_json(config.ROOT / "voice-request.json", {})["voice"]["key"], "sk-private")
        dispatch.run_settings()
        shown = self.alt("machine", "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertNotIn("sk-private", shown.stdout)
        self.assertEqual(json.loads(shown.stdout)["voice"],
                         {"backend": "endpoint", "url": "https://api.example/v1/audio/transcriptions", "model": "gpt-4o-transcribe", "key": "set"})
        (config.ROOT / "empty-key.txt").write_text("\n")
        for args in (("--voice", "local"), ("--voice", "browser", "--voice-key-file", str(key_file)), ("--wip", "3", "--voice-model", "m"),
                     ("--voice", "browser", "--wip", "3"),
                     ("--voice", "https://api.example/v1", "--voice-key-file", str(config.ROOT / "empty-key.txt"))):
            self.assertNotEqual(self.alt("machine", "set", *args, "--reason", "test", env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR}).returncode, 0)


class TestVoiceEndpoint(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "ROOT", self.tmp / "machine")
        config.ensure_root()
        self.service = {"url": "https://voice.example/v1/audio/transcriptions"}
        S.write_json(config.ROOT / "settings.json", {"voice": self.service})
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def request(self, body: bytes, content_type: str, *, length: int | None = None,
                selection: str | None = "current"):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=2)
        headers = {"Content-Type": content_type, "Content-Length": str(len(body) if length is None else length)}
        if selection is not None:
            headers["X-Voice-Selection"] = server.voice_view()["selection"] if selection == "current" else selection
        connection.request(
            "POST",
            "/api/transcribe",
            body=body,
            headers=headers,
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_reports_the_backend_the_composer_should_use(self):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=2)
        connection.request("GET", "/api/voice")
        response = connection.getresponse()
        self.assertEqual((response.status, json.loads(response.read())), (200, server.voice_view()))
        connection.close()

    def test_passes_the_raw_recording_to_the_adapter(self):
        seen = {}

        def transcribe(raw, content_type, *, setting):
            seen.update(raw=raw, content_type=content_type, setting=setting)
            return "review me first"

        self.patch(server, "transcribe_voice", new=transcribe)

        status, payload = self.request(b"aac bytes", "audio/mp4")

        self.assertEqual((status, payload), (200, {"text": "review me first"}))
        self.assertEqual(seen, {"raw": b"aac bytes", "content_type": "audio/mp4", "setting": config.voice_setting()})

    def settings_request(self, body=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=2)
        if body is None:
            connection.request("GET", "/api/voice")
        else:
            connection.request("POST", "/api/voice", body=json.dumps(body), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def save(self, **body):
        return self.settings_request({"selection": self.settings_request()[1]["selection"], **body})

    def test_settings_saves_and_redacts_endpoint_key_through_durable_operator_request(self):
        status, setting = self.save(backend="endpoint", url="https://voice.example/transcribe", key="secret-fixture")
        self.assertEqual(status, 200)
        self.assertEqual({key: value for key, value in setting.items() if key != "selection"},
                         {"backend": "endpoint", "url": "https://voice.example/transcribe",
                          "model": config.VOICE_DEFAULT_MODEL, "key_set": True})
        self.assertNotIn("secret-fixture", json.dumps(setting))
        self.assertEqual(self.settings_request(), (200, setting))
        request = S.read_json(config.ROOT / "voice-request.json", {})
        self.assertEqual((request["actor"], request["status"]), (config.OPERATOR_ACTOR, "done"))
        self.assertEqual(request["voice"]["key"], "secret-fixture")
        self.assertNotIn("secret-fixture", (config.ROOT / "events.jsonl").read_text())
        self.assertEqual(self.save(backend="browser")[0], 200)
        self.assertEqual(config.voice_setting(), {"backend": "browser"})
        self.assertEqual(self.settings_request()[1]["key_set"], False)
        self.assertEqual(self.save(backend="local")[0], 400)

    def test_keep_key_only_for_same_url_and_blank_replacement_removes_it(self):
        url = "https://voice.example/transcribe"
        self.assertEqual(self.save(backend="endpoint", url=url, key="original-secret")[0], 200)
        self.assertEqual(self.save(backend="endpoint", url=url, model="another-model", keep_key=True)[0], 200)
        self.assertEqual(config.voice_setting()["key"], "original-secret")
        status, payload = self.save(backend="endpoint", url="https://other.example/transcribe", keep_key=True)
        self.assertEqual(status, 400)
        self.assertIn("same service URL", payload["error"])
        self.assertEqual(config.voice_setting()["url"], url)
        self.assertEqual(self.save(backend="endpoint", url=url, keep_key=True, key="")[0], 400)
        self.assertEqual(self.save(backend="endpoint", url=url, key="")[0], 200)
        self.assertNotIn("key", config.voice_setting())
        self.assertEqual(self.save(backend="endpoint", url=url, key="second-secret")[0], 200)
        self.assertEqual(self.save(backend="endpoint", url="https://other.example/transcribe")[0], 200)
        self.assertNotIn("key", config.voice_setting())

    def test_invalid_settings_never_replace_the_saved_choice(self):
        saved = config.voice_setting()
        for body in ({"backend": "unknown"}, {"backend": "local"}, {"backend": "browser", "key": "unwanted"},
                     {"backend": "endpoint", "url": "https://user:secret@voice.example"},
                     {"backend": "endpoint", "url": "https://voice.example?key=secret"},
                     {"backend": "endpoint", "url": "file:///recordings"},
                     {"backend": "endpoint", "url": "https://voice.example", "key": 12},
                     {"backend": "endpoint", "url": "https://voice.example", "keep_key": "yes"},
                     {"backend": "browser", "other": True}):
            with self.subTest(body=body):
                status, payload = self.save(**body)
                self.assertEqual(status, 400)
                self.assertNotIn("secret", payload["error"])
                self.assertEqual(config.voice_setting(), saved)
        self.assertFalse((config.ROOT / "voice-request.json").exists())

    def test_stale_settings_save_requires_reload_without_saving(self):
        selection = self.settings_request()[1]["selection"]
        self.assertEqual(self.save(backend="browser")[0], 200)
        status, payload = self.settings_request({**self.service, "backend": "endpoint", "selection": selection})
        self.assertEqual(status, 409)
        self.assertIn("Reload settings", payload["error"])
        self.assertEqual(config.voice_setting(), {"backend": "browser"})
        self.assertEqual(self.settings_request({"backend": "browser"})[0], 409)

    def test_missing_or_stale_recording_selection_never_forwards_audio(self):
        endpoint = self.patch(server, "_transcribe_endpoint")
        for previous, current in (
            ("browser", {"url": "https://voice.example/transcribe"}),
            ({"url": "https://voice.example/transcribe"}, "browser"),
            ({"url": "https://voice.example/transcribe"}, {"url": "https://other.example/transcribe"}),
        ):
            with self.subTest(previous=previous, current=current):
                S.write_json(config.ROOT / "settings.json", {"voice": previous})
                selection = self.settings_request()[1]["selection"]
                S.write_json(config.ROOT / "settings.json", {"voice": current})
                status, payload = self.request(b"audio", "audio/mp4", selection=selection)
                self.assertEqual(status, 409)
                self.assertIn("Record again", payload["error"])
        self.assertEqual(self.request(b"audio", "audio/mp4", selection=None)[0], 409)
        endpoint.assert_not_called()

    def test_accepted_upload_uses_one_snapshot_even_if_settings_change_during_transcription(self):
        setting = {"backend": "endpoint", "url": "https://voice.example/transcribe", "model": "fixture", "key": "secret-fixture"}
        read = self.patch(config, "voice_setting", side_effect=[setting, AssertionError("settings read twice")])
        seen = []

        def transcribe(raw, media_type, extension, accepted):
            S.write_json(config.ROOT / "settings.json", {"voice": {"url": "https://other.example/transcribe"}})
            seen.append(accepted)
            return "original destination"

        self.patch(server, "_transcribe_endpoint", side_effect=transcribe)
        status, payload = self.request(b"audio", "audio/mp4", selection=server._voice_selection(setting))
        self.assertEqual((status, payload), (200, {"text": "original destination"}))
        self.assertEqual(seen, [setting])
        read.assert_called_once_with()

    def test_rejects_missing_oversize_and_unknown_recordings_before_transcription(self):
        transcribe = self.patch(server, "transcribe_voice")

        self.assertEqual(server.VOICE_MAX_BODY, 16 << 20)
        self.assertEqual(self.request(b"", "audio/mp4")[0], 400)
        self.assertEqual(self.request(b"x", "application/octet-stream")[0], 415)
        # The handler rejects the declared size before trying to read that many bytes.
        self.assertEqual(self.request(b"", "audio/mp4", length=server.VOICE_MAX_BODY + 1)[0], 413)
        transcribe.assert_not_called()

    def test_returns_recoverable_safe_errors_without_internal_details(self):
        secret = "/private/path/with-sensitive-debug.wav"
        self.patch(server, "transcribe_voice", side_effect=RuntimeError(secret))

        status, payload = self.request(b"audio", "audio/webm")

        self.assertEqual(status, 503)
        self.assertIn("temporarily unavailable", payload["error"])
        self.assertNotIn(secret, payload["error"])
        self.assertIn(secret, "\n".join(self.logs))


if __name__ == "__main__":
    import unittest

    unittest.main()
