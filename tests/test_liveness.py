"""A `claude --bg` worker that died (`claude agents` state=failed) is a finished-with-fault L2, never "still running"."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-liveness-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch, tasks as T  # noqa: E402


class TestDeadWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})

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
    def test_resume_binds_task_to_the_new_worker_in_its_worktree(self):
        wt = Path(_TMP) / "wt-resume"; wt.mkdir(exist_ok=True)
        S.task_dir("altitude", "resume-me").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "resume-me", "state": "blocked", "session_id": "old-sid", "agent_id": "old",
                                 "dispatch_id": "resume-me-1", "envelope": {"max_turns": 5}, "worktree": str(wt), "class": "S"})
        seen = {}
        def fake_resume(name, sid, prompt, *, cwd, **kw):
            seen.update(name=name, sid=sid, cwd=str(cwd), env=kw.get("extra_env") or {}); return {"stdout": "", "stderr": "", "returncode": 0}
        rows = [{"id": "old", "name": "altitude/resume-me-1", "sessionId": "old-sid", "state": "failed", "startedAt": 1},
                {"id": "new", "name": "altitude/resume-me-1", "sessionId": "new-sid", "state": "working", "startedAt": 2}]
        orig = engines.claude_resume_bg, engines.claude_agents
        engines.claude_resume_bg, engines.claude_agents = fake_resume, (lambda: rows)
        try:
            res = dispatch.resume_session("altitude", "resume-me", "go")
        finally:
            engines.claude_resume_bg, engines.claude_agents = orig
        self.assertEqual(seen["cwd"], str(wt))
        self.assertEqual(seen["env"].get("ALTITUDE_SESSION_KEY"), "altitude--resume-me-1")
        t = S.load_task("altitude", "resume-me")
        self.assertEqual((t["agent_id"], t["session_id"]), ("new", "new-sid"))
        self.assertEqual(res["agent"]["id"], "new")

    def test_resume_without_worktree_is_a_dispatch_again(self):
        S.task_dir("altitude", "no-wt").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "no-wt", "state": "blocked", "session_id": "s", "agent_id": "a", "dispatch_id": "no-wt-1",
                                 "envelope": {"max_turns": 5}, "worktree": str(Path(_TMP) / "gone"), "class": "S"})
        with self.assertRaises(T.TransitionError):
            dispatch.resume_session("altitude", "no-wt", "go")


if __name__ == "__main__":
    unittest.main()
