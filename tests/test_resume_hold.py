"""Usage-window resume holds and daemon handoffs."""
import contextlib
import io
import json
import runpy
import unittest
from unittest import mock

from tests.support import ALT, AltitudeCase
from altitude import dispatch, engines, state as S, tasks as T


class TestResumeHold(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(self.project, wip=20)
        self.launched = []
        self.quiet_engines()
        self.patch(engines, "claude_stop", return_value="stopped")
        self.patch(dispatch.git_policy, "fetch_origin", return_value="a" * 40)
        self.patch(dispatch, "_validate_task_worktree")
        self.patch(engines, "resume_l2", side_effect=self._resume_l2)

    def _resume_l2(self, engine, name, session_id, prompt, **_kw):
        slug = name.split("/", 1)[1].rsplit("-", 1)[0]
        self.launched.append({"slug": slug, "prompt": prompt, "session_id": session_id})
        return {"returncode": 0, "stdout": "", "stderr": "",
                "agent": {"id": f"new-{slug}", "sessionId": session_id, "state": "working"}}

    def _task(self, title, state, path, created):
        task = T.new(self.project, title, "request", actor="l3", paths=[path])
        task.update({"state": state, "created": created, "attempt": 1, "session_id": f"session-{task['slug']}",
                     "agent_id": f"agent-{task['slug']}", "worktree": str(self.repo)})
        S.save_task(self.project, task)
        return task

    def test_usage_window_hold_uses_generic_waiting_wording(self):
        blocked = self._task("usage held", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")
        with mock.patch.object(engines, "usage_hold", return_value="2099-01-03T00:00:00+00:00"):
            result = dispatch.resume(self.project, blocked["slug"])
            self.assertEqual(dispatch.resume_due(self.project), [])

        hold = "usage limit: subscription window exhausted, resets 2099-01-03T00:00:00+00:00"
        self.assertEqual(result, {"held": hold})
        self.assertEqual(S.load_task(self.project, blocked["slug"])["blocked_reason"], f"waiting: {hold}")
        self.assertEqual(self.launched, [])

    def test_overlapping_pending_resumes_are_both_due(self):
        tasks = [self._task(name, "blocked", "docs/ARCHITECTURE.md", created)
                 for name, created in (("first resume", "2026-01-01T00:00:00+00:00"),
                                       ("second resume", "2026-01-02T00:00:00+00:00"))]
        for task in tasks:
            task["resume_after"] = "2026-01-03T00:00:00+00:00"
            S.save_task(self.project, task)
        self.assertEqual(set(dispatch.resume_due(self.project)), {task["slug"] for task in tasks})
        for task in tasks:
            dispatch.resume(self.project, task["slug"])
        self.assertEqual(len(self.launched), 2)

    def test_resume_with_overlap_reattaches_immediately(self):
        self._task("overlapping worker", "running", "altitude/free.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("free resume", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")

        result = dispatch.resume(self.project, blocked["slug"])

        self.assertEqual(result["agent"]["id"], f"new-{blocked['slug']}")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "running")
        self.assertEqual([call["slug"] for call in self.launched], [blocked["slug"]])

    def test_cli_resume_requires_a_reason_and_altd_reports_a_hold_or_the_new_worker(self):
        cli = runpy.run_path(str(ALT))
        main = cli["main"]
        main.__globals__["ACTOR"] = "burak"
        blocked = self._task("cli resume", "blocked", "altitude/cli.py", "2026-01-01T00:00:00+00:00")

        def invoke():
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["--project", self.project, "task", "resume", blocked["slug"],
                      "--reason", "The dependency is available"])
            return json.loads(output.getvalue())

        queued = invoke()
        self.assertTrue(queued["queued"], "resume with a required reason creates the daemon handoff")
        self.assertEqual(queued["request"]["reason"], "The dependency is available",
                         "the required reason survives in the daemon request")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "blocked",
                         "the CLI request does not launch the worker")
        with mock.patch.object(engines, "usage_hold", return_value="2099-01-03T00:00:00+00:00"):
            held = dispatch.run_task_operation(self.project, blocked["slug"])
            held_retry = dispatch.run_task_operation(self.project, blocked["slug"])
        self.assertTrue(held["held"].startswith("usage limit"), held)
        self.assertTrue(held_retry["held"].startswith("usage limit"), held_retry)
        self.assertEqual(held["request"]["status"], "executing",
                         "altd keeps the durable daemon fence while a held request remains retryable")
        self.assertEqual(len([event for event in S.read_events(self.project, blocked["slug"])
                              if event["kind"] == "resume-held"]), 1,
                         "a daemon retry of the same hold does not append another event")
        self.assertNotIn(blocked["slug"], dispatch.pending_task_operations(self.project),
                         "altd does not reschedule an explicit resume before its usage window reopens")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "blocked",
                         "a daemon-side usage hold leaves the worker stopped")

        running = dispatch.run_task_operation(self.project, blocked["slug"])
        self.assertEqual(running["request"]["status"], "done",
                         "altd completes the same durable resume request")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "running",
                         "only the daemon-side operation launches the worker")


if __name__ == "__main__":
    unittest.main()
