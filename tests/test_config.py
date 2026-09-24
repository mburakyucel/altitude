"""The project default L2 model per engine: CLI/API persistence beside effort, and routing that treats it as a preference."""
import http.client
import json
import threading

from tests.support import AltitudeCase
from altitude import config, dispatch, route, server, tasks as T


class TestProjectModel(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()

    def test_cli_sets_lists_and_unsets_each_engine_default_without_touching_effort(self):
        self.register(self.project, l2_effort="high")
        for engine in config.ENGINES:
            flag = config.model_setting("l2", engine).replace("_", "-")
            result = self.alt("project", "set", self.project, f"--{flag}", "chosen-model", "--reason", "Set default model")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["request"]["status"], "pending")
            self.assertNotIn(config.model_setting("l2", engine), config.project(self.project))
            self.assertEqual(dispatch.run_settings(self.project)[config.model_setting("l2", engine)]["status"], "done")
            self.assertEqual(config.project(self.project)[config.model_setting("l2", engine)], "chosen-model")
            choice = route.pick_engine("l2", forced=engine, project=config.project(self.project))
            self.assertEqual((choice["engine"], choice["model"], choice["effort"]), (engine, "chosen-model", "high"))
        listing = self.alt("project", "list")
        self.assertEqual({key: json.loads(listing.stdout)[self.project][key] for key in config.MODEL_SETTINGS},
                         dict.fromkeys(config.MODEL_SETTINGS, "chosen-model"))
        self.assertFalse(route.pick_engine("l2", project=config.project(self.project))["pinned"])
        for engine in config.ENGINES:
            flag = config.model_setting("l2", engine).replace("_", "-")
            result = self.alt("project", "set", self.project, f"--unset-{flag}", "--reason", "Restore the role default")
            self.assertEqual(result.returncode, 0, result.stderr)
            dispatch.run_settings(self.project)
            self.assertNotIn(config.model_setting("l2", engine), config.project(self.project))
            self.assertEqual(route.pick_engine("l2", forced=engine, project=config.project(self.project))["model"],
                             config.default_model("l2", engine))
        self.assertEqual(config.project(self.project)["l2_effort"], "high")

    def test_invalid_values_are_refused_before_anything_is_queued(self):
        self.register(self.project)
        original = config.project(self.project)
        for value in ("", "two words", " opus", 7):
            with self.subTest(value=value), self.assertRaisesRegex(T.TransitionError, "one alias or model id"):
                dispatch.request_setting(self.project, "l2_model", value, "Set default model", actor="burak")
        with self.assertRaisesRegex(T.TransitionError, "unknown project setting"):
            dispatch.request_setting(self.project, "l3_model", "opus", "Set default model", actor="burak")
        self.assertEqual(dispatch.run_settings(self.project)["l2_model"], {})
        self.assertEqual(config.project(self.project), original)


class TestProjectModelHTTP(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(server, "log", new=lambda *args, **kwargs: None)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_defaults_view_reads_saves_and_resets_the_model_beside_effort(self):
        self.register(self.project, l2_effort="medium")
        status, view = self.call("GET", f"/api/effort/{self.project}")
        self.assertEqual((status, view["l2"]), (200, "medium"))
        self.assertEqual(view["models"]["claude"], {"label": "Claude", "value": None, "default": "opus",
                                                    "choices": list(config.MODEL_ALIASES)})
        self.assertEqual(view["models"]["codex"], {"label": "Codex", "value": None, "default": "native", "choices": []})
        status, view = self.call("POST", "/api/model", {"project": self.project, "engine": "claude", "model": "fable"})
        self.assertEqual((status, view["models"]["claude"]["value"], view["l2"]), (200, "fable", "medium"))
        self.assertEqual(config.project(self.project)["l2_model"], "fable")
        for body in ({"project": self.project, "engine": "gemini", "model": "x"},
                     {"project": self.project, "engine": "codex", "model": "two words"},
                     {"project": self.project, "engine": "codex"}):
            status, view = self.call("POST", "/api/model", body)
            self.assertEqual(status, 400, body)
            self.assertTrue(view["error"])
        status, view = self.call("POST", "/api/model", {"project": self.project, "engine": "claude", "model": None})
        self.assertEqual((status, view["models"]["claude"]["value"]), (200, None))
        self.assertNotIn("l2_model", config.project(self.project))
