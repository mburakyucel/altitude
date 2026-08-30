"""The user-facing Git policy boundaries are wired before Altitude can mutate or dispatch."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, git_policy, server  # noqa: E402
from altitude import tasks as T  # noqa: E402


class TestDispatchWorktreePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="altitude-dispatch-worktree-policy-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.remote = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        for key, value in (("user.email", "test@example.invalid"), ("user.name", "Test User")):
            subprocess.run(["git", "-C", str(self.repo), "config", key, value], check=True)
        (self.repo / ".gitignore").write_text(".claude/\n")
        (self.repo / "README.md").write_text("initial\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "initial"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main"], check=True)
        self.origin_sha = git_policy.capture_origin_sha(self.repo)

    def git(self, *args, cwd=None, check=True):
        result = subprocess.run(
            ["git", "-C", str(cwd or self.repo), *args], capture_output=True, text=True
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result

    def test_new_worktree_uses_captured_origin_and_valid_existing_history_is_reused(self):
        worktree = dispatch._task_worktree(self.repo, "demo", "safe-task", self.origin_sha)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree).stdout.strip(), self.origin_sha)
        self.assertEqual(self.git("branch", "--show-current", cwd=worktree).stdout.strip(), "worktree-safe-task")
        (worktree / "safe.txt").write_text("safe\n")
        self.git("add", "safe.txt", cwd=worktree)
        self.git("commit", "-q", "-m", "safe", "-m", "Altitude-Task: demo/safe-task", cwd=worktree)

        self.assertEqual(dispatch._task_worktree(self.repo, "demo", "safe-task", self.origin_sha), worktree)

    def test_existing_branch_with_direct_commit_is_refused(self):
        staging = self.repo / ".claude" / "worktrees" / "bad-task"
        self.git("worktree", "add", "-q", "-b", "worktree-bad-task", str(staging), self.origin_sha)
        (staging / "bad.txt").write_text("bad\n")
        self.git("add", "bad.txt", cwd=staging)
        self.git("commit", "-q", "-m", "direct commit", cwd=staging)

        with self.assertRaisesRegex(T.TransitionError, "without exact.*provenance"):
            dispatch._task_worktree(self.repo, "demo", "bad-task", self.origin_sha)
        self.assertTrue(staging.exists())

    def test_orphan_task_branch_is_not_reattached_after_validation_races(self):
        staging = self.tmp / "orphan-staging"
        self.git("worktree", "add", "-q", "-b", "worktree-orphan-task", str(staging), self.origin_sha)
        self.git("worktree", "remove", str(staging))

        with self.assertRaisesRegex(T.TransitionError, "exists without its registered worktree"):
            dispatch._task_worktree(self.repo, "demo", "orphan-task", self.origin_sha)


class TestDispatchBoundaryOrdering(unittest.TestCase):
    def test_unsafe_main_refuses_before_task_or_agent_mutation(self):
        task = {
            "slug": "blocked", "state": "approved", "dispatching": None,
            "envelope": {"max_turns": 5},
        }
        with mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _project: contextlib.nullcontext()), \
             mock.patch.object(dispatch.S, "load_task", return_value=task), \
             mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.config, "project_path", return_value=Path("/tmp/unsafe-main")), \
             mock.patch.object(
                 dispatch.git_policy, "fetch_and_require_exact_base",
                 side_effect=git_policy.GitPolicyError("main is ahead"),
             ), \
             mock.patch("altitude.improve.system_fault") as fault, \
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


class TestServiceGitPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="altitude-service-git-policy-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name, value in {
            "ROOT": self.tmp / "home",
            "MONITOR_DIR": self.tmp / "home" / "monitor",
            "PROJECTS_FILE": self.tmp / "home" / "projects.json",
            "REPO": self.tmp / "repo",
        }.items():
            old = getattr(config, name)
            setattr(config, name, value)
            self.addCleanup(setattr, config, name, old)
        config.ensure_root()
        config.REPO.mkdir()
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


class TestInstallGitGuardsCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="altitude-install-git-guards-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True, text=True)

    def test_cli_installs_guards_in_the_current_repository(self):
        alt = Path(__file__).resolve().parent.parent / "bin" / "alt"
        env = os.environ.copy()
        env["ALTITUDE_HOME"] = str(self.tmp / "home")

        proc = subprocess.run([str(alt), "install-git-guards"], cwd=self.repo,
                              capture_output=True, text=True, env=env, timeout=30)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        configured = subprocess.run(
            ["git", "config", "--local", "--get", "core.hooksPath"], cwd=self.repo,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertTrue(result["ok"])
        self.assertEqual(result["hooks_path"], configured)
        self.assertTrue(Path(configured).is_absolute())


if __name__ == "__main__":
    unittest.main()
