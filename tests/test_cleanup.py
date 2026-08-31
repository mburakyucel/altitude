"""cleanup_after_done removes only merged worktrees nobody owns: not a running task's, not a locked one (real git)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-cleanup-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch  # noqa: E402


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


class TestCleanupScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        cls.repo = Path(_TMP) / "repo"; cls.repo.mkdir()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        os.environ.update(env)
        git("init", "-q", "-b", "main", cwd=cls.repo); (cls.repo / "f").write_text("1")
        (cls.repo / ".gitignore").write_text(".claude/\n")
        git("add", "-A", cwd=cls.repo); git("commit", "-qm", "init", cwd=cls.repo)
        bare = Path(_TMP) / "origin.git"; git("init", "-q", "--bare", str(bare), cwd=cls.repo)
        git("remote", "add", "origin", str(bare), cwd=cls.repo); git("push", "-q", "origin", "main", cwd=cls.repo)
        config.save_projects({"altitude": {"name": "altitude", "path": str(cls.repo)}})
        for name in ("running-one", "locked-one", "finished-one"):
            git("worktree", "add", "-q", "-b", f"worktree-{name}", f".claude/worktrees/{name}", "origin/main", cwd=cls.repo)
        git("worktree", "lock", ".claude/worktrees/locked-one", cwd=cls.repo)
        S.task_dir("altitude", "running-one").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "running-one", "title": "running-one", "created": S.now(), "updated": S.now(), "state": "running", "worktree": str(cls.repo / ".claude/worktrees/running-one")})

    def test_only_unowned_merged_worktrees_go(self):
        engines.claude_rm, engines.claude_agents = (lambda aid: "removed"), (lambda: [])
        done = {"slug": "finished-one", "state": "done", "agent_id": "x", "worktree": str(self.repo / ".claude/worktrees/finished-one")}
        notes = dispatch.cleanup_after_done("altitude", done)
        left = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertIn("running-one", left, notes)   # zero-commit branch is "merged" but a running task owns it
        self.assertIn("locked-one", left, notes)
        self.assertNotIn("finished-one", left, notes)
        self.assertIn("removed merged worktree finished-one", notes)

    def test_unreadable_session_list_removes_nothing(self):
        git("worktree", "add", "-q", "-b", "worktree-orphan", ".claude/worktrees/orphan", "origin/main", cwd=self.repo)
        def boom(): raise RuntimeError("claude agents down")
        engines.claude_rm, engines.claude_agents = (lambda aid: "removed"), boom
        notes = dispatch.cleanup_after_done("altitude", {"slug": "z", "title": "z", "state": "done", "agent_id": "x", "updated": S.now()})
        self.assertIn("orphan", git("worktree", "list", "--porcelain", cwd=self.repo))
        self.assertTrue(any("skipped worktree cleanup" in n for n in notes), notes)


class TestSelfDeploy(unittest.TestCase):
    def test_pull_after_done_fast_forwards_and_flags_code_changes(self):
        repo = TestCleanupScope.repo
        config.save_projects({"altitude": {"name": "altitude", "path": str(repo), "self_deploy": True}})
        other = Path(_TMP) / "other"; git("clone", "-q", "-b", "main", str(Path(_TMP) / "origin.git"), str(other), cwd=_TMP)
        (other / "altitude").mkdir(exist_ok=True); (other / "altitude" / "x.py").write_text("# new\n")
        (other / "hooks").mkdir(exist_ok=True); (other / "hooks" / "h.py").write_text("# hook\n")
        git("add", "-A", cwd=other); git("commit", "-qm", "code + hook", cwd=other); git("push", "-q", "origin", "main", cwd=other)
        S.task_dir("altitude", "landed").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "landed", "title": "landed", "state": "done", "created": S.now(), "updated": S.now()})
        notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertTrue((repo / "hooks" / "h.py").exists(), notes)          # hooks deploy by the pull itself
        pend = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {})
        self.assertEqual(pend.get("files"), ["altitude/x.py"], notes)       # python needs a restart: flagged, not done
        self.assertTrue(any("restart pending" in n for n in notes), notes)
        self.assertEqual(dispatch.pull_after_done("altitude", {"slug": "landed"}), [])  # nothing new → silent

    def test_not_self_deploy_projects_are_untouched(self):
        config.save_projects({"other": {"name": "other", "path": str(Path(_TMP) / "nowhere")}})
        self.assertEqual(dispatch.pull_after_done("other", {"slug": "x"}), [])

    def test_self_deploy_refuses_an_ahead_main(self):
        repo = TestCleanupScope.repo
        config.save_projects({"altitude": {"name": "altitude", "path": str(repo), "self_deploy": True}})
        (repo / "direct.txt").write_text("must not deploy\n")
        git("add", "direct.txt", cwd=repo)
        git("commit", "-qm", "direct main commit", cwd=repo)
        head = git("rev-parse", "HEAD", cwd=repo).strip()

        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.pull_after_done("altitude", {"slug": "landed"})

        self.assertEqual(git("rev-parse", "HEAD", cwd=repo).strip(), head)
        self.assertTrue(any("self-deploy refused" in note for note in notes), notes)
        fault.assert_called_once()


if __name__ == "__main__":
    unittest.main()
