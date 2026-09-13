"""#252: existing PR history through the real CLI, Git, task state, resume guard and landing gate."""
import json
import os
import subprocess
import sys
from unittest import mock

from tests.support import ALT, AltitudeCase, add_worktree, git, make_repo
from tests import test_land as ordinary
from altitude import dispatch, engines, land, state as S, tasks as T


class TestAdoption(AltitudeCase):
    git = ordinary.TestLand.git
    staged_change = ordinary.TestLand.staged_change
    fake_runner = ordinary.TestLand.fake_runner
    record_commands = ordinary.TestLand.record_commands

    def setUp(self):
        super().setUp()
        self.ghdir = self.fake_gh()
        self.register("demo", path=self.repo)
        self.project_repo = self.repo
        make_repo(self.repo)
        self.remote = self.tmp / "origin.git"
        # Git's transport stays local; gh must still receive the canonical origin repository.
        self.git("config", f"url.{self.remote}.insteadOf", "https://github.com/team/demo.git")
        self.git("remote", "set-url", "origin", "https://github.com/team/demo.git")
        self.repo = add_worktree(self.project_repo, "fix-x")
        task = {"slug": "fix-x", "title": "Reconcile existing proposal", "state": "running", "attempt": 1,
                "branch": "worktree-fix-x",
                "worktree": str(self.repo), "paths": ["src", "docs/NOTES.md"]}
        S.save_task("demo", task)
        for key, value in {"ALTITUDE_PROJECT": "demo", "ALTITUDE_TASK": "fix-x",
                           "ALTITUDE_ACTOR": "l2", "ALTITUDE_ATTEMPT": "1"}.items():
            self.setenv(key, value)
        self.original = self.commit("docs/NOTES.md", "Original external proposal")
        self.git("push", "-q", "origin", "HEAD:proposal/external")
        self.pull = {"number": 101, "url": "https://github.com/team/demo/pull/101", "state": "OPEN",
                     "baseRefName": "main", "headRefName": "proposal/external",
                     "isCrossRepository": False, "isDraft": False, "reviewDecision": ""}
        self.write_pr()

    def commit(self, path, message, trailer=None):
        self.staged_change(path)
        args = ["commit", "-q", "-m", message]
        if trailer:
            args += ["-m", f"Altitude-Task: {trailer}"]
        self.git(*args)
        return self.git("rev-parse", "HEAD").strip()

    def write_pr(self, **updates):
        self.pull.update(updates)
        S.write_json(self.ghdir / "pr.json", self.pull)

    def adopt(self, **kwargs):
        return land.land("docs: reconcile proposal", cwd=self.repo, wait=0, adopt_pr=101,
                         expected_head=self.original, reason="Assigned reconciliation of existing proposal", **kwargs)

    def receipt(self):
        return S.load_task("demo", "fix-x").get("adopted_pr")

    def next_pr(self):
        """Finish the first hosted PR, then incorporate a separately assigned external PR."""
        (self.ghdir / "merge_git.txt").touch()
        self.adopt(merge=True)
        previous = land._pr_view(self.repo, "101")
        S.write_json(self.ghdir / "prs.json", {"101": previous})
        self.git("merge", "--ff-only", "origin/main")
        head = self.commit("src/second.py", "Second external proposal")
        self.git("push", "-q", "origin", "HEAD:proposal/second")
        self.pull = {**self.pull, "number": 102, "url": "https://github.com/team/demo/pull/102",
                     "state": "OPEN", "headRefName": "proposal/second"}
        self.write_pr()
        return {"adopt_pr": 102, "expected_head": head, "reason": "This task is also assigned PR 102"}

    def test_sequential_authorized_adoption_preserves_receipts_and_targets_active_pr(self):
        next_pr = self.next_pr()
        first = self.receipt()
        event = [e for e in S.read_events("demo", "fix-x") if e["kind"] == "pr-adopted"][0]
        commands = self.record_commands()
        result = land.land("second proposal", cwd=self.repo, wait=0, dry_run=True, **next_pr)
        self.assertEqual(result["adopted_pr"]["number"], 102)
        self.assertEqual(self.receipt(), first)
        self.assertNotIn("adoption_history", S.load_task("demo", "fix-x"))
        result = land.land("second proposal", cwd=self.repo, wait=0, **next_pr)
        self.assertEqual(result["pr"], 102)
        second = self.receipt()
        task = S.load_task("demo", "fix-x")
        self.assertEqual(task["adoption_history"], [first])
        self.assertEqual(task["prs"], [101, 102])
        self.assertEqual(second["previous_merge"], self.git("rev-parse", "origin/main").strip())
        dispatch._validate_task_worktree(self.project_repo, "demo", "fix-x", self.repo,
                                        require_clean=True)
        land.land("retry second proposal", cwd=self.repo, wait=0, **next_pr)
        self.assertEqual(self.receipt(), second)
        events = [e for e in S.read_events("demo", "fix-x") if e["kind"] == "pr-adopted"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], event)
        with self.assertRaisesRegex(land.LandError, "earlier adoption"):
            self.adopt()
        merged = land.land("merge second proposal", cwd=self.repo, wait=0, merge=True)
        self.assertEqual((merged["pr"], merged["merged"]), (102, True))
        self.git("merge-base", "--is-ancestor", first["head"], "origin/main")
        self.git("merge-base", "--is-ancestor", second["head"], "origin/main")
        self.assertEqual(S.load_task("demo", "fix-x")["adoption_history"], [first])
        for args in commands:
            if args[:2] == ["git", "push"]:
                self.assertIn("refs/heads/worktree-fix-x:refs/heads/proposal/second", args)
                self.assertFalse(any("--force" in arg for arg in args))

    def test_continuation_refuses_incomplete_or_unverified_previous_delivery(self):
        self.adopt()
        first = self.receipt()
        for update in ({"state": "OPEN"}, {"state": "CLOSED"}, {"state": "MERGED"},
                       {"state": "MERGED", "mergeCommit": {"oid": "0" * 40}}):
            with self.subTest(update=update):
                self.write_pr(**update)
                with self.assertRaisesRegex(land.LandError, "previous adoption"):
                    land.land("next", cwd=self.repo, adopt_pr=102, expected_head=self.original, reason="assigned")
                self.assertEqual(self.receipt(), first)
                self.assertNotIn("adoption_history", S.load_task("demo", "fix-x"))

    def test_concurrent_next_adoption_keeps_receipts(self):
        next_pr = self.next_pr()
        first = self.receipt()
        real = land._record_adoption
        def race(*args, **kwargs):
            task = S.load_task("demo", "fix-x")
            task["adopted_pr"] = {**first, "number": 103}
            S.save_task("demo", task)
            return real(*args, **kwargs)
        with mock.patch.object(land, "_record_adoption", side_effect=race), self.assertRaisesRegex(
                land.LandError, "adoption changed"):
            land.land("next", cwd=self.repo, wait=0, **next_pr)
        self.assertNotIn("adoption_history", S.load_task("demo", "fix-x"))

    def test_continuation_preserves_hold_with_later_manual_history(self):
        next_pr = self.next_pr()
        task = S.load_task("demo", "fix-x")
        task["hold_merge"] = "Review the next PR"
        S.save_task("demo", task)
        land.land("next", cwd=self.repo, wait=0, **next_pr)
        self.assertEqual(S.load_task("demo", "fix-x")["hold_merge"], "Review the next PR")
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("merge next", cwd=self.repo, wait=0, merge=True)
        head = self.commit("src/later.py", "Reviewed later update")
        self.assertEqual(land.land("next", cwd=self.repo, wait=0)["pr"], 102)
        dispatch._validate_task_worktree(self.project_repo, "demo", "fix-x", self.repo, require_clean=True)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("merge next", cwd=self.repo, wait=0, merge=True)

    def test_previous_pr_approval_restores_hold_on_next_adoption_even_with_merge(self):
        next_pr = self.next_pr()
        task = S.load_task("demo", "fix-x")
        task.update(hold_merge=None, hold_merge_id="first-hold",
                    merge_approval={"pr": 101, "hold_id": "first-hold", "hold": "Review each PR"})
        S.save_task("demo", task)
        with self.assertRaisesRegex(land.LandError, "Review each PR"):
            land.land("next", cwd=self.repo, wait=0, merge=True, **next_pr)
        current = S.load_task("demo", "fix-x")
        self.assertEqual(current["adopted_pr"]["number"], 102)
        self.assertEqual(current["hold_merge"], "Review each PR")
        self.assertEqual(current["hold_merge_id"], "first-hold")
        hold = [e for e in S.read_events("demo", "fix-x") if e["kind"] == "hold-merge"][-1]
        self.assertEqual(hold["hold_id"], current["hold_merge_id"])
        T.set_hold_merge("demo", "fix-x", None, actor="burak")
        land.land("retry", cwd=self.repo, wait=0, **next_pr)
        self.assertIsNone(S.load_task("demo", "fix-x")["hold_merge"])

    def test_explicit_later_task_wide_release_survives_next_adoption(self):
        next_pr = self.next_pr()
        task = S.load_task("demo", "fix-x")
        task.update(hold_merge=None, hold_merge_id="first-hold",
                    merge_approval={"pr": 101, "hold_id": "first-hold", "hold": "Review each PR"})
        S.save_task("demo", task)
        T.set_hold_merge("demo", "fix-x", None, actor="burak")
        result = land.land("next", cwd=self.repo, wait=0, merge=True, **next_pr)
        self.assertTrue(result["merged"])
        self.assertIsNone(S.load_task("demo", "fix-x")["hold_merge"])

    def test_stale_base_metadata_uses_current_main_and_preserves_real_history(self):
        old_base = self.git("rev-parse", "origin/main").strip()
        (self.project_repo / "base.txt").write_text("main advances independently\n")
        git("add", "base.txt", cwd=self.project_repo)
        git("commit", "-q", "-m", "Main advances", cwd=self.project_repo)
        git("push", "-q", "origin", "main", cwd=self.project_repo)
        self.git("fetch", "origin", "main")
        new_base = self.git("rev-parse", "origin/main").strip()
        self.git("merge", "--no-ff", "origin/main", "-m", "Incorporate main")
        head = self.git("rev-parse", "HEAD").strip()
        self.git("push", "-q", "origin", "HEAD:proposal/external")
        self.original = head
        self.write_pr(baseRefOid=old_base, baseRef={"target": {"oid": new_base}})
        (self.ghdir / "merge_git.txt").touch()
        result = self.adopt(merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(self.git("show", "-s", "--format=%P", "origin/main").strip(), f"{new_base} {head}")

    def assert_unpublished(self, selected_index=None):
        self.assertIsNone(self.receipt())
        self.assertEqual(self.git("write-tree"), selected_index or self.git("rev-parse", "HEAD^{tree}"))
        self.assertFalse(any(a[:2] in (["pr", "merge"], ["pr", "create"], ["pr", "edit"])
                             for a in self.gh_log()))
        self.assertEqual(git("rev-parse", "proposal/external", cwd=self.remote).strip(), self.original)

    def test_cli_dry_run_then_task_additions_resume_and_history_preserving_merge(self):
        self.commit("src/already.py", "Existing task addition", "demo/fix-x")
        self.staged_change()
        run = subprocess.run([sys.executable, str(ALT), "land", "--message", "reconcile", "--adopt-pr", "101",
                              "--expected-head", self.original, "--reason", "Assigned existing PR", "--dry-run"],
                             cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        preview = json.loads(run.stdout)
        self.assertEqual(preview["adopted_pr"]["head"], self.original)
        self.assertEqual(preview["staged"], ["src/thing.py"])
        self.assert_unpublished(self.selected_index)
        commands = self.record_commands()
        result = self.adopt()
        receipt = self.receipt()
        self.assertEqual((receipt["head"], receipt["actor"], receipt["attempt"]), (self.original, "l2", 1))
        self.assertEqual(S.load_task("demo", "fix-x")["prs"], [101])
        self.assertEqual(result["branch"], "worktree-fix-x")
        self.assertEqual(self.git("log", "-1", "--format=%B").strip(), "docs: reconcile proposal")
        self.assertEqual(self.git("show", "-s", "--format=%B", self.original).strip(), "Original external proposal")
        dispatch._validate_task_worktree(self.project_repo, "demo", "fix-x", self.repo,
                                        require_clean=True)
        self.adopt()  # replay cannot change the assignment or write a second adoption event
        self.assertEqual(self.receipt(), receipt)
        self.assertEqual(len([e for e in S.read_events("demo", "fix-x") if e["kind"] == "pr-adopted"]), 1)
        # Hosted merge advances a real bare origin. The local worktree branch stays task-owned.
        (self.ghdir / "merge_git.txt").touch()
        result = land.land("merge reviewed proposal", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.git("merge-base", "--is-ancestor", self.original, "origin/main")
        self.assertEqual(self.git("branch", "--show-current").strip(), "worktree-fix-x")
        self.assertIn("proposal/external", git("branch", cwd=self.remote))
        self.assertNotIn("worktree-fix-x", git("branch", cwd=self.remote))
        pushes = [a for a in commands if a[:2] == ["git", "push"]]
        self.assertTrue(pushes)
        self.assertFalse(any("--force" in arg for args in pushes for arg in args))
        merges = [a for a in self.gh_log() if a[:2] == ["pr", "merge"]]
        self.assertEqual(len(merges), 1)
        self.assertIn("--merge", merges[0])
        self.assertNotIn("--delete-branch", merges[0])
        self.assertNotIn("--squash", merges[0])
        self.assertFalse(any(a[:2] == ["pr", "create"] for a in self.gh_log()))
        pushes_before = len([a for a in commands if a[:2] == ["git", "push"]])
        again = land.land("confirm merged result", cwd=self.repo, wait=0)
        self.assertEqual(again["checks"], "merged")
        self.assertTrue(again["merged"])
        self.assertEqual(len([a for a in commands if a[:2] == ["git", "push"]]), pushes_before)
        self.staged_change("src/late.py")
        next_delivery = land.land("late edit", cwd=self.repo, wait=0)
        self.assertEqual(next_delivery["pr"], 102)
        current = S.load_task("demo", "fix-x")
        self.assertIsNone(current.get("adopted_pr"))
        self.assertEqual(current["adoption_history"][-1]["head"], self.original)
        self.assertEqual(current["prs"], [101, 102])

    def test_only_explicit_observed_head_adopts(self):
        for kwargs, error in [({"expected_head": self.original}, "require --adopt-pr"),
                              ({"adopt_pr": 101}, "requires a positive"),
                              ({"adopt_pr": 101, "expected_head": "0" * 40, "reason": "assigned"}, "head changed")]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(land.LandError, error):
                land.land("reconcile", cwd=self.repo, wait=0, **kwargs)
        self.assert_unpublished()

    def test_unauthorized_actor_attempt_and_checkout_refuse_before_github(self):
        for env in ({"ALTITUDE_ACTOR": "l3"}, {"ALTITUDE_ATTEMPT": "2"}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env), self.assertRaises(land.LandError):
                self.adopt()
        with mock.patch.object(land.config, "project_path", return_value=self.repo), self.assertRaisesRegex(
                land.LandError, "isolated worktree"):
            self.adopt()
        self.assertEqual(self.gh_log(), [])
        self.assert_unpublished()

    def test_pr_identity_refusals(self):
        for updates in ({"state": "CLOSED"}, {"state": "MERGED"}, {"isCrossRepository": True},
                        {"isCrossRepository": None}, {"baseRefName": "release"}, {"number": 102},
                        {"url": "https://github.com/other/repo/pull/101"},
                        {"headRefName": "worktree-other"}, {"headRefName": "main"}):
            original = dict(self.pull)
            with self.subTest(updates=updates), self.assertRaises(land.LandError):
                self.write_pr(**updates)
                self.adopt()
            self.pull = original
        self.assert_unpublished()

    def test_inherited_gh_repo_cannot_redirect_adoption(self):
        self.setenv("GH_REPO", "unrelated/repository")
        self.adopt(dry_run=True)
        self.assertTrue(self.gh_log())
        for args in self.gh_log():
            self.assertEqual(args[args.index("--repo") + 1], "team/demo")

    def test_original_history_with_old_foreign_labels_is_adopted_unchanged(self):
        self.original = self.commit("src/foreign.py", "Reviewed assigned history", "demo/other")
        self.git("push", "-q", "origin", "HEAD:proposal/external")
        self.assertEqual(self.adopt()["pr"], 101)
        self.assertEqual(self.git("show", "-s", "--format=%B", self.original).strip(),
                         "Reviewed assigned history\n\nAltitude-Task: demo/other")
        self.assertEqual(self.receipt()["head"], self.original)

    def test_later_manual_and_old_label_history_lands_and_resumes_unchanged(self):
        self.adopt()
        for index, trailer in enumerate((None, "demo/other", "another/fix-x")):
            with self.subTest(trailer=trailer):
                head = self.commit(f"src/later-{index}.py", "Reviewed reconciliation", trailer)
                message = self.git("show", "-s", "--format=%B", head)
                land.land("reconcile", cwd=self.repo, wait=0)
                dispatch._validate_task_worktree(self.project_repo, "demo", "fix-x", self.repo,
                                                require_clean=False)
                self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
                self.assertEqual(git("show", "-s", "--format=%B", head, cwd=self.remote), message)

    def test_rewritten_or_unrelated_head_and_widened_adoption_refused(self):
        self.adopt()
        later = self.commit("src/new.py", "Owned update", "demo/fix-x")
        with self.assertRaisesRegex(land.LandError, "immutable"):
            land.land("re-adopt", cwd=self.repo, adopt_pr=101, expected_head=later, reason="broader")
        self.git("reset", "--hard", "origin/main")
        self.commit("src/unrelated.py", "Unrelated branch", "demo/fix-x")
        with self.assertRaisesRegex(land.LandError, "not an ancestor"):
            land.land("reconcile", cwd=self.repo, wait=0)
        with self.assertRaisesRegex(T.TransitionError, "not an ancestor"):
            dispatch._validate_task_worktree(self.project_repo, "demo", "fix-x", self.repo,
                                            require_clean=True)

    def test_adoption_and_selected_additions_do_not_require_predicted_paths(self):
        task = S.load_task("demo", "fix-x")
        task["paths"] = []
        S.save_task("demo", task)
        self.staged_change("needed.txt")
        result = self.adopt()
        self.assertEqual(result["pr"], 101)
        self.assertEqual(result["staged"], ["needed.txt"])
        self.git("merge-base", "--is-ancestor", self.original, "HEAD")
        self.assertEqual(self.git("show", "HEAD:needed.txt"), "changed\n")

    def test_another_task_cannot_adopt_the_same_pr(self):
        S.save_task("demo", {"slug": "other", "state": "running", "adopted_pr":
                             {"number": 101, "branch": "proposal/external"}})
        for dry_run in (True, False):
            with self.assertRaisesRegex(land.LandError, "already belongs"):
                self.adopt(dry_run=dry_run)
        self.assert_unpublished()

    def test_rejected_push_never_retries_with_force(self):
        commands = self.record_commands(lambda args: subprocess.CompletedProcess(args, 1, "", "[rejected]")
                                        if args[:2] == ["git", "push"] else None)
        with self.assertRaisesRegex(land.LandError, "only fast-forward"):
            self.adopt()
        self.assertEqual(len([a for a in commands if a[:2] == ["git", "push"]]), 1)

    def test_real_remote_update_racing_push_preserves_both_histories(self):
        other = self.tmp / "concurrent"
        git("clone", "-q", str(self.remote), str(other), cwd=self.tmp)
        git("checkout", "-q", "proposal/external", cwd=other)
        (other / "src").mkdir()
        (other / "src" / "remote.py").write_text("concurrent update\n")
        git("add", "src/remote.py", cwd=other)
        git("commit", "-q", "-m", "Concurrent task update", cwd=other)
        remote_head = git("rev-parse", "HEAD", cwd=other).strip()
        def race(args):
            if args[:2] == ["git", "push"]:
                git("push", "-q", "origin", "proposal/external", cwd=other)
        commands = self.record_commands(race)
        self.staged_change()
        with self.assertRaisesRegex(land.LandError, "only fast-forward"):
            self.adopt()
        self.assertEqual(git("rev-parse", "proposal/external", cwd=self.remote).strip(), remote_head)
        self.git("merge-base", "--is-ancestor", self.original, "HEAD")
        self.assertEqual(len([a for a in commands if a[:2] == ["git", "push"]]), 1)

    def test_checks_and_reviews_and_hold_still_gate_merge(self):
        self.adopt()
        for bucket in ("fail", "pending", "skipping"):
            S.write_json(self.ghdir / "checks.json", [{"bucket": bucket}])
            result = land.land("reviewed proposal", cwd=self.repo, merge=True, wait=0)
            self.assertFalse(result["merged"])
        S.write_json(self.ghdir / "checks.json", [{"bucket": "pass"}])
        for updates in ({"isDraft": True}, {"isDraft": False, "reviewDecision": "CHANGES_REQUESTED"},
                        {"reviewDecision": "REVIEW_REQUIRED"}, {"reviewDecision": None}):
            self.write_pr(**updates)
            with self.assertRaisesRegex(land.LandError, "review-ready"):
                land.land("reviewed proposal", cwd=self.repo, merge=True, wait=0)
        task = S.load_task("demo", "fix-x")
        task["hold_merge"] = "Operator review"
        S.save_task("demo", task)
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("reviewed proposal", cwd=self.repo, merge=True, wait=0)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_no_ci_suite_gets_two_parent_candidate_with_original_ancestry(self):
        self.staged_change()
        S.write_json(self.ghdir / "checks.json", [])
        self.fake_runner("adoption-suite", script=f"""
import subprocess
parents = subprocess.check_output(['git', 'rev-list', '--parents', '-n', '1', 'HEAD'], text=True).split()
assert len(parents) == 3, parents
subprocess.run(['git', 'merge-base', '--is-ancestor', {self.original!r}, 'HEAD'], check=True)
assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
print('3 passed')
""")
        result = self.adopt(merge=True, test_cmd="adoption-suite")
        self.assertTrue(result["merged"])
        self.assertTrue(result["local_tests"]["passed"])

    def test_hold_set_during_checks_is_reloaded_before_merge(self):
        original = land._checks_state
        def hold(*args):
            task = S.load_task("demo", "fix-x")
            task["hold_merge"] = "Late operator hold"
            S.save_task("demo", task)
            return original(*args)
        with mock.patch.object(land, "_checks_state", side_effect=hold), self.assertRaisesRegex(
                land.LandError, "Late operator hold"):
            self.adopt(merge=True)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_retarget_to_same_sha_base_during_checks_refuses_merge(self):
        self.git("push", "-q", "origin", "origin/main:refs/heads/release")
        original = land._checks_state
        def retarget(*args):
            self.write_pr(baseRefName="release")
            return original(*args)
        with mock.patch.object(land, "_checks_state", side_effect=retarget), self.assertRaisesRegex(
                land.LandError, "base or head moved"):
            self.adopt(merge=True)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_hold_recorded_during_final_pr_observation_is_respected(self):
        original = land._pr_view
        def observe(root, target):
            pr = original(root, target)
            if any(a[:2] == ["pr", "checks"] for a in self.gh_log()):
                task = S.load_task("demo", "fix-x")
                task["hold_merge"] = "Hold during final observation"
                S.save_task("demo", task)
            return pr
        with mock.patch.object(land, "_pr_view", side_effect=observe), self.assertRaisesRegex(
                land.LandError, "Hold during final observation"):
            self.adopt(merge=True)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_canonical_github_url_may_differ_in_case_from_origin(self):
        self.write_pr(url="https://github.com/Team/Demo/pull/101")
        self.adopt()
        self.assertEqual(self.receipt()["url"], "https://github.com/Team/Demo/pull/101")

    def test_main_can_be_merged_without_adopting_unrelated_main_paths(self):
        self.adopt()
        git("checkout", "-q", "main", cwd=self.project_repo)
        (self.project_repo / "another-task.txt").write_text("other task merged\n")
        git("add", "another-task.txt", cwd=self.project_repo)
        git("commit", "-q", "-m", "another task landed", cwd=self.project_repo)
        git("push", "-q", "origin", "main", cwd=self.project_repo)
        self.git("fetch", "-q", "origin", "main")
        self.git("merge", "--no-ff", "origin/main", "-m", "Merge main")
        result = land.land("reconcile", cwd=self.repo, wait=0)
        self.assertEqual(result["checks"], "pass")
        self.git("merge-base", "--is-ancestor", self.original, "HEAD")

    def test_message_resume_keeps_adoption_and_original_session(self):
        task = S.load_task("demo", "fix-x")
        task.update(state="blocked", session_id="existing-thread", agent_id="old-worker", l2_engine="codex",
                    blocked_reason="Waiting for review", waiting_on="l3")
        S.save_task("demo", task)
        with self.assertRaisesRegex(land.LandError, "task is not running"):
            self.adopt()
        # Initial adoption of already-blocked history uses existing operator landing authority.
        with mock.patch.dict(os.environ, {"ALTITUDE_ACTOR": "burak"}):
            self.adopt()
        task = S.load_task("demo", "fix-x")
        self.assertEqual((task["state"], task["session_id"]), ("blocked", "existing-thread"))
        self.private_ledgers()
        self.quiet_engines()
        message = T.message("demo", "fix-x", "l3", "Review is ready", by="l3")
        with mock.patch.object(engines, "resume_l2", return_value={"returncode": 0, "agent": {
                "id": "resumed-worker", "sessionId": "existing-thread", "state": "working"}}) as launch:
            dispatch.resume("demo", "fix-x")
        launch.assert_called_once()
        self.assertIn("Review is ready", launch.call_args.args[3])
        current = S.load_task("demo", "fix-x")
        self.assertEqual((current["state"], current["session_id"], current["attempt"]), ("running", "existing-thread", 1))
        self.assertEqual(current["adopted_pr"], task["adopted_pr"])
        self.assertNotIn(message["id"], [row["id"] for row in T.pending("demo", "fix-x")])
