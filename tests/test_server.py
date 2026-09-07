"""What the shell reads from `/api/overview`: engine rows that name no provider outside the seam, the folders
First run scans, the operator's configured name, and the fault count behind the rail's danger dot; and the
one endpoint the project header uses to start an L3 that never ran (SPEC.md §3.1, §3.2, §3.12)."""
import http.client
import json
import subprocess
import threading
import time
import unittest
from pathlib import Path

from tests.support import AltitudeCase
from tests.test_monitor_view import write_snapshot
from altitude import config, route, server, state as S, tasks as T


class TestRepository(AltitudeCase):
    def test_origin_to_web_url(self):
        for origin in ("https://github.com/example/project", "https://github.com/example/project.git",
                       "git@github.com:example/project.git", "ssh://git@github.com/example/project.git",
                       "https://github.com/example/project.git/\n"):
            with self.subTest(origin=origin):
                self.assertEqual(server.repository_url(origin), "https://github.com/example/project")
        for origin in ("", "/local/repo", "git@gitlab.com:example/project.git",
                       "https://github.com.example.org/example/project", "https://github.com/example/project/pull/1"):
            with self.subTest(origin=origin):
                self.assertIsNone(server.repository_url(origin))

    def test_project_view_reads_its_checkout_origin(self):
        def git(*args):
            subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

        git("init")
        self.assertIsNone(server.project_view(self.project)["repository"])
        git("remote", "add", "origin", "git@github.com:example/project.git")
        self.assertEqual(server.project_view(self.project)["repository"], "https://github.com/example/project")
        git("remote", "set-url", "origin", "https://gitlab.com/example/project.git")
        self.assertIsNone(server.project_view(self.project)["repository"])


class TestOverview(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(server, "restart_status", return_value=None)

    def _engines(self):
        rows = server.overview()["engines"]
        self.assertEqual([r["engine"] for r in rows], list(config.ENGINES))
        self.assertEqual([r["label"] for r in rows], [config.ENGINE_LABELS[e] for e in config.ENGINES])
        return rows

    def test_no_reading_at_all_is_a_row_without_a_figure(self):
        for row in self._engines():
            self.assertEqual((row["week"], row["known"], row["stale"], row["at"]), (None, False, False, None))

    def test_fresh_readings_carry_the_weekly_figure_and_their_time(self):
        at = int(time.time())
        write_snapshot(config.MONITOR_DIR / "statusline-s1.json", at, seven=52)
        (config.MONITOR_DIR / route.QUOTA_CODEX).write_text(json.dumps({
            "known": True, "primary_used": 71.0, "primary_window_minutes": route.WEEK_MINUTES,
            "primary_resets": "2026-09-07T02:27:31+00:00", "read_at": S.now()}))
        first, second = self._engines()
        self.assertEqual((first["week"], first["known"], first["stale"]), (52, True, False))
        self.assertTrue(first["at"].startswith(time.strftime("%Y-%m-%d", time.gmtime(at))))
        self.assertEqual((second["week"], second["known"], second["stale"]), (71.0, True, False))

    def test_an_aged_reading_keeps_its_figure_and_says_stale(self):
        at = int(time.time()) - route.FRESH_SECONDS - 60
        write_snapshot(config.MONITOR_DIR / "statusline-s1.json", at, seven=52)
        (config.MONITOR_DIR / route.QUOTA_CODEX).write_text(json.dumps({
            "known": True, "primary_used": 71.0, "primary_window_minutes": route.WEEK_MINUTES,
            "primary_resets": "2026-09-07T02:27:31+00:00", "read_at": "2026-09-03T00:00:00+00:00"}))
        for row, week in zip(self._engines(), (52, 71.0)):
            self.assertEqual((row["week"], row["known"], row["stale"]), (week, False, True))

    def test_roots_are_named_relative_to_home_and_the_operator_is_configured(self):
        self.patch(config, "PROJECT_ROOTS", [Path.home() / "Projects", Path("/srv/work")])
        self.patch(config, "OPERATOR", "Ada")
        view = server.overview()
        self.assertEqual(view["roots"], ["~/Projects", "/srv/work"])
        self.assertEqual(view["operator"], "Ada")

    def test_a_project_counts_its_faulted_tasks(self):
        task = T.new(self.project, "fault probe", "request", actor="burak")
        task.update({"state": "blocked", "fault": "sandbox refused"}); S.save_task(self.project, task)
        T.new(self.project, "plain", "request", actor="burak")
        counts = next(p for p in server.overview()["projects"] if p["name"] == self.project)["counts"]
        self.assertEqual((counts["fault"], counts["blocked"], counts["queued"]), (1, 1, 1))


class TestStartL3(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.patch(server, "log", new=lambda *a, **k: None)
        self.spawned = self.patch(server, "spawn", return_value=True)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def _post(self, path, body):
        host, port = self.httpd.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"null")

    def test_start_runs_the_start_turn_for_a_managed_project_only(self):
        status, out = self._post("/api/l3/start", {"project": self.project})
        self.assertEqual((status, out), (200, {"ok": True, "started": True}))
        self.spawned.assert_called_once_with(f"start:{self.project}", server.start_l3, self.project)
        status, out = self._post("/api/l3/start", {"project": "nowhere"})
        self.assertEqual(status, 500)
        self.assertIn("unknown project", out["error"])
        self.spawned.assert_called_once()


if __name__ == "__main__":
    unittest.main()
