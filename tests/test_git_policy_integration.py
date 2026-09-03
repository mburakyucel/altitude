"""The user-facing Git policy boundaries are wired before Altitude can mutate or dispatch."""
import contextlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support import ALT, AltitudeCase, git, make_repo
from altitude import config, dispatch, git_policy, server
from altitude import tasks as T


class TestDispatchWorktreePolicy(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.origin_sha = git_policy.capture_origin_sha(self.repo)

    def test_new_worktree_uses_captured_origin_and_valid_existing_history_is_reused(self):
        worktree = dispatch._task_worktree(self.repo, "demo", "safe-task", self.origin_sha)
        self.assertEqual(git("rev-parse", "HEAD", cwd=worktree).strip(), self.origin_sha)
        self.assertEqual(git("branch", "--show-current", cwd=worktree).strip(), "worktree-safe-task")
        (worktree / "safe.txt").write_text("safe\n")
        git("add", "safe.txt", cwd=worktree)
        git("commit", "-q", "-m", "safe", "-m", "Altitude-Task: demo/safe-task", cwd=worktree)

        self.assertEqual(dispatch._task_worktree(self.repo, "demo", "safe-task", self.origin_sha), worktree)

    def test_existing_branch_with_direct_commit_is_refused(self):
        staging = self.repo / ".claude" / "worktrees" / "bad-task"
        git("worktree", "add", "-q", "-b", "worktree-bad-task", str(staging), self.origin_sha, cwd=self.repo)
        (staging / "bad.txt").write_text("bad\n")
        git("add", "bad.txt", cwd=staging)
        git("commit", "-q", "-m", "direct commit", cwd=staging)

        with self.assertRaisesRegex(T.TransitionError, "without exact.*provenance"):
            dispatch._task_worktree(self.repo, "demo", "bad-task", self.origin_sha)
        self.assertTrue(staging.exists())

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
             mock.patch.object(dispatch.engines, "claude_bg") as launch:
            with self.assertRaisesRegex(T.TransitionError, "main is ahead"):
                dispatch.run("demo", "blocked")

        fault.assert_called_once()
        save.assert_not_called()
        write_json.assert_not_called()
        brief.assert_not_called()
        launch.assert_not_called()


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

    def test_safe_service_checkout_clears_restart_flag_and_reaches_bind(self):
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
        self.assertFalse(self.pending.exists())
        httpd.assert_called_once()


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


if __name__ == "__main__":
    unittest.main()
