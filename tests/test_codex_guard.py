"""Codex guard adapter preserves the hardened shell and Git boundaries."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
from altitude import dispatch


class TestCodexGuardAdapter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-codex-guard-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.state = Path(self.temp.name) / "state"
        self.task_dir = self.state / "demo" / "tasks" / "current"
        (self.state / "demo" / "tasks" / "other").mkdir(parents=True)
        (self.state / "monitor").mkdir()
        self.hooks = Path(self.temp.name) / "required-hooks"
        self.hooks.mkdir()
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.repo / "README.md").write_text("safe\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("switch", "-c", "task/topic")
        self.git("config", "core.hooksPath", str(self.hooks))

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def run_guard(self, tool_name, tool_input):
        trusted_git_dir = Path(self.git("rev-parse", "--absolute-git-dir"))
        with mock.patch.object(dispatch.config, "HOOKS", self.hooks):
            guard_env = dispatch._codex_guard_env(self.repo, trusted_git_dir)
        env = {
            **os.environ,
            **guard_env,
            "ALTITUDE_HOME": str(self.state),
            "ALTITUDE_PROJECT": "demo",
            "ALTITUDE_TASK": "current",
        }
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "codex_guard.py")],
            input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
            capture_output=True, text=True, cwd=self.repo, env=env,
        )

    def assert_blocked(self, tool_name, tool_input):
        result = self.run_guard(tool_name, tool_input)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stderr.strip())
        return result

    def test_shell_adapter_forwards_exit_and_stderr(self):
        allowed = self.run_guard("exec_command", {"cmd": "echo safe"})
        blocked = self.assert_blocked(
            "exec_command",
            {"cmd": 'timeout --signal KILL 5 bash -c "systemctl --user restart altitude"'},
        )
        self.assertEqual(allowed.returncode, 0)
        self.assertIn("altitude guard", blocked.stderr)

    def test_adapter_preserves_heredoc_semantics(self):
        data = self.run_guard(
            "shell", {"command": "cat <<'EOF'\nsystemctl --user restart altitude\nEOF\n"},
        )
        shell = self.assert_blocked(
            "bash", {"command": "bash <<'EOF'\nsystemctl --user restart altitude\nEOF\n"},
        )
        self.assertEqual(data.returncode, 0, data.stderr)
        self.assertIn("service units", shell.stderr)

    def test_exact_common_dir_hooks_bypass_chain_is_denied(self):
        command = (
            'printf "[core]\\nhooksPath=/dev/null\\n" '
            '>> "$(git rev-parse --git-common-dir)/config"; '
            'git --git-dir="$(git rev-parse --git-common-dir)" push origin HEAD:main'
        )
        result = self.assert_blocked("exec_command", {"cmd": command})
        self.assertIn("blocked", result.stderr)

    def test_custom_patch_targets_cannot_reach_git_common_dir(self):
        common = Path(self.git("rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = self.repo / common
        target = common.resolve() / "config"
        for marker in ("Update", "Add", "Delete"):
            patch = f"*** Begin Patch\n*** {marker} File: {target}\n*** End Patch\n"
            self.assert_blocked("apply_patch", {"patch": patch})

        relative = "*** Begin Patch\n*** Update File: .git/config\n*** End Patch\n"
        self.assert_blocked("apply_patch", {"patch": relative})
        moved = f"*** Begin Patch\n*** Update File: README.md\n*** Move to: {target}\n*** End Patch\n"
        self.assert_blocked("apply_patch", {"patch": moved})

        safe = "*** Begin Patch\n*** Update File: README.md\n*** End Patch\n"
        result = self.run_guard("apply_patch", {"patch": safe})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unified_patch_target_cannot_reach_git_common_dir(self):
        common = Path(self.git("rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = self.repo / common
        patch = f"--- {common.resolve()}/config\n+++ {common.resolve()}/config\n"
        self.assert_blocked("apply_patch", {"patch": patch})

    def test_preflight_fails_closed_if_required_hook_path_changes(self):
        self.git("config", "core.hooksPath", "/dev/null")
        result = self.assert_blocked("exec_command", {"cmd": "echo safe"})
        self.assertIn("hook path changed", result.stderr)

    def test_non_shell_state_writes_are_checkpoint_only(self):
        forbidden = (
            self.task_dir / "status.json",
            self.task_dir / "events.jsonl",
            self.state / "monitor" / "counts-demo.json",
            self.state / "demo" / "tasks" / "other" / "report.md",
        )
        for index, target in enumerate(forbidden):
            with self.subTest(target=target):
                self.assert_blocked("write", {"file_path": str(target), "content": str(index)})
        for name in ("report.md", "report.json", "progress.md"):
            allowed = self.run_guard("write", {"file_path": str(self.task_dir / name), "content": "checkpoint"})
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
        read = self.run_guard("read", {"file_path": str(self.task_dir / "status.json")})
        self.assertEqual(read.returncode, 0, read.stderr)

    def test_patch_cannot_rewrite_task_status_or_other_task(self):
        for target in (
            self.task_dir / "status.json",
            self.task_dir / "events.jsonl",
            self.state / "demo" / "tasks" / "other" / "progress.md",
        ):
            patch = f"*** Begin Patch\n*** Update File: {target}\n*** End Patch\n"
            self.assert_blocked("apply_patch", {"patch": patch})

    def test_shell_cannot_write_state_directly_but_alt_is_trusted(self):
        commands = (
            'printf x > "$ALTITUDE_HOME/demo/tasks/current/status.json"',
            f"printf x | tee {self.state / 'monitor' / 'counts-demo.json'}",
            'python3 -c \'import os; open(os.environ["ALTITUDE_HOME"] + "/demo/tasks/current/events.jsonl", "w").write("x")\'',
            f"touch {self.state / 'demo' / 'tasks' / 'other' / 'status.json'}",
        )
        for command in commands:
            self.assert_blocked("exec_command", {"cmd": command})
        allowed = self.run_guard("exec_command", {"cmd": "alt task status current"})
        self.assertEqual(allowed.returncode, 0, allowed.stderr)


if __name__ == "__main__":
    unittest.main()
