"""Independent role settings, API/CLI persistence, and real routing across L3 turns."""
import http.client
import json
import threading
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, l3, route, server, tasks as T


def set_effort(project, role, value):
    dispatch.request_setting(project, f"{role}_effort", value, "Set test effort", actor="burak")
    dispatch._run_setting(project, f"{role}_effort")
    return config.effort_view(project)


class TestProjectEffort(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()

    def test_independent_settings_preserve_models_and_reset_defaults(self):
        self.register(self.project, l3_engine="codex", l2_engine="claude", l3_model="chosen-model",
                      l2_model="opus", routing=config.parse_routing("codex > claude:opus"))
        original = config.project(self.project)
        set_effort(self.project, "l3", "ultra")
        view = set_effort(self.project, "l2", "medium")
        self.assertEqual((view["l3"], view["l2"]), ("ultra", "medium"))
        self.assertEqual({k: v for k, v in config.project(self.project).items() if k not in config.EFFORT_SETTINGS}, original)
        set_effort(self.project, "l3", None)
        self.assertNotIn("l3_effort", config.project(self.project))

        self.assertEqual(config.project(self.project)["l2_effort"], "medium")
        set_effort(self.project, "l2", None)
        self.assertEqual(config.project(self.project), original)
        for engine in config.ENGINES:
            self.assertIsNone(config.task_effort(engine, None, role="l3"))
            self.assertEqual(config.task_effort(engine, None), "high" if engine == "codex" else None)

    def test_invalid_settings_and_incompatible_pins_leave_storage_unchanged(self):
        self.register(self.project, l3_engine="claude", l2_engine="claude", l2_effort="high")
        original = config.project(self.project)
        for role, effort in (("l1", "high"), ("l3", "invalid"), ("l2", ""), ("l3", "ultra"), ("l2", "ultra")):
            with self.subTest(role=role, effort=effort), self.assertRaises((ValueError, T.TransitionError)):
                set_effort(self.project, role, effort)
            self.assertEqual(config.project(self.project), original)
        config.set_l3_engine(self.project, "codex")
        set_effort(self.project, "l3", "ultra")
        with self.assertRaisesRegex(ValueError, "does not support reasoning effort"):
            config.set_l3_engine(self.project, "claude")
        self.assertEqual(config.project(self.project)["l3_engine"], "codex")

    def test_auto_filters_unsupported_role_choices_and_never_lowers_effort(self):
        self.register(self.project, routing=config.parse_routing("claude:opus > codex"))
        for role in ("l3", "l2"):
            set_effort(self.project, role, "ultra")
            choice = route.pick_engine(role, project=config.project(self.project))
            self.assertEqual((choice["engine"], choice["effort"], choice["requested_effort"]), ("codex", "ultra", "ultra"))
            self.assertIn("does not support reasoning effort", choice["why"])
            with config.edit_projects() as projects:
                projects[self.project]["routing"] = config.parse_routing("claude:opus")
            unavailable = route.pick_engine(role, project=config.project(self.project))
            self.assertIsNone(unavailable["engine"])
            self.assertIn("ultra", unavailable["why"])
            with config.edit_projects() as projects:
                projects[self.project]["routing"] = config.parse_routing("claude:opus > codex")

    def test_cli_queues_each_role_and_reset_through_real_daemon_storage(self):
        self.register(self.project, l3_engine="codex", l2_engine="codex", l3_model="chosen-model")
        for role, value in (("l3", "ultra"), ("l2", "medium")):
            result = self.alt("project", "set", self.project, f"--{role}-effort", value, "--reason", "Set role effort")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["request"]["status"], "pending")
            self.assertNotIn(f"{role}_effort", config.project(self.project))
            drained = dispatch.run_settings(self.project)
            self.assertEqual(drained[f"{role}_effort"]["status"], "done")
            self.assertEqual(config.project(self.project)[f"{role}_effort"], value)
        result = self.alt("project", "set", self.project, "--unset-l3-effort", "--reason", "Restore default")
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatch.run_settings(self.project)
        saved = config.project(self.project)
        self.assertNotIn("l3_effort", saved)
        self.assertEqual((saved["l2_effort"], saved["l3_model"], saved["l3_engine"]), ("medium", "chosen-model", "codex"))

    def test_queued_cli_change_revalidates_a_changed_engine_pin(self):
        dispatch.request_setting(self.project, "l3_effort", "ultra", "Set effort", actor="burak")
        config.set_l3_engine(self.project, "claude")
        result = dispatch.run_settings(self.project)["l3_effort"]
        self.assertEqual(result["status"], "refused")
        self.assertIn("does not support reasoning effort", result["note"])
        self.assertNotIn("l3_effort", config.project(self.project))
        config.set_l3_engine(self.project, "codex")
        retry = dispatch.request_setting(self.project, "l3_effort", "ultra", "Set effort", actor="burak")
        self.assertFalse(retry["idempotent"], "a refused request must be retryable after the pin changes")
        self.assertEqual(dispatch.run_settings(self.project)["l3_effort"]["status"], "done")
        self.assertEqual(config.project(self.project)["l3_effort"], "ultra")

    def test_l3_changes_next_turn_with_same_session_and_distinct_observation(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                config.set_l3_engine(self.project, engine)
                set_effort(self.project, "l3", "high")
                calls = []

                def provider(prompt, **kwargs):
                    calls.append(kwargs)
                    session = l3.info(self.project)
                    self.assertEqual(session["launch_effort"], kwargs["effort"])
                    self.assertIsNone(session["engine_reasoning_effort"])
                    return {"text": "answer", "session_id": f"{engine}-conversation",
                            "reported_session_id": f"{engine}-conversation", "error": None,
                            "engine_model": "reported-model", "engine_reasoning_effort": "low",
                            "usage": {}, "returncode": 0}

                boundary = "codex_exec" if engine == "codex" else "claude_print"
                with mock.patch.object(engines, boundary, side_effect=provider):
                    self.assertTrue(l3.turn(self.project, "First turn")["completed"])
                    set_effort(self.project, "l3", "max")
                    self.assertEqual(l3.info(self.project)["launch_effort"], "high")
                    self.assertTrue(l3.turn(self.project, "Next turn")["completed"])
                    set_effort(self.project, "l3", "native")
                    self.assertTrue(l3.turn(self.project, "Native turn")["completed"])
                self.assertEqual([call["effort"] for call in calls], ["high", "max", None])
                self.assertEqual([call["resume"] for call in calls], [None, f"{engine}-conversation", f"{engine}-conversation"])
                saved = l3.info(self.project)["sessions"][engine]
                self.assertEqual((saved["effort"], saved["launch_effort"]), ("native", None))
                self.assertEqual(saved["engine_reasoning_effort"], "low" if engine == "codex" else None)

    def test_l3_model_rejection_keeps_requested_effort_without_fallback(self):
        self.register(self.project, routing=config.parse_routing("codex > claude:opus"), l3_effort="max")
        failure = {"text": "", "session_id": "conversation", "reported_session_id": "conversation",
                   "error": "reasoning effort max is unsupported by this model", "returncode": 1}
        with mock.patch.object(engines, "codex_exec", return_value=failure) as provider, \
             mock.patch.object(engines, "claude_print") as fallback:
            result = l3.turn(self.project, "Try requested effort")
        self.assertFalse(result["completed"])
        self.assertIn("unsupported", result["error"])
        provider.assert_called_once()
        fallback.assert_not_called()
        saved = l3.info(self.project)
        self.assertEqual((saved["effort"], saved["launch_effort"]), ("max", "max"))
        self.assertIsNone(saved["engine_reasoning_effort"])


class TestProjectEffortHTTP(AltitudeCase):
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

    def test_effort_api_reads_saves_resets_and_reports_invalid_requests(self):
        self.register(self.project, l3_engine="claude", l3_model="opus")
        status, view = self.call("GET", f"/api/effort/{self.project}")
        self.assertEqual((status, view["l3"], view["l2"]), (200, None, None))
        self.assertEqual([choice["value"] for choice in view["choices"]], list(config.TASK_EFFORTS))
        for role, value in (("l3", "high"), ("l2", "ultra"), ("l3", None)):
            status, view = self.call("POST", "/api/effort", {"project": self.project, "role": role, "effort": value})
            self.assertEqual((status, view[role]), (200, value))
        self.assertEqual(self.call("GET", f"/api/effort/{self.project}")[1]["l2"], "ultra")
        self.assertEqual(config.project(self.project)["l3_model"], "opus")
        for role, value in (("l1", "high"), ("l3", "ultra"), ("l2", "invalid")):
            status, view = self.call("POST", "/api/effort", {"project": self.project, "role": role, "effort": value})
            self.assertEqual(status, 400)
            self.assertTrue(view["error"])
        self.assertEqual(self.call("GET", "/api/effort/not-managed")[0], 404)

    def test_engine_change_api_refuses_incompatible_saved_effort(self):
        set_effort(self.project, "l3", "ultra")
        status, view = self.call("POST", "/api/l3/engine", {"project": self.project, "engine": "claude"})
        self.assertEqual(status, 400)
        self.assertIn("does not support reasoning effort", view["error"])
        self.assertNotIn("l3_engine", config.project(self.project))

    def test_pending_cli_setting_is_not_overwritten_by_a_different_ui_choice(self):
        dispatch.request_setting(self.project, "l3_effort", "high", "CLI setting", actor="burak")
        status, view = self.call("POST", "/api/effort", {"project": self.project, "role": "l3", "effort": "low"})
        self.assertEqual(status, 400)
        self.assertIn("already pending", view["error"])
        self.assertNotIn("l3_effort", config.project(self.project))
        dispatch.run_settings(self.project)
        self.assertEqual(config.project(self.project)["l3_effort"], "high")
        status, view = self.call("POST", "/api/effort", {"project": self.project, "role": "l3", "effort": "low"})
        self.assertEqual((status, view["l3"]), (200, "low"))
        dispatch.run_settings(self.project)
        self.assertEqual(config.project(self.project)["l3_effort"], "low")
