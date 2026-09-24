"""The browser-recording adapter is bounded, temporary, and fails without exposing internals."""
from __future__ import annotations

import http.client
import json
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.support import AltitudeCase
from altitude import config, dispatch, server, state as S


class _Whisper:
    def __init__(self, reply: bytes = b"the dictated words"):
        self.reply = [reply, b""]
        self.path: Path | None = None
        self.closed = False

    def settimeout(self, _seconds):
        pass

    def sendall(self, data: bytes):
        self.path = Path(data.decode())
        if not self.path.is_file():
            raise AssertionError("Whisper received a path that did not exist")

    def shutdown(self, _direction):
        pass

    def recv(self, _size):
        return self.reply.pop(0)

    def close(self):
        self.closed = True


class TestVoiceConversion(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "machine_settings", return_value={"voice": "local"})

    def fake_ffmpeg(self, command, **_kwargs):
        self.converted = command
        target = Path(command[-1])
        with wave.open(str(target), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(b"\0\0" * 16_000)
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_converts_browser_audio_and_removes_every_temporary_file(self):
        whisper = _Whisper()
        self.patch(server.subprocess, "run", side_effect=self.fake_ffmpeg)
        self.patch(server, "_whisper_connection", return_value=whisper)

        text = server.transcribe_voice(b"browser audio", "audio/mp4;codecs=mp4a.40.2")

        self.assertEqual(text, "the dictated words")
        self.assertIn("-t", self.converted)
        self.assertEqual(self.converted[self.converted.index("-t") + 1], "601")
        self.assertTrue(whisper.closed)
        self.assertIsNotNone(whisper.path)
        self.assertFalse(whisper.path.exists())
        self.assertFalse(whisper.path.parent.exists())

    def test_unavailable_whisper_still_removes_the_recording(self):
        self.patch(server.subprocess, "run", side_effect=self.fake_ffmpeg)
        self.patch(server, "_whisper_connection", return_value=None)

        with self.assertRaisesRegex(server.VoiceInputError, "temporarily unavailable") as raised:
            server.transcribe_voice(b"browser audio", "audio/webm;codecs=opus")

        self.assertEqual(raised.exception.status, 503)
        source = Path(self.converted[self.converted.index("-i") + 1])
        target = Path(self.converted[-1])
        self.assertFalse(source.exists())
        self.assertFalse(target.exists())
        self.assertFalse(source.parent.exists())

    def test_rejects_decoded_audio_past_the_ten_minute_limit(self):
        self.patch(server.subprocess, "run", side_effect=self.fake_ffmpeg)
        self.patch(server, "_wav_seconds", return_value=server.VOICE_MAX_SECONDS + 0.1)
        connect = self.patch(server, "_whisper_connection")

        with self.assertRaisesRegex(server.VoiceInputError, "limited to 10 minutes") as raised:
            server.transcribe_voice(b"browser audio", "audio/webm")

        self.assertEqual(raised.exception.status, 413)
        connect.assert_not_called()


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
        self.assertEqual(server.voice_view(), {"backend": "browser"})
        with self.assertRaisesRegex(server.VoiceInputError, "runs in the browser") as raised:
            server.transcribe_voice(b"aac", "audio/mp4")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(_Endpoint.seen, [])

    def test_endpoint_receives_one_openai_compatible_request_and_never_touches_ffmpeg(self):
        self.use({"url": self.url, "key": "sk-private", "model": "whisper-large-v3"})
        run = self.patch(server.subprocess, "run")
        self.assertEqual(server.voice_view(), {"backend": "endpoint"})

        text = server.transcribe_voice(b"opus bytes", "audio/webm;codecs=opus")

        self.assertEqual(text, "the endpoint heard this")
        run.assert_not_called()
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
            ((401, b'{"error": "bad key sk-private"}'), 503, "temporarily unavailable"),
            ((200, b"not json"), 503, "temporarily unavailable"),
            ((200, b'{"result": "no text field"}'), 502, "invalid response"),
            ((200, b'{"text": "   "}'), 422, "No speech was detected"),
        ):
            with self.subTest(answer=answer):
                _Endpoint.answer = answer
                with self.assertRaisesRegex(server.VoiceInputError, pattern) as raised:
                    server.transcribe_voice(b"aac", "audio/mp4")
                self.assertEqual(raised.exception.status, status)
                self.assertNotIn("sk-private", str(raised.exception))
        self.assertIn("voice endpoint refused the recording: HTTP 401", self.logs)
        self.assertNotIn("sk-private", "\n".join(self.logs))

    def test_a_redirecting_endpoint_is_refused_so_the_key_never_follows_it(self):
        _Endpoint.redirect_to = f"http://127.0.0.1:{self.endpoint.server_port}/elsewhere"
        self.use({"url": self.url, "key": "sk-private"})
        with self.assertRaisesRegex(server.VoiceInputError, "temporarily unavailable") as raised:
            server.transcribe_voice(b"aac", "audio/mp4")
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual([seen["path"] for seen in _Endpoint.seen], ["/v1/audio/transcriptions"])
        self.assertIn("voice endpoint refused the recording: HTTP 302", self.logs)
        self.assertNotIn("sk-private", "\n".join(self.logs))

    def test_unreachable_endpoint_is_unavailable_not_a_traceback(self):
        self.endpoint.shutdown()
        self.endpoint.server_close()
        self.use({"url": self.url})
        with self.assertRaisesRegex(server.VoiceInputError, "temporarily unavailable") as raised:
            server.transcribe_voice(b"aac", "audio/mp4")
        self.assertEqual(raised.exception.status, 503)


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

    def test_validation_accepts_the_two_names_and_an_http_endpoint_only(self):
        for value in (None, "browser", "local", {"url": "https://api.example/v1/audio/transcriptions"},
                      {"url": "http://10.0.0.5:8000/v1/audio/transcriptions", "model": "Systran/faster-whisper-small", "key": "k"}):
            config.validate_voice(value)
        for value in ("whisper", "", {"url": "ftp://x"}, {"url": "api.example"}, {"url": "https://x", "extra": 1},
                      {"url": "https://x", "key": " "}, {"model": "m"}, 3,
                      {"url": "https://user:secret@api.example/v1"}, {"url": "https://api.example/v1?api_key=secret"},
                      {"url": "https://api.example/v1#frag"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.validate_voice(value)

    def test_operator_request_applies_the_endpoint_and_records_it_without_the_key(self):
        value = {"url": "https://api.example/v1/audio/transcriptions", "key": "sk-private"}
        with self.assertRaisesRegex(dispatch.T.TransitionError, "endpoint"):
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

        dispatch.request_setting(None, "voice", "local", "Back to Whisper", actor=config.OPERATOR_ACTOR)
        dispatch.run_settings()
        self.assertEqual(config.voice_setting(), {"backend": "local"})
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
        for args in (("--voice", "local", "--voice-key-file", str(key_file)), ("--wip", "3", "--voice-model", "m"),
                     ("--voice", "browser", "--wip", "3"),
                     ("--voice", "https://api.example/v1", "--voice-key-file", str(config.ROOT / "empty-key.txt"))):
            self.assertNotEqual(self.alt("machine", "set", *args, "--reason", "test", env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR}).returncode, 0)


class TestVoiceEndpoint(AltitudeCase):
    def setUp(self):
        super().setUp()
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

    def request(self, body: bytes, content_type: str, *, length: int | None = None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=2)
        connection.request(
            "POST",
            "/api/transcribe",
            body=body,
            headers={"Content-Type": content_type, "Content-Length": str(len(body) if length is None else length)},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_reports_the_backend_the_composer_should_use(self):
        self.patch(config, "machine_settings", return_value={"voice": "local"})
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=2)
        connection.request("GET", "/api/voice")
        response = connection.getresponse()
        self.assertEqual((response.status, json.loads(response.read())), (200, {"backend": "local"}))
        connection.close()

    def test_passes_the_raw_recording_to_the_adapter(self):
        seen = {}

        def transcribe(raw, content_type):
            seen.update(raw=raw, content_type=content_type)
            return "review me first"

        self.patch(server, "transcribe_voice", new=transcribe)

        status, payload = self.request(b"aac bytes", "audio/mp4")

        self.assertEqual((status, payload), (200, {"text": "review me first"}))
        self.assertEqual(seen, {"raw": b"aac bytes", "content_type": "audio/mp4"})

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
