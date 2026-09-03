"""No-code tasks close directly; code changes cannot bypass the PR/report gate."""
import unittest
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import engines, server, state as S, tasks as T


class TestDirectL2Completion(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.register("p", path=self.repo)

    def task(self, title):
        task = T.new("p", title, "Produce a proposal.")
        worktree = add_worktree(self.repo, task["slug"])
        task.update({"state": "running", "attempt": 1, "session_id": "session", "agent_id": "worker",
                     "worktree": str(worktree), "branch": f"worktree-{task['slug']}"})
        S.save_task("p", task)
        return task, worktree

    def test_unchanged_branch_can_close_without_report_artifacts(self):
        task, _ = self.task("Architecture proposal")
        requested = T.done("p", task["slug"], actor="l2", digest="Proposal saved in issue #1.", expected_attempt=1)
        self.assertEqual(requested["state"], "running")
        self.assertEqual(requested["completion_requested"]["digest"], "Proposal saved in issue #1.")
        result = T.finalize_completion("p", task["slug"])
        self.assertEqual(result["state"], "done")
        self.assertFalse((S.task_dir("p", task["slug"]) / "report.json").exists())

    def test_changed_branch_cannot_bypass_code_verification(self):
        task, worktree = self.task("Code task")
        (worktree / "README.md").write_text("changed\n")
        git("commit", "-qam", "change", cwd=worktree)
        with self.assertRaisesRegex(T.TransitionError, "use alt land"):
            T.done("p", task["slug"], actor="l2", digest="done", expected_attempt=1)
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")

    def test_completion_request_never_archives_a_live_worker(self):
        task, _ = self.task("Still exiting")
        T.done("p", task["slug"], actor="l2", digest="result", expected_attempt=1)
        snapshot = S.load_task("p", task["slug"])
        with self.assertRaisesRegex(RuntimeError, "still live"):
            server.on_l2_finished("p", {"task": snapshot,
                                        "agent": {"id": "worker", "state": "working", "status": "busy"}})
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")

    def test_rejection_stays_active_if_codex_worker_cannot_be_stopped(self):
        task, _ = self.task("Unsafe rejection")
        task["l2_engine"] = "codex"
        S.save_task("p", task)
        with mock.patch.object(engines, "remove_l2_worker", side_effect=RuntimeError("still alive")):
            with self.assertRaisesRegex(T.TransitionError, "may still be live"):
                T.reject("p", task["slug"], "cancel")
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")
        self.assertEqual(S.task_dir("p", task["slug"]).parent, S.tasks_dir("p"))


if __name__ == "__main__":
    unittest.main()
