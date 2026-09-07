"""What the Monitor and task pages read: every figure carries its age, and stale is not unknown."""
import json
import socket
import sys
import threading
import time
import unittest

from tests.support import AltitudeCase
from altitude import config, engines, monitor, route, server, state as S, tasks as T


def write_snapshot(path, at, five=10, seven=20):
    """One statusline wrapper snapshot: the wrapper stamps `_at`, the payload has no time of its own."""
    path.write_text(json.dumps({
        "session_id": "s1",
        "cwd": "/repo",
        "model": {"display_name": "Opus 5"},
        "context_window": {"used_percentage": 8},
        "rate_limits": {"five_hour": {"used_percentage": five, "resets_at": at + 3600},
                        "seven_day": {"used_percentage": seven, "resets_at": at + 86_400}},
        "_at": at,
    }))


class TestQuotaAge(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()

    def test_no_snapshot_at_all_is_unknown(self):
        self.assertEqual(monitor.quota(), {"known": False})

    def test_a_fresh_snapshot_reports_its_reset_times_and_when_it_was_taken(self):
        at = int(time.time())
        write_snapshot(config.MONITOR_DIR / "statusline-s1.json", at)
        quota = monitor.quota()
        self.assertTrue(quota["known"])
        self.assertNotIn("stale", quota)
        self.assertEqual(quota["at"], at)
        self.assertEqual((quota["five_hour_resets"], quota["seven_day_resets"]), (at + 3600, at + 86_400))

    def test_an_aged_snapshot_keeps_its_figures_and_says_stale(self):
        at = int(time.time()) - route.FRESH_SECONDS - 60
        write_snapshot(config.MONITOR_DIR / "statusline-s1.json", at, five=41, seven=52)
        quota = monitor.quota()
        self.assertFalse(quota["known"])  # the router still refuses to route on it
        self.assertTrue(quota["stale"])
        self.assertEqual((quota["five_hour"], quota["seven_day"], quota["at"]), (41, 52, at))

    def test_a_session_row_carries_the_time_it_was_observed(self):
        at = int(time.time())
        write_snapshot(config.MONITOR_DIR / "statusline-s1.json", at)
        row = next(s for s in monitor.sessions() if s["kind"] == "statusline")
        self.assertEqual(row["at"], at)

    def test_model_observation_reaches_live_session_rows_and_old_tasks_are_readable(self):
        task = T.new(self.project, "Known model", "request")
        task.update(state="running", l2_engine="codex", engine_model="actual-model", engine_reasoning_effort="high")
        S.save_task(self.project, task)
        S.write_json(config.project_dir(self.project) / "l3.json", {
            "engine_last": "codex", "engine_model": "l3-model", "engine_reasoning_effort": "medium"})
        rows = {row["kind"]: row for row in monitor.sessions()}
        self.assertEqual(rows["l2"]["model"], "actual-model")
        self.assertEqual(rows["l3"]["model"], "l3-model")
        self.assertEqual(rows["l3"]["engine_reasoning_effort"], "medium")
        del task["engine_model"]
        S.save_task(self.project, task)
        S.write_json(config.project_dir(self.project) / "l3.json", {"engine_last": "codex", "engine_model": None})
        for row in monitor.sessions():
            self.assertNotIn("model", row, "unknown model is absent for consumers of older session records")

    def test_an_aged_codex_reading_keeps_its_figures_and_says_stale(self):
        reading = {"known": True, "primary_used": 71.0, "primary_window_minutes": route.WEEK_MINUTES,
                   "primary_resets": "2026-09-07T02:27:31+00:00", "plan_type": "pro",
                   "read_at": "2026-09-03T00:00:00+00:00"}
        (config.MONITOR_DIR / route.QUOTA_CODEX).write_text(json.dumps(reading))
        quota = route.quota_codex()
        self.assertFalse(quota["known"])
        self.assertTrue(quota["stale"])
        self.assertEqual(quota["primary_used"], 71.0)
        self.assertEqual(quota["primary_resets"], "2026-09-07T02:27:31+00:00")


class TestRoutingView(AltitudeCase):
    """The routing card answers "which engine would a turn get now", using the router itself."""

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.claude, self.codex = {"known": False}, {"known": False}
        self.patch(monitor, "quota", side_effect=lambda: self.claude)
        self.patch(route, "quota_codex", side_effect=lambda: self.codex)
        self.patch(engines, "usage_hold", return_value=None)

    def rows(self):
        return {(r["role"], r["project"]): r for r in monitor.routing()}

    def test_the_l2_row_is_the_engine_a_fresh_task_would_get(self):
        self.claude = {"known": True, "five_hour": 1, "seven_day": 10}
        self.codex = {"known": True, "primary_used": 80, "primary_window_minutes": route.WEEK_MINUTES}
        row = self.rows()[("l2", None)]
        self.assertEqual(row["engine"], "claude")
        self.assertIn("more weekly headroom", row["why"])

    def test_a_project_pin_shows_as_the_pin_and_an_exhausted_pin_returns_no_engine(self):
        self.register(self.project, l3_engine="codex")
        self.claude = {"known": True, "five_hour": 1, "seven_day": 10}
        self.codex = {"known": True, "primary_used": 100, "primary_window_minutes": route.WEEK_MINUTES}
        row = self.rows()[("l3", self.project)]
        self.assertEqual(row["pin"], "codex")
        self.assertIsNone(row["engine"])
        self.assertEqual(row["why"], "forced codex is unavailable: weekly window exhausted")

    def test_no_engine_available_says_so_for_every_role(self):
        self.claude = {"known": True, "five_hour": 100, "seven_day": 100}
        self.codex = {"known": True, "primary_used": 100, "primary_window_minutes": route.WEEK_MINUTES}
        row = self.rows()[("l2", None)]
        self.assertIsNone(row["engine"])
        self.assertTrue(row["why"].startswith("no engine available ("))

    def test_an_auto_l3_stays_on_the_engine_that_ran_its_last_turn(self):
        project_dir = config.project_dir(self.project)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "l3.json").write_text(json.dumps({"engine_last": "codex"}))
        self.claude = {"known": True, "five_hour": 1, "seven_day": 20}
        self.codex = {"known": True, "primary_used": 25, "primary_window_minutes": route.WEEK_MINUTES}
        row = self.rows()[("l3", self.project)]
        self.assertIsNone(row["pin"])
        self.assertEqual(row["current"], "codex")
        self.assertEqual(row["engine"], "codex")
        self.assertIn("staying on codex", row["why"])


