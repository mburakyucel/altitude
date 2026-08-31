"""Concrete requests queue one L2 directly; no classifier or proposal stages exist."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-direct-dispatch-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from altitude import config, l1, server, state as S, tasks as T  # noqa: E402


class TestDirectDispatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"direct": {"name": "direct", "path": config.ROOT.as_posix(), "stacks": []}})

    def test_new_task_is_immediately_queued_without_pipeline_metadata(self):
        task = T.new("direct", "Fix the focused bug", "Fix it and test it.", actor="burak")

        self.assertEqual(task["state"], "approved")
        for removed in ("class", "envelope", "proposal_attempts", "decision", "estimate"):
            self.assertNotIn(removed, task)
        self.assertEqual(T.decisions("direct"), [])
        self.assertEqual(l1.list_runs("direct", task["slug"]), [], "zero-L1 execution is valid")

    def test_server_has_no_classifier_proposal_or_critic_entrypoint(self):
        self.assertFalse(hasattr(server, "size_task"))
        self.assertFalse(hasattr(server, "run_proposal_flow"))
        for removed in ("intake.py", "propose.py"):
            self.assertFalse((config.REPO / "altitude" / removed).exists())
        for removed in ("size.md", "proposal.md", "critic.md"):
            self.assertFalse((config.PERSONAS / removed).exists())

    def test_waiting_task_dispatches_directly_to_l2(self):
        queued = {"slug": "direct-one", "state": "approved"}
        result = {"dispatch_id": "direct-one-1", "agent": {"id": "l2-agent"}}
        with mock.patch.object(S, "list_tasks", return_value=[queued]), \
             mock.patch.object(server.dispatch, "wip_hold", return_value=None), \
             mock.patch.object(server.dispatch, "run", return_value=result) as run:
            server.dispatch_waiting("direct")
        run.assert_called_once_with("direct", "direct-one")


if __name__ == "__main__":
    unittest.main()
