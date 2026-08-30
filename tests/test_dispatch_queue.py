"""Decision 51: a per-task hold (lease, ledger serialization) skips that task; the queue behind it still dispatches."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-queue-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, dispatch, engines  # noqa: E402
from altitude import server  # noqa: E402


class TestQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"q": {"name": "q", "path": str(_TMP / "repo"), "stacks": ["python"], "wip": 5}})
        engines.claude_agents = lambda: []

    def test_per_task_hold_does_not_block_the_queue(self):
        self.assertTrue(dispatch.per_task_hold("file lease: `x` is running on a.py"))
        self.assertTrue(dispatch.per_task_hold("one rule-application task at a time (they edit the same ledger)"))
        self.assertFalse(dispatch.per_task_hold("WIP limit: 5 running in q"))
        self.assertFalse(dispatch.per_task_hold(None))
        running = T.new("q", "busy", "S", "r", actor="l3", paths=["altitude/server.py"])
        running["state"] = "running"; S.save_task("q", running)
        leased = T.new("q", "leased", "S", "r", actor="l3", paths=["altitude/server.py"])
        free = T.new("q", "free", "S", "r", actor="l3", paths=["altitude/monitor.py"])
        for t in (leased, free):
            t["state"] = "approved"; S.save_task("q", t)
        started = []
        real = dispatch.run
        dispatch.run = lambda project, slug: started.append(slug) or {"dispatch_id": slug, "agent": None}
        try:
            server.dispatch_waiting("q")
        finally:
            dispatch.run = real
        self.assertEqual(started, [free["slug"]], "the leased task is skipped, the free one behind it dispatches")


if __name__ == "__main__":
    unittest.main()
