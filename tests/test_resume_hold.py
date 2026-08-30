"""Decision 39: a blocked task queued to resume keeps its file lease until it can run."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-resume-hold-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, state as S, tasks as T  # noqa: E402
from altitude import monitor  # noqa: E402


class TestResumeHold(unittest.TestCase):
    _number = 0

    def setUp(self):
        type(self)._number += 1
        self.project = f"resume-hold-{self._number}"
        self.repo = _TMP / self.project / "repo"
        self.repo.mkdir(parents=True)
        projects = config.load_projects()
        projects[self.project] = {"name": self.project, "path": str(self.repo), "stacks": ["python"], "wip": 20}
        config.save_projects(projects)

        self.resumed = []
        self.stopped = []
        self.originals = (dispatch.resume_session, engines.claude_stop, engines.claude_agents,
                          engines.usage_hold, monitor.quota, monitor.quota_hold)
        dispatch.resume_session = self._resume_session
        engines.claude_stop = self.stopped.append
        engines.claude_agents = lambda: []
        engines.usage_hold = lambda: None
        monitor.quota = lambda: {"known": True}
        monitor.quota_hold = lambda: None

    def tearDown(self):
        (dispatch.resume_session, engines.claude_stop, engines.claude_agents,
         engines.usage_hold, monitor.quota, monitor.quota_hold) = self.originals

    def _resume_session(self, project, slug, text, session_id=None):
        self.resumed.append({"project": project, "slug": slug, "text": text, "session_id": session_id})
        return {"agent": {"id": f"new-{slug}"}, "stdout": ""}

    def _task(self, title, state, path, created):
        task = T.new(self.project, title, "S", "request", actor="l3", paths=[path])
        task.update({
            "state": state,
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

        result = dispatch.resume_blocked(self.project, blocked["slug"], "Use the approved value.", prefix="Altitude: ")

        task = S.load_task(self.project, blocked["slug"])
        self.assertTrue(result["deferred"])
        self.assertEqual(task["state"], "blocked")
        self.assertIn("waiting for lease: `running-holder`", task["blocked_reason"])
        self.assertIn("altitude/shared.py", task["blocked_reason"])
        self.assertEqual(task["resume_answer"], "Use the approved value.")
        self.assertEqual(task["resume_prefix"], "Altitude: ")
        self.assertTrue(task["resume_after"])
        self.assertEqual(self.resumed, [])
        self.assertEqual(self.stopped, [])

    def test_due_resume_uses_stored_answer_after_holder_finishes(self):
        holder = self._task("active lease", "running", "bin/alt", "2026-01-01T00:00:00+00:00")
        blocked = self._task("waiting resume", "blocked", "bin/alt", "2026-01-02T00:00:00+00:00")
        dispatch.resume_blocked(self.project, blocked["slug"], "Keep the raised envelope.", prefix="Altitude: ")
        holder["state"] = "done"
        S.save_task(self.project, holder)

        resumed = dispatch.resume_due(self.project)

        task = S.load_task(self.project, blocked["slug"])
        self.assertEqual(resumed, [blocked["slug"]])
        self.assertEqual(task["state"], "running")
        self.assertEqual(self.resumed[0]["text"],
                         "Altitude: Keep the raised envelope.\nContinue from your progress file; finish to *done* and rewrite the report.")
        self.assertNotIn("resume_after", task)
        self.assertNotIn("resume_answer", task)
        self.assertNotIn("resume_prefix", task)

    def test_resume_without_overlap_reattaches_immediately(self):
        self._task("unrelated holder", "running", "altitude/other.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("free resume", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")

        result = dispatch.resume_blocked(self.project, blocked["slug"], "Continue now.")

        self.assertFalse(result["deferred"])
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "running")
        self.assertEqual([call["slug"] for call in self.resumed], [blocked["slug"]])
        self.assertEqual(self.stopped, [blocked["agent_id"]])

    def test_only_blocked_task_with_pending_resume_holds_its_files(self):
        pending = self._task("pending lease", "blocked", "altitude/pending.py", "2026-01-01T00:00:00+00:00")
        pending["resume_after"] = "2026-01-01T00:00:00+00:00"
        S.save_task(self.project, pending)
        self._task("plain block", "blocked", "altitude/plain.py", "2026-01-01T00:00:00+00:00")
        pending_target = self._task("pending target", "approved", "altitude/pending.py", "2026-01-02T00:00:00+00:00")
        plain_target = self._task("plain target", "approved", "altitude/plain.py", "2026-01-02T00:00:00+00:00")

        leases = dispatch.leases(self.project)

        self.assertEqual(leases, [{"slug": pending["slug"], "paths": ["altitude/pending.py"], "pending_resume": True}])
        self.assertIn("blocked with a pending resume", dispatch.wip_hold(self.project, pending_target) or "")
        self.assertIsNone(dispatch.wip_hold(self.project, plain_target))

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


if __name__ == "__main__":
    unittest.main()
