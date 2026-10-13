"""Global engine pairs use real settings/API storage and deterministic routing."""
import http.client
import json
import threading

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, l3, route, server, state as S, tasks as T


EMPTY = {"model": None, "effort": None}


class TestModelDefaults(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        for name in ("events.jsonl", *(f"{key}-request.json" for key in config.MODEL_DEFAULT_SETTINGS)):
            path = config.ROOT / name
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
        self.original = config.machine_settings()
        settings_path = config.ROOT / "settings.json"
        if settings_path.exists():
            self.addCleanup(S.write_json, settings_path, self.original)
        else:
            self.addCleanup(settings_path.unlink, missing_ok=True)
        S.write_json(config.ROOT / "settings.json", {})

    def call(self, method, body=None, suffix=""):
        conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        conn.request(method, "/api/model-defaults" + suffix, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def save(self, engine, model=None, effort=None):
        status, result = self.call("POST", {"engine": engine, "value": {"model": model, "effort": effort},
                                           "expected": config.model_default_pair(engine)})
        self.assertEqual(status, 200, result)
        return result

    def test_pairs_save_independently_and_stale_pair_never_overwrites(self):
        status, initial = self.call("GET")
        self.assertEqual(status, 200)
        self.assertTrue(all(row["value"] == EMPTY for row in initial["engines"]))
        self.save("claude", "opus", "max")
        first = self.save("codex", "gpt-6.1-sol", "high")
        row = next(row for row in first["engines"] if row["engine"] == "codex")
        self.assertIsNone(row["unavailable"], "unknown access remains selectable")
        self.assertIn({"value": "gpt-6.1-sol", "label": "6.1 Sol"}, row["models"])
        status, result = self.call("POST", {"engine": "codex", "value": {"model": "other", "effort": None},
                                           "expected": EMPTY})
        self.assertEqual((status, result["changed"]), (409, True))
        self.assertEqual(config.model_default_pair("claude"), {"model": "opus", "effort": "max"})
        self.assertEqual(config.model_default_pair("codex"), {"model": "gpt-6.1-sol", "effort": "high"})
        self.save("codex", "gpt-6.1-sol", "native")
        self.save("codex")
        self.assertEqual(config.model_default_pair("codex"), EMPTY)

    def test_invalid_pair_and_missing_expected_do_not_mutate(self):
        for value in ({"model": "two words", "effort": None}, {"model": None, "effort": "ultra"},
                      {"model": None}, {"model": None, "effort": None, "engine": "codex"}):
            status, _ = self.call("POST", {"engine": "claude", "value": value, "expected": EMPTY})
            self.assertEqual(status, 400)
        status, _ = self.call("POST", {"engine": "claude", "value": EMPTY})
        self.assertEqual(status, 400)
        self.assertEqual(config.machine_settings(), {})

    def test_machine_pair_queue_requires_operator(self):
        with self.assertRaises(T.TransitionError):
            dispatch.request_setting(None, "model_defaults_codex", EMPTY, "Change pair", actor="l3")

    def test_defaults_resolve_across_projects_roles_and_explicit_requests_win(self):
        self.save("claude", "fable", "medium")
        self.save("codex", "gpt-6.1-sol", "ultra")
        for engine, model, effort in (("claude", "fable", "medium"), ("codex", "gpt-6.1-sol", "ultra")):
            for role in config.ROLES:
                choice = route.pick_engine(role, forced=engine, project={})
                self.assertEqual((choice["model"], choice["effort"]), (model, effort))
                project = {config.role_setting(role, engine, "model"): "scoped",
                           config.role_setting(role, engine, "effort"): "native"}
                choice = route.pick_engine(role, forced=engine, project=project)
                self.assertEqual((choice["model"], choice["effort"]), ("scoped", config.task_effort(engine, "native")))
                pinned = route.pick_engine(role, forced=engine, model="exact", effort="low", project=project)
                self.assertEqual((pinned["model"], pinned["effort"]), ("exact", "low"))
        S.write_json(config.ROOT / "settings.json", {**config.machine_settings(), "new_tasks": {
            "engine": "codex", "model": "explicit-new-task", "effort": "low"}})
        choice = route.pick_engine("l2", project={})
        self.assertEqual((choice["model"], choice["effort"]), ("explicit-new-task", "low"))

    def test_inherited_and_native_effort_have_distinct_role_behavior(self):
        for role, expected in (("l2", "high"), ("l3", "medium")):
            self.assertEqual(route.pick_engine(role, forced="codex")["effort"], expected)
        self.save("codex", effort="native")
        for role in config.ROLES:
            choice = route.pick_engine(role, forced="codex")
            self.assertEqual((choice["effort"], choice["requested_effort"]), ("medium", "native"))
        texts = {row["engine"]: row["native_effort"] for row in config.model_defaults_view()["engines"]}
        self.assertEqual(texts, {"claude": "Provider chooses effort", "codex": "Altitude sends Medium"})

    def test_review_uses_engine_pair_without_copying_owner_task_effort(self):
        inherited = route.pick_review({"l2_engine": "claude", "effort": "ultra"}, {"l2_codex_effort": "max"})
        self.assertEqual(inherited["effort"], "medium", "reviews use the chat role's effort, never the owner's")
        self.save("codex", "gpt-6.1-sol", "ultra")
        choice = route.pick_review({"l2_engine": "claude", "effort": "low"}, {})
        self.assertEqual((choice["engine"], choice["model"], choice["effort"]), ("codex", "gpt-6.1-sol", "ultra"))
        explicit = route.pick_review({"l2_engine": "claude"}, {}, engine="codex", model="exact")
        self.assertEqual((explicit["model"], explicit["effort"]), ("exact", "ultra"))
        self.save("codex", "gpt-6.1-sol", "native")
        self.assertEqual(route.pick_review({"l2_engine": "claude"}, {"l2_codex_effort": "max"})["effort"], "medium")

    def test_reset_clears_pair_atomically_keeps_engine_and_preserves_other_scopes(self):
        self.register(self.project, l3_choice={"engine": "codex", "model": "custom", "effort": "high"},
                      l2_codex_model="scoped", l2_codex_effort="native", l2_engine="codex",
                      routing=config.parse_routing("codex:tier-model"))
        _, view = self.call("GET")
        row = next(row for row in view["overrides"] if row["model"] == "custom")
        status, _ = self.call("POST", {"id": row["id"], "expected": None}, "/reset")
        self.assertEqual(status, 409)
        status, _ = self.call("POST", {"id": row["id"], "expected": row["expected"]}, "/reset")
        self.assertEqual(status, 200)
        self.assertEqual(config.project(self.project)["l3_choice"], {"engine": "codex"})
        row = next(row for row in view["overrides"] if row["model"] == "scoped")
        status, result = self.call("POST", {"id": row["id"], "expected": row["expected"]}, "/reset")
        self.assertEqual(status, 200)
        self.assertEqual([row["model"] for row in result["overrides"]], ["tier-model"])
        entry = config.project(self.project)
        self.assertNotIn("l2_codex_model", entry)
        self.assertNotIn("l2_codex_effort", entry)
        self.assertEqual(entry["l2_engine"], "codex")
        tier = result["overrides"][0]
        status, _ = self.call("POST", {"id": tier["id"], "expected": None}, "/reset")
        self.assertEqual(status, 400)

    def test_successful_pair_save_wakes_queued_coordinator(self):
        path = l3.queue_path(self.project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        wake = self.patch(server, "request_l3_drain")
        self.save("codex", "gpt-6.1-sol", "high")
        wake.assert_called_once_with(self.project)

    def test_coordinator_next_turn_uses_changed_pair_in_same_session(self):
        self.register(self.project, routing=config.parse_routing("codex"))
        calls = []
        def provider(prompt, **kwargs):
            calls.append(kwargs)
            return {"text": "answer", "session_id": "conversation", "reported_session_id": "conversation",
                    "error": None, "usage": {}, "returncode": 0}
        self.patch(engines, "codex_exec", side_effect=provider)
        self.save("codex", "gpt-6.1-sol", "high")
        self.assertTrue(l3.turn(self.project, "First turn")["completed"])
        self.save("codex", "other-model", "native")
        self.assertTrue(l3.turn(self.project, "Next turn")["completed"])
        self.assertEqual([(row["model"], row["effort"], row["resume"]) for row in calls],
                         [("gpt-6.1-sol", "high", None), ("other-model", "medium", "conversation")])

    def test_null_saved_session_model_is_not_reinterpreted_after_global_change(self):
        self.save("codex", "other-model", "high")
        hold = self.patch(route, "_hold", return_value=None)
        self.assertIsNone(dispatch.resume_engine_hold({"l2_engine": "codex", "launch_model": None}))
        self.assertEqual(hold.call_args.args[0]["model"], "gpt-6.1-sol")
