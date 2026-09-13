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
    def test_unsafe_main_refuses_before_task_or_agent_mutation(self):
        task = {
            "slug": "blocked", "state": "queued", "dispatching": None,
        }
        with mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _project: contextlib.nullcontext()), \
             mock.patch.object(dispatch.S, "load_task", return_value=task), \
             mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.config, "project_path", return_value=Path("/tmp/unsafe-main")), \
             mock.patch.object(
                 dispatch.git_policy, "fetch_and_require_exact_base",
                 side_effect=git_policy.GitPolicyError("main is ahead"),
             ), \
             mock.patch("altitude.incidents.system_fault") as fault, \
             mock.patch.object(dispatch.S, "save_task") as save, \
             mock.patch.object(dispatch.S, "write_json") as write_json, \
             mock.patch.object(dispatch.T, "brief") as brief, \
             mock.patch.object(dispatch.engines, "start_l2") as launch:
            with self.assertRaisesRegex(T.TransitionError, "main is ahead"):
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
        worktree = add_worktree(self.repo, self.slug)
        task.update({"state": "blocked", "attempt": 1, "session_id": "thread-old", "agent_id": "agent-old",
                     "l2_engine": "codex", "worktree": str(worktree), "blocked_reason": "waiting", "waiting_on": "l3"})
        S.save_task(self.project, task)

    def test_real_daemon_provenance_failure_blocks_and_reports_without_losing_the_message(self):
        message = T.message(self.project, self.slug, "l3", "The restriction is fixed.", by="l3")
        error = git_policy.GitPolicyError("git fetch origin main: genuine remote provenance failure")

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch, "settle_deploy_checkout"), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", side_effect=error), \
             mock.patch.object(engines, "resume_l2") as launch, \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(T.TransitionError, "genuine remote provenance failure"):
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
        error = git_policy.GitPolicyError("remote provenance is still invalid")
        arrived = []

        def fail_after_message(*_args, **_kwargs):
            arrived.append(T.message(self.project, self.slug, "l3", "Second answer.", by="l3"))
            raise error

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch, "settle_deploy_checkout"), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", side_effect=fail_after_message), \
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
             mock.patch.object(git_policy, "service_preflight",
                               side_effect=git_policy.GitPolicyError("main is ahead")) as preflight, \
             mock.patch.object(git_policy, "require_hooks_installed") as hooks, \
             mock.patch.object(server.threading, "Thread") as thread, \
             mock.patch.object(server, "ThreadingHTTPServer") as httpd, \
             mock.patch.object(server, "log") as log:
            with self.assertRaises(SystemExit) as stopped:
                server.main()

        self.assertEqual(stopped.exception.code, 1)
        preflight.assert_called_once_with(config.REPO)
        hooks.assert_not_called()
        self.assertTrue(self.pending.exists())
        thread.assert_not_called()
        httpd.assert_not_called()
        self.assertIn("service startup refused", log.call_args.args[0])

    def test_failed_api_bind_keeps_restart_hold_even_with_a_safe_checkout(self):
        with mock.patch.dict(os.environ, {"ALTITUDE_SERVICE": "1", "ALTITUDE_TIMERS": "0"}), \
             mock.patch.object(git_policy, "service_preflight") as preflight, \
             mock.patch.object(git_policy, "require_hooks_installed") as hooks, \
             mock.patch.object(server, "ThreadingHTTPServer", side_effect=OSError("stop after preflight")) as httpd, \
             mock.patch.object(server, "log"):
            with self.assertRaises(SystemExit) as stopped:
                server.main()

        self.assertEqual(stopped.exception.code, 1)
        preflight.assert_called_once_with(config.REPO)
        hooks.assert_called_once_with(config.REPO)
        self.assertTrue(self.pending.exists())
        httpd.assert_called_once()

    def test_ready_replacement_releases_restart_hold_before_serving(self):
        def serve():
            self.assertFalse(self.pending.exists())
        with mock.patch.dict(os.environ, {"ALTITUDE_SERVICE": "1", "ALTITUDE_TIMERS": "0"}), \
             mock.patch.object(git_policy, "service_preflight"), \
             mock.patch.object(git_policy, "require_hooks_installed"), \
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