class TestMonitorApi(AltitudeCase):
    """/api/monitor carries both seats and the routing view under their own keys."""

    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(server, "log", new=lambda *_: None)
        self.patch(monitor, "quota", return_value={"known": False})
        self.patch(route, "quota_codex", return_value={"known": False, "why": "Codex binary not found"})
        self.patch(monitor, "routing", return_value=[{"role": "l2", "engine": None, "why": "no engine available (x)"}])
        self.private_ledgers()
        self.patch(engines, "claude_agents", return_value=[])
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def get(self, path):
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            raw = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                raw += chunk
        return json.loads(raw.split(b"\r\n\r\n", 1)[1])

    def test_the_payload_names_both_seats_and_the_routing_view(self):
        body = self.get("/api/monitor")
        self.assertEqual(set(body), {"quota", "quota_codex", "routing", "sessions", "agents"})
        self.assertEqual(body["quota_codex"]["why"], "Codex binary not found")
        self.assertIsNone(body["routing"][0]["engine"])

    def test_task_and_monitor_apis_expose_model_under_their_public_field_names(self):
        task = T.new(self.project, "Model API", "request")
        task.update(state="running", l2_engine="codex", engine_model="actual-model", engine_reasoning_effort="high")
        S.save_task(self.project, task)
        live = next(row for row in self.get("/api/monitor")["sessions"] if row["kind"] == "l2")
        self.assertEqual((live["engine"], live["model"], live["engine_reasoning_effort"]),
                         ("codex", "actual-model", "high"))
        detail = self.get(f"/api/task/{self.project}/{task['slug']}")
        self.assertEqual(detail["engine_model"], "actual-model")
        self.assertEqual(detail["engine_reasoning_effort"], "high")
        task["engine_model"] = None
        S.save_task(self.project, task)
        self.assertNotIn("model", next(row for row in self.get("/api/monitor")["sessions"] if row["kind"] == "l2"))


if __name__ == "__main__":
    unittest.main()
