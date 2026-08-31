"""Daemon-owned merge authority: durable claims, exact evidence, no model-selected merge inputs."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-merge-coordinator-bootstrap-"))

from altitude import config, merge_coordinator as MC, state as S  # noqa: E402


class MergeCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="altitude-merge-coordinator-")
        self.root = Path(self.tmp.name) / "state"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True)
        self.old = (config.ROOT, config.PROJECTS_FILE, config.MONITOR_DIR)
        config.ROOT = self.root
        config.PROJECTS_FILE = self.root / "projects.json"
        config.MONITOR_DIR = self.root / "monitor"
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": str(self.repo), "stacks": ["python"]}})
        self.slug = "work"
        self.dispatch_id = "work-1"
        self.head = "b" * 40
        self.base = "a" * 40
        self.tree = "f" * 40
        self.guard = {"kind": "strict-required-status-checks", "repository": "owner/repo",
                      "base": "main", "strict": True, "enforce_admins": True,
                      "required_checks": ["ci"]}
        self.real_server_guard = MC._server_stale_base_guard
        task_dir = S.tasks_dir("p") / self.slug
        task_dir.mkdir(parents=True)
        self.task = {"slug": self.slug, "state": "running", "class": "M", "attempt": 1,
                     "dispatch_id": self.dispatch_id, "origin_sha": self.base,
                     "worktree": str(self.repo / ".claude" / "worktrees" / self.slug),
                     "paths": ["altitude/owned.py"], "hold_merge": None}
        S.save_task("p", dict(self.task))
        self.pr = {"number": 17, "state": "OPEN", "isDraft": False, "baseRefName": "main",
                   "baseRefOid": self.base, "headRefName": "worktree-work-l1-build",
                   "headRefOid": self.head, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                   "reviewDecision": "APPROVED", "mergeCommit": None,
                   "statusCheckRollup": [{"conclusion": "SUCCESS"}]}
        self.patchers = [
            mock.patch.object(MC, "_pr_view", side_effect=lambda project, pr: dict(self.pr)),
            mock.patch.object(MC, "_authority", return_value={
                "kind": "l1", "worktree": "/owned", "branch": self.pr["headRefName"],
                "head_sha": self.head, "origin_sha": self.base,
                "files": ["altitude/owned.py"], "lease": ["altitude/owned.py"]}),
            mock.patch.object(MC, "_active_runs", return_value=[]),
            mock.patch.object(MC, "_clean_reviewer", return_value={
                "name": "review", "generation": "review-g", "reviewed_pr": 17,
                "n": 2, "head_sha": self.head, "base_sha": self.base,
                "candidate_tree": self.tree}),
            mock.patch.object(MC, "_candidate_tree", return_value=self.tree),
            mock.patch.object(MC, "_server_stale_base_guard", return_value=self.guard),
            mock.patch.object(MC, "_post_merge_proof", return_value={
                "merge_sha": "c" * 40, "parent_sha": self.base,
                "tree_sha": self.tree, "candidate_tree": self.tree}),
            mock.patch.object(MC, "_main_run", return_value={
                "id": 91, "head_sha": "c" * 40, "status": "completed", "conclusion": "success"}),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        config.ROOT, config.PROJECTS_FILE, config.MONITOR_DIR = self.old
        self.tmp.cleanup()

    def _request(self):
        return MC.request("p", self.slug, 17)

    def _record(self):
        return MC.status("p", self.slug, 17)

    def _clear_retry(self):
        rec = self._record()
        rec["retry_after"] = None
        S.write_json(MC._record_path("p", self.slug, 17), rec)

    def test_request_pins_host_evidence_and_is_idempotent(self):
        rec = self._request()
        self.assertEqual((rec["dispatch_id"], rec["base_sha"], rec["head_sha"]),
                         (self.dispatch_id, self.base, self.head))
        self.assertEqual(rec["state"], "requested")
        self.assertEqual(MC.request("p", self.slug, 17)["generation"], rec["generation"])
        events = S.read_events("p", self.slug)
        self.assertEqual([event["kind"] for event in events], ["merge-requested"])

    def test_failed_exact_request_cannot_reset_bounded_attempts(self):
        rec = self._request()
        rec.update({"state": "failed", "attempts": MC.MAX_ATTEMPTS,
                    "terminal_attempts": MC.MAX_ATTEMPTS,
                    "last_error": "bounded failure"})
        S.write_json(MC._record_path("p", self.slug, 17), rec)
        same = self._request()
        self.assertEqual(same["generation"], rec["generation"])
        self.assertEqual((same["state"], same["attempts"]), ("failed", MC.MAX_ATTEMPTS))

    def test_same_candidate_fresh_clean_review_supersedes_terminal_failure_once(self):
        old = self._request()
        old.update({"state": "failed", "attempts": 9, "terminal_attempts": MC.MAX_ATTEMPTS,
                    "last_error": "trusted authority was temporarily invalid"})
        S.write_json(MC._record_path("p", self.slug, 17), old)
        MC._clean_reviewer.return_value = {
            "name": "review-repair", "generation": "review-g-2", "reviewed_pr": 17,
            "head_sha": self.head}

        repaired = self._request()
        again = self._request()

        self.assertNotEqual(repaired["generation"], old["generation"])
        self.assertEqual((repaired["state"], repaired["head_sha"], repaired["terminal_attempts"]),
                         ("requested", self.head, 0))
        self.assertEqual(repaired["reviewer"]["generation"], "review-g-2")
        self.assertEqual(again["generation"], repaired["generation"])
        history = MC._history_path("p", self.slug, 17).read_text().splitlines()
        self.assertEqual(len(history), 1)

    def test_same_candidate_changed_authority_proof_supersedes_terminal_failure(self):
        old = self._request()
        old.update({"state": "failed", "terminal_attempts": MC.MAX_ATTEMPTS})
        S.write_json(MC._record_path("p", self.slug, 17), old)
        repaired_authority = {**old["authority"], "worktree": "/owned-repaired"}
        MC._authority.return_value = repaired_authority
        repaired = self._request()
        self.assertNotEqual(repaired["generation"], old["generation"])
        self.assertEqual(repaired["authority"], repaired_authority)

    def test_stale_merged_record_never_short_circuits_new_task_attempt(self):
        old = self._request()
        old.update({"state": "merged", "result": {"merge_sha": "c" * 40}})
        S.write_json(MC._record_path("p", self.slug, 17), old)
        task = S.load_task("p", self.slug)
        task["attempt"] = 2
        S.save_task("p", task)

        current = self._request()

        self.assertNotEqual(current["generation"], old["generation"])
        self.assertEqual((current["state"], current["task_attempt"]), ("requested", 2))
        archived = __import__("json").loads(
            MC._history_path("p", self.slug, 17).read_text().splitlines()[0])
        self.assertEqual((archived["state"], archived["task_attempt"]), ("merged", 1))

    def test_main_advance_creates_new_generation_and_preserves_superseded_evidence(self):
        old = self._request()
        old.update({"state": "failed", "attempts": 7, "terminal_attempts": MC.MAX_ATTEMPTS,
                    "last_error": "PR #17 base moved"})
        S.write_json(MC._record_path("p", self.slug, 17), old)
        new_base = "d" * 40
        self.pr["baseRefOid"] = new_base

        current = self._request()

        self.assertNotEqual(current["generation"], old["generation"])
        self.assertEqual((current["base_sha"], current["state"], current["attempts"],
                          current["terminal_attempts"]),
                         (new_base, "requested", 0, 0))
        history = MC._history_path("p", self.slug, 17).read_text().splitlines()
        self.assertEqual(len(history), 1)
        archived = __import__("json").loads(history[0])
        self.assertEqual((archived["generation"], archived["base_sha"], archived["state"]),
                         (old["generation"], self.base, "failed"))
        self.assertEqual([event["kind"] for event in S.read_events("p", self.slug)],
                         ["merge-requested", "merge-requested", "merge-request-superseded"])

    def test_request_rejects_hold_wrong_task_and_unclean_reviewer(self):
        task = S.load_task("p", self.slug)
        task["hold_merge"] = "Burak must merge"
        S.save_task("p", task)
        with self.assertRaisesRegex(MC.MergePending, "merge hold"):
            self._request()
        task["hold_merge"] = None
        task["dispatch_id"] = None
        S.save_task("p", task)
        with self.assertRaisesRegex(MC.MergeCoordinatorError, "current running dispatch"):
            self._request()
        task["dispatch_id"] = self.dispatch_id
        S.save_task("p", task)
        with mock.patch.object(MC, "_clean_reviewer", side_effect=MC.MergeCoordinatorError("no clean review")):
            with self.assertRaisesRegex(MC.MergeCoordinatorError, "clean review"):
                self._request()

    def test_generic_green_request_waits_before_checks_local_gate_or_merge(self):
        self._request()
        candidate = mock.Mock(return_value={"passed": True})
        with mock.patch.object(MC, "_pr_view") as view, \
             mock.patch.object(MC, "_checks") as checks, \
             mock.patch.object(MC, "_merge_pr") as merge:
            rec = MC.process("p", self.slug, 17, candidate_gate=candidate)
            rec = MC.process("p", self.slug, 17)
        self.assertEqual(rec["state"], "waiting")
        self.assertIn("trusted remote landing integration is pending", rec["last_error"])
        self.assertEqual(rec["terminal_attempts"], 0)
        view.assert_not_called()
        checks.assert_not_called()
        candidate.assert_not_called()
        merge.assert_not_called()

    def test_privileged_merge_uses_match_head_and_verifies_postcondition(self):
        self.pr.update({"state": "MERGED", "baseRefOid": "c" * 40,
                        "mergeCommit": {"oid": "c" * 40}})
        completed = __import__("subprocess").CompletedProcess([], 0, "", "")
        with mock.patch.object(MC.subprocess, "run", return_value=completed) as run:
            result = MC._merge_pr("p", 17, self.head, self.pr["headRefName"], self.base, self.tree,
                                  self.guard)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["gh", "api", "--method", "PUT"])
        self.assertIn("repos/owner/repo/pulls/17/merge", argv)
        self.assertIn(f"sha={self.head}", argv)
        self.assertEqual((result["head_sha"], result["base_sha"], result["merge_sha"]),
                         (self.head, self.base, "c" * 40))
        self.assertEqual(result["post_merge_proof"]["tree_sha"], self.tree)

    def test_server_stale_base_guard_requires_strict_nonempty_required_checks(self):
        with mock.patch.object(MC.verify, "gh", side_effect=[
                {"nameWithOwner": "owner/repo"},
                {"required_status_checks": {"strict": True, "contexts": ["ci"], "checks": []},
                 "enforce_admins": {"enabled": True}},
        ]):
            guard = self.real_server_guard("p", dict(self.pr))
        self.assertEqual(guard, self.guard)

        unsupported = (
            {"required_status_checks": {"strict": False, "contexts": ["ci"]},
             "enforce_admins": {"enabled": True}},
            {"required_status_checks": {"strict": True, "contexts": [], "checks": []},
             "enforce_admins": {"enabled": True}},
            {"required_status_checks": {"strict": True, "contexts": ["ci"]},
             "enforce_admins": {"enabled": False}},
            None,
        )
        for protection in unsupported:
            with self.subTest(protection=protection), \
                 mock.patch.object(MC.verify, "gh", side_effect=[
                     {"nameWithOwner": "owner/repo"}, protection,
                 ]), self.assertRaises(MC.MergePending):
                self.real_server_guard("p", dict(self.pr))
        with mock.patch.object(
                MC.verify, "gh",
                side_effect=MC.verify.VerifierFault("gh api exit 1: HTTP 403 branch protection unavailable")), \
             self.assertRaisesRegex(MC.MergePending, "403"):
            self.real_server_guard("p", dict(self.pr))


    def test_atomic_rest_merge_refuses_a_base_race_as_nonterminal_pending(self):
        self.pr["baseRefOid"] = "d" * 40
        completed = __import__("subprocess").CompletedProcess([], 1, "", "strict checks require update")
        with mock.patch.object(MC.subprocess, "run", return_value=completed), \
             self.assertRaisesRegex(MC.MergePending, "pair moved"):
            MC._merge_pr("p", 17, self.head, self.pr["headRefName"], self.base, self.tree,
                         self.guard)

    def test_merge_without_proven_server_guard_never_invokes_github(self):
        with mock.patch.object(MC.subprocess, "run") as run, \
             self.assertRaisesRegex(MC.MergePending, "no proven server"):
            MC._merge_pr("p", 17, self.head, self.pr["headRefName"], self.base, self.tree, {})
        run.assert_not_called()



    def test_pending_worker_block_before_readiness_never_merges(self):
        self._request()
        task = S.load_task("p", self.slug)
        task["block_pending"] = {"dispatch_id": self.dispatch_id, "reason": "stop all workers"}
        S.save_task("p", task)
        with mock.patch.object(MC, "_merge_pr") as merge:
            rec = MC.process("p", self.slug, 17)
        merge.assert_not_called()
        self.assertEqual(rec["state"], "waiting")
        self.assertEqual(rec["terminal_attempts"], 0)
        self.assertIn("pending exact L1/L2 stop proof", rec["last_error"])





    def test_skipped_configured_check_is_never_green(self):
        self.pr["statusCheckRollup"] = [{"conclusion": "SKIPPED"}]
        with self.assertRaisesRegex(MC.MergeCoordinatorError, "literally succeed"):
            self._request()
        self.assertNotEqual(MC._checks(self.pr), "pass")

    def test_neutral_configured_check_is_never_green(self):
        self.pr["statusCheckRollup"] = [{"conclusion": "NEUTRAL"}]
        with self.assertRaisesRegex(MC.MergeCoordinatorError, "literally succeed"):
            self._request()
        self.assertEqual(MC._checks(self.pr), "reject")







    def test_indeterminate_claim_owner_never_overlaps(self):
        rec = self._request()
        rec.update({"state": "processing", "attempts": 1,
                    "claim": {"generation": "unknown", "owner_pid": os.getpid(),
                              "owner_pid_start": None,
                              "deadline": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}})
        S.write_json(MC._record_path("p", self.slug, 17), rec)
        with mock.patch.object(MC, "_merge_pr") as merge:
            same = MC.process("p", self.slug, 17)
        merge.assert_not_called()
        self.assertEqual(same["claim"]["generation"], "unknown")


    def test_candidate_gate_evidence_is_exact_and_fail_closed(self):
        good = {"sandboxed": True, "passed": True, "base_sha": self.base,
                "head_sha": self.head, "tests": 42, "skipped": 1,
                "expected_failures": 0}
        self.assertEqual(MC._candidate_evidence(good, self.base, self.head), good)
        for field, value in (("head_sha", "e" * 40), ("base_sha", "e" * 40),
                             ("passed", False), ("sandboxed", False), ("tests", 0)):
            bad = {**good, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(
                    MC.MergeCoordinatorError, "mismatched evidence"):
                MC._candidate_evidence(bad, self.base, self.head)



    def test_pending_lists_only_durable_nonterminal_requests(self):
        self._request()
        self.assertEqual(MC.pending("p"), [(self.slug, 17)])
        rec = self._record()
        rec["state"] = "failed"
        S.write_json(MC._record_path("p", self.slug, 17), rec)
        self.assertEqual(MC.pending("p"), [])

    def test_pending_ignores_terminal_and_superseded_tasks_without_deleting_forensics(self):
        rec = self._request()
        record_path = MC._record_path("p", self.slug, 17)
        for state in ("parked", "rejected"):
            task = S.load_task("p", self.slug)
            task["state"] = state
            S.save_task("p", task)
            self.assertEqual(MC.pending("p"), [])
            self.assertEqual(S.read_json(record_path)["generation"], rec["generation"])
        task = S.load_task("p", self.slug)
        task.update({"state": "running", "dispatch_id": "work-2"})
        S.save_task("p", task)
        self.assertEqual(MC.pending("p"), [])
        self.assertTrue(record_path.exists())


class ReviewerBindingTest(unittest.TestCase):
    def test_clean_reviewer_requires_exact_pr_head_and_structured_empty_findings(self):
        base = {"dispatch_id": "d1", "role": "reviewer", "state": "done", "done": S.now(),
                "review_pr": 9, "review_head_at_start": "a" * 40,
                "review_base_at_start": "b" * 40,
                "name": "review", "generation": "g", "n": 1,
                "result": {"error": None, "review_pr": 9, "reviewed_head_sha": "a" * 40,
                           "reviewed_base_sha": "b" * 40,
                           "structured": {"findings": []}}}
        with mock.patch.object(MC.l1, "list_runs", return_value=[base]):
            self.assertEqual(MC._clean_reviewer(
                "p", "s", 9, "d1", "a" * 40, "b" * 40, "c" * 40)["name"], "review")
        for mutation in ({"review_head_at_start": "b" * 40}, {"review_pr": 10},
                         {"result": {"error": None, "review_pr": 9,
                                     "reviewed_head_sha": "b" * 40, "reviewed_base_sha": "b" * 40,
                                     "structured": {"findings": []}}},
                         {"result": {"error": None, "structured": {"findings": [{"severity": "major"}]}}},
                         {"result": {"error": None, "structured": None}}):
            run = {**base, **mutation}
            with mock.patch.object(MC.l1, "list_runs", return_value=[run]):
                with self.assertRaisesRegex(MC.MergeCoordinatorError, "reviewer"):
                    MC._clean_reviewer("p", "s", 9, "d1", "a" * 40, "b" * 40, "c" * 40)


class PersistedRunSupersessionTest(unittest.TestCase):
    """Selection uses durable l1.list_runs ordering, not model/mocked list order."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="altitude-merge-runs-")
        self.root = Path(self.tmp.name) / "state"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.old = (config.ROOT, config.PROJECTS_FILE, config.MONITOR_DIR)
        config.ROOT = self.root
        config.PROJECTS_FILE = self.root / "projects.json"
        config.MONITOR_DIR = self.root / "monitor"
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": str(self.repo), "stacks": ["python"]}})
        self.slug, self.dispatch = "persisted", "persisted-1"
        self.head, self.base, self.tree = "b" * 40, "a" * 40, "f" * 40
        (S.tasks_dir("p") / self.slug).mkdir(parents=True)

    def tearDown(self):
        config.ROOT, config.PROJECTS_FILE, config.MONITOR_DIR = self.old
        self.tmp.cleanup()

    def reviewer(self, n: int, *, base: str | None = None, findings=None) -> dict:
        base = base or self.base
        return {"n": n, "name": f"reviewer-{n}", "generation": f"review-g-{n}",
                "dispatch_id": self.dispatch, "role": "reviewer", "state": "done", "done": S.now(),
                "review_pr": 9, "review_head_at_start": self.head,
                "review_base_at_start": base,
                "result": {"error": None, "review_pr": 9, "reviewed_head_sha": self.head,
                           "reviewed_base_sha": base,
                           "structured": {"findings": [] if findings is None else findings}}}

    def test_latest_of_two_persisted_clean_reviewers_supersedes_the_old_one(self):
        MC.l1.save("p", self.slug, self.reviewer(1))
        MC.l1.save("p", self.slug, self.reviewer(2))

        proof = MC._clean_reviewer(
            "p", self.slug, 9, self.dispatch, self.head, self.base, self.tree)

        self.assertEqual((proof["name"], proof["generation"], proof["n"]),
                         ("reviewer-2", "review-g-2", 2))
        self.assertEqual((proof["base_sha"], proof["candidate_tree"]), (self.base, self.tree))
        MC.l1.save("p", self.slug, self.reviewer(3, findings=[{"severity": "major"}]))
        with self.assertRaisesRegex(MC.MergeCoordinatorError, "latest exact reviewer"):
            MC._clean_reviewer("p", self.slug, 9, self.dispatch, self.head, self.base, self.tree)

    def test_main_advance_requires_a_fresh_persisted_reviewer(self):
        advanced = "c" * 40
        MC.l1.save("p", self.slug, self.reviewer(1, base=self.base))
        with self.assertRaisesRegex(MC.MergeCoordinatorError, "PR/base/head"):
            MC._clean_reviewer("p", self.slug, 9, self.dispatch, self.head, advanced, "d" * 40)

        MC.l1.save("p", self.slug, self.reviewer(2, base=advanced))
        proof = MC._clean_reviewer(
            "p", self.slug, 9, self.dispatch, self.head, advanced, "d" * 40)
        self.assertEqual((proof["name"], proof["base_sha"]), ("reviewer-2", advanced))

    def test_latest_of_two_persisted_exact_authorities_is_used(self):
        branch = "worktree-persisted-l1-build"
        for n in (1, 2):
            MC.l1.save("p", self.slug, {
                "n": n, "name": f"implementer-{n}", "generation": f"build-g-{n}",
                "dispatch_id": self.dispatch, "role": "implementer", "state": "done", "done": S.now(),
                "result": {"pr": 4}, "head_sha": self.head, "branch": branch,
                "worktree": f"/owned-{n}",
            })
        task = {"dispatch_id": self.dispatch, "origin_sha": self.base,
                "attempt": 1, "paths": ["altitude/owned.py"]}
        selected = []

        def validate(project, slug, run, worktree, dispatch_id=None):
            selected.append((run["name"], str(worktree)))
            return {"GIT_WORK_TREE": str(worktree), "GIT_DIR": "/trusted.git"}

        def git(cwd, *args, env=None):
            if args[:3] == ("remote", "get-url", "origin"):
                return "https://example.invalid/owner/repo.git"
            if args[:3] == ("symbolic-ref", "--quiet", "--short"):
                return branch
            if args[:3] == ("rev-parse", "--verify", "HEAD"):
                return self.head
            if args and args[0] == "status":
                return ""
            if args[:2] == ("diff", "--name-only"):
                return "altitude/owned.py"
            if args[:2] == ("rev-list", "--reverse"):
                return "e" * 40
            if args[:2] == ("show", "--quiet"):
                return "p/persisted"
            raise AssertionError(args)

        with mock.patch.object(MC.l1, "validate_broker_worktree", side_effect=validate), \
             mock.patch.object(MC.config, "project_path", return_value=Path("/project")), \
             mock.patch.object(MC, "_git", side_effect=git), \
             mock.patch.object(MC, "_require_pinned_hooks"), \
             mock.patch.object(MC.dispatch, "task_paths", return_value=["altitude/owned.py"]):
            proof = MC._authority("p", self.slug, 4, task, self.head, branch)

        self.assertEqual(selected, [("implementer-2", "/owned-2")])
        self.assertEqual(proof["owner"], {"name": "implementer-2", "generation": "build-g-2", "n": 2})


