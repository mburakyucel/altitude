"""Issue #247: a fictional dirty project recovers without a privileged worker or lost edits."""
import subprocess
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, dispatch, engines, git_policy, l3, server, state as S, status, tasks as T


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

    def refuse_dispatch(self, slug):
        with self.assertRaisesRegex(T.TransitionError, "uncommitted changes"):
            dispatch.run(self.project, slug)
        self.assertEqual(S.load_task(self.project, slug)["fault"], "main-unpushed")
        self.launch.assert_not_called()
        self.assertEqual(self.snapshot(self.repo), self.before)

    def preserve(self):
        dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Review existing edits", actor="l3")
        return dispatch.run_task_operation(self.project, self.slug)

    def test_deadlock_restart_preservation_and_normal_dispatch(self):
        ordinary = T.new(self.project, "Ordinary fictional task", "Do useful work.")
        for slug in (ordinary["slug"], self.slug):
            self.refuse_dispatch(slug)
        with mock.patch.object(server, "log"):
            server.restart_notice()
        notice = next(row["text"] for row in l3.queued(self.project) if row["trigger"] == "restart")
        self.assertIn("restart does not resolve checkout faults", notice)
        for slug in (ordinary["slug"], self.slug):
            self.assertIn(f"{slug}: blocked (fault main-unpushed)", notice)
            reason = S.load_task(self.project, slug)["blocked_reason"]
            dispatch.request_task_operation(self.project, slug, "resume", "Check after restart", actor="l3")
            with self.assertRaisesRegex(dispatch.ResumeFailure, "remains unresolved"):
                dispatch.run_task_operation(self.project, slug)
            self.assertEqual(S.load_task(self.project, slug)["daemon_request"]["status"], "failed")
            blocked = S.load_task(self.project, slug)
            self.assertEqual((blocked["state"], blocked["fault"], blocked["blocked_reason"]),
                             ("blocked", "main-unpushed", reason))
        self.assertEqual(self.snapshot(self.repo), self.before)
        result = self.preserve()
        self.assertEqual(result["request"]["status"], "done")
        task = S.load_task(self.project, self.slug)
        sha = task["preserved_checkout"]
        self.assertEqual(task["state"], "blocked", "preservation does not silently resume tasks")
        self.assertIn(sha, result["request"]["note"])
        self.assertEqual(status.status(self.project, self.slug)["preserved_checkout"], sha)
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        self.assertEqual((self.repo / "runtime.txt").read_text(), "local runtime\n")
        events = [row for row in S.read_events(self.project, self.slug) if row["kind"] == "checkout-preserved"]
        self.assertEqual([(e["sha"], e["by"], e["reason"]) for e in events], [(sha, "l3", "Review existing edits")])
        self.assertTrue(dispatch.run_task_operation(self.project, self.slug)["idempotent"])
        self.assertFalse(dispatch.request_task_operation(self.project, self.slug, "preserve-checkout",
                         "Review existing edits", actor="l3")["queued"])
        self.assertEqual(git("stash", "list", "--format=%H", cwd=self.repo).splitlines(), [sha])
        for slug in (self.slug, ordinary["slug"]):
            dispatch.request_task_operation(self.project, slug, "resume", "Checkout preserved", actor="l3")
            self.assertEqual(dispatch.run_task_operation(self.project, slug)["state"], "queued")
            dispatch.run(self.project, slug)
        worktree = config.project_path(self.project) / ".claude/worktrees" / self.slug
        git("stash", "apply", "--index", sha, cwd=worktree)
        self.assertEqual(self.snapshot(worktree), self.before)
        self.assertEqual((worktree / "draft.bin").read_bytes(), b"\x00\xfffictional\n")
        self.assertTrue((worktree / "link").is_symlink())
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        self.assertEqual(git("rev-parse", "refs/stash", cwd=self.repo).strip(), sha)
        # New ordinary dispatch still refuses if someone dirties main again.
        (self.repo / "README.md").write_text("another edit\n")
        self.before = self.snapshot(self.repo)
        self.launch.reset_mock()
        self.refuse_dispatch(T.new(self.project, "Another normal task", "Work.")["slug"])

    def test_cli_and_broker_only_queue_reason_bearing_requests(self):
        self.refuse_dispatch(self.slug)
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
        self.refuse_dispatch(self.slug)
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
        self.refuse_dispatch(self.slug)
        git("switch", "-c", "local-work", cwd=self.repo)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)
        git("commit", "-qm", "local work", cwd=self.repo)
        # Protected main cannot be advanced even by the recovery command.
        self.assertNotEqual(subprocess.run(["git", "branch", "-f", "main", "HEAD"], cwd=self.repo,
                                          capture_output=True).returncode, 0)

    def test_interrupted_request_never_replays_or_clears_the_fault(self):
        self.refuse_dispatch(self.slug)
        request = dispatch.request_task_operation(self.project, self.slug, "preserve-checkout", "Preserve edits", actor="l3")["request"]
        task = S.load_task(self.project, self.slug)
        task["daemon_request"]["status"] = "executing"
        S.save_task(self.project, task)
        result = dispatch.run_task_operation(self.project, self.slug)
        self.assertEqual(result["request"]["status"], "refused")
        self.assertIn(request["id"], result["request"]["note"])
        self.assertEqual(self.snapshot(self.repo), self.before)
        self.assertEqual(S.load_task(self.project, self.slug)["fault"], "main-unpushed")

    def test_stash_saved_before_cleanup_failure_is_recorded_and_retained(self):
        self.refuse_dispatch(self.slug)
        real_run = git_policy._run

        def fail_after_save(repo, *args, **kwargs):
            result = real_run(repo, *args, **kwargs)
            if args[:2] == ("stash", "push"):
                self.assertEqual(result.returncode, 0)
                return subprocess.CompletedProcess(result.args, 1, result.stdout, "fictional cleanup failure")
            return result

        with mock.patch.object(git_policy, "_run", side_effect=fail_after_save):
            result = self.preserve()
        self.assertEqual(result["request"]["status"], "refused")
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["fault"], "main-unpushed")
        self.assertEqual(task["preserved_checkout"], git("rev-parse", "refs/stash", cwd=self.repo).strip())

    def test_message_resume_preserves_fault_and_consumes_only_the_attempted_wake(self):
        self.refuse_dispatch(self.slug)
        message = T.message(self.project, self.slug, "l3", "Check after restart", by="l3")
        self.assertIn(self.slug, dispatch.resume_due(self.project))
        with self.assertRaises(dispatch.ResumeFailure):
            dispatch.resume(self.project, self.slug)
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["fault"]), ("blocked", "main-unpushed"))
        self.assertNotIn(self.slug, dispatch.resume_due(self.project))
        self.assertEqual(T.pending(self.project, self.slug)[0]["id"], message["id"])

    def test_nested_repository_is_not_removed_or_declared_recovered(self):
        nested = self.repo / "nested"
        nested.mkdir()
        git("init", "-q", cwd=nested)
        (nested / "private-draft.txt").write_text("fictional nested work\n")
        self.before = self.snapshot(self.repo)
        self.refuse_dispatch(self.slug)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual((nested / "private-draft.txt").read_text(), "fictional nested work\n")
        self.assertTrue(git_policy.inspect_repository(self.repo).dirty)
        self.assertEqual(S.load_task(self.project, self.slug)["fault"], "main-unpushed")

    def test_a_previous_attempt_without_worker_identity_is_not_unlaunched(self):
        self.refuse_dispatch(self.slug)
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 1
        S.save_task(self.project, task)
        self.assertEqual(self.preserve()["request"]["status"], "refused")
        self.assertEqual(self.snapshot(self.repo), self.before)

    def test_shared_stash_stack_keeps_old_and_concurrent_stashes(self):
        self.refuse_dispatch(self.slug)
        other = self.tmp / "other-worktree"
        git("worktree", "add", "-q", "-b", "other-task", str(other), "origin/main", cwd=self.repo)
        (other / "README.md").write_text("previous stash\n")
        git("stash", "push", "-m", "previous user stash", cwd=other)
        old = git("rev-parse", "refs/stash", cwd=other).strip()
        real_run = git_policy._run

        def concurrent_stash(repo, *args, **kwargs):
            result = real_run(repo, *args, **kwargs)
            if args[:2] == ("stash", "push"):
                (other / "README.md").write_text("concurrent stash\n")
                git("stash", "push", "-m", "another task stash", cwd=other)
            return result

        with mock.patch.object(git_policy, "_run", side_effect=concurrent_stash):
            self.assertEqual(self.preserve()["request"]["status"], "done")
        sha = S.load_task(self.project, self.slug)["preserved_checkout"]
        stack = git("stash", "list", "--format=%H", cwd=other).splitlines()
        self.assertEqual(len(stack), 3)
        self.assertEqual(stack[1:], [sha, old])
        self.assertEqual(git("show", f"{sha}:README.md", cwd=other), "working version\n")
