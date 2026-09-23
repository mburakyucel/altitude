"""The user-facing Git policy boundaries are wired before Altitude can mutate or dispatch."""
import contextlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support import ALT, AltitudeCase, add_worktree, fyi_rows, git, make_repo
from altitude import config, dispatch, engines, git_policy, server, state as S
from altitude import tasks as T


class TestDispatchWorktreePolicy(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register("demo")
        make_repo(self.repo)
        self.origin_sha = git_policy.capture_origin_sha(self.repo)

    def test_new_worktree_uses_captured_origin_and_valid_existing_history_is_reused(self):
        T.new("demo", "Safe task", "Verify worktree reuse")
        worktree = dispatch._task_worktree(self.repo, "demo", "safe-task", self.origin_sha)
        self.assertEqual(git("rev-parse", "HEAD", cwd=worktree).strip(), self.origin_sha)
        self.assertEqual(git("branch", "--show-current", cwd=worktree).strip(), "worktree-safe-task")
        (worktree / "safe.txt").write_text("safe\n")
        git("add", "safe.txt", cwd=worktree)
        git("commit", "-q", "-m", "Reviewed manual change", cwd=worktree)

        self.assertEqual(dispatch._task_worktree(self.repo, "demo", "safe-task", self.origin_sha), worktree)

    def test_existing_task_history_with_old_foreign_labels_is_reused_unchanged(self):
        T.new("demo", "Assigned task", "Resume reviewed assigned history")
        worktree = dispatch._task_worktree(self.repo, "demo", "assigned-task", self.origin_sha)
        message = "Reviewed inherited change\n\nAltitude-Task: demo/previous-owner"
        (worktree / "assigned.txt").write_text("assigned\n")
        git("add", "assigned.txt", cwd=worktree)
        git("commit", "-q", "-m", message, cwd=worktree)
        head = git("rev-parse", "HEAD", cwd=worktree)
        self.assertEqual(dispatch._task_worktree(self.repo, "demo", "assigned-task", self.origin_sha), worktree)
        self.assertEqual(git("rev-parse", "HEAD", cwd=worktree), head)
        self.assertEqual(git("show", "-s", "--format=%B", "HEAD", cwd=worktree).strip(), message)

    def test_orphan_task_branch_is_not_reattached_after_validation_races(self):
        staging = self.tmp / "orphan-staging"
        git("worktree", "add", "-q", "-b", "worktree-orphan-task", str(staging), self.origin_sha, cwd=self.repo)
        git("worktree", "remove", str(staging), cwd=self.repo)

        with self.assertRaisesRegex(T.TransitionError, "exists without its registered worktree"):
            dispatch._task_worktree(self.repo, "demo", "orphan-task", self.origin_sha)


class TestDispatchBoundaryOrdering(AltitudeCase):
    def test_failed_origin_fetch_refuses_before_task_or_agent_mutation(self):
        task = {
            "slug": "blocked", "state": "queued", "dispatching": None,
        }
        with mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _project: contextlib.nullcontext()), \
             mock.patch.object(dispatch.S, "load_task", return_value=task), \
             mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.config, "project_path", return_value=Path("/tmp/unreachable-origin")), \
             mock.patch.object(
                 dispatch.git_policy, "fetch_origin",
                 side_effect=git_policy.GitPolicyError("origin unavailable"),
             ), \
             mock.patch("altitude.incidents.system_fault") as fault, \
             mock.patch.object(dispatch.S, "save_task") as save, \
             mock.patch.object(dispatch.S, "write_json") as write_json, \
             mock.patch.object(dispatch.T, "brief") as brief, \
             mock.patch.object(dispatch.engines, "start_l2") as launch:
            with self.assertRaisesRegex(T.TransitionError, "origin unavailable"):
                dispatch.run("demo", "blocked")

        fault.assert_called_once()
        save.assert_not_called()
        write_json.assert_not_called()
        brief.assert_not_called()
        launch.assert_not_called()


