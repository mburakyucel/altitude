"""Reports retain earlier merges and describe the task's current published work."""
from datetime import datetime, timezone
import json
import os

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import state as S, verify


class TestVerifyDelivery(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.worktree = add_worktree(self.repo, "continue")
        self.ghdir = self.fake_gh()
        self.pulls = {}
        self.task = {"slug": "continue", "state": "running", "attempt": 1,
                     "session_id": "same-owner-conversation", "branch": "worktree-continue",
                     "worktree": str(self.worktree), "prs": []}
        self.report_path = S.task_dir(self.project, "continue") / "report.json"

    def publish(self, number, *, merged=True, branch="worktree-continue"):
        base = git("rev-parse", "main", cwd=self.repo).strip()
        (self.worktree / f"change-{number}").write_text(f"delivery {number}\n")
        git("add", "-A", cwd=self.worktree)
        git("commit", "-qm", f"delivery {number}", cwd=self.worktree)
        head = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        merge_sha = None
        if merged:
            git("merge", "--squash", "worktree-continue", cwd=self.repo)
            git("commit", "-qm", f"squashed delivery {number}", cwd=self.repo)
            merge_sha = git("rev-parse", "HEAD", cwd=self.repo).strip()
            git("push", "-q", "origin", "main", cwd=self.repo)
        self.pulls[str(number)] = {"number": number, "state": "MERGED" if merged else "OPEN",
                                   "headRefName": branch, "headRefOid": head,
                                   "mergeCommit": {"oid": merge_sha} if merge_sha else None}
        self.task["prs"].append(number)
        self.task["delivery"] = {"number": number, "head": head, "base": base,
                                 "branch": branch, "at": datetime.now(timezone.utc).isoformat()}
        self.save()
        return head, merge_sha

    def save(self):
        S.save_task(self.project, self.task)
        (self.ghdir / "prs.json").write_text(json.dumps(self.pulls))

    def report(self, numbers=None, *, blocked=""):
        report = {"landed": {"prs": [], "main_runs": [], "deploy": "not-applicable"},
                  "review": [], "blocked": blocked}
        for number in self.task["prs"] if numbers is None else numbers:
            pull = self.pulls[str(number)]
            report["landed"]["prs"].append({"number": number, "title": f"delivery {number}",
                                          "merged": pull["state"] == "MERGED",
                                          "merge_sha": (pull.get("mergeCommit") or {}).get("oid")})
        S.write_json(self.report_path, report)
        return report

    def result(self):
        return verify._verify(self.project, self.task["slug"])

    def continue_after_merge(self):
        git("reset", "--hard", "origin/main", cwd=self.worktree)

    def test_two_squashed_deliveries_keep_both_merges_and_current_snapshot(self):
        first_head, first_merge = self.publish(41)
        self.report()
        self.assertEqual(self.result()["verdict"], "ok")
        self.assertNotEqual(first_head, first_merge)
        self.continue_after_merge()
        second_head, second_merge = self.publish(42)
        self.report()

        result = self.result()

        self.assertEqual(result["verdict"], "ok", result["problems"])
        self.assertEqual(result["prs"], [41, 42])
        self.assertEqual(result["delivery"], self.task["delivery"])
        self.assertNotEqual(second_head, second_merge)
        self.assertEqual(self.task["session_id"], "same-owner-conversation")
        self.assertEqual(S.read_json(self.report_path)["landed"]["prs"][0]["merge_sha"], first_merge)
        self.assertEqual(self.result()["verdict"], "ok")

    def test_old_report_cannot_complete_an_unpublished_follow_up(self):
        self.publish(41)
        self.report()
        self.continue_after_merge()
        self.task["delivery"].update(number=None, head=None,
                                     at=datetime.now(timezone.utc).isoformat())
        self.save()

        result = self.result()

        self.assertEqual(result["verdict"], "contradicted")
        self.assertTrue(any("unpublished work" in problem for problem in result["problems"]))
        self.report(blocked="Publication failed; retry the same task.")
        self.assertEqual(self.result()["verdict"], "blocked")

    def test_report_must_include_previous_and_current_pr(self):
        self.publish(41)
        self.continue_after_merge()
        self.publish(42)
        for included, missing in (([41], 42), ([42], 41)):
            with self.subTest(included=included):
                self.report(included)
                result = self.result()
                self.assertEqual(result["verdict"], "contradicted")
                self.assertIn(f"recorded PR #{missing} missing from report", result["problems"])

    def test_rejects_uncommitted_work_in_each_git_state(self):
        self.publish(41)
        self.report()
        for state in ("untracked", "unstaged", "staged"):
            with self.subTest(state=state):
                path = self.worktree / ("untracked" if state == "untracked" else "change-41")
                path.write_text("further authorized work\n")
                if state == "staged":
                    git("add", "change-41", cwd=self.worktree)
                result = self.result()
                self.assertEqual(result["verdict"], "contradicted")
                self.assertIn("task worktree has uncommitted work", result["problems"])
                if state == "untracked":
                    path.unlink()
                git("reset", "--hard", "HEAD", cwd=self.worktree)

    def test_rejects_committed_unpublished_work_even_with_clean_worktree(self):
        self.publish(41)
        self.report()
        (self.worktree / "change-41").write_text("follow-up\n")
        git("commit", "-qam", "unpublished follow-up", cwd=self.worktree)

        result = self.result()

        self.assertEqual(result["verdict"], "contradicted")
        self.assertTrue(any("HEAD differs" in problem for problem in result["problems"]))

    def test_clean_branch_reconciled_to_main_retains_harmless_merged_retry(self):
        self.publish(41)
        self.report()
        self.continue_after_merge()

        self.assertEqual(self.result()["verdict"], "ok")
        (self.worktree / "change-41").write_text("unpublished after reconciliation\n")
        self.assertIn("task worktree has uncommitted work", self.result()["problems"])

    def test_clean_head_before_the_merge_cannot_claim_current_delivery(self):
        self.publish(41)
        self.report()
        git("reset", "--hard", self.task["delivery"]["base"], cwd=self.worktree)

        result = self.result()

        self.assertEqual(result["verdict"], "contradicted")
        self.assertTrue(any("HEAD differs" in problem for problem in result["problems"]))

    def test_rejects_github_movement_after_candidate_checks(self):
        self.publish(41, merged=False)
        self.report(blocked="PR held for review.")
        self.pulls["41"]["headRefOid"] = "f" * 40
        self.save()

        result = self.result()

        self.assertEqual(result["verdict"], "blocked")
        self.assertTrue(any("GitHub head differs" in problem for problem in result["problems"]))

    def test_held_adopted_pr_report_binds_current_head_without_matching_local_branch_name(self):
        self.publish(41, merged=False, branch="external-author-branch")
        self.task["hold_merge"] = "operator review"
        self.save()
        self.report(blocked="PR held for operator review.")

        result = self.result()

        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["delivery"]["branch"], "external-author-branch")

    def test_old_report_for_same_pr_must_be_refreshed_after_publication(self):
        self.publish(41, merged=False)
        self.report()
        published_at = datetime.fromisoformat(self.task["delivery"]["at"]).timestamp()
        os.utime(self.report_path, (published_at - 0.01, published_at - 0.01))

        result = self.result()

        self.assertEqual(result["verdict"], "contradicted")
        self.assertIn("report predates the current delivery; refresh report.json", result["problems"])
        self.report()
        self.assertEqual(self.result()["problems"], [])

    def test_reported_squash_sha_must_match_github(self):
        head, _ = self.publish(41)
        report = self.report()
        report["landed"]["prs"][0]["merge_sha"] = head
        S.write_json(self.report_path, report)

        result = self.result()

        self.assertEqual(result["verdict"], "contradicted")
        self.assertIn("PR #41: reported merge SHA differs from GitHub", result["problems"])