class AuthorityTest(unittest.TestCase):
    def setUp(self):
        self.head, self.origin = "b" * 40, "a" * 40
        self.task = {"dispatch_id": "d1", "origin_sha": self.origin,
                     "paths": ["altitude/owned.py"]}
        self.run = {"dispatch_id": "d1", "role": "implementer", "state": "done", "done": S.now(),
                    "result": {"pr": 4}, "head_sha": self.head, "branch": "worktree-s-l1-build",
                    "worktree": "/owned", "name": "build", "generation": "build-g", "n": 1}

    def _git(self, cwd, *args, env=None):
        if args[:3] == ("config", "--path", "--get"):
            return str(config.HOOKS)
        if args[:3] == ("remote", "get-url", "origin"):
            return "https://example.invalid/owner/repo.git"
        if args[:3] == ("symbolic-ref", "--quiet", "--short"):
            return "worktree-s-l1-build"
        if args[:3] == ("rev-parse", "--verify", "HEAD"):
            return self.head
        if args and args[0] == "status":
            return ""
        if args[:2] == ("diff", "--name-only"):
            return "altitude/owned.py"
        if args[:2] == ("rev-list", "--reverse"):
            return "c" * 40
        if args[:2] == ("show", "--quiet"):
            return "p/s"
        raise AssertionError(args)

    def test_exact_l1_authority_proves_trailers_and_lease(self):
        with mock.patch.object(MC.l1, "list_runs", return_value=[self.run]), \
             mock.patch.object(MC.l1, "validate_broker_worktree",
                               return_value={"GIT_WORK_TREE": "/owned", "GIT_DIR": "/trusted.git"}), \
             mock.patch.object(MC.config, "project_path", return_value=Path("/project")), \
             mock.patch.object(MC, "_git", side_effect=self._git), \
             mock.patch.object(MC, "_require_pinned_hooks"), \
             mock.patch.object(MC.dispatch, "task_paths", return_value=["altitude/owned.py"]):
            proof = MC._authority("p", "s", 4, self.task, self.head, "worktree-s-l1-build")
        self.assertEqual((proof["kind"], proof["files"]), ("l1", ["altitude/owned.py"]))

    def test_authority_rejects_missing_trailer_and_outside_lease(self):
        common = [
            mock.patch.object(MC.l1, "list_runs", return_value=[self.run]),
            mock.patch.object(MC.l1, "validate_broker_worktree",
                              return_value={"GIT_WORK_TREE": "/owned", "GIT_DIR": "/trusted.git"}),
            mock.patch.object(MC.config, "project_path", return_value=Path("/project")),
            mock.patch.object(MC, "_git", side_effect=self._git),
            mock.patch.object(MC, "_require_pinned_hooks"),
            mock.patch.object(MC.dispatch, "task_paths", return_value=["altitude/owned.py"]),
        ]
        with common[0], common[1], common[2], common[3], common[4], common[5], \
             mock.patch.object(MC, "_missing_trailers", return_value=["c" * 40]):
            with self.assertRaisesRegex(MC.MergeCoordinatorError, "without exact task provenance"):
                MC._authority("p", "s", 4, self.task, self.head, "worktree-s-l1-build")

        def outside(cwd, *args, env=None):
            value = self._git(cwd, *args, env=env)
            return "secrets.txt" if args[:2] == ("diff", "--name-only") else value

        with mock.patch.object(MC.l1, "list_runs", return_value=[self.run]), \
             mock.patch.object(MC.l1, "validate_broker_worktree",
                               return_value={"GIT_WORK_TREE": "/owned", "GIT_DIR": "/trusted.git"}), \
             mock.patch.object(MC.config, "project_path", return_value=Path("/project")), \
             mock.patch.object(MC, "_git", side_effect=outside), \
             mock.patch.object(MC, "_require_pinned_hooks"), \
             mock.patch.object(MC.dispatch, "task_paths", return_value=["altitude/owned.py"]), \
             mock.patch.object(MC, "_missing_trailers", return_value=[]):
            with self.assertRaisesRegex(MC.MergeCoordinatorError, "outside the task lease"):
                MC._authority("p", "s", 4, self.task, self.head, "worktree-s-l1-build")


