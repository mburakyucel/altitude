"""Decision 39 guard: inspect executed shell text, not quoted prose."""
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class TestGuardExecutedText(unittest.TestCase):
    def run_guard(self, command):
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "guard.py")],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True,
            text=True,
        )

    def assert_blocked(self, command):
        result = self.run_guard(command)
        self.assertEqual(result.returncode, 2, (command, result.stderr))
        return result

    def assert_allowed(self, command):
        result = self.run_guard(command)
        self.assertEqual(result.returncode, 0, (command, result.stderr))

    def test_blocks_executed_service_restart(self):
        self.assert_blocked("systemctl --user restart altitude")
        self.assert_blocked('systemctl --user restart "altitude"')

    def test_ignores_heredoc_body(self):
        self.assert_allowed(
            "cat > report.md <<'EOF'\n"
            "after merge this needs systemctl --user restart altitude\n"
            "EOF\n"
        )

    def test_handles_multiple_and_tab_stripped_heredocs(self):
        self.assert_allowed(
            'cat <<ONE <<-"TWO"\n'
            "systemctl --user restart altitude\n"
            "ONE\n"
            "\tsystemctl --user restart altitude\n"
            "\tTWO\n"
            "systemctl --user status altitude\n"
        )

    def test_unterminated_heredoc_drops_to_end(self):
        self.assert_allowed(
            "cat <<EOF\n"
            "systemctl --user restart altitude\n"
        )

    def test_heredoc_like_text_does_not_hide_later_commands(self):
        for command in (
            "echo ok # <<EOF\nsystemctl --user restart altitude",
            'cat <<< "ordinary input"\nsystemctl --user restart altitude',
            "echo $((1 << 2))\nsystemctl --user restart altitude",
            'alt fyi "quoted line\n<<EOF\nstill quoted"\nsystemctl --user restart altitude',
        ):
            self.assert_blocked(command)

    def test_ignores_heredoc_inside_quoted_shell_code(self):
        self.assert_allowed(
            'bash -c "cat <<\'EOF\'\n'
            "systemctl --user restart altitude\n"
            'EOF\n"'
        )

    def test_ignores_quoted_prose_arguments(self):
        self.assert_allowed('alt fyi "after merge this needs systemctl --user restart altitude"')
        self.assert_allowed('git commit -m "note: systemctl --user restart altitude after merge"')

    def test_blocks_quoted_shell_code_recursively(self):
        for command in (
            'bash -c "systemctl --user restart altitude"',
            'bash -lc "systemctl --user restart altitude"',
            'bash -c "bash -c \'systemctl --user restart altitude\'"',
            'eval "systemctl --user restart altitude"',
        ):
            self.assert_blocked(command)

    def test_other_rules_keep_executed_operands(self):
        for command in (
            "sudo ufw allow 8890",
            "rm -rf ~/.altitude",
            'rm -rf "$HOME/.altitude"',
            "git push --force origin main",
        ):
            self.assert_blocked(command)
        self.assert_allowed("git push -u origin HEAD")

    def test_port_rule(self):
        self.assert_blocked("ALTITUDE_PORT=8890 bin/alt serve")
        self.assert_blocked('ALTITUDE_PORT="8890" bin/alt serve')
        self.assert_allowed("ALTITUDE_TIMERS=0 ALTITUDE_PORT=8931 bin/alt serve")
        self.assert_allowed('alt fyi "ALTITUDE_PORT=8890 is reserved"')

    def test_unterminated_quote_fails_closed(self):
        self.assert_blocked('alt fyi "unfinished: systemctl --user restart altitude')

    def test_refusal_reports_matched_fragment(self):
        result = self.assert_blocked(
            "echo " + ("ordinary-text-" * 20) + "; systemctl --user restart altitude"
        )
        self.assertIn("Matched: systemctl --user restart altitude", result.stderr)


if __name__ == "__main__":
    unittest.main()
