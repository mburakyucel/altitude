"""Post-merge continuation keeps local work recoverable across each publication boundary."""
import copy
import json
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import dispatch, land, state as S, tasks as T, verify


class TestLandContinuation(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.register("demo", path=self.repo)
        self.project_repo = self.repo
        self.remote = self.tmp / "origin.git"
        git("config", f"url.{self.remote}.insteadOf", "https://github.com/team/demo.git", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        self.repo = add_worktree(self.repo, "fix-x")
        S.save_task("demo", {"slug": "fix-x", "title": "Continue work", "state": "running", "attempt": 1,
                             "branch": "worktree-fix-x", "worktree": str(self.repo), "paths": ["src/"]})
        for key, value in dispatch.l2_env("demo", "fix-x", 1).items():
            self.setenv(key, value)
        self.ghdir = self.fake_gh()
        (self.ghdir / "merge_git.txt").touch()
        (self.repo / "src").mkdir()
        (self.repo / "src/value").write_text("first\n")
        self.first = land.land("first", cwd=self.repo, merge=True, wait=0)
        self.old_head = self.first["head"]
        self.merge = self.git("rev-parse", "origin/main").strip()

    def git(self, *args):
        return git(*args, cwd=self.repo)

    def followup(self):
        (self.repo / "src/followup").write_text("second\n")

    def land(self, **kwargs):
        return land.land("second", cwd=self.repo, wait=0, **kwargs)

    def task(self):
        return S.load_task("demo", "fix-x")

    def test_committed_only_followup_and_unchanged_retry(self):
        before = copy.deepcopy(self.task())
        self.assertEqual(self.land()["checks"], "merged")
        self.assertEqual(self.task()["delivery"], before["delivery"])
        self.followup()
        self.git("add", "src/followup")
        self.git("commit", "-m", "followup", "-m", "Altitude-Task: demo/fix-x")
        result = self.land(merge=True)
        self.assertEqual((result["pr"], result["merged"]), (102, True))
        self.assertEqual(self.task()["prs"], [101, 102])
        self.assertEqual(self.git("show", "HEAD:src/value"), "first\n")
        self.assertEqual(self.git("show", "-s", "--format=%P", "HEAD").strip(), self.merge)

    def test_retry_after_rebase_or_push_does_not_replay_first_pr(self):
        self.followup()
        with mock.patch.object(land, "_push", side_effect=land.LandError("interrupted before push")):
            with self.assertRaisesRegex(land.LandError, "interrupted"):
                self.land()
        self.assertIsNone(self.task()["delivery"]["number"])
        self.assertEqual(self.git("show", "-s", "--format=%P", "HEAD").strip(), self.merge)
        rebased_head = self.git("rev-parse", "HEAD").strip()
        with mock.patch.object(land, "_ensure_pr", side_effect=land.LandError("interrupted after push")):
            with self.assertRaisesRegex(land.LandError, "interrupted"):
                self.land()
        result = self.land()
        self.assertEqual(result["pr"], 102)
        self.assertEqual(result["head"], rebased_head)
        self.assertEqual(self.git("diff", "--name-only", "origin/main...HEAD").strip(), "src/followup")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_log()), 2)

    def test_conflict_aborts_to_resumable_branch_and_preserves_work(self):
        (self.project_repo / "src").mkdir(exist_ok=True)
        git("merge", "--ff-only", "origin/main", cwd=self.project_repo)
        (self.project_repo / "src/value").write_text("other task\n")
        git("add", "src/value", cwd=self.project_repo)
        git("commit", "-m", "other change", cwd=self.project_repo)
        git("push", "origin", "main", cwd=self.project_repo)
        (self.repo / "src/value").write_text("followup\n")
        self.followup()
        with self.assertRaisesRegex(land.LandError, "continuation could not reconcile.*retained"):
            self.land()
        self.assertEqual(self.git("branch", "--show-current").strip(), "worktree-fix-x")
        self.assertEqual((self.repo / "src/value").read_text(), "followup\n")
        self.assertEqual((self.repo / "src/followup").read_text(), "second\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        dispatch._validate_task_worktree(self.project_repo, "demo", "fix-x", self.repo,
                                        self.git("rev-parse", "origin/main").strip(), require_clean=False)

    def test_raised_rebase_failure_also_aborts(self):
        git("merge", "--ff-only", "origin/main", cwd=self.project_repo)
        (self.project_repo / "src/value").write_text("main changed\n")
        git("add", "src/value", cwd=self.project_repo)
        git("commit", "-m", "main changed", cwd=self.project_repo)
        git("push", "origin", "main", cwd=self.project_repo)
        (self.repo / "src/value").write_text("my change\n")
        real = land._git
        def interrupted(root, *args, **kwargs):
            result = real(root, *args, **kwargs)
            if args[:2] == ("rebase", "--onto"):
                raise land.LandError("simulated timeout during rebase")
            return result
        with mock.patch.object(land, "_git", side_effect=interrupted):
            with self.assertRaisesRegex(land.LandError, "simulated timeout"):
                self.land()
        self.assertEqual(self.git("branch", "--show-current").strip(), "worktree-fix-x")
        self.assertEqual((self.repo / "src/value").read_text(), "my change\n")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_work_already_on_main_repairs_pending_receipt_without_another_pr(self):
        git("merge", "--ff-only", "origin/main", cwd=self.project_repo)
        (self.project_repo / "src/followup").write_text("second\n")
        git("add", "src/followup", cwd=self.project_repo)
        git("commit", "-m", "same change independently merged", cwd=self.project_repo)
        git("push", "origin", "main", cwd=self.project_repo)
        task = self.task()
        task.update(hold_merge=None, hold_merge_id="first-hold",
                    merge_approval={"pr": 101, "hold_id": "first-hold", "hold": "Review each PR"})
        S.save_task("demo", task)
        self.followup()
        result = self.land()
        self.assertEqual((result["pr"], result["checks"]), (101, "merged"))
        self.assertEqual(self.task()["delivery"]["number"], 101)
        self.assertIsNone(self.task()["hold_merge"])
        self.assertEqual(self.git("diff", "origin/main", "HEAD"), "")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_log()), 1)
        receipt = self.task()["delivery"]
        self.assertEqual(self.land()["checks"], "merged")
        self.assertEqual(self.task()["delivery"], receipt)

    def test_followup_cannot_hide_committed_out_of_lease_or_unowned_work(self):
        (self.repo / "outside").write_text("outside\n")
        self.git("add", "outside")
        self.git("commit", "-m", "outside lease", "-m", "Altitude-Task: demo/fix-x")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(land.LandError, "outside the lease"):
            self.land()
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.task()["prs"], [101])
        self.followup()
        self.git("add", "src/followup")
        self.git("commit", "-m", "missing provenance")
        with self.assertRaisesRegex(land.LandError, "without exact.*provenance"):
            self.land()

    def test_merge_resolution_edits_are_preserved_by_refusing_automatic_replay(self):
        self.git("checkout", "-b", "owned-side")
        self.followup()
        self.git("add", "src/followup")
        self.git("commit", "-m", "side", "-m", "Altitude-Task: demo/fix-x")
        self.git("checkout", "worktree-fix-x")
        (self.repo / "src/main-side").write_text("main side\n")
        self.git("add", "src/main-side")
        self.git("commit", "-m", "main side", "-m", "Altitude-Task: demo/fix-x")
        self.git("merge", "--no-commit", "owned-side")
        (self.repo / "src/merge-only").write_text("merge resolution\n")
        self.git("add", "src/merge-only")
        self.git("commit", "-m", "resolve owned merge", "-m", "Altitude-Task: demo/fix-x")
        before = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(land.LandError, "merge-resolution edits"):
            self.land()
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual((self.repo / "src/merge-only").read_text(), "merge resolution\n")
        self.assertEqual(self.task()["prs"], [101])

    def test_resume_claim_invalidates_prior_verification_before_launch(self):
        task = self.task()
        task["verified"] = {"verdict": "ok", "attempt": 1, "delivery": task["delivery"]}
        S.save_task("demo", task)
        T.block("demo", "fix-x", "resume to continue", actor="altd")
        claim = T.claim_resume("demo", "fix-x")
        self.assertIsNotNone(claim)
        self.assertNotIn("verified", self.task())

    def test_prior_pr_approval_restores_hold_and_preserves_question(self):
        question = T.block("demo", "fix-x", "Keep the optional behavior?", actor="l2")["questions"][-1]
        task = self.task()
        task.update(state="running", hold_merge=None, hold_merge_id="first-hold",
                    merge_approval={"pr": 101, "hold_id": "first-hold", "hold": "Review each PR"})
        S.save_task("demo", task)
        self.followup()
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            self.land(merge=True)
        current = self.task()
        self.assertEqual(current["hold_merge"], "Review each PR")
        self.assertNotEqual(current["hold_merge_id"], "first-hold")
        self.assertEqual(current["questions"][-1], question)
        self.assertEqual(current["prs"], [101, 102])
        self.assertEqual(land._pr_view(self.repo, "102")["state"], "OPEN")

    def test_explicit_later_hold_release_applies_to_task(self):
        task = self.task()
        task.update(hold_merge=None, hold_merge_id="later-release",
                    merge_approval={"pr": 101, "hold_id": "first-hold", "hold": "Review each PR"})
        S.save_task("demo", task)
        self.followup()
        self.assertTrue(self.land(merge=True)["merged"])
        self.assertIsNone(self.task()["hold_merge"])

    def test_continuation_clears_old_verification_and_fences_racing_report(self):
        old = self.task()["delivery"]
        task = self.task()
        task.update(verified={"verdict": "ok", "delivery": old}, l3_handled="earlier",
                    completion_requested={"digest": "earlier"})
        S.save_task("demo", task)
        self.followup()
        with mock.patch.object(land, "_push", side_effect=land.LandError("stop before publication")):
            with self.assertRaises(land.LandError):
                self.land()
        for key in ("verified", "l3_handled", "completion_requested"):
            self.assertNotIn(key, self.task())
        with self.assertRaisesRegex(T.TransitionError, "delivery changed"):
            T.report("demo", "fix-x", {"verdict": "ok", "delivery": old})
        with self.assertRaisesRegex(T.TransitionError, "code delivery"):
            T.done("demo", "fix-x", actor="l2")
        T.block("demo", "fix-x", "unfinished work", actor="altd")
        with self.assertRaisesRegex(T.TransitionError, "current delivery requires a verified report"):
            T.done("demo", "fix-x", actor="altd")
