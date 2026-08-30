"""Decision 39: parallel L2s without collisions — leases, session ceiling, guard hook."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-par-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from altitude import config, state as S, tasks as T, dispatch, engines  # noqa: E402


class TestLeases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": config.ROOT.as_posix(), "stacks": []}})
        engines.claude_agents = lambda: []  # no live sessions

    def test_overlap_semantics(self):
        self.assertEqual(dispatch.paths_overlap(["web/"], ["web/src/app.tsx"]), ["web/src/app.tsx"])
        self.assertEqual(dispatch.paths_overlap(["altitude/rules.py"], ["altitude/rules.py"]), ["altitude/rules.py"])
        self.assertEqual(dispatch.paths_overlap(["altitude/rules.py"], ["altitude/rules_extra.py"]), [])
        self.assertEqual(dispatch.paths_overlap(["./web"], ["web/"]), ["web"])

    def test_hold_on_overlapping_running_task(self):
        a = T.new("p", "web rebuild", "S", "r", actor="burak", paths=["web/", "altitude/server.py"])
        a["state"] = "running"; S.save_task("p", a)
        b = T.new("p", "server tweak", "S", "r", actor="burak", paths=["altitude/server.py"])
        c = T.new("p", "docs", "S", "r", actor="burak", paths=["docs/"])
        self.assertIn("file lease", dispatch.wip_hold("p", b))
        self.assertIn(a["slug"], dispatch.wip_hold("p", b))
        self.assertIsNone(dispatch.wip_hold("p", c))
        d = T.new("p", "undeclared", "S", "r", actor="burak")
        self.assertIsNone(dispatch.wip_hold("p", d), "no declared paths → no lease hold (surfaces at PR time)")

    def test_session_ceiling(self):
        many = [{"kind": "background", "state": "working"}] * config.SESSIONS_PER_MACHINE
        old = engines.claude_agents
        engines.claude_agents = lambda: many
        try:
            t = T.new("p", "ceiling", "S", "r", actor="burak", paths=["x/"])
            self.assertIn("session ceiling", dispatch.wip_hold("p", t))
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