class TestDaemonResumeProvenance(AltitudeCase):
    """A genuine daemon-side provenance refusal remains a task fault and consumes only its resume request."""

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        task = T.new(self.project, "Blocked provenance", "Resume it.")
        self.slug = task["slug"]
        worktree = self.worktree = add_worktree(self.repo, self.slug)
        task.update({"state": "blocked", "attempt": 1, "session_id": "thread-old", "agent_id": "agent-old",
                     "l2_engine": "codex", "worktree": str(worktree), "blocked_reason": "waiting", "waiting_on": "l3"})
        S.save_task(self.project, task)

    def test_real_daemon_provenance_failure_blocks_and_reports_without_losing_the_message(self):
        message = T.message(self.project, self.slug, "l3", "The restriction is fixed.", by="l3")
        git("switch", "-c", "wrong-owner", cwd=self.worktree)

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(engines, "resume_l2") as launch, \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(T.TransitionError, "wrong-owner"):
                dispatch.resume(self.project, self.slug)

        launch.assert_not_called()
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "task-git-provenance")
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["session_id"]), ("blocked", "thread-old"))
        self.assertNotIn("resume_after", task, "a terminal fault waits for another explicit daemon request")
        self.assertTrue(task["resume_failed"])
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)], [message["id"]])
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_a_message_arriving_during_a_failed_resume_keeps_a_fresh_daemon_request(self):
        first = T.message(self.project, self.slug, "l3", "First answer.", by="l3")
        error = T.TransitionError("task worktree ownership is invalid")
        arrived = []

        def fail_after_message(*_args, **_kwargs):
            arrived.append(T.message(self.project, self.slug, "l3", "Second answer.", by="l3"))
            raise error

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch, "_validate_task_worktree", side_effect=fail_after_message), \
             mock.patch("altitude.incidents.system_fault"):
            with self.assertRaises(dispatch.ResumeFailure):
                dispatch.resume(self.project, self.slug)

        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["resume_request"], arrived[0]["id"])
        self.assertTrue(task["resume_after"])
        self.assertNotIn("resume_failed", task)
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)],
                         [first["id"], arrived[0]["id"]])
        self.assertEqual(dispatch.resume_due(self.project), [self.slug])


