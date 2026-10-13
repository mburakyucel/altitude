"""A project's own model settings: how they resolve, and how Settings lists and resets them through the real API."""
import http.client
import json
import threading

from tests.support import AltitudeCase
from altitude import config, engines, l3, route, server, state as S


class TestProjectModelSettings(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        path = config.ROOT / "events.jsonl"
        if path.exists():
            self.addCleanup(path.write_bytes, path.read_bytes())
        else:
            self.addCleanup(path.unlink, missing_ok=True)
        self.quiet_engines()
        self.patch(server, "log")
        self.patch(engines, "installation", return_value={"available": None, "why": "Access unknown"})
        self.patch(engines, "review_capability", return_value={"available": True})
        self.patch(engines, "usage_hold", return_value=None)
        self.patch(route, "_usage", return_value={engine: (None, None) for engine in config.ENGINES})
        self.register(self.project)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        settings_path = config.ROOT / "settings.json"
        if settings_path.exists():
            self.addCleanup(S.write_json, settings_path, config.machine_settings())
        else:
            self.addCleanup(settings_path.unlink, missing_ok=True)
        S.write_json(settings_path, {})

    def call(self, method, body=None, suffix=""):
        conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        conn.request(method, "/api/project-model-settings" + suffix, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_project_settings_resolve_below_explicit_requests_and_the_role_choices(self):
        for engine in config.ENGINES:
            for role in config.ROLES:
                built_in = route.pick_engine(role, forced=engine, project={})
                self.assertEqual((built_in["model"], built_in["effort"]),
                                 (config.MODELS[engine][role], config.task_effort(engine, None, role=role)))
                project = {config.role_setting(role, engine, "model"): "scoped",
                           config.role_setting(role, engine, "effort"): "native"}
                choice = route.pick_engine(role, forced=engine, project=project)
                self.assertEqual((choice["model"], choice["effort"]), ("scoped", config.task_effort(engine, "native")))
                pinned = route.pick_engine(role, forced=engine, model="exact", effort="low", project=project)
                self.assertEqual((pinned["model"], pinned["effort"]), ("exact", "low"))
        S.write_json(config.ROOT / "settings.json", {"new_tasks": {"engine": "codex", "model": "explicit-new-task", "effort": "low"}})
        choice = route.pick_engine("l2", project={"l2_codex_effort": "max"})
        self.assertEqual((choice["model"], choice["effort"]), ("explicit-new-task", "low"))

    def test_a_pair_saved_by_the_removed_settings_table_no_longer_applies(self):
        S.write_json(config.ROOT / "settings.json", {"model_defaults_codex": {"model": "other-model", "effort": "ultra"},
                                                     "model_defaults_claude": {"model": "opus", "effort": "max"}})
        for engine in config.ENGINES:
            for role in config.ROLES:
                choice = route.pick_engine(role, forced=engine, project={})
                self.assertEqual((choice["model"], choice["effort"]),
                                 (config.MODELS[engine][role], config.task_effort(engine, None, role=role)))
        review = route.pick_review({"l2_engine": "claude"}, {})
        self.assertEqual((review["engine"], review["effort"]), ("codex", "medium"))

    def test_inherited_and_native_effort_have_distinct_role_behavior(self):
        for role, expected in (("l2", "high"), ("l3", "medium")):
            self.assertEqual(route.pick_engine(role, forced="codex")["effort"], expected)
            choice = route.pick_engine(role, forced="codex", project={config.role_setting(role, "codex", "effort"): "native"})
            self.assertEqual((choice["effort"], choice["requested_effort"]), ("medium", "native"))

    def test_a_saved_native_choice_runs_each_engines_own_effort(self):
        # Codex's effort list offers no Native row, yet a choice saved with native keeps meaning the engine's own.
        self.assertIsNone(config.validate_choice({"engine": "codex", "effort": "native"}))
        project = {"l3_choice": {"engine": "codex", "effort": "native"}, config.role_setting("l3", "codex", "effort"): "high"}
        choice = route.pick_engine("l3", project=project)
        self.assertEqual((choice["engine"], choice["effort"], choice["requested_effort"]), ("codex", "medium", "native"))

    def test_review_never_copies_the_owner_or_project_task_effort(self):
        inherited = route.pick_review({"l2_engine": "claude", "effort": "ultra"}, {"l2_codex_effort": "max"})
        self.assertEqual((inherited["engine"], inherited["effort"]), ("codex", "medium"))
        explicit = route.pick_review({"l2_engine": "claude"}, {}, engine="codex", model="exact")
        self.assertEqual((explicit["model"], explicit["effort"]), ("exact", "medium"))

    def test_settings_lists_project_settings_and_reset_clears_one_pair_and_keeps_routing(self):
        status, initial = self.call("GET")
        self.assertEqual((status, initial), (200, {"rows": []}))
        self.register(self.project, l3_choice={"engine": "codex", "model": "custom", "effort": "high"},
                      l2_codex_model="gpt-6.1-sol", l2_codex_effort="native", l2_engine="codex",
                      routing=config.parse_routing("codex:tier-model"))
        _, view = self.call("GET")
        self.assertEqual([(row["label"], row["detail"], row["editable"]) for row in view["rows"]],
                         [(f"{self.project} · Tasks", "Codex · 6.1 Sol · Default", True),
                          (f"{self.project} · Routing", "Codex · tier-model", False)],
                         "the coordinator and New tasks choices live on their buttons, not here")
        row, tier = view["rows"]
        status, result = self.call("POST", {"id": row["id"], "expected": None}, "/reset")
        self.assertEqual((status, result["changed"]), (409, True))
        status, result = self.call("POST", {"id": row["id"], "expected": row["expected"]}, "/reset")
        self.assertEqual(status, 200)
        self.assertEqual([row["label"] for row in result["rows"]], [f"{self.project} · Routing"])
        entry = config.project(self.project)
        self.assertNotIn("l2_codex_model", entry)
        self.assertNotIn("l2_codex_effort", entry)
        self.assertEqual((entry["l2_engine"], entry["l3_choice"]["model"]), ("codex", "custom"))
        status, _ = self.call("POST", {"id": tier["id"], "expected": None}, "/reset")
        self.assertEqual(status, 400)
        status, _ = self.call("POST", {"id": tier["id"]}, "/reset")
        self.assertEqual(status, 400)

    def test_reset_wakes_a_waiting_coordinator(self):
        self.register(self.project, l3_codex_effort="high")
        path = l3.queue_path(self.project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        wake = self.patch(server, "request_l3_drain")
        _, view = self.call("GET")
        status, _ = self.call("POST", {"id": view["rows"][0]["id"], "expected": view["rows"][0]["expected"]}, "/reset")
        self.assertEqual(status, 200)
        wake.assert_called_once_with(self.project)
