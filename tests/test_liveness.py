"""A `claude --bg` worker that died (`claude agents` state=failed) is a finished-with-fault L2, never "still running"."""
import json
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import state as S, engines, dispatch, server, tasks as T


class TestDeadWorker(AltitudeCase):
    def _poll(self, rows, tasks):
        self.patch(engines, "claude_agents", return_value=rows)
        self.patch(S, "list_tasks", return_value=tasks)
        return dispatch.poll(self.project)

    def test_failed_worker_without_report_is_died(self):
        task = {"slug": "dead-one", "state": "running", "session_id": "sid-1", "agent_id": "a1"}
        out = self._poll([{"id": "a1", "sessionId": "sid-1", "state": "failed"}], [task])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].get("died"))

    def test_failed_worker_with_report_is_a_normal_finish(self):
        d = S.task_dir(self.project, "reported-then-died"); d.mkdir(parents=True, exist_ok=True)
        (d / "report.json").write_text(json.dumps({"landed": {}}))
        task = {"slug": "reported-then-died", "state": "running", "session_id": "sid-2", "agent_id": "a2"}
        out = self._poll([{"id": "a2", "sessionId": "sid-2", "state": "failed"}], [task])
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].get("died"))

    def test_working_worker_is_not_finished(self):
        task = {"slug": "alive", "state": "running", "session_id": "sid-3", "agent_id": "a3"}
        out = self._poll([{"id": "a3", "sessionId": "sid-3", "state": "working", "status": "busy", "pid": 1}], [task])
        self.assertEqual(out, [])


class TestResumeRebinds(AltitudeCase):
    def test_finished_snapshot_cannot_block_a_replacement_worker(self):
        slug = "stale-finished-worker"
        S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
        old = {"slug": slug, "title": slug, "created": S.now(), "state": "running",
               "attempt": 1, "session_id": "session-a", "agent_id": "agent-a", "l2_engine": "claude"}
        S.save_task(self.project, old)
        replacement = {**old, "session_id": "session-b", "agent_id": "agent-b"}
        S.save_task(self.project, replacement)

        with mock.patch.object(T, "block") as block, mock.patch.object(server.incidents, "system_fault") as fault:
            server.on_l2_finished(self.project, {"task": old, "agent": {"state": "failed"}, "died": True})

        block.assert_not_called()
        fault.assert_not_called()
        current = S.load_task(self.project, slug)
        self.assertEqual((current["state"], current["agent_id"]), ("running", "agent-b"))

    def test_resume_binds_task_to_the_new_worker_in_its_worktree(self):
        wt = self.tmp / "wt-resume"; wt.mkdir()
        S.task_dir(self.project, "resume-me").mkdir(parents=True, exist_ok=True)
        S.save_task(self.project, {"slug": "resume-me", "title": "resume-me", "created": S.now(), "updated": S.now(),
                                   "state": "blocked", "session_id": "old-sid", "agent_id": "old",
                                   "attempt": 1, "worktree": str(wt)})
        seen = {}
        def fake_resume(name, sid, prompt, *, cwd, **kw):
            seen.update(name=name, sid=sid, cwd=str(cwd), env=kw.get("extra_env") or {})
            return {"stdout": "", "stderr": "", "returncode": 0, "agent": {"id": "new", "sessionId": "new-sid", "state": "working"}}
        rows = [{"id": "old", "name": f"{self.project}/resume-me-1", "sessionId": "old-sid", "state": "working",
                 "status": "idle", "pid": 1, "startedAt": 1, "cwd": str(wt)}]
        with mock.patch.object(engines, "claude_resume_bg", fake_resume), \
             mock.patch.object(engines, "claude_agents", return_value=rows), \
             mock.patch.object(engines, "claude_stop", side_effect=lambda _id: rows.clear() or "stopped") as stop, \
             mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree") as validate:
            res = dispatch.resume(self.project, "resume-me")
        validate.assert_called_once()
        self.assertEqual((seen["name"], seen["sid"], seen["cwd"]), (f"{self.project}/resume-me-1", "old-sid", str(wt)))
        self.assertEqual(seen["env"].get("ALTITUDE_SESSION_KEY"), f"{self.project}--resume-me-1")
        t = S.load_task(self.project, "resume-me")
        self.assertEqual((t["state"], t["agent_id"], t["session_id"], t["attempt"]), ("running", "new", "new-sid", 1),
                         "the attempt survives a physical worker replacement")
        self.assertEqual(res["agent"]["id"], "new")
        stop.assert_called_once_with("old")

    def test_resume_without_worktree_is_a_dispatch_again(self):
        S.task_dir(self.project, "no-wt").mkdir(parents=True, exist_ok=True)
        S.save_task(self.project, {"slug": "no-wt", "title": "no-wt", "created": S.now(), "updated": S.now(),
                                   "state": "blocked", "session_id": "s", "agent_id": "a", "attempt": 1,
                                   "worktree": str(self.tmp / "gone")})
        with mock.patch.object(dispatch, "wip_hold", return_value=None):
            with self.assertRaisesRegex(T.TransitionError, "worktree missing"):
                dispatch.resume(self.project, "no-wt")

    def test_resume_without_a_concrete_replacement_never_rebinds(self):
        wt = self.tmp / "wt-no-replacement"; wt.mkdir()
        S.task_dir(self.project, "no-replacement").mkdir(parents=True, exist_ok=True)
        original = {"slug": "no-replacement", "title": "no-replacement", "created": S.now(),
                    "state": "blocked", "session_id": "old-sid", "agent_id": "old-agent",
                    "attempt": 1, "worktree": str(wt)}
        S.save_task(self.project, original)
        with mock.patch.object(engines, "claude_resume_bg", return_value={
                 "stdout": "started", "stderr": "", "returncode": 0,
             }), mock.patch.object(engines, "claude_agents", return_value=[]), \
             mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(RuntimeError, "no concrete live worker"):
                dispatch.resume(self.project, "no-replacement")

        current = S.load_task(self.project, "no-replacement")
        self.assertEqual((current["state"], current["session_id"], current["agent_id"]),
                         ("blocked", "old-sid", "old-agent"))
        self.assertEqual(S.read_events(self.project, "no-replacement")[-1]["kind"], "resume-failed")
        fault.assert_called_once()

    def test_resume_provenance_failure_never_launches_the_engine(self):
        wt = self.tmp / "wt-refused-resume"; wt.mkdir()
        S.task_dir(self.project, "refused-resume").mkdir(parents=True, exist_ok=True)
        S.save_task(self.project, {
            "slug": "refused-resume", "title": "refused-resume", "created": S.now(), "updated": S.now(),
            "state": "blocked", "session_id": "old", "agent_id": "old-agent",
            "attempt": 1, "worktree": str(wt),
        })

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(
                 dispatch, "_validate_task_worktree", side_effect=T.TransitionError("foreign commit")
             ), \
             mock.patch("altitude.incidents.system_fault") as fault, \
             mock.patch.object(engines, "claude_resume_bg") as launch:
            with self.assertRaisesRegex(T.TransitionError, "foreign commit"):
                dispatch.resume(self.project, "refused-resume")

        fault.assert_called_once()
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
