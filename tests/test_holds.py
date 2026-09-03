"""Stopped sessions are not live; the queue is held by real capacity and file leases only."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-holds-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, dispatch, engines, monitor  # noqa: E402


class TestHolds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"h": {"name": "h", "path": str(_TMP / "repo"), "wip": 5}})
        cls._agents = engines.claude_agents
        engines.claude_agents = lambda: []

    @classmethod
    def tearDownClass(cls):
        engines.claude_agents = cls._agents

    def setUp(self):
        self._quota = monitor.quota
        monitor.quota = lambda: {"known": True}

    def tearDown(self):
        monitor.quota = self._quota

    def test_unrelated_tasks_do_not_serialize(self):
        a = T.new("h", "fix server", "r", actor="l3", paths=["altitude/server.py"])
        a["state"] = "running"; S.save_task("h", a)
        b = T.new("h", "fix monitor", "r", actor="l3", paths=["altitude/monitor.py"])
        self.assertIsNone(dispatch.wip_hold("h", b), "ordinary tasks on different files may run in parallel")

    def test_stopped_sessions_are_not_live(self):
        agents = [{"kind": "background", "state": "stopped"}] * 24 + [{"kind": "background", "state": "working"}]
        engines.claude_agents = lambda: agents
        try:
            t = T.new("h", "room", "r", actor="l3", paths=["x/"])
            self.assertIsNone(dispatch.wip_hold("h", t))
        finally:
            engines.claude_agents = lambda: []


if __name__ == "__main__":
    unittest.main()
