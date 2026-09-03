"""cleanup_after_done removes a task's worktree once its branch is on origin/main and nothing still uses it (real git)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-cleanup-")
os.environ["ALTITUDE_HOME"] = _TMP
os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch  # noqa: E402


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def make_repo(name: str) -> Path:
    """A repo with one commit, a bare origin, and .claude/ ignored."""
    root = Path(tempfile.mkdtemp(prefix=f"altitude-{name}-", dir=_TMP))
    repo = root / "repo"; repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / ".gitignore").write_text(".claude/\n"); (repo / "doc.md").write_text("before\n")
    git("add", "-A", cwd=repo); git("commit", "-qm", "base", cwd=repo)
    git("init", "-q", "--bare", str(root / "origin.git"), cwd=repo)
    git("remote", "add", "origin", str(root / "origin.git"), cwd=repo); git("push", "-q", "origin", "main", cwd=repo)
    return repo


class TestCleanup(unittest.TestCase):
    def setUp(self):
        config.ensure_root()
        self.repo = make_repo("cleanup")
        self.project = f"p-{self.repo.parent.name}"
        config.save_projects({self.project: {"name": self.project, "path": str(self.repo)}})
        for patch in (mock.patch.object(engines, "claude_agents", return_value=[]),
                      mock.patch.object(engines, "remove_l2_worker", return_value="removed")):
            patch.start(); self.addCleanup(patch.stop)

    def task(self, slug="task-one", **extra) -> dict:
        wt, branch = self.repo / ".claude" / "worktrees" / slug, f"worktree-{slug}"
        git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=self.repo)
        (wt / "doc.md").write_text(f"after {slug}\n"); git("commit", "-qam", "task change", cwd=wt)
        S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
        task = {"slug": slug, "title": slug, "state": "done", "agent_id": "a1", "worktree": str(wt), "branch": branch,
                "created": S.now(), "updated": S.now(), **extra}
        S.save_task(self.project, task)
        return task

    def merge(self, branch, *, squash=False):
        if squash:
            git("merge", "--squash", branch, cwd=self.repo); git("commit", "-qm", "squash", cwd=self.repo)
        else:
            git("merge", "--no-ff", "-qm", "merge", branch, cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)

    def listed(self, task) -> bool:
        return task["worktree"] in git("worktree", "list", "--porcelain", cwd=self.repo)

    def branch_exists(self, task) -> bool:
        return f"refs/heads/{task['branch']}" in git("show-ref", "--heads", cwd=self.repo)

    def last_event(self, slug):
        return [e for e in S.read_events(self.project, slug) if e.get("kind") == "cleanup-worktree"][-1]

    def test_merged_branch_is_removed_with_its_worker(self):
        task = self.task(); self.merge(task["branch"])
        notes = dispatch.cleanup_after_done(self.project, task)
        self.assertFalse(self.listed(task), notes); self.assertFalse(self.branch_exists(task))
        engines.remove_l2_worker.assert_called_once()
        self.assertEqual(self.last_event(task["slug"])["action"], "removed")
        self.assertIn("removed merged worktree task-one", notes)

    def test_squash_merge_needs_a_verified_merged_pr(self):
        task = self.task(verified={"verdict": "ok", "prs": [7]}); self.merge(task["branch"], squash=True)
        tip = git("rev-parse", task["branch"], cwd=self.repo).strip()
        with mock.patch.object(dispatch, "_pr_merged_at", return_value=False):
            notes = dispatch.cleanup_after_done(self.project, task)
        self.assertTrue(self.listed(task), notes)
        self.assertIn("branch is not on origin/main", self.last_event(task["slug"])["reason"])
        with mock.patch.object(dispatch, "_pr_merged_at", return_value=True) as receipt:
            dispatch.cleanup_after_done(self.project, task)
        self.assertFalse(self.listed(task))
        self.assertEqual(receipt.call_args.args[2], tip)  # asked about the branch tip, not main

    def test_unmerged_dirty_and_live_worktrees_are_kept(self):
        unmerged = self.task("unmerged")
        dirty = self.task("dirty"); self.merge(dirty["branch"]); (Path(dirty["worktree"]) / "notes.txt").write_text("mine\n")
        live = self.task("live"); self.merge(live["branch"])
        for task, reason in ((unmerged, "not on origin/main"), (dirty, "uncommitted changes")):
            notes = dispatch.cleanup_after_done(self.project, task)
            self.assertTrue(self.listed(task) and self.branch_exists(task), notes)
            self.assertIn(reason, self.last_event(task["slug"])["reason"])
            self.assertTrue(any(reason in n for n in notes), notes)
        with mock.patch.object(engines, "worker_live", return_value=True):
            notes = dispatch.cleanup_after_done(self.project, live)
        self.assertTrue(self.listed(live), notes)
        self.assertIn("still running", self.last_event("live")["reason"])
        engines.remove_l2_worker.assert_not_called()
        self.assertTrue((Path(dirty["worktree"]) / "notes.txt").exists())

    def test_no_worktree_means_nothing_to_clean(self):
        self.assertEqual(dispatch.cleanup_after_done(self.project, {"slug": "x", "state": "done"}), [])


class TestSelfDeploy(unittest.TestCase):
    def setUp(self):
        config.ensure_root()
        self.repo = make_repo("deploy")
        config.save_projects({"altitude": {"name": "altitude", "path": str(self.repo), "self_deploy": True}})
        S.task_dir("altitude", "landed").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "landed", "title": "landed", "state": "done", "created": S.now(), "updated": S.now()})

    def test_pull_after_done_fast_forwards_and_flags_code_changes(self):
        other = self.repo.parent / "other"; git("clone", "-q", "-b", "main", str(self.repo.parent / "origin.git"), str(other), cwd=_TMP)
        (other / "altitude").mkdir(); (other / "altitude" / "x.py").write_text("# new\n")
        (other / "hooks").mkdir(); (other / "hooks" / "h.py").write_text("# hook\n")
        git("add", "-A", cwd=other); git("commit", "-qm", "code + hook", cwd=other); git("push", "-q", "origin", "main", cwd=other)
        notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertTrue((self.repo / "hooks" / "h.py").exists(), notes)          # hooks deploy by the pull itself
        pend = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {})
        self.assertEqual(pend.get("files"), ["altitude/x.py"], notes)            # python needs a restart: flagged, not done
        self.assertTrue(any("restart pending" in n for n in notes), notes)
        self.assertEqual(dispatch.pull_after_done("altitude", {"slug": "landed"}), [])  # nothing new → silent

    def test_not_self_deploy_projects_are_untouched(self):
        config.save_projects({"other": {"name": "other", "path": str(Path(_TMP) / "nowhere")}})
        self.assertEqual(dispatch.pull_after_done("other", {"slug": "x"}), [])

    def test_self_deploy_refuses_an_ahead_main(self):
        (self.repo / "direct.txt").write_text("must not deploy\n")
        git("add", "direct.txt", cwd=self.repo); git("commit", "-qm", "direct main commit", cwd=self.repo)
        head = git("rev-parse", "HEAD", cwd=self.repo).strip()
        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), head)
        self.assertTrue(any("self-deploy refused" in note for note in notes), notes)
        fault.assert_called_once()


if __name__ == "__main__":
    unittest.main()
