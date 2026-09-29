"""The voice setting: host voice by default where the speech model can run, browser recognition elsewhere, one
durable operator request to change it, and the removal of a speech-service URL and key saved before."""
from __future__ import annotations

import http.client
import json
import threading

from tests.support import AltitudeCase
from altitude import config, dispatch, platform, server, speech, state as S


class VoiceCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "ROOT", self.tmp / "machine")
        config.ensure_root()

    def runs_here(self, supported: bool):
        self.patch(platform, "speech_runtime",
                   return_value=("linux-x86_64-cp312", "") if supported else (None, "voice runs on Linux x86_64 only for now"))


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
        self.assertEqual(json.loads(shown.stdout)["voice_request"]["voice"], "removed speech service")

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


if __name__ == "__main__":
    import unittest
    unittest.main()