class TestSelfDeployFastForwardAtDispatch(AltitudeCase):
    """I-20260903-075410: an L2 merges its PR while its task is still running, so the deployment checkout stays one
    commit behind origin/main until that report lands. The gate fast-forwards that checkout instead of refusing."""

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

    def dispatch_refuses(self, pattern: str) -> None:
        task = T.new("altitude", "Dispatch refused", "Dispatch it.", actor="burak")
        head = self.head()
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2") as launch, \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(T.TransitionError, pattern):
                dispatch.run("altitude", task["slug"])
        self.assertEqual(self.head(), head)                       # a checkout it may not move is left alone
        launch.assert_not_called()
        fault.assert_called_once()

    def test_an_unrelated_web_file_is_fast_forwarded_without_pending_activation(self):
        task = T.new("altitude", "Dispatch behind main", "Dispatch it.", actor="burak")
        merged = self.merged_on_origin("web/README.md")
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=self.launched), \
             mock.patch("altitude.incidents.system_fault") as fault:
            dispatch.run("altitude", task["slug"])

        self.assertEqual(self.head(), merged)
        self.assertEqual(S.load_task("altitude", task["slug"])["state"], "running")
        fault.assert_not_called()
        self.assertFalse(self.pending.exists())

    def test_web_source_and_build_inputs_mark_activation_pending(self):
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

        self.assertEqual(self.head(), merged)
        pend = S.read_json(self.pending, {})
        self.assertEqual(pend.get("files"), sorted(inputs))
        self.assertIn("the deployed web bundle is older than main", fyi_rows("altitude")[-1]["text"])
        fault.assert_not_called()

    def test_backend_code_pulled_at_dispatch_marks_activation_pending(self):
        task = T.new("altitude", "Dispatch behind code", "Dispatch it.", actor="burak")
        merged = self.merged_on_origin("altitude/x.py", "# new\n")
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=self.launched), \
             mock.patch("altitude.incidents.system_fault") as fault:
            dispatch.run("altitude", task["slug"])

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
        with mock.patch.object(server.quota_codex, "refresh_if_due"), \
             mock.patch.object(server, "morning_digest"), \
             mock.patch.object(server, "spawn"), \
             mock.patch.object(dispatch, "poll", return_value=[]), \
             mock.patch.object(server, "_request_restart_unit", return_value={"ok": True, "unit": "test-restart"}) as restart:
            server.tick()
        self.assertEqual(self.head(), merged)
        self.assertEqual(S.load_task("altitude", task["slug"])["state"], "running")
        self.assertEqual(S.read_json(self.pending)["unit"], "test-restart")
        restart.assert_called_once()

    def test_a_resumed_task_fast_forwards_the_same_way(self):
        task = T.new("altitude", "Resume behind main", "Resume it.", actor="burak")
        slug = task["slug"]
        worktree = add_worktree(self.repo, slug)
        task.update({"state": "blocked", "attempt": 1, "agent_id": "agent-0", "session_id": "session-0",
                     "worktree": str(worktree), "branch": f"worktree-{slug}", "blocked_reason": "waiting"})
        S.save_task("altitude", task)
        merged = self.merged_on_origin("templates/t.md")
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "worker_live", return_value=False), \
             mock.patch.object(dispatch.engines, "resume_l2", return_value=self.launched), \
             mock.patch("altitude.incidents.system_fault") as fault:
            dispatch.resume("altitude", slug)

        self.assertEqual(self.head(), merged)
        self.assertEqual(S.load_task("altitude", slug)["state"], "running")
        fault.assert_not_called()

    def test_a_dirty_checkout_behind_origin_still_refuses(self):
        self.merged_on_origin("web/src/app.tsx")
        (self.repo / "README.md").write_text("uncommitted\n")
        self.dispatch_refuses("uncommitted changes")

    def test_a_checkout_ahead_of_origin_still_refuses(self):
        (self.repo / "direct.txt").write_text("developed in the deployment checkout\n")
        git("add", "direct.txt", cwd=self.repo)
        git("commit", "-qm", "direct main commit", cwd=self.repo)
        self.dispatch_refuses("ahead of origin/main")

    def test_a_diverged_checkout_still_refuses(self):
        self.merged_on_origin("web/src/app.tsx")
        (self.repo / "direct.txt").write_text("developed in the deployment checkout\n")
        git("add", "direct.txt", cwd=self.repo)
        git("commit", "-qm", "direct main commit", cwd=self.repo)
        self.dispatch_refuses("diverged from origin/main")

    def test_a_checkout_off_main_still_refuses(self):
        self.merged_on_origin("web/src/app.tsx")
        git("checkout", "-q", "-b", "side", cwd=self.repo)
        self.dispatch_refuses("checkout is on side, expected main")


if __name__ == "__main__":
    unittest.main()