class PostMergeProofTest(unittest.TestCase):
    def setUp(self):
        self.merge = "c" * 40
        self.base = "a" * 40
        self.tree = "f" * 40

    def _prove(self, parent=None, tree=None):
        def git(cwd, *args, env=None):
            if args[:3] == ("rev-list", "--parents", "-n"):
                return f"{self.merge} {parent or self.base}"
            if args[:3] == ("show", "-s", "--format=%T"):
                return tree or self.tree
            raise AssertionError(args)

        with mock.patch.object(MC, "_fetch_object") as fetch, \
             mock.patch.object(MC.config, "project_path", return_value=Path("/project")), \
             mock.patch.object(MC, "_git", side_effect=git):
            value = MC._post_merge_proof("p", self.merge, self.base, self.tree)
        fetch.assert_called_once_with("p", self.merge, fallback_ref="main")
        return value

    def test_proves_one_parent_and_exact_candidate_tree(self):
        proof = self._prove()
        self.assertEqual((proof["parent_sha"], proof["tree_sha"]), (self.base, self.tree))

    def test_rejects_merge_on_moved_base_or_different_tree(self):
        with self.assertRaisesRegex(MC.MergeCoordinatorError, "one-parent squash"):
            self._prove(parent="d" * 40)
        with self.assertRaisesRegex(MC.MergeCoordinatorError, "tree differs"):
            self._prove(tree="e" * 40)
if __name__ == "__main__":
    unittest.main()
