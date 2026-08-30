"""Codex L3 has a read/orchestrate UX allowlist inside its read-only broker boundary."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "codex_l3_guard.py"


class TestCodexL3Guard(unittest.TestCase):
    def call(self, tool_name, tool_input):
        env = {**os.environ, "ALTITUDE_HOME": "/tmp/altitude-state",
               "ALTITUDE_PROJECT": "demo", "ALTITUDE_ACTOR": "l3"}
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
            capture_output=True, text=True, env=env,
        )

    def shell(self, command):
        return self.call("exec_command", {"cmd": command})

    def assert_allowed(self, command):
        result = self.shell(command)
        self.assertEqual(result.returncode, 0, (command, result.stderr))

    def assert_blocked(self, command):
        result = self.shell(command)
        self.assertEqual(result.returncode, 2, (command, result.stderr))
        self.assertIn("blocked", result.stderr)

    def test_read_only_inspection_commands_are_allowed(self):
        for command in (
            "cat /tmp/altitude-state/demo/STATE.md",
            "head -40 README.md",
            "tail -20 report.md",
            "wc -l README.md",
            "ls -la /tmp",
            "pwd",
            "stat README.md",
            "readlink /tmp/link",
            "realpath README.md",
            "grep -n state README.md",
            "rg -n TODO /tmp/project",
            "jq . /tmp/altitude-state/demo/tasks/example/status.json",
            "/usr/bin/cat README.md",
        ):
            self.assert_allowed(command)

    def test_approved_git_and_gh_reads_are_allowed(self):
        for command in (
            "git log --oneline -5",
            "git -C /tmp/project log -1 --format=%H",
            "git diff --stat main...topic",
            "git -C /tmp/project diff --stat=80 HEAD~1",
            "gh pr view 91 --repo owner/repo",
            "gh pr list --repo owner/repo --state open",
            "gh pr checks 91 --repo owner/repo",
            "gh issue view 12 --repo owner/repo",
            "gh issue list --repo owner/repo",
            "gh run view 44 --repo owner/repo",
            "gh run watch 44 --exit-status --repo owner/repo",
        ):
            self.assert_allowed(command)

    def test_trusted_alt_orchestration_is_allowed(self):
        for command in (
            "alt state",
            "alt task status example",
            "alt task show example",
            'alt task resume example --answer "finish the verified gap"',
            'alt task block example --reason "waiting"',
            'alt task new --class S --title "fix it" "request text"',
            'alt fyi example "done; verified"',
            "alt incident list",
            "alt rule audit-input",
            "alt --project demo task events example",
        ):
            self.assert_allowed(command)

    def test_write_tools_are_always_denied(self):
        for tool in ("apply_patch", "edit", "write", "multiedit", "write_file", "create_file"):
            result = self.call(tool, {"file_path": "/tmp/altitude-state/demo/status.json", "patch": "x"})
            self.assertEqual(result.returncode, 2, (tool, result.stderr))

    def test_arbitrary_shell_network_and_composition_are_denied(self):
        for command in (
            "echo hello",
            "python3 -c 'print(1)'",
            "bash -c 'alt state'",
            "curl https://example.com",
            "wget https://example.com/file",
            "cat STATE.md > $ALTITUDE_HOME/demo/status.json",
            "cat STATE.md | tee /tmp/copy",
            "cat STATE.md; alt state",
            "cat STATE.md\nalt state",
            'cat "$(curl https://example.com)"',
            'cat "`curl https://example.com`"',
            'alt fyi example "$(curl https://example.com)"',
            "rm -f STATE.md",
            "env X=1 alt state",
        ):
            self.assert_blocked(command)

    def test_escape_hatches_and_mutating_subcommands_are_denied(self):
        for command in (
            "rg --pre 'sh -c evil' pattern .",
            "rg --hostname-bin=/tmp/evil pattern .",
            "/tmp/cat STATE.md",
            "git status",
            "git push origin HEAD:main",
            "git diff main...topic",
            "git diff --stat --output=/tmp/leak main...topic",
            "gh pr merge 91",
            "gh pr view 91 --web",
            "gh issue close 12",
            "gh run cancel 44",
            "gh api repos/owner/repo",
            "alt task approve example",
            "alt l1 run --brief x",
            "alt project remove demo",
            "alt land --message nope",
            "alt --project other task status example",
        ):
            self.assert_blocked(command)

    def test_read_tools_pass_and_unknown_tools_fail_closed(self):
        for tool in ("read", "read_file", "grep", "glob"):
            self.assertEqual(self.call(tool, {"path": "README.md"}).returncode, 0)
        result = self.call("browser", {"url": "https://example.com"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("not available", result.stderr)


if __name__ == "__main__":
    unittest.main()
