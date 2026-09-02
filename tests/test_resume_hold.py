"""A blocked task queued to resume keeps its file lease until it can run."""
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-resume-hold-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, github_intake, recovery, state as S, tasks as T  # noqa: E402
from altitude import monitor  # noqa: E402


class TestResumeHold(unittest.TestCase):
    _number = 0

    def setUp(self):
        recovery.hold_path().unlink(missing_ok=True)
        type(self)._number += 1
        self.project = f"resume-hold-{self._number}"
        self.repo = _TMP / self.project / "repo"
        self.repo.mkdir(parents=True)
        config.save_projects({self.project: {
            "name": self.project, "path": str(self.repo), "wip": 20,
        }})

        self.resumed = []
        self.stopped = []
        self.originals = (dispatch.resume_session, engines.claude_stop, engines.claude_agents,
                          engines.usage_hold, monitor.quota)
        dispatch.resume_session = self._resume_session
        engines.claude_stop = self.stopped.append
        engines.claude_agents = lambda: []
        engines.usage_hold = lambda: None
        monitor.quota = lambda: {"known": True}

    def tearDown(self):
        (dispatch.resume_session, engines.claude_stop, engines.claude_agents,
         engines.usage_hold, monitor.quota) = self.originals
        recovery.hold_path().unlink(missing_ok=True)

    def _resume_session(self, project, slug, text, session_id=None, **_expected):
        self.resumed.append({"project": project, "slug": slug, "text": text, "session_id": session_id})
        with S.project_lock(project):
            task = S.load_task(project, slug)
            task["agent_id"] = f"new-{slug}"
            S.save_task(project, task)
        return {"agent": {"id": f"new-{slug}", "sessionId": task["session_id"]}, "stdout": ""}

    def _task(self, title, state, path, created):
        task = T.new(self.project, title, "request", actor="l3", paths=[path])
        task.update({
            "state": state,
            "l2_engine": "codex",
            "created": created,
            "dispatch_id": f"{task['slug']}-1",
            "session_id": f"session-{task['slug']}",
            "agent_id": f"agent-{task['slug']}",
            "worktree": str(self.repo),
        })
        S.save_task(self.project, task)
        return task

    def test_blocked_resume_waits_for_running_holder_and_records_answer(self):
        self._task("running holder", "running", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("blocked worker", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")

        result = dispatch.resume_blocked(self.project, blocked["slug"], "Use the selected value.", prefix="Altitude: ")

        task = S.load_task(self.project, blocked["slug"])
        self.assertTrue(result["deferred"])
        self.assertEqual(task["state"], "blocked")
        self.assertIn("waiting for lease: `running-holder`", task["blocked_reason"])
        self.assertIn("altitude/shared.py", task["blocked_reason"])
        self.assertEqual(task["resume_answer"], "Use the selected value.")
        self.assertEqual(task["resume_prefix"], "Altitude: ")
        self.assertTrue(task["resume_after"])
        self.assertEqual(self.resumed, [])
        self.assertEqual(self.stopped, [])

    def test_due_resume_uses_stored_answer_after_holder_finishes(self):
        holder = self._task("active lease", "running", "bin/alt", "2026-01-01T00:00:00+00:00")
        blocked = self._task("waiting resume", "blocked", "bin/alt", "2026-01-02T00:00:00+00:00")
        blocked["blocked_reason"] = "Which retry strategy should I use?"
        S.save_task(self.project, blocked)
        dispatch.resume_blocked(self.project, blocked["slug"], "Use the focused retry.", prefix="Altitude: ")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["blocked_question"],
                         "Which retry strategy should I use?")
        holder["state"] = "done"
        S.save_task(self.project, holder)

        resumed = dispatch.resume_due(self.project)

        task = S.load_task(self.project, blocked["slug"])
        self.assertEqual(resumed, [blocked["slug"]])
        self.assertEqual(task["state"], "running")
        self.assertEqual(self.resumed[0]["text"],
                         "Altitude: Use the focused retry.\nContinue from your progress file; finish to *done* and rewrite the report.")
        self.assertEqual(task["blocked_question"], "Which retry strategy should I use?")
        self.assertNotIn("resume_after", task)
        self.assertNotIn("resume_answer", task)
        self.assertNotIn("resume_prefix", task)

    def test_non_lease_resume_hold_uses_generic_waiting_wording(self):
        blocked = self._task("usage held", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")
        blocked["l2_engine"] = "claude"
        S.save_task(self.project, blocked)
        engines.usage_hold = lambda: "2026-01-03T00:00:00+00:00"

        with mock.patch.object(config, "AUTONOMOUS_ENGINES", ("claude", "codex")):
            result = dispatch.resume_blocked(self.project, blocked["slug"], "Continue later.")

        waiting = "waiting: usage limit: subscription window exhausted, resets 2026-01-03T00:00:00+00:00"
        self.assertEqual(result["waiting"], waiting)
        self.assertEqual(S.load_task(self.project, blocked["slug"])["blocked_reason"], waiting)

    def test_resume_without_overlap_reattaches_immediately(self):
        self._task("unrelated holder", "running", "altitude/other.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("free resume", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")

        result = dispatch.resume_blocked(self.project, blocked["slug"], "Continue now.")

        self.assertFalse(result["deferred"])
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "running")
        self.assertEqual([call["slug"] for call in self.resumed], [blocked["slug"]])
        self.assertEqual(self.stopped, [], "the mocked public resume seam owns worker replacement in this test")

    def test_legacy_issue_context_is_appended_once_across_exact_resume_retries(self):
        blocked = self._task("Address https://github.com/acme/widget/issues/121", "blocked",
                             "altitude/free.py", "2026-01-02T00:00:00+00:00")
        snapshot = {"number": 121, "title": "Live view", "body": "Acceptance", "state": "open",
                    "url": "https://github.com/acme/widget/issues/121", "content_sha256": "a" * 64}
        with mock.patch.object(github_intake, "ensure_snapshot", return_value=snapshot):
            first, digest = dispatch._issue_resume_prompt(self.project, blocked, "Continue now.")
            blocked["github_issue_context_delivered"] = digest
            second, repeated = dispatch._issue_resume_prompt(self.project, blocked, "Continue again.")

        self.assertIn(github_intake.marker(snapshot), first)
        self.assertNotIn(github_intake.marker(snapshot), second)
        self.assertEqual(digest, "a" * 64)
        self.assertIsNone(repeated)

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

    def test_overlapping_pending_resumes_choose_oldest_without_deadlock(self):
        oldest = self._task("oldest resume", "blocked", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        youngest = self._task("youngest resume", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")
        for task in (oldest, youngest):
            task["resume_after"] = "2026-01-03T00:00:00+00:00"
            task["resume_answer"] = f"Resume {task['slug']}"
            S.save_task(self.project, task)

        self.assertIsNone(dispatch.wip_hold(self.project, oldest))
        self.assertIn(f"`{oldest['slug']}`", dispatch.wip_hold(self.project, youngest) or "")

        resumed = dispatch.resume_due(self.project)

        self.assertEqual(resumed, [oldest["slug"]])
        self.assertEqual(S.load_task(self.project, oldest["slug"])["state"], "running")
        self.assertEqual(S.load_task(self.project, youngest["slug"])["state"], "blocked")
        self.assertTrue(S.load_task(self.project, youngest["slug"])["resume_after"])

    def test_cli_resume_payloads_for_deferred_and_running(self):
        main = runpy.run_path(str(Path(__file__).resolve().parent.parent / "bin" / "alt"))["main"]
        blocked = self._task("cli resume", "blocked", "altitude/cli.py", "2026-01-01T00:00:00+00:00")
        original = dispatch.resume_blocked

        def invoke(fake):
            dispatch.resume_blocked = fake
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    main(["--project", self.project, "task", "resume", blocked["slug"], "--answer", "Continue."])
            finally:
                dispatch.resume_blocked = original
            return json.loads(output.getvalue())

        def deferred(project, slug, answer, prefix=""):
            task = S.load_task(project, slug)
            task["blocked_reason"] = "waiting: usage limit: test window"
            S.save_task(project, task)
            return {"deferred": True}

        deferred_payload = invoke(deferred)
        self.assertEqual(deferred_payload["state"], "blocked")
        self.assertTrue(deferred_payload["deferred"])
        self.assertEqual(deferred_payload["waiting"], "waiting: usage limit: test window")

        def resumed(project, slug, answer, prefix=""):
            task = S.load_task(project, slug)
            task["state"] = "running"
            S.save_task(project, task)
            return {"deferred": False, "agent": {"id": "new-cli-agent"}}

        running_payload = invoke(resumed)
        self.assertEqual(running_payload["state"], "running")
        self.assertFalse(running_payload["deferred"])
        self.assertEqual(running_payload["agent"], "new-cli-agent")


if __name__ == "__main__":
    unittest.main()
