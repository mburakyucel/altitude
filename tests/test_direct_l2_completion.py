"""No-code tasks close directly; code changes cannot bypass the PR/report gate."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import engines, l3, server, state as S, tasks as T


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

    def test_completed_no_code_tasks_stay_in_work_with_findings_and_a_coordinator_handoff(self):
        # #296: Done this week lists every task finished in the last seven days by finish time, newest
        # first, whatever the slugs; older completions stay out.
        week = datetime.now(timezone.utc) - timedelta(days=7)
        for n in range(25):
            S.write_json(S.archive_dir("p") / f"zz-earlier-{n:02}" / "status.json",
                         {"slug": f"zz-earlier-{n:02}", "state": "rejected" if n % 5 == 0 else "done",
                          "title": f"Earlier {n}", "prs": [n + 1],
                          "updated": (week + timedelta(hours=n + 1)).isoformat(timespec="seconds")})
        S.write_json(S.archive_dir("p") / "zz-last-week" / "status.json",
                     {"slug": "zz-last-week", "state": "done", "title": "Last week",
                      "updated": (week - timedelta(days=1)).isoformat(timespec="seconds")})
        finished, completed = [f"zz-earlier-{n:02}" for n in reversed(range(25))], set()
        for title, engine in (("Architecture proposal", "claude"), ("Backlog review", "codex")):
            task, _ = self.task(title)
            task["l2_engine"] = engine
            S.save_task("p", task)
            T.message("p", task["slug"], "l2", f"{title} findings: keep the queue.", expected_attempt=1)
            T.done("p", task["slug"], actor="l2", digest=f"{title} findings are ready.", expected_attempt=1)
            server.on_l2_finished("p", {"task": S.load_task("p", task["slug"]), "died": True,
                                        "agent": {"id": "worker", "state": "done"}})
            self.assertEqual(S.load_task("p", task["slug"])["state"], "done")
            completed.add(task["slug"])  # both may finish within the same second
            listed = [row["slug"] for row in server.project_view("p")["archive"]]
            self.assertEqual((set(listed[:len(completed)]), listed[len(completed):]), (completed, finished))
            self.assertIn(f"{title} findings: keep the queue.",
                          [row["text"] for row in T.task_messages("p", task["slug"])])
            fyi = l3.chat_history("p")[-1]
            self.assertEqual((fyi["role"], fyi["trigger"], fyi["slug"]), ("system", "fyi", task["slug"]))
            self.assertIn(f"{title} completed without code changes", fyi["text"])
            self.assertIn(f"{title} findings are ready.", fyi["text"])

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
