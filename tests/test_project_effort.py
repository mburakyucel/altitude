"""Independent model/effort defaults per role and engine: API/CLI persistence and real routing across L3 turns."""
import http.client
import json
import threading
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, l3, route, server, tasks as T


def set_default(project, setting, value):
    dispatch.request_setting(project, setting, value, "Set test default", actor="burak")
    dispatch._run_setting(project, setting)
    return config.defaults_view(project)


def field(view, role, engine, kind):
    row = next(r for r in view["roles"] if r["role"] == role)
    return next(e for e in row["engines"] if e["engine"] == engine)[kind]


class TestProjectEffort(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()

    def test_each_role_and_engine_default_is_independent_and_resets(self):
        self.register(self.project, l3_engine="codex", l2_engine="claude", routing=config.parse_routing("codex > claude:opus"))
        original = config.project(self.project)
        values = {"l3_model": "sonnet", "l3_effort": "low", "l3_codex_model": "codex-model", "l3_codex_effort": "ultra",
                  "l2_model": "fable", "l2_effort": "xhigh", "l2_codex_model": "other-model", "l2_codex_effort": "medium"}
        self.assertEqual(set(values), set(config.DEFAULT_SETTINGS))
        for setting, value in values.items():
            before = config.project(self.project)
            view = set_default(self.project, setting, value)
            role, engine, kind = config.DEFAULT_SETTINGS[setting]
            self.assertEqual(field(view, role, engine, kind)["value"], value)
            self.assertEqual(config.project(self.project), {**before, setting: value}, setting)
        for setting in values:
            set_default(self.project, setting, None)
        self.assertEqual(config.project(self.project), original)
        for engine in config.ENGINES:
            self.assertIsNone(config.task_effort(engine, None, role="l3"))
            self.assertEqual(config.task_effort(engine, None), "high" if engine == "codex" else None)

    def test_defaults_follow_the_routed_engine_and_an_explicit_launch_choice_wins(self):
        self.register(self.project, l3_model="sonnet", l3_effort="low", l3_codex_model="codex-l3", l3_codex_effort="high",
                      l2_effort="max", l2_codex_model="codex-l2")
        saved = config.project(self.project)
        expected = {("l3", "claude"): ("sonnet", "low"), ("l3", "codex"): ("codex-l3", "high"),
                    ("l2", "claude"): ("opus", "max"), ("l2", "codex"): ("codex-l2", "high")}
        for (role, engine), pair in expected.items():
            with self.subTest(role=role, engine=engine):
                with config.edit_projects() as projects:
                    projects[self.project]["routing"] = [[{"engine": engine, "model": None}]]
                choice = route.pick_engine(role, project=config.project(self.project))
                self.assertEqual((choice["engine"], choice["model"], choice["effort"]), (engine, *pair))
                self.assertFalse(choice["pinned"], "a default never pins an engine")
                explicit = route.pick_engine(role, project=config.project(self.project), effort="medium", model="chosen", forced=engine)
                self.assertEqual((explicit["model"], explicit["effort"], explicit["requested_effort"]), ("chosen", "medium", "medium"))
        self.assertEqual({k: v for k, v in config.project(self.project).items() if k != "routing"}, saved)

    def test_invalid_settings_leave_storage_unchanged_and_the_engine_pin_is_independent(self):
        self.register(self.project, l3_engine="claude", l2_engine="claude", l2_effort="high")
        original = config.project(self.project)
        for setting, value in (("l1_effort", "high"), ("l3_effort", "invalid"), ("l2_effort", ""), ("l3_effort", "ultra"),
                               ("l2_effort", "ultra"), ("l3_model", "two words"), ("l2_codex_model", "")):
            with self.subTest(setting=setting, value=value), self.assertRaises((ValueError, T.TransitionError)):
                set_default(self.project, setting, value)
            self.assertEqual(config.project(self.project), original)
        set_default(self.project, "l3_codex_effort", "ultra")
        set_default(self.project, "l3_engine", "claude")
        set_default(self.project, "l3_engine", None)
        self.assertEqual(config.project(self.project)["l3_codex_effort"], "ultra")
        self.assertNotIn("l3_engine", config.project(self.project))

    def test_auto_filters_an_unsupported_explicit_choice_and_never_lowers_effort(self):
        self.register(self.project, routing=config.parse_routing("claude:opus > codex"))
        for role in ("l3", "l2"):
            choice = route.pick_engine(role, project=config.project(self.project), effort="ultra")
            self.assertEqual((choice["engine"], choice["effort"], choice["requested_effort"]), ("codex", "ultra", "ultra"))
            self.assertIn("does not support reasoning effort", choice["why"])
            unavailable = route.pick_engine(role, project=config.project(self.project), effort="ultra", forced="claude")
            self.assertIsNone(unavailable["engine"])
            self.assertIn("ultra", unavailable["why"])

    def test_cli_queues_each_default_and_reset_through_real_daemon_storage(self):
        self.register(self.project, l3_engine="codex", l2_engine="codex")
        for setting, (role, engine, kind) in config.DEFAULT_SETTINGS.items():
            value = "chosen-model" if kind == "model" else config.ENGINE_EFFORTS[engine][-1]
            result = self.alt("project", "set", self.project, "--" + setting.replace("_", "-"), value, "--reason", "Set default")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["request"]["status"], "pending")
            self.assertNotIn(setting, config.project(self.project))
            self.assertEqual(dispatch.run_settings(self.project)[setting]["status"], "done")
            self.assertEqual(config.project(self.project)[setting], value)
        refused = self.alt("project", "set", self.project, "--l3-effort", "ultra", "--reason", "Claude has no Ultra")
        self.assertNotEqual(refused.returncode, 0)
        result = self.alt("project", "set", self.project, "--unset-l3-codex-effort", "--reason", "Restore default")
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatch.run_settings(self.project)
        saved = config.project(self.project)
        self.assertNotIn("l3_codex_effort", saved)
        self.assertEqual((saved["l3_effort"], saved["l2_codex_effort"], saved["l3_engine"]), ("max", "ultra", "codex"))

    def test_cli_sets_only_engines_and_the_l3_choice(self):
        for flags, setting, value in ((("--l3-choice", "fable@high"), "l3_choice", {"engine": "claude", "model": "fable", "effort": "high"}),
                                      (("--l2-engine", config.ENGINES[1]), "l2_engine", config.ENGINES[1]),
                                      (("--unset-l3-choice",), "l3_choice", None)):
            result = self.alt("project", "set", self.project, *flags, "--reason", "Set from the CLI")
            self.assertEqual(result.returncode, 0, result.stderr)
            dispatch.run_settings(self.project)
            self.assertEqual(config.project(self.project).get(setting), value)
        refused = self.alt("project", "set", self.project, "--l3-choice", "claude@ultra", "--reason", "Claude has no Ultra")
        self.assertNotEqual(refused.returncode, 0)

    def test_l3_changes_next_turn_with_same_session_and_distinct_observation(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                set_default(self.project, "l3_engine", engine)
                setting = config.role_setting("l3", engine, "effort")
                set_default(self.project, setting, "high")
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
                    set_default(self.project, setting, "max")
                    self.assertEqual(l3.info(self.project)["launch_effort"], "high")
                    self.assertTrue(l3.turn(self.project, "Next turn")["completed"])
                    set_default(self.project, setting, "native")
                    self.assertTrue(l3.turn(self.project, "Native turn")["completed"])
                self.assertEqual([call["effort"] for call in calls], ["high", "max", None])
                self.assertEqual([call["resume"] for call in calls], [None, f"{engine}-conversation", f"{engine}-conversation"])
                saved = l3.info(self.project)["sessions"][engine]
                self.assertEqual((saved["effort"], saved["launch_effort"]), ("native", None))
                self.assertEqual(saved["engine_reasoning_effort"], "low" if engine == "codex" else None)

    def test_l3_model_rejection_keeps_requested_effort_without_fallback(self):
        self.register(self.project, routing=config.parse_routing("codex > claude:opus"), l3_codex_effort="max")
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

    def test_defaults_api_reads_saves_resets_and_reports_invalid_requests(self):
        self.register(self.project, l3_engine="claude", l2_effort="medium")
        status, view = self.call("GET", f"/api/defaults/{self.project}")
        self.assertEqual((status, view["l3_engine"]), (200, "claude"))
        self.assertEqual([row["role"] for row in view["roles"]], ["l3", "l2"])
        self.assertEqual(field(view, "l2", "claude", "effort")["value"], "medium")
        self.assertEqual(field(view, "l3", "claude", "model"), {"setting": "l3_model", "value": None, "default": "fable",
                                                               "choices": list(config.MODEL_ALIASES)})
        self.assertEqual(field(view, "l2", "codex", "model"), {"setting": "l2_codex_model", "value": None,
                                                              "default": "CLI default", "choices": []})
        self.assertEqual(field(view, "l2", "codex", "effort")["default"], "High")
        self.assertEqual([c["value"] for c in field(view, "l3", "claude", "effort")["choices"]], list(config.ENGINE_EFFORTS["claude"]))
        self.assertEqual([c["value"] for c in field(view, "l3", "codex", "effort")["choices"]], list(config.TASK_EFFORTS))
        for setting, value in (("l3_effort", "high"), ("l2_codex_effort", "ultra"), ("l3_codex_model", "codex-model"), ("l3_effort", None)):
            status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": setting, "value": value})
            role, engine, kind = config.DEFAULT_SETTINGS[setting]
            self.assertEqual((status, field(view, role, engine, kind)["value"]), (200, value))
        self.assertEqual(field(self.call("GET", f"/api/defaults/{self.project}")[1], "l2", "claude", "effort")["value"], "medium")
        for body in ({"setting": "l1_effort", "value": "high"}, {"setting": "l3_effort", "value": "ultra"},
                     {"setting": "l2_effort", "value": "invalid"}, {"setting": "routing", "value": "codex"},
                     {"setting": "l2_model", "value": "two words"}):
            status, view = self.call("POST", "/api/defaults", {"project": self.project, **body})
            self.assertEqual(status, 400, body)
            self.assertTrue(view["error"])
        self.assertEqual(self.call("GET", "/api/defaults/not-managed")[0], 404)

    def test_l2_preference_api_saves_reloads_refuses_and_names_custom_routing(self):
        status, view = self.call("GET", f"/api/defaults/{self.project}")
        self.assertEqual((view["l2_preference"], view["l2_engine"], view["routing"]), (None, None, None))
        self.assertEqual(view["engines"], [{"value": e, "label": config.ENGINE_LABELS[e], "routed": True,
                                            "efforts": list(config.ENGINE_EFFORTS[e])} for e in config.ENGINES])
        engine = config.ENGINES[0]
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l2_preference", "value": engine})
        self.assertEqual((status, view["l2_preference"]), (200, engine))
        self.assertEqual(self.call("GET", f"/api/defaults/{self.project}")[1]["l2_preference"], engine)
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l2_preference", "value": "other"})
        self.assertEqual(status, 400); self.assertIn("provider preference", view["error"])
        self.assertEqual(config.project(self.project)["l2_preference"], engine)
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l2_preference", "value": None})
        self.assertEqual((status, view["l2_preference"]), (200, None))
        self.register(self.project, routing=config.parse_routing(f"{engine}:chosen"), l2_engine=engine)
        view = self.call("GET", f"/api/defaults/{self.project}")[1]
        self.assertEqual((view["routing"], view["l2_engine"]), (f"{engine}:chosen", engine))
        self.assertEqual([e["routed"] for e in view["engines"]], [e == engine for e in config.ENGINES])

    def test_only_pins_and_l3_choice_save_through_the_settings_api_and_refuse_a_stale_page(self):
        engine, other = config.ENGINES
        for setting in ("l2_engine", "l3_engine"):
            status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": setting, "value": engine})
            self.assertEqual((status, view[setting]), (200, engine))
            status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": setting, "value": "gemini"})
            self.assertEqual(status, 400); self.assertIn("Only engine", view["error"])
            status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": setting, "value": None})
            self.assertEqual((status, view[setting]), (200, None))
        choice = {"engine": other, "model": "chosen", "effort": "high"}
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l3_choice", "value": choice,
                                                           "expected": None})
        self.assertEqual((status, view["l3_choice"]), (200, choice))
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l3_choice", "value": None,
                                                           "expected": {"engine": engine}})
        self.assertEqual((status, view["changed"]), (409, True))
        self.assertEqual(config.project(self.project)["l3_choice"], choice)
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l3_choice",
                                                           "value": {"model": "chosen", "effort": "high"}})
        self.assertEqual(status, 400); self.assertIn("named engine", view["error"])
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l3_choice", "value": None,
                                                           "expected": choice})
        self.assertEqual((status, view["l3_choice"]), (200, None))
        self.assertNotIn("l3_choice", config.project(self.project))

    def test_pending_cli_setting_is_not_overwritten_by_a_different_ui_choice(self):
        dispatch.request_setting(self.project, "l3_effort", "high", "CLI setting", actor="burak")
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l3_effort", "value": "low"})
        self.assertEqual(status, 400)
        self.assertIn("already pending", view["error"])
        self.assertNotIn("l3_effort", config.project(self.project))
        dispatch.run_settings(self.project)
        self.assertEqual(config.project(self.project)["l3_effort"], "high")
        status, view = self.call("POST", "/api/defaults", {"project": self.project, "setting": "l3_effort", "value": "low"})
        self.assertEqual((status, field(view, "l3", "claude", "effort")["value"]), (200, "low"))
        dispatch.run_settings(self.project)
        self.assertEqual(config.project(self.project)["l3_effort"], "low")
