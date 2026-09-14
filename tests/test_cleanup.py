"""cleanup_after_done removes a task's worktree once its branch is on origin/main and nothing still uses it (real git)."""
import unittest
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, dispatch, engines, state as S


class TestCleanup(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        (self.repo / "doc.md").write_text("before\n")
        git("add", "doc.md", cwd=self.repo)
        git("commit", "-qm", "doc", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        self.quiet_engines()
        self.patch(engines, "remove_l2_worker", return_value="removed")

    def task(self, slug="task-one", **extra) -> dict:
        wt, branch = self.repo / ".claude" / "worktrees" / slug, f"worktree-{slug}"
        git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=self.repo)
        (wt / "doc.md").write_text(f"after {slug}\n")
        git("commit", "-qam", "task change", cwd=wt)
        S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
        task = {"slug": slug, "title": slug, "state": "done", "agent_id": "a1", "worktree": str(wt), "branch": branch,
                "created": S.now(), "updated": S.now(), **extra}
        S.save_task(self.project, task)
        return task

    def merge(self, branch, *, squash=False):
        if squash:
            git("merge", "--squash", branch, cwd=self.repo)
            git("commit", "-qm", "squash", cwd=self.repo)
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
        task = self.task()
        self.merge(task["branch"])
        notes = dispatch.cleanup_after_done(self.project, task)
        self.assertFalse(self.listed(task), notes)
        self.assertFalse(self.branch_exists(task))
        engines.remove_l2_worker.assert_called_once()
        self.assertEqual(self.last_event(task["slug"])["action"], "removed")
        self.assertIn("removed merged worktree task-one", notes)

    def test_squash_merge_needs_a_verified_merged_pr(self):
        task = self.task(verified={"verdict": "ok", "prs": [7]})
        self.merge(task["branch"], squash=True)
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
        dirty = self.task("dirty")
        self.merge(dirty["branch"])
        (Path(dirty["worktree"]) / "notes.txt").write_text("mine\n")
        live = self.task("live")
        self.merge(live["branch"])
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


class TestSelfDeploy(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        self.register("altitude", path=self.repo, self_deploy=True)
        S.task_dir("altitude", "landed").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "landed", "title": "landed", "state": "done",
                                 "created": S.now(), "updated": S.now()})

    def test_pull_after_done_fast_forwards_and_flags_code_changes(self):
        other = self.tmp / "other"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(other), cwd=self.tmp)
        (other / "altitude").mkdir()
        (other / "altitude" / "x.py").write_text("# new\n")
        (other / "hooks").mkdir()
        (other / "hooks" / "h.py").write_text("# hook\n")
        git("add", "-A", cwd=other)
        git("commit", "-qm", "code + hook", cwd=other)
        git("push", "-q", "origin", "main", cwd=other)
        notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertTrue((self.repo / "hooks" / "h.py").exists(), notes)
        pend = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {})
        self.assertEqual(pend.get("files"), ["altitude/x.py", "hooks/h.py"], notes)
        self.assertTrue(any("restart pending" in n for n in notes), notes)
        self.assertEqual(dispatch.pull_after_done("altitude", {"slug": "landed"}), [])  # nothing new → silent

    def test_not_self_deploy_projects_are_untouched(self):
        self.register("other", path=self.tmp / "nowhere")
        self.assertEqual(dispatch.pull_after_done("other", {"slug": "x"}), [])

    def test_self_deploy_refuses_an_ahead_main(self):
        (self.repo / "direct.txt").write_text("must not deploy\n")
        git("add", "direct.txt", cwd=self.repo)
        git("commit", "-qm", "direct main commit", cwd=self.repo)
        head = git("rev-parse", "HEAD", cwd=self.repo).strip()
        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), head)
        self.assertTrue(any("self-deploy refused" in note for note in notes), notes)
        fault.assert_called_once()


if __name__ == "__main__":
    unittest.main()
