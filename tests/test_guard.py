"""hooks/guard.py refuses the shell forms a task must never run and lets ordinary work through."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "hooks" / "guard.py"


def run(command: str, tool: str = "Bash") -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True)


class GuardTest(unittest.TestCase):
    def assert_blocked(self, command: str, reason: str) -> None:
        result = run(command)
        self.assertEqual(result.returncode, 2, command)
        self.assertIn(reason, result.stderr)
        self.assertIn("Matched:", result.stderr)

    def assert_allowed(self, command: str) -> None:
        result = run(command)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_service_lifecycle(self):
        self.assert_blocked("systemctl --user restart altitude.service", "service units")
        self.assert_blocked("sudo systemctl stop tutor", "service units")
        self.assert_allowed("systemctl --user status altitude.service")

    def test_firewall_ports_home_and_altd(self):
        self.assert_blocked("sudo ufw allow 22", "firewall")
        self.assert_blocked("python3 -m http.server --port 8890", "service ports")
        self.assert_allowed("ALTITUDE_TIMERS=0 bin/alt serve --port 9001")
        self.assert_blocked("rm -rf ~/.altitude/monitor", "live state")
        self.assert_blocked('rm -r "$ALTITUDE_HOME"', "live state")
        self.assert_blocked("pkill -f altd", "not yours to kill")

    def test_protected_branch_and_hook_bypasses(self):
        self.assert_blocked("git push origin main", "alt land")
        self.assert_blocked("git -C ../altitude push origin HEAD:refs/heads/main", "alt land")
        self.assert_blocked("git branch -f main origin/main", "alt land")
        self.assert_blocked("git push -f", "force pushes")
        self.assert_blocked("git push --force origin worktree-my-task", "force pushes")
        self.assert_blocked("git commit --no-verify -m x", "bypass")
        self.assert_blocked("git -c core.hooksPath=/dev/null push origin topic", "hook configuration")
        self.assert_blocked("GIT_CONFIG_COUNT=1 git push", "hook configuration")
        self.assert_allowed("git push -u origin worktree-my-task")
        self.assert_allowed("git log origin/main..HEAD --oneline")
        self.assert_allowed("git config --get remote.origin.url")

    def test_heredoc_bodies_are_prose(self):
        self.assert_allowed("cat > notes.md <<'EOF'\nRun systemctl restart altitude after merging.\nEOF\n")
        self.assert_blocked("cat <<'EOF' > x\nprose\nEOF\nsystemctl restart altitude", "service units")

    def test_other_tools_and_empty_input_pass(self):
        self.assertEqual(run("systemctl restart altitude", tool="Read").returncode, 0)
        empty = subprocess.run([sys.executable, str(GUARD)], input="{}", capture_output=True, text=True)
        self.assertEqual(empty.returncode, 0)


if __name__ == "__main__":
    unittest.main()
