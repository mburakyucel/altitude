"""No-code tasks close directly; code changes cannot bypass the PR/report gate."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-l2-complete-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, server, state as S, tasks as T  # noqa: E402


class TestDirectL2Completion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root(); cls.repo = _ROOT / "repo"; cls.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=cls.repo, check=True)
        (cls.repo / "README.md").write_text("base\n")
        subprocess.run(["git", "add", "README.md"], cwd=cls.repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
                       cwd=cls.repo, check=True)
        remote = _ROOT / "origin.git"; subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=cls.repo, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=cls.repo, check=True)
        config.save_projects({"p": {"name": "p", "path": str(cls.repo)}})

    def task(self, title):
        task = T.new("p", title, "Produce a proposal.")
        worktree = self.repo / ".claude" / "worktrees" / task["slug"]
        subprocess.run(["git", "worktree", "add", "-q", "-b", f"worktree-{task['slug']}", str(worktree),
                        "origin/main"], cwd=self.repo, check=True)
        task.update({"state": "running", "dispatch_id": f"{task['slug']}-1", "session_id": "session",
                     "agent_id": "worker", "l2_token": "token", "worktree": str(worktree),
                     "branch": f"worktree-{task['slug']}", "l2_engine": "codex"})
        S.save_task("p", task)
        return task, worktree

    def test_unchanged_branch_can_close_without_report_artifacts(self):
        task, _ = self.task("Architecture proposal")
        requested = T.done("p", task["slug"], actor="l2", digest="Proposal saved in issue #1.",
                           expected_dispatch_id=task["dispatch_id"], expected_l2_token="token")
        self.assertEqual(requested["state"], "running")
        self.assertEqual(requested["completion_requested"]["agent_id"], "worker")
        result = T.finalize_completion("p", task["slug"], expected_dispatch_id=task["dispatch_id"],
                                       expected_agent_id="worker", expected_session_id="session")
        self.assertEqual(result["state"], "done")
        self.assertFalse((S.task_dir("p", task["slug"]) / "report.json").exists())

    def test_changed_branch_cannot_bypass_code_verification(self):
        task, worktree = self.task("Code task")
        (worktree / "README.md").write_text("changed\n")
        subprocess.run(["git", "add", "README.md"], cwd=worktree, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "change"],
                       cwd=worktree, check=True)
        with self.assertRaisesRegex(T.TransitionError, "use alt land"):
            T.done("p", task["slug"], actor="l2", digest="done",
                   expected_dispatch_id=task["dispatch_id"], expected_l2_token="token")
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")

    def test_completion_request_never_archives_a_live_worker(self):
        task, _ = self.task("Still exiting")
        T.done("p", task["slug"], actor="l2", digest="result",
               expected_dispatch_id=task["dispatch_id"], expected_l2_token="token")
        snapshot = S.load_task("p", task["slug"])
        with self.assertRaisesRegex(RuntimeError, "still live"):
            server.on_l2_finished("p", {"task": snapshot,
                                         "agent": {"id": "worker", "state": "working", "status": "busy"}})
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")

    def test_rejection_stays_active_if_codex_worker_cannot_be_stopped(self):
        task, _ = self.task("Unsafe rejection")
        task["l2_engine"] = "codex"; S.save_task("p", task)
        with self.assertRaisesRegex(T.TransitionError, "unknown legacy Codex ownership"):
            T.reject("p", task["slug"], "cancel")
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")
        self.assertEqual(S.task_dir("p", task["slug"]).parent, S.tasks_dir("p"))


if __name__ == "__main__":
    unittest.main()
