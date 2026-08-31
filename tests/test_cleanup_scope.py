"""Done-time cleanup is scoped to one task's persisted L2 and L1 ownership records."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from altitude import config, dispatch, engines, improve, server, state as S


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
        self.remove_results = {}
        self.branch_results = {}
        self.live_agents = []
        self.agents_error = None
        self.claude_error = None
        self.claude_remove_path = None

        patchers = [
            mock.patch.object(config, "ROOT", self.state_root),
            mock.patch.object(config, "MONITOR_DIR", self.monitor),
            mock.patch.object(config, "PROJECTS_FILE", self.state_root / "projects.json"),
            mock.patch.object(config, "INCIDENT_INDEX", self.state_root / "incidents.jsonl"),
            mock.patch.object(improve, "FAULTS", self.monitor / "faults.json"),
            mock.patch.object(config, "project_path", return_value=self.repo),
            mock.patch.object(dispatch, "pull_after_done", return_value=[]),
            mock.patch.object(engines, "claude_agents", side_effect=self._fake_agents),
            mock.patch.object(engines, "claude_rm", side_effect=self._fake_claude_rm),
            mock.patch.object(subprocess, "run", side_effect=self._fake_run),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        # cleanup_after_done imports altitude.improve locally; this is the exact function it calls. The FAULTS path is
        # also redirected above so a broken mock still cannot touch the real monitor or file an I-071 successor.
        fault_patcher = mock.patch("altitude.improve.system_fault")
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
            returncode, stderr = self.merge_results.get(args[3], (0, ""))
            return self.result(args, returncode, stderr=stderr)
        if args[:4] == ["git", "worktree", "remove", "--force"]:
            returncode, stderr = self.remove_results.get(args[4], (0, ""))
            return self.result(args, returncode, stderr=stderr)
        if args[:3] == ["git", "branch", "-D"]:
            returncode, stderr = self.branch_results.get(args[3], (0, ""))
            return self.result(args, returncode, stderr=stderr)
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


class TestDoneCleanupScope(CleanupHarness):
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

        removed = {call[4] for call in self.calls if call[:4] == ("git", "worktree", "remove", "--force")}
        merge_checks = {call[3] for call in self.calls if call[:3] == ("git", "merge-base", "--is-ancestor")}
        self.assertEqual(removed, {str(a_l2), str(a_done)})
        self.assertEqual([call for call in self.calls if call[:1] == ("claude_rm",)], [("claude_rm", "agent-a")])
        self.assertFalse({str(a_flight), str(a_locked), str(a_unmerged), str(b_codex), str(orphan)} & removed)
        self.assertNotIn(f"l1/{b_slug[:30]}-implementer-1", merge_checks)
        self.assertNotIn("worktree-orphan", merge_checks)
        self.assertTrue(any("persisted L1 record has no done stamp" in note for note in notes))
        claude_rm_index = self.calls.index(("claude_rm", "agent-a"))
        for guard in [
            ("git", "fetch", "-q", "origin", "main"),
            ("git", "worktree", "list", "--porcelain"),
            ("claude_agents",),
            ("git", "merge-base", "--is-ancestor", f"worktree-{a_slug}", "origin/main"),
        ]:
            self.assertLess(self.calls.index(guard), claude_rm_index)
        self.assertLess(claude_rm_index,
                        self.calls.index(("git", "worktree", "remove", "--force", str(a_l2))))

        by_path = {event["worktree"]: event for event in events}
        self.assertEqual(by_path[str(a_l2)]["action"], "removed")
        self.assertEqual(by_path[str(a_l2)]["reason"],
                         "task-owned branch is merged into origin/main; L2 agent removed via claude rm")
        self.assertEqual(by_path[str(a_done)]["action"], "removed")
        self.assertEqual(by_path[str(a_flight)]["action"], "deferred")
        self.assertEqual(by_path[str(a_locked)]["reason"], "git worktree is locked")
        self.assertEqual(by_path[str(a_unmerged)]["reason"], "branch has commits not on origin/main")
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

        self.assertIn(("git", "worktree", "remove", "--force", str(shared)), self.calls)
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
        self.assertFalse(any(call[:4] == ("git", "worktree", "remove", "--force") for call in self.calls))
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
        self.assertFalse(any(call[:4] == ("git", "worktree", "remove", "--force") for call in self.calls))

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

        self.assertTrue(any("live Claude session is using the worktree" in note for note in notes), notes)
        self.assertEqual(self.cleanup_events(slug)[0]["reason"], "live Claude session is using the worktree")
        self.assertFalse(any(call[:3] == ("git", "merge-base", "--is-ancestor") for call in self.calls))
        self.assertNotIn(("claude_rm", "live-agent"), self.calls)
    def test_server_retries_live_cleanup_then_stamps_only_after_terminal_removal(self):
        slug = "live-then-terminal"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="retry-agent")
        self.porcelain = self.row(l2, f"worktree-{slug}")
        self.live_agents = [{"id": "retry-agent", "cwd": str(l2), "state": "running"}]

        self.assertFalse(server.cleanup_done_task(self.project, task))
        first = S.load_task(self.project, slug)
        self.assertIsNone(first.get("cleaned"))
        self.assertEqual(first["cleanup_retry"]["attempt"], 1)
        self.assertTrue(first["cleanup_retry"]["after"])
        self.assertFalse(any(call[:4] == ("git", "worktree", "remove", "--force") for call in self.calls))

        with S.project_lock(self.project):
            due = S.load_task(self.project, slug)
            due["cleanup_retry"]["after"] = "2000-01-01T00:00:00+00:00"
            S.save_task(self.project, due)
        self.live_agents = [{"id": "retry-agent", "cwd": str(l2), "state": "done"}]

        self.assertTrue(server.cleanup_done_task(self.project, S.load_task(self.project, slug)))
        final = S.load_task(self.project, slug)
        self.assertIsNotNone(final.get("cleaned"))
        self.assertNotIn("cleanup_retry", final)
        self.assertIn(("claude_rm", "retry-agent"), self.calls)
        self.assertIn(("git", "worktree", "remove", "--force", str(l2)), self.calls)
        cleanup_events = [event for event in S.read_events(self.project, slug) if event["kind"] == "cleanup"]
        self.assertEqual([event["complete"] for event in cleanup_events], [False, True])

    def test_terminal_claude_row_is_removed_when_owned_worktree_is_proven_absent(self):
        slug = "terminal-row-absent-tree"
        l2 = self.worktree(slug)
        task = self.make_task(slug, archive=True, worktree=str(l2), agent_id="terminal-agent")
        self.porcelain = self.row(self.repo, "main")
        self.live_agents = [{"id": "terminal-agent", "cwd": str(l2), "state": "done"}]

        result = self.cleanup(task)

        self.assertTrue(result.complete)
        self.assertIn(("claude_rm", "terminal-agent"), self.calls)
        self.assertTrue(any(event["kind"] == "cleanup-agent" and event["action"] == "removed"
                            for event in S.read_events(self.project, slug)))
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

        self.assertTrue(any("claude rm failed: rm command failed" in note for note in notes), notes)
        self.assertFalse(any(call[:4] == ("git", "worktree", "remove", "--force") for call in self.calls))
        self.assertEqual(self.cleanup_events(slug)[0]["action"], "skipped")
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-claude-rm")

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
        remove_call = ("git", "worktree", "remove", "--force", str(l2))
        self.assertLess(claude_index, self.calls.index(remove_call))
        self.assertFalse(any(call[:3] == ("git", "branch", "-D") for call in self.calls))
        self.assertTrue(any("git worktree remove failed: not a working tree" in note for note in notes), notes)
        self.assertEqual(self.cleanup_events(slug)[0]["action"], "skipped")
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-worktree-remove")

    def test_branch_delete_failure_is_not_a_successful_removal_audit(self):
        slug = "branch-delete-failure"
        task = self.make_task(slug, archive=True)
        self.add_record(slug, "implementer-1")
        l1 = self.worktree(f"{slug[:30]}-implementer-1")
        branch = f"l1/{slug[:30]}-implementer-1"
        self.porcelain = self.row(l1, branch)
        self.branch_results[branch] = (1, "branch is checked out")

        notes = self.cleanup(task)

        self.assertIn(("git", "worktree", "remove", "--force", str(l1)), self.calls)
        self.assertTrue(any("git branch delete failed after worktree removal: branch is checked out" in note
                            for note in notes), notes)
        event = self.cleanup_events(slug)[0]
        self.assertEqual(event["action"], "skipped")
        self.assertNotEqual(event["action"], "removed")
        self.fault.assert_called_once()
        self.assertEqual(self.fault.call_args.args[0], "cleanup-branch-delete")
        self.assertFalse(notes.complete)
        self.assertEqual(notes.branches, [branch])

        self.porcelain = self.row(self.repo, "main")
        self.branch_results[branch] = (0, "")
        retry = {**task, "cleanup_retry": {"branches": notes.branches}}
        second = self.cleanup(retry)

        self.assertTrue(second.complete)
        self.assertEqual(self.calls.count(("git", "branch", "-D", branch)), 2)

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