class TestServiceGitPreflight(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(config, "REPO", new=self.repo)
        self.pending = config.MONITOR_DIR / dispatch.RESTART_PENDING
        self.pending.write_text("{}\n")

    def test_unsafe_service_checkout_refuses_before_state_or_timers_change(self):
        with mock.patch.dict(os.environ, {"ALTITUDE_SERVICE": "1", "ALTITUDE_TIMERS": "1"}), \
             mock.patch.object(git_policy, "activate_source",
                               side_effect=git_policy.GitPolicyError("main is ahead")) as preflight, \
             mock.patch.object(server.threading, "Thread") as thread, \
             mock.patch.object(server, "ThreadingHTTPServer") as httpd, \
             mock.patch.object(server, "log") as log:
            with self.assertRaises(SystemExit) as stopped:
                server.main()

        self.assertEqual(stopped.exception.code, 1)
        preflight.assert_called_once_with()
        self.assertTrue(self.pending.exists())
        thread.assert_not_called()
        httpd.assert_not_called()
        self.assertIn("service startup refused", log.call_args.args[0])

    def test_failed_api_bind_keeps_restart_hold_even_with_a_safe_checkout(self):
        with mock.patch.dict(os.environ, {"ALTITUDE_SERVICE": "1", "ALTITUDE_TIMERS": "0"}), \
             mock.patch.object(config, "TLS", False), \
             mock.patch.object(git_policy, "activate_source") as preflight, \
             mock.patch.object(server, "ThreadingHTTPServer", side_effect=OSError("stop after preflight")) as httpd, \
             mock.patch.object(server, "log"):
            with self.assertRaises(SystemExit) as stopped:
                server.main()

        self.assertEqual(stopped.exception.code, 1)
        preflight.assert_called_once_with()
        self.assertTrue(self.pending.exists())
        httpd.assert_called_once()

    def test_ready_replacement_releases_restart_hold_before_serving(self):
        def serve():
            self.assertFalse(self.pending.exists())
        with mock.patch.dict(os.environ, {"ALTITUDE_SERVICE": "1", "ALTITUDE_TIMERS": "0"}), \
             mock.patch.object(git_policy, "activate_source"), \
             mock.patch.object(server, "ensure_l3_verb_broker"), \
             mock.patch.object(server, "stop_l3_verb_brokers"), \
             mock.patch.object(config, "TLS", False), \
             mock.patch.object(server, "ThreadingHTTPServer") as httpd:
            httpd.return_value.serve_forever.side_effect = serve
            server.main()
        self.assertFalse(self.pending.exists())
        httpd.return_value.serve_forever.assert_called_once()


class TestInstallGitGuardsCommand(AltitudeCase):
    def test_cli_installs_guards_in_the_current_repository(self):
        git("init", "-q", cwd=self.repo)
        proc = subprocess.run([sys.executable, str(ALT), "install-git-guards"], cwd=self.repo,
                              capture_output=True, text=True, timeout=30)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        configured = git("config", "--local", "--get", "core.hooksPath", cwd=self.repo).strip()
        self.assertTrue(result["ok"])
        self.assertEqual(result["hooks_path"], configured)
        self.assertTrue(Path(configured).is_absolute())


class TestDispatchDeploymentIndependence(AltitudeCase):
    """Task launches read current origin while deployment advances only through activation."""

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        self.register("altitude", path=self.repo, self_deploy=True)
        self.quiet_engines()
        self.clone = self.tmp / "clone"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(self.clone), cwd=self.tmp)
        self.pending = config.MONITOR_DIR / dispatch.RESTART_PENDING
        self.launched = {"stdout": "started", "stderr": "", "returncode": 0,
                         "agent": {"id": "agent-1", "sessionId": "session-1"}}

    def merged_on_origin(self, path: str, body: str = "merged\n") -> str:
        """Another task's PR lands on origin/main while this checkout stays where it is."""
        return self.merged_files_on_origin({path: body})

    def merged_files_on_origin(self, files: dict[str, str]) -> str:
        """Another task's PR lands several paths in one commit while this checkout stays behind."""
        for path, body in files.items():
            target = self.clone / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        git("add", "-A", cwd=self.clone)
        git("commit", "-qm", "merge files", cwd=self.clone)
        git("push", "-q", "origin", "main", cwd=self.clone)
        return git("rev-parse", "HEAD", cwd=self.clone).strip()

    def head(self) -> str:
        return git("rev-parse", "HEAD", cwd=self.repo).strip()

    def snapshot(self, repo):
        return (git("rev-parse", "HEAD", cwd=repo), git("branch", "--show-current", cwd=repo),
                git("status", "--porcelain", "--untracked-files=all", cwd=repo),
                git("diff", "--binary", cwd=repo), git("diff", "--cached", "--binary", cwd=repo))

    def dispatch_preserves_deployment(self, expected_origin):
        task = T.new("altitude", "Independent dispatch", "Dispatch it.", actor="burak")
        before = self.snapshot(self.repo)
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=self.launched), \
             mock.patch("altitude.incidents.system_fault") as fault:
            dispatch.run("altitude", task["slug"])
        self.assertEqual(self.snapshot(self.repo), before)
        launched = S.load_task("altitude", task["slug"])
        self.assertEqual(launched["state"], "running")
        self.assertEqual(git("rev-parse", "HEAD", cwd=launched["worktree"]).strip(), expected_origin)
        fault.assert_not_called()
        self.assertFalse(self.pending.exists())
        return launched

    def test_new_task_uses_remote_commit_without_advancing_deployment(self):
        merged = self.merged_on_origin("web/README.md")
        self.dispatch_preserves_deployment(merged)

    def test_independent_web_deployment_marks_activation_pending(self):
        task = T.new("altitude", "Dispatch behind web", "Dispatch it.", actor="burak")
        inputs = {
            "web/src/app.tsx": "export default 1;\n",
            "web/design/tokens.css": ":root {}\n",
            "web/index.html": "<div id=\"root\"></div>\n",
            "web/package.json": "{}\n",
            "web/pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "web/tsconfig.json": "{}\n",
            "web/vite.config.ts": "export default {};\n",
        }
        merged = self.merged_files_on_origin(inputs)
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=self.launched), \
             mock.patch("altitude.incidents.system_fault") as fault:
            dispatch.run("altitude", task["slug"])
            self.assertNotEqual(self.head(), merged)
            self.assertFalse(self.pending.exists())
            dispatch.self_deploy_fast_forward("altitude", task["slug"])

        self.assertEqual(self.head(), merged)
        pend = S.read_json(self.pending, {})
        self.assertEqual(pend.get("files"), sorted(inputs))
        self.assertIn("the deployed web bundle is older than main", fyi_rows("altitude")[-1]["text"])
        fault.assert_not_called()

    def test_independent_backend_deployment_marks_activation_pending(self):
        task = T.new("altitude", "Dispatch behind code", "Dispatch it.", actor="burak")
        merged = self.merged_on_origin("altitude/x.py", "# new\n")
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=self.launched), \
             mock.patch("altitude.incidents.system_fault") as fault:
            dispatch.run("altitude", task["slug"])
            self.assertNotEqual(self.head(), merged)
            self.assertFalse(self.pending.exists())
            dispatch.self_deploy_fast_forward("altitude", task["slug"])

        self.assertEqual(self.head(), merged)
        pend = S.read_json(self.pending, {})
        self.assertEqual((pend.get("files"), pend.get("head")), (["altitude/x.py"], merged))
        self.assertIn("the running Altitude backend is older than main", fyi_rows("altitude")[-1]["text"])
        fault.assert_not_called()                                 # flagged for an authorized restart, never restarted

    def test_decision_11_tick_discovers_merge_and_requests_activation_while_its_worker_runs(self):
        task = T.new("altitude", "Still running after merge", "Finish the delivery.", actor="burak")
        task.update(state="running", agent_id="detached", session_id="same-session")
        S.save_task("altitude", task)
        merged = self.merged_on_origin("altitude/x.py", "# merged while worker runs\n")
        with mock.patch.object(server.engines, "refresh_quotas"), \
             mock.patch.object(server, "morning_digest"), \
             mock.patch.object(server, "spawn"), \
             mock.patch.object(dispatch, "poll", return_value=[]), \
             mock.patch.object(server, "_request_restart_unit", return_value={"ok": True, "unit": "test-restart"}) as restart:
            server.tick()
        self.assertEqual(self.head(), merged)
        self.assertEqual(S.load_task("altitude", task["slug"])["state"], "running")
        self.assertEqual(S.read_json(self.pending)["unit"], "test-restart")
        restart.assert_called_once()

    def test_same_owner_resume_preserves_both_dirty_trees_without_remote_access(self):
        task = T.new("altitude", "Resume own edits", "Resume it.", actor="burak")
        slug = task["slug"]
        worktree = add_worktree(self.repo, slug)
        task.update({"state": "blocked", "attempt": 1, "agent_id": "agent-0", "session_id": "session-0",
                     "worktree": str(worktree), "branch": f"worktree-{slug}", "blocked_reason": "waiting"})
        S.save_task("altitude", task)
        for tree in (self.repo, worktree):
            (tree / "README.md").write_text("staged edits\n")
            git("add", "README.md", cwd=tree)
            (tree / "README.md").write_text("working edits\n")
            (tree / "draft.bin").write_bytes(b"\x00untracked\xff")
        before = [self.snapshot(tree) for tree in (self.repo, worktree)]
        self.merged_on_origin("templates/t.md")
        git("remote", "set-url", "origin", str(self.tmp / "missing-origin"), cwd=self.repo)
        message = T.message("altitude", slug, "l3", "Continue your edits", by="l3")
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "worker_live", return_value=False), \
             mock.patch.object(dispatch.engines, "resume_l2", return_value=self.launched) as launch, \
             mock.patch.object(git_policy, "fetch_origin", side_effect=AssertionError("resume must not fetch")), \
             mock.patch("altitude.incidents.system_fault") as fault:
            dispatch.resume("altitude", slug)
        self.assertEqual([self.snapshot(tree) for tree in (self.repo, worktree)], before)
        for tree in (self.repo, worktree):
            self.assertEqual((tree / "draft.bin").read_bytes(), b"\x00untracked\xff")
        self.assertEqual(S.load_task("altitude", slug)["state"], "running")
        self.assertEqual(launch.call_args.args[2], "session-0")
        self.assertIn(message["text"], launch.call_args.args[3])
        self.assertEqual(T.pending("altitude", slug), [])
        fault.assert_not_called()

    def test_dirty_deployment_failure_remains_visible_without_blocking_dispatch(self):
        merged = self.merged_on_origin("web/src/app.tsx")
        (self.repo / "README.md").write_text("staged\n")
        git("add", "README.md", cwd=self.repo)
        (self.repo / "README.md").write_text("working\n")
        (self.repo / "untracked.bin").write_bytes(b"\x00draft\xff")
        task = self.dispatch_preserves_deployment(merged)
        before = self.snapshot(self.repo)
        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.pull_after_done("altitude", task)
        self.assertIn("self-deploy refused", notes[0])
        self.assertEqual(fault.call_args.args[0], "self-deploy")
        self.assertNotIn("task", fault.call_args.kwargs)
        self.assertIn("self-deploy refused", fyi_rows("altitude")[-1]["text"])
        self.assertEqual(S.load_task("altitude", task["slug"])["state"], "running")
        self.assertEqual(self.snapshot(self.repo), before)
        self.assertEqual((self.repo / "untracked.bin").read_bytes(), b"\x00draft\xff")

    def test_unavailable_origin_blocks_fresh_dispatch_and_retains_its_inbox(self):
        task = T.new("altitude", "Remote unavailable", "Use a trusted fresh base.", actor="burak")
        T.block("altitude", task["slug"], "Waiting before first launch")
        message = T.message("altitude", task["slug"], "l3", "Preserve this instruction", by="l3")
        T.requeue("altitude", task["slug"])
        git("remote", "set-url", "origin", str(self.tmp / "missing-origin"), cwd=self.repo)
        before = self.snapshot(self.repo)
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2") as launch:
            with self.assertRaisesRegex(T.TransitionError, "git fetch origin main"):
                dispatch.run("altitude", task["slug"])
        launch.assert_not_called()
        blocked = S.load_task("altitude", task["slug"])
        self.assertEqual((blocked["state"], blocked["fault"]), ("blocked", "task-git-provenance"))
        self.assertEqual([row["id"] for row in T.pending("altitude", task["slug"])], [message["id"]])
        self.assertEqual(self.snapshot(self.repo), before)
        self.assertFalse((self.repo / ".claude/worktrees" / task["slug"]).exists())

    def test_a_checkout_ahead_of_origin_dispatches_from_origin(self):
        origin = git_policy.capture_origin_sha(self.repo)
        (self.repo / "direct.txt").write_text("local deployment commit\n")
        git("add", "direct.txt", cwd=self.repo)
        git("commit", "-qm", "direct main commit", cwd=self.repo)
        task = self.dispatch_preserves_deployment(origin)
        self.assertFalse((Path(task["worktree"]) / "direct.txt").exists())

    def test_a_diverged_checkout_dispatches_from_current_origin(self):
        merged = self.merged_on_origin("web/src/app.tsx")
        (self.repo / "direct.txt").write_text("local deployment commit\n")
        git("add", "direct.txt", cwd=self.repo)
        git("commit", "-qm", "direct main commit", cwd=self.repo)
        task = self.dispatch_preserves_deployment(merged)
        self.assertFalse((Path(task["worktree"]) / "direct.txt").exists())

    def test_a_checkout_off_main_dispatches_from_current_origin(self):
        merged = self.merged_on_origin("web/src/app.tsx")
        git("checkout", "-q", "-b", "side", cwd=self.repo)
        self.dispatch_preserves_deployment(merged)


if __name__ == "__main__":
    unittest.main()
