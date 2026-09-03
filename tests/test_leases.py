"""File leases, WIP holds and queue order in `dispatch`."""
import unittest

from tests.support import AltitudeCase
from altitude import dispatch, server, state as S, tasks as T


class TestLeases(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(self.project, wip=5)
        self.quiet_engines()

    def test_overlap_semantics(self):
        self.assertEqual(dispatch.paths_overlap(["web/"], ["web/src/app.tsx"]), ["web/src/app.tsx"])
        self.assertEqual(dispatch.paths_overlap(["altitude/rules.py"], ["altitude/rules.py"]), ["altitude/rules.py"])
        self.assertEqual(dispatch.paths_overlap(["altitude/rules.py"], ["altitude/rules_extra.py"]), [])
        self.assertEqual(dispatch.paths_overlap(["./web"], ["web/"]), ["web"])

    def test_expand_entry(self):
        cases = [
            ("altitude/dispatch.py (lease logic), tests/test_leases.py (new tests)",
             ["altitude/dispatch.py", "tests/test_leases.py"]),
            ("web/app.js (deleted), web/style.css", ["web/app.js", "web/style.css"]),
            ("web/app.js (removed, generated), web/style.css", ["web/app.js", "web/style.css"]),
            ("my dir/file.py", ["my dir/file.py"]),
            ("web/img (1).png", ["web/img (1).png"]),
            ("altitude/server.py and web/app.js", ["altitude/server.py and web/app.js"]),
            ("(all files under web/)", []),
            ("- altitude/dispatch.py", ["- altitude/dispatch.py"]),
            ("web/{a,b}/{c,d}", ["web/a/c", "web/a/d", "web/b/c", "web/b/d"]),
            ("web/{a,b/{c,d}}", ["web/a", "web/b/c", "web/b/d"]),
            ("web/src/{a.tsx,b.tsx", []),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(dispatch._expand_entry(entry), expected)

    def test_annotated_task_paths_overlap_bare_leases(self):
        entries = [
            "altitude/server.py (Handler._file/_static and do_GET static branches only; /api/* untouched)",
            "web/app.js, web/style.css (deleted in the same PR)",
            "web/src/shell/{AppShell.tsx,theme.tsx,theme.test.tsx} (new)",
            "altitude/server_x.py",
        ]
        candidate = T.new(self.project, "annotated request", "r", actor="burak", paths=entries)
        paths = dispatch.task_paths(self.project, candidate)
        expected = [
            "altitude/server.py", "web/app.js", "web/style.css", "web/src/shell/AppShell.tsx",
            "web/src/shell/theme.tsx", "web/src/shell/theme.test.tsx", "altitude/server_x.py",
        ]
        self.assertEqual(paths, expected)
        for bare in ("altitude/server.py", "web/app.js", "web/src/shell/theme.tsx"):
            holder = T.new(self.project, f"holder {bare}", "r", actor="burak", paths=[bare])
            holder["state"] = "running"; S.save_task(self.project, holder)
            try:
                self.assertEqual(dispatch.paths_overlap(paths, [bare]), [bare])
                if bare == "altitude/server.py":
                    hold = dispatch.wip_hold(self.project, candidate)
                    self.assertIsNotNone(hold)
                    self.assertTrue(hold.startswith("file lease:"), hold)
                    self.assertIn("altitude/server.py", hold)
            finally:
                holder["state"] = "done"; S.save_task(self.project, holder)
        self.assertEqual(dispatch.paths_overlap(["altitude/server_x.py"], ["altitude/server.py"]), [])

    def test_declared_paths_survive_task_paths_and_hold(self):
        declared = ["my dir/file.py", "web/img (1).png"]
        candidate = T.new(self.project, "literal declared paths", "r", actor="burak", paths=declared)
        self.assertEqual(dispatch.task_paths(self.project, candidate), declared)
        holder = T.new(self.project, "literal path holder", "r", actor="burak", paths=[declared[1]])
        holder["state"] = "running"; S.save_task(self.project, holder)
        hold = dispatch.wip_hold(self.project, candidate)
        self.assertIsNotNone(hold)
        self.assertTrue(hold.startswith("file lease:"), hold)
        self.assertIn(declared[1], hold)

    def test_hold_on_overlapping_running_task(self):
        a = T.new(self.project, "web rebuild", "r", actor="burak", paths=["web/", "altitude/server.py"])
        a["state"] = "running"; S.save_task(self.project, a)
        b = T.new(self.project, "server tweak", "r", actor="burak", paths=["altitude/server.py"])
        c = T.new(self.project, "docs", "r", actor="burak", paths=["docs/"])
        self.assertIn("file lease", dispatch.wip_hold(self.project, b))
        self.assertIn(a["slug"], dispatch.wip_hold(self.project, b))
        self.assertIsNone(dispatch.wip_hold(self.project, c))
        d = T.new(self.project, "undeclared", "r", actor="burak")
        self.assertIsNone(dispatch.wip_hold(self.project, d), "no declared paths → no lease hold (surfaces at PR time)")

    def test_unrelated_tasks_do_not_serialize(self):
        a = T.new(self.project, "fix server", "r", actor="l3", paths=["altitude/server.py"])
        a["state"] = "running"; S.save_task(self.project, a)
        b = T.new(self.project, "fix monitor", "r", actor="l3", paths=["altitude/monitor.py"])
        self.assertIsNone(dispatch.wip_hold(self.project, b), "ordinary tasks on different files may run in parallel")

    def test_claude_session_count_does_not_globally_block_codex_dispatch(self):
        self.quiet_engines([{"kind": "background", "state": "working"}] * 24)
        t = T.new(self.project, "ceiling", "r", actor="burak", paths=["x/"])
        self.assertIsNone(dispatch.wip_hold(self.project, t))

    def test_top_level_directory_claims_do_not_lease(self):
        self.assertEqual(dispatch.narrow(["tests/", "tests/test_x.py", "docs", "web/dist/assets/", "altitude/server.py"]),
                         ["tests/test_x.py", "web/dist/assets/", "altitude/server.py"])
        running = T.new(self.project, "broad", "r", actor="l3", paths=["tests/", "altitude/lane.py"])
        running["state"] = "running"; S.save_task(self.project, running)
        t = T.new(self.project, "narrow", "r", actor="l3", paths=["tests/test_other.py"])
        t["state"] = "queued"; S.save_task(self.project, t)
        self.assertIsNone(dispatch.wip_hold(self.project, t), "a bare tests/ claim must not hold a task touching one test file")
        t2 = T.new(self.project, "same-file", "r", actor="l3", paths=["altitude/lane.py"])
        self.assertIn("file lease", dispatch.wip_hold(self.project, t2) or "")

    def test_per_task_hold_does_not_block_the_queue(self):
        self.assertTrue(dispatch.per_task_hold("file lease: `x` is running on a.py"))
        self.assertFalse(dispatch.per_task_hold("WIP limit: 5 running in q"))
        self.assertFalse(dispatch.per_task_hold(None))
        running = T.new(self.project, "busy", "r", actor="l3", paths=["altitude/server.py"])
        running["state"] = "running"; S.save_task(self.project, running)
        leased = T.new(self.project, "leased", "r", actor="l3", paths=["altitude/server.py"])
        free = T.new(self.project, "free", "r", actor="l3", paths=["altitude/monitor.py"])
        for t in (leased, free):
            t["state"] = "queued"; S.save_task(self.project, t)
        started = []
        self.patch(dispatch, "run",
                   side_effect=lambda project, slug: started.append(slug) or {"attempt": 1, "agent": None})

        server.dispatch_waiting(self.project)

        self.assertEqual(started, [free["slug"]], "the leased task is skipped, the free one behind it dispatches")


if __name__ == "__main__":
    unittest.main()
