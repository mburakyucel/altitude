"""A blocked task Altitude cannot bring back yet keeps its file lease and waits for the next tick."""
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
        self.patch(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40)
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

    def test_resume_held_by_a_running_lease_holder_waits_with_the_reason(self):
        self._task("running holder", "running", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("blocked worker", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")
        T.message(self.project, blocked["slug"], "burak", "Use the selected value.")

        result = dispatch.resume(self.project, blocked["slug"])

        task = S.load_task(self.project, blocked["slug"])
        self.assertIn("file lease: `running-holder` is running", result["held"])
        self.assertEqual(task["state"], "blocked")
        self.assertEqual(task["blocked_reason"], f"waiting: {result['held']}")
        self.assertIn("altitude/shared.py", task["blocked_reason"])
        self.assertTrue(task["resume_after"])
        self.assertEqual([m["text"] for m in T.pending(self.project, blocked["slug"])], ["Use the selected value."])
        self.assertEqual(S.read_events(self.project, blocked["slug"])[-1]["kind"], "resume-held")
        self.assertEqual(self.launched, [])

    def test_due_resume_delivers_the_waiting_message_after_the_holder_finishes(self):
        holder = self._task("active lease", "running", "bin/alt", "2026-01-01T00:00:00+00:00")
        blocked = self._task("waiting resume", "blocked", "bin/alt", "2026-01-02T00:00:00+00:00")
        T.message(self.project, blocked["slug"], "burak", "Use the focused retry.")
        dispatch.resume(self.project, blocked["slug"])
        self.assertEqual(dispatch.resume_due(self.project), [], "the lease still holds it")
        holder["state"] = "done"
        S.save_task(self.project, holder)

        self.assertEqual(dispatch.resume_due(self.project), [blocked["slug"]])
        result = dispatch.resume(self.project, blocked["slug"])

        task = S.load_task(self.project, blocked["slug"])
        self.assertEqual(result["agent"]["id"], f"new-{blocked['slug']}")
        self.assertEqual(task["state"], "running")
        self.assertNotIn("resume_after", task)
        self.assertTrue(self.launched[0]["prompt"].endswith("\nUse the focused retry."), self.launched)
        self.assertEqual(T.pending(self.project, blocked["slug"]), [])

    def test_usage_window_hold_uses_generic_waiting_wording(self):
        blocked = self._task("usage held", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")
        with mock.patch.object(engines, "usage_hold", return_value="2026-01-03T00:00:00+00:00"):
            result = dispatch.resume(self.project, blocked["slug"])
            self.assertEqual(dispatch.resume_due(self.project), [])

        hold = "usage limit: subscription window exhausted, resets 2026-01-03T00:00:00+00:00"
        self.assertEqual(result, {"held": hold})
        self.assertEqual(S.load_task(self.project, blocked["slug"])["blocked_reason"], f"waiting: {hold}")
        self.assertEqual(self.launched, [])

    def test_resume_without_overlap_reattaches_immediately(self):
        self._task("unrelated holder", "running", "altitude/other.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("free resume", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")

        result = dispatch.resume(self.project, blocked["slug"])

        self.assertEqual(result["agent"]["id"], f"new-{blocked['slug']}")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "running")
        self.assertEqual([call["slug"] for call in self.launched], [blocked["slug"]])

    def test_only_blocked_task_with_pending_resume_holds_its_files(self):
        pending = self._task("pending lease", "blocked", "altitude/pending.py", "2026-01-01T00:00:00+00:00")
        pending["resume_after"] = "2026-01-01T00:00:00+00:00"
        S.save_task(self.project, pending)
        self._task("plain block", "blocked", "altitude/plain.py", "2026-01-01T00:00:00+00:00")
        pending_target = self._task("pending target", "queued", "altitude/pending.py", "2026-01-02T00:00:00+00:00")
        plain_target = self._task("plain target", "queued", "altitude/plain.py", "2026-01-02T00:00:00+00:00")

        leases = dispatch.leases(self.project)

        self.assertEqual(leases, [{"slug": pending["slug"], "paths": ["altitude/pending.py"], "pending_resume": True}])
        self.assertIn("blocked with a pending resume", dispatch.wip_hold(self.project, pending_target) or "")
        self.assertIsNone(dispatch.wip_hold(self.project, plain_target))

    def test_older_queued_candidate_is_held_by_pending_resume_lease(self):
        candidate = self._task("older candidate", "queued", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        pending = self._task("pending holder", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")
        pending["resume_after"] = "2026-01-03T00:00:00+00:00"
        S.save_task(self.project, pending)

        hold = dispatch.wip_hold(self.project, candidate)

        self.assertIn(f"`{pending['slug']}` is blocked with a pending resume", hold or "")

    def test_overlapping_pending_resumes_come_back_oldest_first(self):
        oldest = self._task("oldest resume", "blocked", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        youngest = self._task("youngest resume", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")
        for task in (oldest, youngest):
            task["resume_after"] = "2026-01-03T00:00:00+00:00"
            S.save_task(self.project, task)

        self.assertIsNone(dispatch.wip_hold(self.project, oldest))
        self.assertIn(f"`{oldest['slug']}`", dispatch.wip_hold(self.project, youngest) or "")
        self.assertEqual(dispatch.resume_due(self.project), [oldest["slug"]])

    def test_cli_resume_reports_a_hold_or_the_new_worker(self):
        main = runpy.run_path(str(ALT))["main"]
        blocked = self._task("cli resume", "blocked", "altitude/cli.py", "2026-01-01T00:00:00+00:00")

        def invoke():
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["--project", self.project, "task", "resume", blocked["slug"]])
            return json.loads(output.getvalue())

        with mock.patch.object(engines, "usage_hold", return_value="2026-01-03T00:00:00+00:00"):
            held = invoke()
        self.assertTrue(held["held"].startswith("usage limit"), held)
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "blocked")

        running = invoke()
        self.assertEqual(running["agent"]["id"], f"new-{blocked['slug']}")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "running")


if __name__ == "__main__":
    unittest.main()
