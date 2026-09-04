"""The browser-recording adapter is bounded, temporary, and fails without exposing internals."""
from __future__ import annotations

import http.client
import json
import subprocess
import threading
import wave
from pathlib import Path

from tests.support import AltitudeCase
from altitude import server


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
