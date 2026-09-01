"""Done-time cleanup is scoped to one task's persisted L2 and L1 ownership records."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from altitude import config, dispatch, engines, incidents, state as S


class CleanupHarness(unittest.TestCase):
    project = "altitude"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state_root = self.root / "state"
        self.monitor = self.state_root / "monitor"
        self.calls = []
        self.fetch_returncode = 0
        self.fetch_stderr = ""
        self.list_returncode = 0
        self.list_stderr = ""
        self.porcelain = ""
        self.merge_results = {}
        self.rev_parse_results = {}
        self.ref_by_sha = {}
        self.merge_base_results = {}
        self.diff_results = {}
        self.status_results = {}
        self.remove_results = {}
        self.update_ref_results = {}
        self.gh_results = {}
        self.live_agents = []
        self.agents_error = None
        self.claude_error = None
        self.claude_remove_path = None

        patchers = [
            mock.patch.object(config, "ROOT", self.state_root),
            mock.patch.object(config, "MONITOR_DIR", self.monitor),
            mock.patch.object(config, "PROJECTS_FILE", self.state_root / "projects.json"),
            mock.patch.object(config, "INCIDENT_INDEX", self.state_root / "incidents.jsonl"),
            mock.patch.object(incidents, "FAULTS", self.monitor / "faults.json"),
            mock.patch.object(config, "project_path", return_value=self.repo),
            mock.patch.object(dispatch, "pull_after_done", return_value=[]),
            mock.patch.object(engines, "claude_agents", side_effect=self._fake_agents),
            mock.patch.object(engines, "claude_rm", side_effect=self._fake_claude_rm),
            mock.patch.object(subprocess, "run", side_effect=self._fake_run),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        # cleanup_after_done imports altitude.incidents locally; this is the exact function it calls. The FAULTS path is
        # also redirected above so a broken mock still cannot touch the real monitor or file real incident evidence.
        fault_patcher = mock.patch("altitude.incidents.system_fault")
        self.fault = fault_patcher.start()
        self.addCleanup(fault_patcher.stop)

    @staticmethod
    def result(args, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def _fake_run(self, args, **kwargs):
        self.calls.append(tuple(args))
        if args == ["git", "fetch", "-q", "origin", "main"]:
            return self.result(args, self.fetch_returncode, stderr=self.fetch_stderr)
        if args == ["git", "worktree", "list", "--porcelain"]:
            return self.result(args, self.list_returncode, stdout=self.porcelain, stderr=self.list_stderr)
        if args[:3] == ["git", "merge-base", "--is-ancestor"]:
            key = args[3]
            ref = self.ref_by_sha.get(key)
            if ref:
                key = ref.removeprefix("refs/heads/").removesuffix("^{commit}")
            if key.startswith("refs/heads/") and key.endswith("^{commit}-tip"):
                key = key.removeprefix("refs/heads/").removesuffix("^{commit}-tip")
            returncode, stderr = self.merge_results.get(key, (0, ""))
            return self.result(args, returncode, stderr=stderr)
        if args[:3] == ["git", "rev-parse", "--verify"]:
            configured = self.rev_parse_results.get(args[3], (0, f"{args[3]}-tip\n"))
            returncode, stdout = configured.pop(0) if isinstance(configured, list) else configured
            if returncode == 0 and (stdout or "").strip():
                self.ref_by_sha[(stdout or "").strip()] = args[3]
            return self.result(args, returncode, stdout=stdout)
        if args[:2] == ["git", "merge-base"]:
            returncode, stdout, stderr = self.merge_base_results.get((args[2], args[3]), (0, "base\n", ""))
            return self.result(args, returncode, stdout=stdout, stderr=stderr)
        if args[:2] == ["git", "diff"]:
            returncode, stdout, stderr = self.diff_results.get(tuple(args[1:]), (0, "changed\0", ""))
            return self.result(args, returncode, stdout=stdout, stderr=stderr)
        if len(args) >= 4 and args[:2] == ["git", "-C"] and args[3] == "status":
            returncode, stdout, stderr = self.status_results.get(args[2], (0, "", ""))
            return self.result(args, returncode, stdout=stdout, stderr=stderr)
        if args[:3] == ["git", "worktree", "remove"]:
            path = args[4] if args[3] == "--force" else args[3]
            returncode, stderr = self.remove_results.get(path, (0, ""))
            return self.result(args, returncode, stderr=stderr)
        if args[:3] == ["git", "update-ref", "-d"]:
            returncode, stderr = self.update_ref_results.get(args[3], (0, ""))
            return self.result(args, returncode, stderr=stderr)
        if args[:3] == ["gh", "pr", "view"]:
            returncode, stdout, stderr = self.gh_results.get(int(args[3]), (0, "{}", ""))
            return self.result(args, returncode, stdout=stdout, stderr=stderr)
        raise AssertionError(f"unexpected subprocess: {args}")

    def _fake_agents(self):
        self.calls.append(("claude_agents",))
        if self.agents_error:
            raise self.agents_error
        return self.live_agents

    def _fake_claude_rm(self, agent_id):
        self.calls.append(("claude_rm", agent_id))
        if self.claude_error:
            raise self.claude_error
        if self.claude_remove_path and self.claude_remove_path.exists():
            self.claude_remove_path.rmdir()
        return "removed"

    def worktree(self, name):
        return self.repo / ".claude" / "worktrees" / name

    @staticmethod
    def row(path, branch=None, *, locked=False):
        lines = [f"worktree {path}"]
        if branch:
            lines.append(f"branch refs/heads/{branch}")
        if locked:
            lines.append("locked cleanup-test")
        return "\n".join(lines) + "\n\n"

    def make_task(self, slug, *, archive=False, **fields):
        task = {"slug": slug, "state": fields.pop("state", "done"), **fields}
        bucket = "archive" if archive else "tasks"
        directory = self.state_root / self.project / bucket / slug
        directory.mkdir(parents=True)
        S.write_json(directory / "status.json", task)
        return task

    def add_record(self, slug, name, *, role="implementer", done="2026-08-30T01:00:00Z", **fields):
        directory = S.task_dir(self.project, slug) / "l1"
        directory.mkdir(parents=True, exist_ok=True)
        record = {"name": name, "role": role, "done": done, **fields}
        S.write_json(directory / f"{name}.json", record)
        return record

    def cleanup(self, task):
        return dispatch.cleanup_after_done(self.project, task)

    def cleanup_events(self, slug):
        return [event for event in S.read_events(self.project, slug) if event["kind"] == "cleanup-worktree"]

    def merged_receipt(self, task, branch, head, *, number=42):
        task["verified"] = {"verdict": "ok", "prs": [number]}
        self.gh_results[number] = (0, json.dumps({
            "number": number, "state": "MERGED", "baseRefName": "main",
            "headRefName": branch, "headRefOid": head,
        }), "")


class TestDoneCleanupScope(CleanupHarness):
    def test_merged_pr_receipt_requires_exact_number_state_base_head_and_oid(self):
        task = {"verified": {"verdict": "ok", "prs": [42]}}
        branch, head = "worktree-receipt", "a" * 40
        valid = {"number": 42, "state": "MERGED", "baseRefName": "main",
                 "headRefName": branch, "headRefOid": head}
        for field, value in (("number", 41), ("state", "OPEN"), ("baseRefName", "release"),
                             ("headRefName", "other"), ("headRefOid", "b" * 40)):
            with self.subTest(field=field):
                self.gh_results[42] = (0, json.dumps({**valid, field: value}), "")
                self.assertEqual(dispatch._merged_pr_receipt(self.repo, task, branch, head)[0], False)

    def test_merged_pr_receipt_malformed_inputs_and_gh_failure_fail_closed(self):
        branch, head = "worktree-receipt", "a" * 40
        for malformed in (None, 7, "42", {"number": 42}):
            with self.subTest(verified_prs=malformed):
                task = {"verified": {"verdict": "ok", "prs": malformed}}
                self.assertEqual(dispatch._merged_pr_receipt(self.repo, task, branch, head)[0], False)
        task = {"verified": {"verdict": "ok", "prs": [42]}}
        for payload in ("null", "[]", '"not-an-object"', "{"):
            with self.subTest(payload=payload):
                self.gh_results[42] = (0, payload, "")
                self.assertEqual(dispatch._merged_pr_receipt(self.repo, task, branch, head)[0], None)
        self.gh_results[42] = (1, "", "network unavailable")
        self.assertEqual(dispatch._merged_pr_receipt(self.repo, task, branch, head)[0], None)

    def test_squash_equivalent_task_branch_allows_cleanup(self):
        slug = "squash-merged-l2"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch=branch, l2_engine="codex")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")
        ref, main_ref = f"refs/heads/{branch}^{{commit}}", "refs/remotes/origin/main^{commit}"
        head, main, base = "a" * 40, "9" * 40, "b" * 40
        self.rev_parse_results[ref] = (0, head + "\n")
        self.rev_parse_results[main_ref] = (0, main + "\n")
        self.merge_base_results[(head, main)] = (0, base + "\n", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", base, head)] = (
            0, "docs/SESSION_LIFECYCLE.md\0", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", head, main)] = (
            0, "unrelated-main-change.md\0", "")
        self.merged_receipt(task, branch, head)

        notes = self.cleanup(task)

        self.assertIn(("git", "worktree", "remove", str(l2)), self.calls)
        self.assertIn("removed merged worktree squash-merged-l2", notes)
        event = self.cleanup_events(slug)[0]
        self.assertEqual(event["action"], "removed")
        self.assertEqual(event["reason"], "task branch content is present in origin/main (squash-equivalent)")

    def test_squash_cleanup_refuses_a_difference_on_a_branch_touched_path(self):
        slug = "changed-after-squash"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch=branch, l2_engine="codex")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")
        ref, main_ref = f"refs/heads/{branch}^{{commit}}", "refs/remotes/origin/main^{commit}"
        head, main, base = "c" * 40, "8" * 40, "d" * 40
        self.rev_parse_results[ref] = (0, head + "\n")
        self.rev_parse_results[main_ref] = (0, main + "\n")
        self.merge_base_results[(head, main)] = (0, base + "\n", "")
        touched = "docs/SESSION_LIFECYCLE.md\0"
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", base, head)] = (0, touched, "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", head, main)] = (0, touched, "")

        notes = self.cleanup(task)

        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("branch has commits not on origin/main" in note for note in notes), notes)
        event = self.cleanup_events(slug)[0]
        self.assertEqual((event["action"], event["reason"]),
                         ("skipped", "branch has commits not on origin/main"))

    def test_squash_equivalence_command_failure_faults_and_removes_nothing(self):
        slug = "squash-proof-failed"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch=branch, agent_id="proof-agent")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")
        ref, main_ref, head, main = (f"refs/heads/{branch}^{{commit}}", "refs/remotes/origin/main^{commit}",
                                     "f" * 40, "7" * 40)
        self.rev_parse_results[ref] = (0, head + "\n")
        self.rev_parse_results[main_ref] = (0, main + "\n")
        self.merge_base_results[(head, main)] = (128, "", "bad graph")

        notes = self.cleanup(task)

        self.assertNotIn(("claude_rm", "proof-agent"), self.calls)
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("squash equivalence indeterminate" in note for note in notes), notes)
        self.assertEqual(self.fault.call_args.args[0], "cleanup-equivalence")

    def test_branch_move_after_squash_proof_faults_before_removal(self):
        slug = "squash-proof-race"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch=branch, agent_id="race-agent")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")
        ref, main_ref = f"refs/heads/{branch}^{{commit}}", "refs/remotes/origin/main^{commit}"
        head, moved, main, base = "1" * 40, "2" * 40, "4" * 40, "3" * 40
        self.rev_parse_results[ref] = [(0, head + "\n"), (0, moved + "\n")]
        self.rev_parse_results[main_ref] = (0, main + "\n")
        self.merge_base_results[(head, main)] = (0, base + "\n", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", base, head)] = (0, "doc.md\0", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", head, main)] = (0, "", "")
        self.merged_receipt(task, branch, head)

        notes = self.cleanup(task)

        self.assertNotIn(("claude_rm", "race-agent"), self.calls)
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("branch or origin/main moved after cleanup proof" in note for note in notes), notes)
        self.assertEqual(self.fault.call_args.args[0], "cleanup-branch-race")

    def test_origin_main_move_after_squash_proof_faults_before_removal(self):
        slug = "squash-main-race"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch=branch, l2_engine="codex")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")
        ref, main_ref = f"refs/heads/{branch}^{{commit}}", "refs/remotes/origin/main^{commit}"
        head, main, moved, base = "5" * 40, "6" * 40, "7" * 40, "8" * 40
        self.rev_parse_results[ref] = (0, head + "\n")
        self.rev_parse_results[main_ref] = [(0, main + "\n"), (0, moved + "\n")]
        self.merge_base_results[(head, main)] = (0, base + "\n", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", base, head)] = (0, "doc.md\0", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", head, main)] = (0, "", "")
        self.merged_receipt(task, branch, head)

        notes = self.cleanup(task)

        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("branch or origin/main moved after cleanup proof" in note for note in notes), notes)
        self.assertEqual(self.fault.call_args.args[0], "cleanup-branch-race")

    def test_identical_content_without_a_verified_landing_receipt_is_preserved(self):
        slug = "independently-identical"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch=branch, l2_engine="codex")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")
        ref, main_ref = f"refs/heads/{branch}^{{commit}}", "refs/remotes/origin/main^{commit}"
        head, main, base = "a" * 40, "b" * 40, "c" * 40
        self.rev_parse_results[ref] = (0, head + "\n")
        self.rev_parse_results[main_ref] = (0, main + "\n")
        self.merge_base_results[(head, main)] = (0, base + "\n", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", base, head)] = (0, "same.md\0", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", head, main)] = (0, "", "")
        self.merged_receipt(task, "different-task-branch", head)

        notes = self.cleanup(task)

        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("no verified merged PR matches" in note for note in notes), notes)

    def test_empty_net_branch_is_not_treated_as_a_squash_merge(self):
        slug = "empty-net-branch"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch=branch, l2_engine="codex")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")
        ref, main_ref = f"refs/heads/{branch}^{{commit}}", "refs/remotes/origin/main^{commit}"
        head, main, base = "d" * 40, "e" * 40, "f" * 40
        self.rev_parse_results[ref] = (0, head + "\n")
        self.rev_parse_results[main_ref] = (0, main + "\n")
        self.merge_base_results[(head, main)] = (0, base + "\n", "")
        self.diff_results[("diff", "--name-only", "--no-renames", "-z", base, head)] = (0, "", "")

        notes = self.cleanup(task)

        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("branch has no net changed paths" in note for note in notes), notes)

    def test_squash_fallback_requires_the_exact_persisted_canonical_l2_branch(self):
        slug = "wrong-persisted-branch"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), branch="other-branch", l2_engine="codex")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (1, "")

        notes = self.cleanup(task)

        self.assertFalse(any(call[:3] == ("gh", "pr", "view") for call in self.calls))
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("exact persisted L2 branch and worktree" in note for note in notes), notes)

    def test_dirty_tracked_worktree_is_preserved_before_worker_cleanup(self):
        slug = "dirty-tracked"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="tracked-agent")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.status_results[str(l2)] = (0, " M tracked.md\0", "")

        notes = self.cleanup(task)

        self.assertNotIn(("claude_rm", "tracked-agent"), self.calls)
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("tracked or untracked changes" in note for note in notes), notes)

    def test_dirty_untracked_worktree_is_preserved_before_worker_cleanup(self):
        slug = "dirty-untracked"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="untracked-agent")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.status_results[str(l2)] = (0, "?? notes.txt\0", "")

        notes = self.cleanup(task)

        self.assertNotIn(("claude_rm", "untracked-agent"), self.calls)
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("tracked or untracked changes" in note for note in notes), notes)

    def test_only_ignored_generated_python_bytecode_is_explicitly_disposable(self):
        slug = "pycache-only"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), l2_engine="codex")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.status_results[str(l2)] = (0, "!! altitude/__pycache__/module.cpython-312.pyc\0", "")

        notes = self.cleanup(task)

        self.assertIn(("git", "worktree", "remove", str(l2)), self.calls)
        self.assertTrue(any("removed merged worktree" in note for note in notes), notes)

    def test_dirty_completed_l1_is_forced_only_after_its_captured_patch_is_validated(self):
        slug = "dirty-l1-captured"
        task = self.make_task(slug, archive=True)
        patch = S.task_dir(self.project, slug) / "l1" / "implementer-1.patch"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text("diff --git a/a b/a\n")
        l1 = self.worktree(f"{slug[:30]}-implementer-1")
        branch = f"l1/{slug[:30]}-implementer-1"
        head = "1" * 40
        self.add_record(slug, "implementer-1", parent_sha=head, worktree=str(l1),
                        result={"error": None, "patch": str(patch)})
        self.porcelain = self.row(l1, branch)
        self.rev_parse_results[f"refs/heads/{branch}^{{commit}}"] = (0, head + "\n")
        self.status_results[str(l1)] = (0, " M a\0", "")

        with mock.patch.object(dispatch, "_l1_patch_matches_current", return_value=(True, None)):
            notes = self.cleanup(task)

        self.assertIn(("git", "worktree", "remove", "--force", str(l1)), self.calls)
        event = self.cleanup_events(slug)[0]
        self.assertIn("dirty L1 worktree removed after validating its captured patch", event["reason"])
        self.assertEqual(event["action"], "removed", notes)

    def test_dirty_l1_without_a_valid_captured_patch_is_preserved(self):
        slug = "dirty-l1-no-patch"
        task = self.make_task(slug, archive=True)
        self.add_record(slug, "implementer-1", result={"error": None, "patch": "/outside/missing.patch"})
        l1 = self.worktree(f"{slug[:30]}-implementer-1")
        branch = f"l1/{slug[:30]}-implementer-1"
        self.porcelain = self.row(l1, branch)
        self.status_results[str(l1)] = (0, " M a\0", "")

        notes = self.cleanup(task)

        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any("without one exact captured L1 patch" in note for note in notes), notes)

    def test_task_ownership_is_checked_before_every_guard(self):
        a_slug = "task-a-with-a-deliberately-long-cleanup-slug"
        b_slug = "task-b-running-codex"
        a_l2 = self.worktree(a_slug)
        a_done = self.worktree(f"{a_slug[:30]}-done-l1")
        a_flight = self.worktree(f"{a_slug[:30]}-in-flight-l1")
        a_locked = self.worktree(f"{a_slug[:30]}-locked-l1")
        a_unmerged = self.worktree(f"{a_slug[:30]}-unmerged-l1")
        b_codex = self.worktree(f"{b_slug[:30]}-implementer-1")
        orphan = self.worktree("orphan-with-no-task-record")
        task_a = self.make_task(a_slug, archive=True, worktree=str(a_l2), agent_id="agent-a")
        self.make_task(b_slug, state="running", l2_engine="codex")
        self.add_record(a_slug, "done-l1", engine="claude")
        self.add_record(a_slug, "in-flight-l1", done=None, engine="codex")
        self.add_record(a_slug, "locked-l1", engine="codex")
        self.add_record(a_slug, "unmerged-l1", engine="claude")
        self.add_record(b_slug, "implementer-1", done=None, engine="codex")
        a_l2.mkdir(parents=True)
        self.claude_remove_path = a_l2
        self.porcelain = "".join([
            self.row(self.repo, "main"),
            self.row(a_l2, f"worktree-{a_slug}"),
            self.row(a_done, f"l1/{a_slug[:30]}-done-l1"),
            self.row(a_flight, f"l1/{a_slug[:30]}-in-flight-l1"),
            self.row(a_locked, f"l1/{a_slug[:30]}-locked-l1", locked=True),
            self.row(a_unmerged, f"l1/{a_slug[:30]}-unmerged-l1"),
            self.row(b_codex, f"l1/{b_slug[:30]}-implementer-1"),
            self.row(orphan, "worktree-orphan"),
        ])
        self.merge_results[f"l1/{a_slug[:30]}-unmerged-l1"] = (1, "")

        notes = self.cleanup(task_a)
        events = self.cleanup_events(a_slug)

        removed = {call[3] for call in self.calls if call[:3] == ("git", "worktree", "remove")}
        merge_checks = {self.ref_by_sha.get(call[3], call[3])
                        for call in self.calls if call[:3] == ("git", "merge-base", "--is-ancestor")}
        self.assertEqual(removed, {str(a_l2), str(a_done)})
        self.assertEqual([call for call in self.calls if call[:1] == ("claude_rm",)], [("claude_rm", "agent-a")])
        self.assertFalse({str(a_flight), str(a_locked), str(a_unmerged), str(b_codex), str(orphan)} & removed)
        self.assertNotIn(f"refs/heads/l1/{b_slug[:30]}-implementer-1^{{commit}}", merge_checks)
        self.assertNotIn("refs/heads/worktree-orphan^{commit}", merge_checks)
        self.assertTrue(any("persisted L1 record has no done stamp" in note for note in notes))
        claude_rm_index = self.calls.index(("claude_rm", "agent-a"))
        for guard in [
            ("git", "fetch", "-q", "origin", "main"),
            ("git", "worktree", "list", "--porcelain"),
            ("claude_agents",),
        ]:
            self.assertLess(self.calls.index(guard), claude_rm_index)
        l2_ref = f"refs/heads/worktree-{a_slug}^{{commit}}"
        l2_merge = next(call for call in self.calls
                        if call[:3] == ("git", "merge-base", "--is-ancestor")
                        and self.ref_by_sha.get(call[3]) == l2_ref)
        self.assertLess(self.calls.index(l2_merge), claude_rm_index)
        self.assertLess(claude_rm_index,
                        self.calls.index(("git", "worktree", "remove", str(a_l2))))

        by_path = {event["worktree"]: event for event in events}
        self.assertEqual(by_path[str(a_l2)]["action"], "removed")
        self.assertEqual(by_path[str(a_l2)]["reason"],
                         "task-owned branch is merged into origin/main; L2 worker removed")
        self.assertEqual(by_path[str(a_done)]["action"], "removed")
        self.assertEqual(by_path[str(a_flight)]["action"], "deferred")
        self.assertEqual(by_path[str(a_locked)]["reason"], "git worktree is locked")
        self.assertEqual(by_path[str(a_unmerged)]["reason"],
                         "squash cleanup is limited to the task's exact persisted L2 branch and worktree")
        self.assertNotIn(str(b_codex), by_path)
        self.assertNotIn(str(orphan), by_path)

    def test_cleaned_archive_relinquishes_a_reused_long_slug_prefix(self):
        prefix = "x" * 30
        old_slug = prefix + "-old"
        new_slug = prefix + "-new"
        shared = self.worktree(f"{prefix}-implementer-1")
        old = self.make_task(old_slug, archive=True, cleaned="2026-08-30T02:00:00Z")
        current = self.make_task(new_slug, archive=True)
        self.add_record(old["slug"], "implementer-1")
        self.add_record(current["slug"], "implementer-1")
        branch = f"l1/{prefix}-implementer-1"
        self.porcelain = self.row(shared, branch)

        self.cleanup(current)

        self.assertIn(("git", "worktree", "remove", str(shared)), self.calls)
        event = self.cleanup_events(new_slug)[0]
        self.assertEqual((event["action"], event["reason"]),
                         ("removed", "task-owned branch is merged into origin/main"))

    def test_not_yet_cleaned_task_retains_a_reused_long_slug_prefix(self):
        prefix = "y" * 30
        other_slug = prefix + "-running"
        current_slug = prefix + "-done"
        shared = self.worktree(f"{prefix}-reviewer-1")
        other = self.make_task(other_slug, state="running")
        current = self.make_task(current_slug, archive=True)
        self.add_record(other["slug"], "reviewer-1", role="reviewer")
        self.add_record(current["slug"], "reviewer-1", role="reviewer")
        self.porcelain = self.row(shared, f"l1/{prefix}-reviewer-1")

        notes = self.cleanup(current)

        self.assertFalse(any(call[:3] == ("git", "merge-base", "--is-ancestor") for call in self.calls))
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertTrue(any(f"also owned by task(s): {other_slug}" in note for note in notes), notes)

    def test_persisted_cwd_reviewer_and_namespace_paths_defer_the_real_worktrees(self):
        slug = "reviewer-cwd-protects-real-worktree"
        l2 = self.worktree(slug)
        namespace = self.worktree(f"{slug[:30]}-custom-cwd")
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="agent-review")
        self.add_record(slug, "reviewer-5", role="reviewer", done=None, worktree=str(l2))
        self.add_record(slug, "implementer-6", done=None, worktree=str(namespace))
        self.porcelain = self.row(l2, f"worktree-{slug}") + self.row(namespace, "l1/custom")

        notes = self.cleanup(task)
        by_path = {event["worktree"]: event for event in self.cleanup_events(slug)}

        self.assertEqual(by_path[str(l2)]["action"], "deferred")
        self.assertEqual(by_path[str(namespace)]["action"], "deferred")
        self.assertTrue(any(f"deferred worktree {l2.name}" in note for note in notes), notes)
        self.assertNotIn(("claude_rm", "agent-review"), self.calls)
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))

    def test_unfinished_l1_defers_without_worktree_list_membership(self):
        slug = "deferred-l1-omitted"
        task = self.make_task(slug, archive=True)
        self.add_record(slug, "implementer-1", done=None)
        expected = str(self.worktree(f"{slug[:30]}-implementer-1").resolve())
        self.porcelain = self.row(self.repo, "main")

        notes = self.cleanup(task)

        self.assertTrue(any(note.startswith("deferred worktree ") for note in notes), notes)
        self.assertEqual([(e["action"], e["worktree"], e["reason"]) for e in self.cleanup_events(slug)],
                         [("deferred", expected, "persisted L1 record has no done stamp")])
        self.assertIn(("claude_agents",), self.calls)
        self.fault.assert_not_called()

    def test_unfinished_l1_defers_when_worktree_list_fails(self):
        slug = "deferred-l1-failed"
        task = self.make_task(slug, archive=True)
        self.add_record(slug, "implementer-1", done=None)
        expected = str(self.worktree(f"{slug[:30]}-implementer-1").resolve())
        self.list_returncode = 2
        self.list_stderr = "simulated list failure"

        notes = self.cleanup(task)

        self.assertTrue(any(note.startswith("deferred worktree ") for note in notes), notes)
        self.assertEqual([(e["action"], e["worktree"], e["reason"]) for e in self.cleanup_events(slug)],
                         [("deferred", expected, "persisted L1 record has no done stamp")])
        self.assertNotIn(("claude_agents",), self.calls)
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-git")
        dispatch.pull_after_done.assert_called_once_with(self.project, task)

    def test_live_claude_session_skips_the_exact_owned_worktree(self):
        slug = "live-session-owned"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="live-agent")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.live_agents = [{"cwd": str(l2), "state": "running"}]

        notes = self.cleanup(task)

        self.assertTrue(any("live L2 worker is using the worktree" in note for note in notes), notes)
        self.assertEqual(self.cleanup_events(slug)[0]["reason"], "live L2 worker is using the worktree")
        self.assertFalse(any(call[:3] == ("git", "merge-base", "--is-ancestor") for call in self.calls))
        self.assertNotIn(("claude_rm", "live-agent"), self.calls)

    def test_live_session_lookup_failure_fails_closed_and_still_pulls(self):
        slug = "agent-list-failure"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="agent-list")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.agents_error = RuntimeError("claude agents down")

        notes = self.cleanup(task)

        self.assertTrue(any("skipped worktree cleanup: claude agents down" in note for note in notes), notes)
        self.assertEqual(self.cleanup_events(slug)[0]["action"], "skipped")
        self.assertNotIn(("claude_rm", "agent-list"), self.calls)
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-agents")
        dispatch.pull_after_done.assert_called_once_with(self.project, task)

    def test_codex_cleanup_does_not_depend_on_claude_agent_enumeration(self):
        slug = "codex-cleanup-with-claude-down"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), l2_engine="codex")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.agents_error = RuntimeError("claude agents down")

        notes = self.cleanup(task)

        self.assertNotIn(("claude_agents",), self.calls)
        self.assertIn(("git", "worktree", "remove", str(l2)), self.calls)
        self.assertTrue(any(f"removed merged worktree {l2.name}" in note for note in notes), notes)
        self.fault.assert_not_called()

    def test_fetch_failure_fails_closed_records_a_fault_and_still_pulls(self):
        slug = "fetch-failure"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="fetch-agent")
        self.fetch_returncode = 2
        self.fetch_stderr = "network unavailable"

        notes = self.cleanup(task)

        self.assertTrue(any("could not refresh origin/main: network unavailable" in note for note in notes), notes)
        self.assertEqual(self.cleanup_events(slug)[0]["reason"],
                         "could not refresh origin/main: network unavailable")
        self.assertNotIn(("git", "worktree", "list", "--porcelain"), self.calls)
        self.assertNotIn(("claude_agents",), self.calls)
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-fetch")
        dispatch.pull_after_done.assert_called_once_with(self.project, task)

    def test_merge_base_indeterminate_is_a_fault_and_never_removes(self):
        slug = "merge-base-indeterminate"
        l2 = self.worktree(slug)
        branch = f"worktree-{slug}"
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="agent-indeterminate")
        self.porcelain = self.row(l2, branch)
        self.merge_results[branch] = (128, "fatal: bad revision")

        notes = self.cleanup(task)

        self.assertNotIn(("claude_rm", "agent-indeterminate"), self.calls)
        self.assertTrue(any("merge-base indeterminate (exit 128); stderr: fatal: bad revision" in note
                            for note in notes), notes)
        event = self.cleanup_events(slug)[0]
        self.assertEqual(event["action"], "skipped")
        self.assertIn("stderr: fatal: bad revision", event["reason"])
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-merge-base")

    def test_claude_rm_exception_never_falls_through_to_git_removal(self):
        slug = "claude-rm-exception"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="agent-rm")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.claude_error = RuntimeError("rm command failed")

        notes = self.cleanup(task)

        self.assertTrue(any("claude worker cleanup failed: rm command failed" in note for note in notes), notes)
        self.assertFalse(any(call[:3] == ("git", "worktree", "remove") for call in self.calls))
        self.assertEqual(self.cleanup_events(slug)[0]["action"], "skipped")
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-worker")

    def test_post_claude_git_deregistration_failure_is_a_faulted_skip(self):
        slug = "deregister-after-claude"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="agent-deregister")
        l2.mkdir(parents=True)
        self.claude_remove_path = l2
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.remove_results[str(l2)] = (3, "not a working tree")

        notes = self.cleanup(task)

        self.assertFalse(l2.exists())
        claude_index = self.calls.index(("claude_rm", "agent-deregister"))
        remove_call = ("git", "worktree", "remove", str(l2))
        self.assertLess(claude_index, self.calls.index(remove_call))
        self.assertFalse(any(call[:3] == ("git", "update-ref", "-d") for call in self.calls))
        self.assertTrue(any("git worktree remove failed: not a working tree" in note for note in notes), notes)
        self.assertEqual(self.cleanup_events(slug)[0]["action"], "skipped")
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-worktree-remove")

    def test_branch_move_after_final_repin_is_refused_by_compare_and_delete(self):
        slug = "branch-delete-failure"
        task = self.make_task(slug, archive=True)
        self.add_record(slug, "implementer-1")
        l1 = self.worktree(f"{slug[:30]}-implementer-1")
        branch = f"l1/{slug[:30]}-implementer-1"
        self.porcelain = self.row(l1, branch)
        ref = f"refs/heads/{branch}^{{commit}}"
        head = "1" * 40
        self.rev_parse_results[ref] = [(0, head + "\n"), (0, head + "\n")]
        self.update_ref_results[f"refs/heads/{branch}"] = (1, "reference moved")

        notes = self.cleanup(task)

        self.assertIn(("git", "worktree", "remove", str(l1)), self.calls)
        self.assertIn(("git", "update-ref", "-d", f"refs/heads/{branch}", head), self.calls)
        self.assertTrue(any("git branch compare-and-delete failed after worktree removal: reference moved" in note
                            for note in notes), notes)
        event = self.cleanup_events(slug)[0]
        self.assertEqual(event["action"], "skipped")
        self.assertNotEqual(event["action"], "removed")
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-branch-delete")

    def test_owned_worktree_without_a_branch_is_skipped(self):
        slug = "no-branch-worktree"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="no-branch-agent")
        self.porcelain = self.row(l2)

        notes = self.cleanup(task)

        self.assertTrue(any("git worktree has no branch" in note for note in notes), notes)
        self.assertEqual(self.cleanup_events(slug)[0]["reason"], "git worktree has no branch")
        self.assertFalse(any(call[:3] == ("git", "merge-base", "--is-ancestor") for call in self.calls))
        self.assertNotIn(("claude_rm", "no-branch-agent"), self.calls)


if __name__ == "__main__":
    unittest.main()
