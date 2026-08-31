"""A per-task file-lease hold skips that task; the queue behind it still dispatches."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-queue-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, dispatch, engines, monitor, recovery  # noqa: E402
from altitude import server  # noqa: E402


class TestQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"q": {"name": "q", "path": str(_TMP / "repo"), "stacks": ["python"], "wip": 5}})

    def setUp(self):
        recovery.hold_path().unlink(missing_ok=True)
        self._quota, self._quota_hold = monitor.quota, monitor.quota_hold
        monitor.quota = lambda: {"known": True}
        monitor.quota_hold = lambda: None

    def tearDown(self):
        monitor.quota, monitor.quota_hold = self._quota, self._quota_hold
        recovery.hold_path().unlink(missing_ok=True)

    def test_top_level_directory_claims_do_not_lease(self):
        self.assertEqual(dispatch.narrow(["tests/", "tests/test_x.py", "docs", "web/dist/assets/", "altitude/server.py"]),
                         ["tests/test_x.py", "web/dist/assets/", "altitude/server.py"])
        running = T.new("q", "broad", "r", actor="l3", paths=["tests/", "altitude/lane.py"])
        running["state"] = "running"; S.save_task("q", running)
        t = T.new("q", "narrow", "r", actor="l3", paths=["tests/test_other.py"])
        t["state"] = "approved"; S.save_task("q", t)
        real = engines.claude_agents; engines.claude_agents = lambda: []
        try:
            self.assertIsNone(dispatch.wip_hold("q", t), "a bare tests/ claim must not hold a task touching one test file")
            t2 = T.new("q", "same-file", "r", actor="l3", paths=["altitude/lane.py"]); S.save_task("q", t2)
            self.assertIn("file lease", dispatch.wip_hold("q", t2) or "")
        finally:
            engines.claude_agents = real
        for x in (running, t, t2):
            x["state"] = "rejected"; S.save_task("q", x)

    def test_per_task_hold_does_not_block_the_queue(self):
        self.assertTrue(dispatch.per_task_hold("file lease: `x` is running on a.py"))
        self.assertTrue(dispatch.per_task_hold("recovery hold: system fault"))
        self.assertFalse(dispatch.per_task_hold("WIP limit: 5 running in q"))
        self.assertFalse(dispatch.per_task_hold(None))
        running = T.new("q", "busy", "r", actor="l3", paths=["altitude/server.py"])
        running["state"] = "running"; S.save_task("q", running)
        leased = T.new("q", "leased", "r", actor="l3", paths=["altitude/server.py"])
        free = T.new("q", "free", "r", actor="l3", paths=["altitude/monitor.py"])
        for t in (leased, free):
            t["state"] = "approved"; S.save_task("q", t)
        started = []
        real_run, real_agents = dispatch.run, engines.claude_agents
        dispatch.run = lambda project, slug: started.append(slug) or {"dispatch_id": slug, "agent": None}
        engines.claude_agents = lambda: []
        try:
            server.dispatch_waiting("q")
        finally:
            dispatch.run, engines.claude_agents = real_run, real_agents
        self.assertEqual(started, [free["slug"]], "the leased task is skipped, the free one behind it dispatches")


if __name__ == "__main__":
    unittest.main()
