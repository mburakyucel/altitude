"""A `claude --bg` worker that died (`claude agents` state=failed) is a finished-with-fault L2, never "still running"."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-liveness-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch, server, tasks as T  # noqa: E402


class TestDeadWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP}})

    def _poll(self, rows, tasks):
        orig_agents, orig_list = engines.claude_agents, S.list_tasks
        engines.claude_agents = lambda: rows
        S.list_tasks = lambda project: tasks
        try:
            return dispatch.poll("altitude")
        finally:
            engines.claude_agents, S.list_tasks = orig_agents, orig_list

    def test_failed_worker_without_report_is_died(self):
        task = {"slug": "dead-one", "state": "running", "session_id": "sid-1", "agent_id": "a1"}
        out = self._poll([{"id": "a1", "sessionId": "sid-1", "state": "failed"}], [task])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].get("died"))

    def test_failed_worker_with_report_is_a_normal_finish(self):
        d = S.task_dir("altitude", "reported-then-died"); d.mkdir(parents=True, exist_ok=True)
        (d / "report.json").write_text(json.dumps({"landed": {}}))
        task = {"slug": "reported-then-died", "state": "running", "session_id": "sid-2", "agent_id": "a2"}
        out = self._poll([{"id": "a2", "sessionId": "sid-2", "state": "failed"}], [task])
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].get("died"))

    def test_working_worker_is_not_finished(self):
        task = {"slug": "alive", "state": "running", "session_id": "sid-3", "agent_id": "a3"}
        out = self._poll([{"id": "a3", "sessionId": "sid-3", "state": "working", "status": "busy", "pid": 1}], [task])
        self.assertEqual(out, [])


class TestResumeRebinds(unittest.TestCase):
    def test_finished_snapshot_cannot_block_a_replacement_worker(self):
        slug = "stale-finished-worker"
        S.task_dir("altitude", slug).mkdir(parents=True, exist_ok=True)
        old = {"slug": slug, "title": slug, "created": S.now(), "state": "running",
               "dispatch_id": f"{slug}-1", "session_id": "session-a", "agent_id": "agent-a",
               "l2_engine": "claude"}
        S.save_task("altitude", old)
        replacement = {**old, "dispatch_id": f"{slug}-2", "session_id": "session-b", "agent_id": "agent-b"}
        S.save_task("altitude", replacement)

        with mock.patch.object(T, "block") as block, mock.patch.object(server.incidents, "system_fault") as fault:
            server.on_l2_finished("altitude", {"task": old, "agent": {"state": "failed"}, "died": True})

        block.assert_not_called()
        fault.assert_not_called()
        current = S.load_task("altitude", slug)
        self.assertEqual((current["state"], current["dispatch_id"], current["agent_id"]),
                         ("running", f"{slug}-2", "agent-b"))

    def test_legacy_claude_owner_cannot_be_resumed(self):
        wt = Path(_TMP) / "legacy-resume"; wt.mkdir(exist_ok=True)
        S.task_dir("altitude", "legacy-resume").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "legacy-resume", "title": "legacy", "created": S.now(),
                                 "state": "blocked", "session_id": "old", "agent_id": "old-agent",
                                 "dispatch_id": "legacy-resume-1", "worktree": str(wt),
                                 "l2_engine": "claude"})
        with mock.patch.object(engines, "claude_resume_bg") as launch:
            with self.assertRaisesRegex(T.TransitionError, "claude autonomous/mutating launch disabled"):
                dispatch.resume_session("altitude", "legacy-resume", "go")
        launch.assert_not_called()
