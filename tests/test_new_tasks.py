"""New tasks is one installation setting the quota control saves; projects with an Only engine are named."""
import http.client
import json
import threading
import unittest

from tests.support import AltitudeCase, set_project_setting
from altitude import config, dispatch, engines, server, state as S, tasks as T


class TestNewTasks(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(server, "log", new=lambda *a, **k: None)
        self.patch(engines, "installation", return_value={"available": None, "why": "test installation"})
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self._auto)
        self._auto()

    @staticmethod
    def _auto():
        settings = config.machine_settings()
        settings.pop("new_tasks", None)
        S.write_json(config.ROOT / "settings.json", settings)
        (config.ROOT / "new_tasks-request.json").unlink(missing_ok=True)

    def _call(self, method, path, body=None):
        host, port = self.httpd.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"null")

    def test_saves_refuses_a_stale_window_and_returns_to_auto(self):
        view = server.new_tasks_view()
        self.assertEqual((view["value"], view["unavailable"], view["only"]), (None, None, []))
        self.assertTrue(view["models"]); self.assertEqual(list(view["efforts"]), list(config.ENGINES))
        choice = {"engine": config.ENGINES[0], "model": "chosen", "effort": "high"}
        status, out = self._call("POST", "/api/new-tasks", {"value": choice, "expected": None})
        self.assertEqual((status, out["value"]), (200, choice))
        self.assertEqual(config.machine_settings()["new_tasks"], choice)
        status, out = self._call("POST", "/api/new-tasks", {"value": None, "expected": None})
        self.assertEqual((status, out["changed"]), (409, True))
        status, out = self._call("POST", "/api/new-tasks", {"value": {"engine": "gemini"}})
        self.assertEqual(status, 400); self.assertIn("engine must be one of", out["error"])
        status, out = self._call("POST", "/api/new-tasks", {"value": None, "expected": choice})
        self.assertEqual((status, out["value"]), (200, None))
        self.assertNotIn("new_tasks", config.machine_settings())

    def test_a_stale_expected_value_is_refused_when_applied_not_only_when_requested(self):
        """Two windows both read Auto; the second save, requested after the first applied, must not overwrite it."""
        first, second = {"effort": "high"}, {"effort": "low"}
        dispatch.request_setting(None, "new_tasks", first, "Settings", actor=config.OPERATOR_ACTOR, expected=None)
        self.assertEqual(dispatch._run_setting(None, "new_tasks")["status"], "done")
        dispatch.request_setting(None, "new_tasks", second, "Settings", actor=config.OPERATOR_ACTOR, expected=None)
        result = dispatch._run_setting(None, "new_tasks")
        self.assertEqual((result["status"], result.get("changed")), ("refused", True))
        self.assertEqual(config.machine_settings()["new_tasks"], first)

    def test_only_engine_projects_are_listed_and_l3_cannot_change_every_project(self):
        set_project_setting(self.project, "l2_engine", config.ENGINES[1])
        self.assertEqual(server.new_tasks_view()["only"], [{"project": self.project, "engine": config.ENGINES[1]}])
        with self.assertRaises(T.TransitionError):
            dispatch.request_setting(None, "new_tasks", "@high", "L3 cannot steer every project", actor="l3")
        dispatch.request_setting(None, "new_tasks", "@high", "CLI spelling", actor="burak")
        dispatch._run_setting(None, "new_tasks")
        self.assertEqual(config.machine_settings()["new_tasks"], {"effort": "high"})


if __name__ == "__main__":
    unittest.main()
