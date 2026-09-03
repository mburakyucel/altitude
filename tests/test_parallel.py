"""File leases and capacity limits let independent L2 tasks run without collisions."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-par-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from altitude import config, state as S, tasks as T, dispatch, engines, monitor  # noqa: E402


class TestLeases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": config.ROOT.as_posix()}})
        engines.claude_agents = lambda: []  # no live sessions

    def setUp(self):
        self._quota = monitor.quota
        monitor.quota = lambda: {"known": True}

    def tearDown(self):
        monitor.quota = self._quota

    def test_overlap_semantics(self):
        self.assertEqual(dispatch.paths_overlap(["web/"], ["web/src/app.tsx"]), ["web/src/app.tsx"])
        self.assertEqual(dispatch.paths_overlap(["altitude/rules.py"], ["altitude/rules.py"]), ["altitude/rules.py"])
        self.assertEqual(dispatch.paths_overlap(["altitude/rules.py"], ["altitude/rules_extra.py"]), [])
        self.assertEqual(dispatch.paths_overlap(["./web"], ["web/"]), ["web"])

    def test_annotated_task_paths_overlap_bare_leases(self):
        entries = [
            "altitude/server.py (Handler._file/_static and do_GET static branches only; /api/* untouched)",
            "web/app.js, web/style.css (deleted in the same PR)",
            "web/src/shell/{AppShell.tsx,theme.tsx,theme.test.tsx} (new)",
            "altitude/server_x.py",
        ]
        candidate = T.new("p", "annotated request", "r", actor="burak", paths=entries)
        paths = dispatch.task_paths("p", candidate)
        expected = [
            "altitude/server.py", "web/app.js", "web/style.css", "web/src/shell/AppShell.tsx",
            "web/src/shell/theme.tsx", "web/src/shell/theme.test.tsx", "altitude/server_x.py",
        ]
        self.assertEqual(paths, expected)
        for bare in ("altitude/server.py", "web/app.js", "web/src/shell/theme.tsx"):
            holder = T.new("p", f"holder {bare}", "r", actor="burak", paths=[bare])
            holder["state"] = "running"; S.save_task("p", holder)
            try:
                self.assertEqual(dispatch.paths_overlap(paths, [bare]), [bare])
                if bare == "altitude/server.py":
                    hold = dispatch.wip_hold("p", candidate)
                    self.assertIsNotNone(hold)
                    self.assertTrue(hold.startswith("file lease:"), hold)
                    self.assertIn("altitude/server.py", hold)
            finally:
                holder["state"] = "done"; S.save_task("p", holder)
        self.assertEqual(dispatch.paths_overlap(["altitude/server_x.py"], ["altitude/server.py"]), [])

    def test_expand_entry(self):
        cases = [
            ("altitude/dispatch.py (lease logic), tests/test_parallel.py (new tests)",
             ["altitude/dispatch.py", "tests/test_parallel.py"]),
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

    def test_declared_paths_survive_task_paths_and_hold(self):
        declared = ["my dir/file.py", "web/img (1).png"]
        candidate = T.new("p", "literal declared paths", "r", actor="burak", paths=declared)
        self.assertEqual(dispatch.task_paths("p", candidate), declared)
        holder = T.new("p", "literal path holder", "r", actor="burak", paths=[declared[1]])
        holder["state"] = "running"; S.save_task("p", holder)
        try:
            hold = dispatch.wip_hold("p", candidate)
            self.assertIsNotNone(hold)
            self.assertTrue(hold.startswith("file lease:"), hold)
            self.assertIn(declared[1], hold)
        finally:
            holder["state"] = "done"; S.save_task("p", holder)

    def test_hold_on_overlapping_running_task(self):
        a = T.new("p", "web rebuild", "r", actor="burak", paths=["web/", "altitude/server.py"])
        a["state"] = "running"; S.save_task("p", a)
        b = T.new("p", "server tweak", "r", actor="burak", paths=["altitude/server.py"])
        c = T.new("p", "docs", "r", actor="burak", paths=["docs/"])
        self.assertIn("file lease", dispatch.wip_hold("p", b))
        self.assertIn(a["slug"], dispatch.wip_hold("p", b))
        self.assertIsNone(dispatch.wip_hold("p", c))
        d = T.new("p", "undeclared", "r", actor="burak")
        self.assertIsNone(dispatch.wip_hold("p", d), "no declared paths → no lease hold (surfaces at PR time)")

    def test_claude_session_count_does_not_globally_block_codex_dispatch(self):
        many = [{"kind": "background", "state": "working"}] * 24
        old = engines.claude_agents
        engines.claude_agents = lambda: many
        try:
            t = T.new("p", "ceiling", "r", actor="burak", paths=["x/"])
            self.assertIsNone(dispatch.wip_hold("p", t))
        finally:
            engines.claude_agents = old


class TestGuardHook(unittest.TestCase):
    def run_guard(self, cmd):
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "guard.py")], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                           capture_output=True, text=True)
        return p.returncode

    def test_blocks_shared_service_actions(self):
        for cmd in ("systemctl --user restart altitude", "sudo ufw allow 8890", "ALTITUDE_PORT=8890 bin/alt serve", "python3 -m http.server --port 8080",
                    "rm -rf ~/.altitude", "git push --force origin main", "git push -f"):
            self.assertEqual(self.run_guard(cmd), 2, cmd)

    def test_allows_ordinary_work(self):
        for cmd in ("make test", "git push -u origin HEAD", "ALTITUDE_TIMERS=0 ALTITUDE_PORT=8931 bin/alt serve", "curl -s http://127.0.0.1:8931/",
                    "systemctl --user status altitude", "pnpm build"):
            self.assertEqual(self.run_guard(cmd), 0, cmd)


if __name__ == "__main__":
    unittest.main()
