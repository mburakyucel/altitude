"""Decision 51: only ledger edits serialize; stopped sessions are not live; the queue is held by real capacity only."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-holds-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, dispatch, engines  # noqa: E402


class TestHolds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"h": {"name": "h", "path": str(_TMP / "repo"), "stacks": ["python"], "wip": 5}})
        cls._agents = engines.claude_agents
        engines.claude_agents = lambda: []

    @classmethod
    def tearDownClass(cls):
        engines.claude_agents = cls._agents

    def test_only_ledger_edits_serialize(self):
        a = T.new("h", "fix server", "S", "r", actor="l3", source="improve", paths=["altitude/server.py"])
        a["state"] = "running"; S.save_task("h", a)
        b = T.new("h", "fix monitor", "S", "r", actor="l3", source="improve", paths=["altitude/monitor.py"])
        self.assertIsNone(dispatch.wip_hold("h", b), "two improve tasks on different files run in parallel")
        c = T.new("h", "apply rule", "S", "r", actor="l3", source="improve", paths=["docs/RULES.md", "docs/incidents/I-020.md"])
        self.assertIsNone(dispatch.wip_hold("h", c), "a ledger edit is not held by a code fix")
        c["state"] = "running"; S.save_task("h", c)
        d = T.new("h", "apply another rule", "S", "r", actor="l3", source="improve", paths=["docs/RULES.md"])
        self.assertIn("one rule-application task at a time", dispatch.wip_hold("h", d))
        self.assertTrue(dispatch.rule_application({"slug": "apply-r-005-i-021", "paths": []}))

    def test_stopped_sessions_are_not_live(self):
        agents = [{"kind": "background", "state": "stopped"}] * config.SESSIONS_PER_MACHINE + [{"kind": "background", "state": "working"}]
        engines.claude_agents = lambda: agents
        try:
            t = T.new("h", "room", "S", "r", actor="l3", paths=["x/"])
            self.assertIsNone(dispatch.wip_hold("h", t))
        finally:
            engines.claude_agents = lambda: []


if __name__ == "__main__":
    unittest.main()
