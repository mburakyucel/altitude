"""A `claude --bg` worker that died (`claude agents` state=failed) is a finished-with-fault L2, never "still running"."""
import json
import os
from datetime import datetime, timezone
import unittest
import time
from unittest import mock

from tests.support import AltitudeCase
from altitude import state as S, engines, dispatch, server, tasks as T


class TestDeadWorker(AltitudeCase):
    def test_incident_165145_stale_report_is_a_fault_not_a_verdict(self):
        # I-20260907-165145: a pre-resume report must not be replayed after the worker disappears.
        for state in (None, "failed", "done", "stopped"):
            with self.subTest(state=state):
                slug = f"stale-{state or 'absent'}"
                task = T.new(self.project, slug, "Repair worker lifecycle")
                task.update(state="running", attempt=1, agent_id="worker", session_id="session",
                            dispatched="2026-09-07T10:00:00+00:00",
                            worker_started_at="2026-09-07T16:00:00.500000+00:00")
                S.save_task(self.project, task)
                report = S.task_dir(self.project, slug) / "report.json"
                report.write_text(json.dumps({"blocked": "old reason"}))
                stale = datetime(2026, 9, 7, 16, 0, 0, 250000, timezone.utc).timestamp()
                os.utime(report, (stale, stale))
                rows = [] if state is None else [{"id": "worker", "sessionId": "session", "state": state}]
                item = self._poll(rows, [task])[0]
                self.assertTrue(item.get("died"))
                with mock.patch.object(server.verify, "verify") as verify, \
                     mock.patch.object(server.incidents, "system_fault") as fault:
                    server.on_l2_finished(self.project, item)
                verify.assert_not_called()
                fault.assert_called_once()
                self.assertEqual(fault.call_args.args[0], "l2-died")
                saved = S.load_task(self.project, slug)
                self.assertEqual(saved["state"], "blocked")
                self.assertIn("fresh report", saved["blocked_reason"])

    def test_incident_171446_owned_worker_error_reaches_fault_detail(self):
        task = T.new(self.project, "owned-error", "Repair worker lifecycle")
        task.update(state="running", attempt=1, l2_engine="claude", agent_id="owned", session_id="session")
        S.save_task(self.project, task)
        paths = engines._codex_paths(dispatch.l2_job_root(self.project, task["slug"]), "owned")
        for stream in ("result", "stderr", "stdout"):
            with self.subTest(stream=stream):
                S.save_task(self.project, task)
                S.write_json(paths["record"], {"engine": "claude", "session_id": "session", "unit": "owned.service"})
                paths["stdout"].write_text(json.dumps({"type": "result", "is_error": True,
                    "errors": ["provider rejected the request"]}) if stream == "result" else
                    '{"type":"assistant","message":{"content":"last provider output before exit"}}' if stream == "stdout" else "")
                paths["stderr"].write_text("launcher could not open settings" if stream == "stderr" else "")
                with mock.patch.object(engines, "_unit_active", return_value=False), \
                     mock.patch.object(server.incidents, "system_fault") as fault:
                    item = dispatch.poll(self.project)[0]
                    self.assertTrue(item["died"])
                    server.on_l2_finished(self.project, item)
                self.assertEqual(fault.call_args.args[0], "l2-died")
                self.assertIn({"result": "provider rejected", "stderr": "could not open settings",
                               "stdout": "last provider output before exit"}[stream], fault.call_args.args[1])

    def test_fresh_report_after_resume_and_existing_resume_timestamp(self):
        task = T.new(self.project, "resumed-report", "Repair worker lifecycle")
        task.update(state="running", agent_id="worker", session_id="session", dispatched="2026-09-07T10:00:00+00:00")
        report = S.task_dir(self.project, task["slug"]) / "report.json"
        report.write_text("{}")
        S.append_event(self.project, task["slug"], "state", frm="blocked", to="running", previous_worker="old")
        rows = [{"id": "worker", "state": "done"}]
        self.assertFalse(self._poll(rows, [task])[0].get("died"))
        os.utime(report, (1, 1))
        self.assertTrue(self._poll(rows, [task])[0].get("died"))

    def _poll(self, rows, tasks):
        self.patch(engines, "worker", side_effect=lambda engine, task, **kw: next(
            (row for row in rows if row.get("id") == task.get("agent_id")), None))
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
        def fake_resume(engine, name, sid, prompt, *, cwd, **kw):
            seen.update(name=name, sid=sid, cwd=str(cwd), env=kw.get("extra_env") or {})
            return {"stdout": "", "stderr": "", "returncode": 0, "agent": {"id": "new", "sessionId": "new-sid", "state": "working"}}
        rows = [{"id": "old", "name": f"{self.project}/resume-me-1", "sessionId": "old-sid", "state": "working",
                 "status": "idle", "pid": 1, "startedAt": 1, "cwd": str(wt)}]
        before_resume = time.time()
        with mock.patch.object(engines, "resume_l2", fake_resume), \
             mock.patch.object(engines, "worker_live", side_effect=lambda *a, **kw: bool(rows)), \
             mock.patch.object(engines, "stop_l2_worker", side_effect=lambda *a, **kw: rows.clear() or "stopped") as stop, \
             mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree") as validate:
            res = dispatch.resume(self.project, "resume-me")
        validate.assert_called_once()
        self.assertEqual((seen["name"], seen["sid"], seen["cwd"]), (f"{self.project}/resume-me-1", "old-sid", str(wt)))
        self.assertEqual(seen["env"].get("ALTITUDE_SESSION_KEY"), f"{self.project}--resume-me-1")
        t = S.load_task(self.project, "resume-me")
        self.assertGreaterEqual(datetime.fromisoformat(t["worker_started_at"]).timestamp(), before_resume)
        self.assertEqual((t["state"], t["agent_id"], t["session_id"], t["attempt"]), ("running", "new", "new-sid", 1),
                         "the attempt survives a physical worker replacement")
        self.assertEqual(res["agent"]["id"], "new")
        stop.assert_called_once_with("claude", "old", job_root=dispatch.l2_job_root(self.project, "resume-me"))

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
        with mock.patch.object(engines, "resume_l2", return_value={
                 "stdout": "started", "stderr": "", "returncode": 0,
             }), mock.patch.object(engines, "worker_live", return_value=False), \
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
             mock.patch.object(engines, "resume_l2") as launch:
            with self.assertRaisesRegex(T.TransitionError, "foreign commit"):
                dispatch.resume(self.project, "refused-resume")

        fault.assert_called_once()
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
