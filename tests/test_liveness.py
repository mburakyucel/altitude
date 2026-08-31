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
from altitude import config, state as S, engines, dispatch, tasks as T  # noqa: E402


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
    def test_resume_binds_task_to_the_new_worker_in_its_worktree(self):
        wt = Path(_TMP) / "wt-resume"; wt.mkdir(exist_ok=True)
        S.task_dir("altitude", "resume-me").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "resume-me", "title": "resume-me", "created": S.now(), "updated": S.now(), "state": "blocked", "session_id": "old-sid", "agent_id": "old",
                                 "dispatch_id": "resume-me-1", "l2_token": "old-token", "worktree": str(wt)})
        seen = {}
        def fake_resume(name, sid, prompt, *, cwd, **kw):
            seen.update(name=name, sid=sid, cwd=str(cwd), env=kw.get("extra_env") or {}); return {"stdout": "", "stderr": "", "returncode": 0}
        rows = [{"id": "old", "name": "altitude/resume-me-1", "sessionId": "old-sid", "state": "failed", "startedAt": 1},
                {"id": "new", "name": "altitude/resume-me-1", "sessionId": "new-sid", "state": "working", "startedAt": 2}]
        with mock.patch.object(engines, "claude_resume_bg", fake_resume), \
             mock.patch.object(engines, "claude_agents", return_value=rows), \
             mock.patch.object(engines, "claude_stop", return_value="stopped") as stop, \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree") as validate:
            res = dispatch.resume_session("altitude", "resume-me", "go")
        validate.assert_called_once()
        self.assertEqual(seen["cwd"], str(wt))
        self.assertEqual(seen["env"].get("ALTITUDE_SESSION_KEY"), "altitude--resume-me-1")
        t = S.load_task("altitude", "resume-me")
        self.assertEqual((t["agent_id"], t["session_id"]), ("new", "new-sid"))
        self.assertNotEqual(t["l2_token"], "old-token")
        self.assertEqual(res["agent"]["id"], "new")
        stop.assert_called_once_with("old")

    def test_resume_without_worktree_is_a_dispatch_again(self):
        S.task_dir("altitude", "no-wt").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "no-wt", "title": "no-wt", "created": S.now(), "updated": S.now(), "state": "blocked", "session_id": "s", "agent_id": "a", "dispatch_id": "no-wt-1",
                                 "worktree": str(Path(_TMP) / "gone")})
        with self.assertRaises(T.TransitionError):
            dispatch.resume_session("altitude", "no-wt", "go")

    def test_resume_without_a_concrete_replacement_never_rebinds(self):
        wt = Path(_TMP) / "wt-no-replacement"; wt.mkdir(exist_ok=True)
        S.task_dir("altitude", "no-replacement").mkdir(parents=True, exist_ok=True)
        original = {"slug": "no-replacement", "title": "no-replacement", "created": S.now(),
                    "state": "running", "session_id": "old-sid", "agent_id": "old-agent",
                    "dispatch_id": "no-replacement-1", "l2_token": "old-token", "worktree": str(wt)}
        S.save_task("altitude", original)
        with mock.patch.object(engines, "claude_resume_bg", return_value={
                 "stdout": "started", "stderr": "", "returncode": 0,
             }), mock.patch.object(engines, "claude_agents", return_value=[]), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(RuntimeError, "no concrete live worker"):
                dispatch.resume_session("altitude", "no-replacement", "go")

        current = S.load_task("altitude", "no-replacement")
        self.assertEqual((current["session_id"], current["agent_id"], current["l2_token"]),
                         ("old-sid", "old-agent", "old-token"))
        fault.assert_called_once()

    def test_resume_provenance_failure_never_launches_the_engine(self):
        wt = Path(_TMP) / "wt-refused-resume"; wt.mkdir(exist_ok=True)
        S.task_dir("altitude", "refused-resume").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {
            "slug": "refused-resume", "title": "refused-resume", "created": S.now(), "updated": S.now(),
            "state": "blocked", "session_id": "old", "agent_id": "old-agent",
            "dispatch_id": "refused-resume-1", "worktree": str(wt),
        })

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(
                 dispatch, "_validate_task_worktree", side_effect=T.TransitionError("foreign commit")
             ), \
             mock.patch("altitude.incidents.system_fault") as fault, \
             mock.patch.object(engines, "claude_resume_bg") as launch:
            with self.assertRaisesRegex(T.TransitionError, "foreign commit"):
                dispatch.resume_session("altitude", "refused-resume", "go")

        fault.assert_called_once()
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
