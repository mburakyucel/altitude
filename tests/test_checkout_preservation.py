"""Issue #247: a fictional dirty project recovers without a privileged worker or lost edits."""
import subprocess
import os
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, dispatch, engines, git_policy, server, state as S, status, tasks as T


class TestCheckoutPreservation(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        git_policy.install_hooks(self.repo)
        self.quiet_engines()
        self.patch(dispatch, "wip_hold", lambda *_: None)
        self.launch = self.enterContext(mock.patch.object(engines, "start_l2", return_value={
            "returncode": 0, "agent": {"id": "fictional-worker", "sessionId": "fictional-session"}}))
        self.task = T.new(self.project, "Reconcile fictional edits", "Preserve and review the edits.", source="recovery")
        self.slug = self.task["slug"]
        # A staged/unstaged split, deletion, binary untracked file, symlink and ignored runtime file.
        (self.repo / "README.md").write_text("staged version\n")
        git("add", "README.md", cwd=self.repo)
        (self.repo / "README.md").write_text("working version\n")
        (self.repo / ".gitignore").unlink()
        (self.repo / "draft.bin").write_bytes(b"\x00\xfffictional\n")
        (self.repo / "link").symlink_to("README.md")
        # Use info/exclude because the fixture's .gitignore is deliberately deleted.
        (self.repo / ".git/info/exclude").write_text(".claude/\nruntime.txt\n")
        (self.repo / "runtime.txt").write_text("local runtime\n")
        self.before = self.snapshot(self.repo)

    def snapshot(self, repo):
        return (git("status", "--porcelain", "--untracked-files=all", cwd=repo),
                git("diff", "--binary", cwd=repo), git("diff", "--cached", "--binary", cwd=repo))

    def legacy_checkout_fault(self, slug):
        """An existing pre-M4 blocked record remains eligible for managed recovery."""
        task = S.load_task(self.project, slug)
        task.update(state="blocked", fault="main-unpushed", blocked_reason="Deployment has uncommitted changes")
        S.save_task(self.project, task)

    def preserve(self):
        dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Review existing edits", actor="l3")
        return dispatch.run_task_operation(self.project, self.slug)

    def apply_snapshot(self, sha, worktree):
        patch = subprocess.run(["git", "diff", "--binary", f"{sha}~2", sha], cwd=self.repo,
                               capture_output=True, check=True).stdout
        subprocess.run(["git", "apply", "--index"], input=patch, cwd=worktree, capture_output=True, check=True)

    def assert_archive(self):
        record = S.load_task(self.project, self.slug)["checkout_archive"]
        self.assertEqual(git("rev-parse", record["branch"], cwd=self.repo).strip(), record["sha"])
        self.assertEqual(git("show", f"{record['sha']}:README.md", cwd=self.repo), "working version\n")
        self.assertEqual(git("show", f"{record['sha']}^:README.md", cwd=self.repo), "staged version\n")
        return record["sha"]

    def test_dirty_dispatch_and_managed_preservation_are_independent(self):
        ordinary = T.new(self.project, "Ordinary fictional task", "Do useful work.")
        origin = git_policy.capture_origin_sha(self.repo)
        dispatch.run(self.project, ordinary["slug"])
        self.assertEqual(S.load_task(self.project, ordinary["slug"])["state"], "running")
        ordinary_worktree = config.project_path(self.project) / ".claude/worktrees" / ordinary["slug"]
        self.assertEqual(git("rev-parse", "HEAD", cwd=ordinary_worktree).strip(), origin)
        self.assertEqual((ordinary_worktree / "README.md").read_text(), "readme\n")
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual((self.repo / "draft.bin").read_bytes(), b"\x00\xfffictional\n")
        self.assertTrue((self.repo / "link").is_symlink())
        self.legacy_checkout_fault(self.slug)
        result = self.preserve()
        self.assertEqual(result["request"]["status"], "done")
        task = S.load_task(self.project, self.slug)
        record = task["checkout_archive"]
        sha = self.assert_archive()
        self.assertEqual(task["state"], "blocked", "preservation does not silently resume tasks")
        self.assertIn(sha, result["request"]["note"])
        self.assertEqual(status.status(self.project, self.slug)["checkout_archive"], record)
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        self.assertEqual((self.repo / "runtime.txt").read_text(), "local runtime\n")
        events = [row for row in S.read_events(self.project, self.slug) if row["kind"] == "checkout-preserved"]
        self.assertEqual([(e["sha"], e["by"], e["reason"]) for e in events], [(sha, "l3", "Review existing edits")])
        self.assertEqual(events[0]["branch"], record["branch"])
        self.assertTrue(dispatch.run_task_operation(self.project, self.slug)["idempotent"])
        self.assertFalse(dispatch.request_task_operation(self.project, self.slug, "preserve-checkout",
                         "Review existing edits", actor="l3")["queued"])
        self.assertEqual(git("stash", "list", cwd=self.repo), "")
        dispatch.request_task_operation(self.project, self.slug, "resume", "Checkout preserved", actor="l3")
        self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["state"], "queued")
        dispatch.run(self.project, self.slug)
        worktree = config.project_path(self.project) / ".claude/worktrees" / self.slug
        self.apply_snapshot(sha, worktree)
        self.assertEqual((worktree / "README.md").read_text(), "working version\n")
        self.assertFalse((worktree / ".gitignore").exists())
        self.assertEqual(git("diff", cwd=worktree), "", "applying the net snapshot flattens staging intent")
        self.assertEqual((worktree / "draft.bin").read_bytes(), b"\x00\xfffictional\n")
        self.assertTrue((worktree / "link").is_symlink())
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        self.assert_archive()
        self.assertEqual(git("ls-remote", "--heads", "origin", "archive/*", cwd=self.repo), "")

    def test_cli_and_broker_only_queue_reason_bearing_requests(self):
        self.legacy_checkout_fault(self.slug)
        for actor in ("l3", "burak"):
            with self.subTest(actor=actor):
                result = self.alt("--project", self.project, "task", "preserve-checkout", self.slug,
                                  "--reason", "Preserve edits", env={"ALTITUDE_ACTOR": actor})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.snapshot(self.repo), self.before)
                task = S.load_task(self.project, self.slug)
                task.pop("daemon_request")
                S.save_task(self.project, task)
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": ["task", "preserve-checkout", self.slug,
                                                                                 "--reason", "Preserve edits"]})
        self.assertEqual(result["returncode"], 0, result["stderr"])
        self.assertEqual(self.snapshot(self.repo), self.before)
        for args, env in ((["task", "preserve-checkout", self.slug], {"ALTITUDE_ACTOR": "l3"}),
                          (["task", "preserve-checkout", self.slug, "--reason", ""], {"ALTITUDE_ACTOR": "l3"}),
                          (["task", "preserve-checkout", self.slug, "--reason", "No worker authority"], {"ALTITUDE_ACTOR": "l2"})):
            self.assertNotEqual(self.alt("--project", self.project, *args, env=env).returncode, 0)
        with self.assertRaises(ValueError):
            server.l3_verb_request(self.project, {"kind": "alt", "args": ["task", "preserve-checkout", "../other", "--reason", "No"]})
        self.assertIn("Bash(alt task preserve-checkout *)", engines.L3_ALLOWED_TOOLS)

    def test_existing_worker_and_changed_task_are_refused(self):
        self.legacy_checkout_fault(self.slug)
        dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Preserve edits", actor="l3")
        task = S.load_task(self.project, self.slug)
        task.update(agent_id="existing", session_id="session")
        S.save_task(self.project, task)
        self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "refused")
        # Even if the caller saw this worker, it cannot preserve a checkout on that worker's behalf.
        dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "A fresh reason", actor="l3")
        self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)

    def test_off_main_checkout_is_untouched_and_protected_main_stays_protected(self):
        self.legacy_checkout_fault(self.slug)
        git("switch", "-c", "local-work", cwd=self.repo)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        git("commit", "-qm", "local work", cwd=self.repo)
        # Protected main cannot be advanced even by the recovery command.
        self.assertNotEqual(subprocess.run(["git", "branch", "-f", "main", "HEAD"], cwd=self.repo,
                                          capture_output=True).returncode, 0)

    def test_interrupted_request_never_replays_or_clears_the_fault(self):
        self.legacy_checkout_fault(self.slug)
        request = dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Preserve edits", actor="l3")["request"]
        task = S.load_task(self.project, self.slug)
        task["daemon_request"]["status"] = "executing"
        S.save_task(self.project, task)
        result = dispatch.run_task_operation(self.project, self.slug)
        self.assertEqual(result["request"]["status"], "refused")
        self.assertIn(request["id"], result["request"]["note"])
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual(S.load_task(self.project, self.slug)["fault"], "main-unpushed")

    def test_snapshot_saved_before_cleanup_failure_is_recorded_and_retained(self):
        self.legacy_checkout_fault(self.slug)
        real_run = git_policy._run

        def fail_after_save(repo, *args, **kwargs):
            if args[:3] == ("read-tree", "-m", "-u"):
                self.assert_archive()
                return subprocess.CompletedProcess(args, 1, "", "fictional cleanup failure")
            return real_run(repo, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=fail_after_save):
            result = self.preserve()
        self.assertEqual(result["request"]["status"], "refused")
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["fault"], "main-unpushed")
        sha = self.assert_archive()
        other = self.tmp / "recovery-worktree"
        git("worktree", "add", "-q", "-b", "review", str(other), "origin/main", cwd=self.repo)
        self.apply_snapshot(sha, other)
        self.assertEqual((other / "draft.bin").read_bytes(), b"\x00\xfffictional\n")

    def test_legacy_checkout_fault_requeues_without_mutating_deployment_or_losing_inbox(self):
        self.legacy_checkout_fault(self.slug)
        message = T.message(self.project, self.slug, "l3", "Continue isolated work", by="l3")
        requested = dispatch.request_task_operation(self.project, self.slug, "resume",
                                                    "Continue isolated work", actor="l3")
        dispatch.run_task_operation(self.project, self.slug)
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["daemon_request"]["id"], requested["request"]["id"])
        self.assertEqual(task["daemon_request"]["status"], "done")
        self.assertEqual(task["state"], "queued")
        self.assertFalse(task.get("fault"))
        self.assertEqual(T.pending(self.project, self.slug)[0]["id"], message["id"])
        self.assertEqual(self.snapshot(self.repo), self.before)
        dispatch.run(self.project, self.slug)
        self.assertEqual(S.load_task(self.project, self.slug)["state"], "running")
        self.assertEqual(self.snapshot(self.repo), self.before)

    def test_nested_repository_is_not_removed_or_declared_recovered(self):
        nested = self.repo / "nested"
        nested.mkdir()
        git("init", "-q", cwd=nested)
        (nested / "private-draft.txt").write_text("fictional nested work\n")
        self.before = self.snapshot(self.repo)
        self.legacy_checkout_fault(self.slug)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual((nested / "private-draft.txt").read_text(), "fictional nested work\n")
        self.assertTrue(git_policy.inspect_repository(self.repo).dirty)
        self.assertEqual(S.load_task(self.project, self.slug)["fault"], "main-unpushed")

    def test_a_previous_attempt_without_worker_identity_is_not_unlaunched(self):
        self.legacy_checkout_fault(self.slug)
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 1
        S.save_task(self.project, task)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)

    def test_shared_stash_stack_keeps_old_and_concurrent_stashes(self):
        self.legacy_checkout_fault(self.slug)
        other = self.tmp / "other-worktree"
        git("worktree", "add", "-q", "-b", "other-task", str(other), "origin/main", cwd=self.repo)
        (other / "README.md").write_text("previous stash\n")
        git("stash", "push", "-m", "previous user stash", cwd=other)
        old = git("rev-parse", "refs/stash", cwd=other).strip()
        real_run = git_policy._run

        def concurrent_stash(repo, *args, **kwargs):
            result = real_run(repo, *args, **kwargs)
            if args[0] == "update-ref" and args[1].startswith("refs/heads/archive/"):
                (other / "README.md").write_text("concurrent stash\n")
                git("stash", "push", "-m", "another task stash", cwd=other)
            return result

        with mock.patch.object(git_policy, "_run", side_effect=concurrent_stash):
            self.assertEqual(self.preserve()["request"]["status"], "done")
        sha = self.assert_archive()
        stack = git("stash", "list", "--format=%H", cwd=other).splitlines()
        self.assertEqual(len(stack), 2)
        self.assertEqual(stack[1:], [old])
        self.assertEqual(git("show", f"{sha}:README.md", cwd=other), "working version\n")

    def test_staged_only_content_modes_and_unusual_paths_survive(self):
        staged_only = self.repo / " staged-only\nfile"
        staged_only.write_bytes(b"\x00staged then deleted\xff")
        git("add", "--", staged_only.name, cwd=self.repo)
        staged_only.unlink()
        executable = self.repo / " script\nname"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        self.before = self.snapshot(self.repo)
        self.legacy_checkout_fault(self.slug)
        self.assertEqual(self.preserve()["request"]["status"], "done")
        sha = self.assert_archive()
        content = subprocess.run(["git", "show", f"{sha}^:{staged_only.name}"], cwd=self.repo,
                                 capture_output=True, check=True).stdout
        self.assertEqual(content, b"\x00staged then deleted\xff")
        other = self.tmp / "review-worktree"
        git("worktree", "add", "-q", "-b", "review", str(other), "origin/main", cwd=self.repo)
        self.apply_snapshot(sha, other)
        self.assertTrue(os.access(other / executable.name, os.X_OK))
        self.assertFalse((other / staged_only.name).exists())

    def test_unique_branches_are_retained_even_after_gc_and_task_cleanup(self):
        self.legacy_checkout_fault(self.slug)
        self.assertEqual(self.preserve()["request"]["status"], "done")
        first = S.load_task(self.project, self.slug)["checkout_archive"]
        (self.repo / "README.md").write_text("second recovery\n")
        dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "New edits", actor="burak")
        self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "done")
        second = S.load_task(self.project, self.slug)["checkout_archive"]
        self.assertNotEqual(first["branch"], second["branch"])
        T.reject(self.project, self.slug, "Review complete", actor="burak")
        git("reflog", "expire", "--expire=now", "--all", cwd=self.repo)
        git("-c", "gc.packRefs=false", "gc", "--prune=now", cwd=self.repo)
        for record in (first, second):
            self.assertEqual(git("rev-parse", record["branch"], cwd=self.repo).strip(), record["sha"])
        self.assertEqual(git("show", f"{first['sha']}^:README.md", cwd=self.repo), "staged version\n")

    def test_archive_collision_never_overwrites_or_cleans(self):
        self.legacy_checkout_fault(self.slug)
        request = dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Review existing edits", actor="l3")["request"]
        branch = f"archive/checkout-{request['id']}"
        git("branch", branch, cwd=self.repo)
        base = git("rev-parse", "HEAD", cwd=self.repo)
        self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "refused")
        self.assertEqual(git("rev-parse", branch, cwd=self.repo), base)
        self.assertEqual(self.snapshot(self.repo), self.before)

    def test_interruption_before_record_or_during_cleanup_retains_archive_without_replay(self):
        self.legacy_checkout_fault(self.slug)
        for phase in ("record", "cleanup"):
            with self.subTest(phase=phase):
                dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", phase, actor="l3")
                if phase == "record":
                    real_save = S.save_task

                    def interrupt_record(project, task):
                        if task.get("checkout_archive"):
                            raise KeyboardInterrupt()
                        return real_save(project, task)

                    patcher = mock.patch.object(S, "save_task", side_effect=interrupt_record)
                else:
                    patcher = mock.patch.object(git_policy, "clean_archived_checkout", side_effect=KeyboardInterrupt)
                with patcher, self.assertRaises(KeyboardInterrupt):
                    dispatch.run_task_operation(self.project, self.slug)
                task = S.load_task(self.project, self.slug)
                branch = f"archive/checkout-{task['daemon_request']['id']}"
                sha = git("rev-parse", branch, cwd=self.repo).strip()
                self.assertEqual(git("show", f"{sha}:README.md", cwd=self.repo), "working version\n")
                self.assertEqual(self.snapshot(self.repo), self.before)
                result = dispatch.run_task_operation(self.project, self.slug)
                self.assertEqual(result["request"]["status"], "refused")
                self.assertIn(branch, result["request"]["note"])
                self.assertEqual(self.snapshot(self.repo), self.before)

    def test_capture_failure_leaves_original_index_and_files_untouched(self):
        self.legacy_checkout_fault(self.slug)
        real_run = git_policy._run

        def fail_commit(repo, *args, **kwargs):
            if args[0] == "commit-tree":
                return subprocess.CompletedProcess(args, 1, "", "fictional storage failure")
            return real_run(repo, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=fail_commit):
            self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual(git("for-each-ref", "refs/heads/archive/", cwd=self.repo), "")

    def test_legacy_stash_record_and_contents_survive_new_archive(self):
        self.legacy_checkout_fault(self.slug)
        git("stash", "push", "-u", "-m", "legacy preservation", cwd=self.repo)
        old = git("rev-parse", "refs/stash", cwd=self.repo).strip()
        git("stash", "apply", "--index", old, cwd=self.repo)
        task = S.load_task(self.project, self.slug)
        task["preserved_checkout"] = old
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "checkout-preserved", sha=old, request_id="legacy", by="l3", reason="legacy")
        self.assertEqual(self.preserve()["request"]["status"], "done")
        self.assertEqual(status.status(self.project, self.slug)["preserved_checkout"], old)
        self.assert_archive()
        self.assertEqual(git("rev-parse", "refs/stash", cwd=self.repo).strip(), old)
        other = self.tmp / "legacy-worktree"
        git("worktree", "add", "-q", "-b", "legacy", str(other), "origin/main", cwd=self.repo)
        git("stash", "apply", "--index", old, cwd=other)
        self.assertEqual(self.snapshot(other), self.before)

    def test_ignored_obstructions_are_untouched_and_keep_task_blocked(self):
        self.legacy_checkout_fault(self.slug)
        git("rm", "--cached", "-f", "README.md", cwd=self.repo)
        with (self.repo / ".git/info/exclude").open("a") as handle:
            handle.write("README.md\n")
        self.before = self.snapshot(self.repo)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual((self.repo / "README.md").read_text(), "working version\n")
        self.assertIn("checkout_archive", S.load_task(self.project, self.slug))

    def test_initialized_nested_repository_is_untouched(self):
        nested = self.repo / "nested"
        make_repo(nested)
        (nested / "README.md").write_text("nested edits\n")
        self.before = self.snapshot(self.repo)
        self.legacy_checkout_fault(self.slug)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual((nested / "README.md").read_text(), "nested edits\n")

    def test_edits_after_capture_refuse_cleanup_and_retain_both_versions(self):
        self.legacy_checkout_fault(self.slug)
        real_clean = git_policy.clean_archived_checkout
        for stage in (False, True):
            with self.subTest(stage=stage):
                dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", f"Late edits {stage}", actor="l3")

                def edit_before_clean(*args):
                    (self.repo / "late.txt").write_text("new staged content\n")
                    if stage:
                        git("add", "late.txt", cwd=self.repo)
                    else:
                        (self.repo / "README.md").write_text("late working edit\n")
                    return real_clean(*args)

                with mock.patch.object(git_policy, "clean_archived_checkout", side_effect=edit_before_clean):
                    self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "refused")
                self.assertEqual((self.repo / "README.md").read_text(), "late working edit\n")
                self.assertEqual((self.repo / "late.txt").read_text(), "new staged content\n")
                record = S.load_task(self.project, self.slug)["checkout_archive"]
                self.assertEqual(git("rev-parse", record["branch"], cwd=self.repo).strip(), record["sha"])

    def test_ignored_directory_obstruction_is_not_removed(self):
        self.legacy_checkout_fault(self.slug)
        git("rm", "-f", "README.md", cwd=self.repo)
        (self.repo / "README.md").mkdir()
        (self.repo / "README.md" / "runtime.txt").write_text("ignored nested runtime\n")
        self.before = self.snapshot(self.repo)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual((self.repo / "README.md" / "runtime.txt").read_text(), "ignored nested runtime\n")

    def test_mixed_added_directory_keeps_ignored_files(self):
        (self.repo / "added").mkdir()
        (self.repo / "added" / "draft.txt").write_text("capture this\n")
        (self.repo / "added" / "runtime.txt").write_text("keep here\n")
        self.before = self.snapshot(self.repo)
        self.legacy_checkout_fault(self.slug)
        self.assertEqual(self.preserve()["request"]["status"], "done")
        self.assertFalse((self.repo / "added" / "draft.txt").exists())
        self.assertEqual((self.repo / "added" / "runtime.txt").read_text(), "keep here\n")
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")

    def test_index_only_removal_restores_main_and_preserves_working_content(self):
        git("rm", "--cached", "-f", "README.md", cwd=self.repo)
        self.before = self.snapshot(self.repo)
        self.legacy_checkout_fault(self.slug)
        self.assertEqual(self.preserve()["request"]["status"], "done")
        self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
        sha = S.load_task(self.project, self.slug)["checkout_archive"]["sha"]
        self.assertEqual(git("show", f"{sha}:README.md", cwd=self.repo), "working version\n")
        self.assertEqual(git("ls-tree", f"{sha}^", "README.md", cwd=self.repo), "")

    def test_dirty_submodule_refuses_even_when_configured_to_ignore_dirt(self):
        self.legacy_checkout_fault(self.slug)
        git("stash", "push", "-u", cwd=self.repo)
        peer = self.tmp / "peer"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(peer), cwd=self.repo)
        module = make_repo(self.tmp / "module-source" / "module")
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(module), "module", cwd=peer)
        git("commit", "-qam", "Add module", cwd=peer)
        git("push", "-q", "origin", "main", cwd=peer)
        git("fetch", "-q", "origin", "main", cwd=self.repo)
        git("merge", "--ff-only", "origin/main", cwd=self.repo)
        git("-c", "protocol.file.allow=always", "submodule", "update", "--init", cwd=self.repo)
        git("stash", "apply", "--index", cwd=self.repo)
        git("config", "submodule.module.ignore", "all", cwd=self.repo)
        (self.repo / "module" / "README.md").write_text("dirty module\n")
        self.before = self.snapshot(self.repo)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual((self.repo / "module" / "README.md").read_text(), "dirty module\n")
        # A clean existing gitlink does not block preserving unrelated checkout edits.
        git("restore", "README.md", cwd=self.repo / "module")
        dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Module clean", actor="l3")
        self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "done")
        self.assertEqual((self.repo / "module" / "README.md").read_text(), "readme\n")

    def test_local_commits_and_divergence_are_never_archived(self):
        self.legacy_checkout_fault(self.slug)
        self.repo = make_repo(self.tmp / "local-case" / "repo")
        self.register(self.project, path=self.repo)
        base = git("rev-parse", "HEAD", cwd=self.repo).strip()
        tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo).strip()
        local = git("commit-tree", tree, "-p", base, "-m", "local commit", cwd=self.repo).strip()
        remote = git("commit-tree", tree, "-p", base, "-m", "remote commit", cwd=self.repo).strip()
        # Install protection after constructing a checkout with pre-existing local commits.
        git("update-ref", "refs/heads/main", local, cwd=self.repo)
        git_policy.install_hooks(self.repo)
        (self.repo / "README.md").write_text("uncommitted local edits\n")
        before = self.snapshot(self.repo)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), before)
        git("push", "-q", "origin", f"{remote}:refs/heads/fixture-remote", cwd=self.repo)
        git("update-ref", "refs/heads/main", remote, cwd=self.repo.parent / "origin.git")
        dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Diverged main", actor="l3")
        self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), before)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), local)
        self.assertEqual(git("for-each-ref", "refs/heads/archive/", cwd=self.repo), "")

    def test_cleanup_failure_after_files_restored_retains_recoverable_snapshot(self):
        self.legacy_checkout_fault(self.slug)
        real_run = git_policy._run

        def fail_real_index(repo, *args, **kwargs):
            if args[0] == "read-tree" and not kwargs.get("env"):
                self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
                return subprocess.CompletedProcess(args, 1, "", "fictional index lock failure")
            return real_run(repo, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=fail_real_index):
            self.assertEqual(self.preserve()["request"]["status"], "refused")
        sha = self.assert_archive()
        other = self.tmp / "partial-cleanup-review"
        git("worktree", "add", "-q", "-b", "review", str(other), "origin/main", cwd=self.repo)
        self.apply_snapshot(sha, other)
        self.assertEqual((other / "draft.bin").read_bytes(), b"\x00\xfffictional\n")
        self.assertTrue(dispatch.run_task_operation(self.project, self.slug)["idempotent"])
        self.assertEqual(S.load_task(self.project, self.slug)["fault"], "main-unpushed")

    def test_behind_main_refuses_preservation(self):
        self.legacy_checkout_fault(self.slug)
        peer = self.tmp / "peer"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(peer), cwd=self.repo)
        (peer / "remote.txt").write_text("remote change\n")
        git("add", ".", cwd=peer)
        git("commit", "-qm", "Advance remote", cwd=peer)
        git("push", "-q", "origin", "main", cwd=peer)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual(git("for-each-ref", "refs/heads/archive/", cwd=self.repo), "")
