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
from tests.test_monitor_view import write_quota
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
        write_quota(at, seven=52)
        (config.MONITOR_DIR / route.QUOTA_CODEX).write_text(json.dumps({
            "known": True, "primary_used": 71.0, "primary_window_minutes": route.WEEK_MINUTES,
            "primary_resets": "2026-09-07T02:27:31+00:00", "read_at": S.now()}))
        first, second = self._engines()
        self.assertEqual((first["week"], first["known"], first["stale"]), (52, True, False))
        self.assertTrue(first["at"].startswith(time.strftime("%Y-%m-%d", time.gmtime(at))))
        self.assertEqual((second["week"], second["known"], second["stale"]), (71.0, True, False))

    def test_an_aged_reading_keeps_its_figure_and_says_stale(self):
        at = int(time.time()) - route.FRESH_SECONDS - 60
        write_quota(at, seven=52)
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


class TestChanges(AltitudeCase):
    """The web shell's change stream: task, decision and hold records written by any process name their project."""

    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(server, "log", new=lambda *a, **k: None)
        self.patch(server, "CHANGE_SECONDS", new=0.05)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = False  # server_close joins the stream: a closed client must end it
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def changed(self, before):
        after = server.change_marks()
        return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))

    def test_task_decision_hold_and_archive_writes_name_their_project(self):
        other = "other-project"
        self.register(other)
        before = server.change_marks()
        self.assertEqual(self.changed(before), [])
        slug = T.new(self.project, "Keep pagination", "Fictional request.")["slug"]
        self.assertEqual(self.changed(before), [self.project])
        before = server.change_marks()
        T.block(self.project, slug, "Which page size?", actor="l2", questions={"questions": [{"question": "Which page size?"}]})
        self.assertEqual(self.changed(before), [self.project])
        before = server.change_marks()
        S.write_json(config.project_dir(other) / "hold.json", {"at": S.now(), "reason": "fixture"})
        self.assertEqual(self.changed(before), [other])
        before = server.change_marks()
        T.reject(self.project, slug, "Fixture no longer needed.")
        self.assertEqual(self.changed(before), [self.project])
        before = server.change_marks()
        self.register("third-project")
        self.assertEqual(self.changed(before), ["", "third-project"])

    def test_stream_reports_changes_after_its_baseline_and_ends_when_the_client_leaves(self):
        host, port = self.httpd.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/changes")
        res = conn.getresponse()
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/event-stream; charset=utf-8"))
        self.assertEqual(res.fp.readline(), f"retry: {server.CHANGE_RETRY_MS}\n".encode())
        self.assertEqual(res.fp.readline(), b"\n")
        T.new(self.project, "Keep pagination", "Fictional request.")
        self.assertEqual(res.fp.readline(), b"event: change\n")
        self.assertEqual(json.loads(res.fp.readline().removeprefix(b"data: ")), {"projects": [self.project]})
        self.assertEqual(res.fp.readline(), b"\n")
        self.register("other-project")
        self.assertEqual(res.fp.readline(), b"event: change\n")
        self.assertEqual(json.loads(res.fp.readline().removeprefix(b"data: ")), {"projects": ["other-project", self.project]})
        self.assertEqual(res.fp.readline(), b"\n")
        conn.close()
        res.close()
        self.httpd.shutdown()
        closing = threading.Thread(target=self.httpd.server_close)  # joins every request thread
        closing.start()
        closing.join(3)
        self.assertFalse(closing.is_alive())

    def test_head_answers_without_streaming(self):
        host, port = self.httpd.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("HEAD", "/api/changes")
        res = conn.getresponse()
        self.assertEqual((res.status, res.read()), (200, b""))
        conn.close()


if __name__ == "__main__":
    unittest.main()
