"""The test process never touches the operator's live state: the fixture isolates it, and config refuses it."""
import os
import signal
import subprocess
import sys
import tempfile
import socket
import unittest
from pathlib import Path

from tests.support import REPO, SUITE, OFFLINE_COMMANDS
from altitude import config


class TestIsolation(unittest.TestCase):
    def test_git_and_cli_fixtures_finish_with_open_stdin_and_captured_output(self):
        # Recurring validation stall: keep the writer open even during communicate().
        read_fd, write_fd = os.pipe()
        with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb"):
            with subprocess.Popen(
                [sys.executable, "-m", "unittest", "-v",
                 "tests.test_git_policy.TestGitPolicy.test_fetch_automatic_gc_packs_and_prunes_lagging_main",
                 "tests.test_l3_privilege.TestIssueVerbs.test_operator_close_cli_and_api_validate_the_same_operation"],
                cwd=REPO, stdin=reader, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            ) as child:
                try:
                    stdout, stderr = child.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    stdout, stderr = child.communicate(timeout=5)
                    self.fail(f"validation fixture stalled with open stdin:\n{stdout}\n{stderr}")
                self.assertEqual(child.returncode, 0, stdout + stderr)
                self.assertIn("Ran 2 tests", stderr)

    def test_suite_process_uses_throwaway_homes(self):
        self.assertEqual(Path.home(), SUITE / "home")
        self.assertEqual(config.ROOT, SUITE / "altitude")

    def test_inherited_provider_credentials_and_worker_identity_are_removed_before_import(self):
        env = dict(os.environ, CODEX_HOME="/operator/provider", CLAUDE_CONFIG_DIR="/operator/provider",
                   OPENAI_API_KEY="fixture-secret", ANTHROPIC_API_KEY="fixture-secret", GH_TOKEN="fixture-secret",
                   DBUS_SESSION_BUS_ADDRESS="unix:path=/operator/bus", ALTITUDE_ACTOR="l2", ALTITUDE_TASK="live-task",
                   ALTITUDE_OPERATOR="Private operator", ALTITUDE_PRIMARY_ENGINE="unconfigured-provider",
                   WHISPER_SOCKET="/operator/speech.sock", WHISPER_BRIDGE="127.0.0.1:8890")
        script = """from tests.support import SUITE, config
import os
from pathlib import Path
for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'XDG_RUNTIME_DIR'):
    assert Path(os.environ[key]).is_relative_to(SUITE), key
for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GH_TOKEN', 'DBUS_SESSION_BUS_ADDRESS', 'ALTITUDE_ACTOR', 'ALTITUDE_TASK'):
    assert key not in os.environ, key
assert config.ROOT.is_relative_to(SUITE)
assert config.OPERATOR == 'Operator'
assert 'ALTITUDE_PRIMARY_ENGINE' not in os.environ
from altitude import server
assert Path(server.VOICE_SOCKET).is_relative_to(SUITE)
assert server.VOICE_BRIDGE == '127.0.0.1:0'
assert server._whisper_connection() is None
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_external_commands_are_denied_in_child_clis_and_absolute_launches(self):
        for command in OFFLINE_COMMANDS:
            with self.subTest(command=command):
                result = subprocess.run([command, "--version"], capture_output=True, text=True)
                self.assertEqual(result.returncode, 86)
                self.assertIn("external executable denied", result.stderr)
                # Use an existing installed executable: absent paths must preserve FileNotFoundError.
                if Path("/usr/bin/" + command).exists():
                    with self.assertRaisesRegex(AssertionError, "external executable"):
                        subprocess.run(["/usr/bin/" + command, "--version"], capture_output=True)

    def test_python_network_clients_cannot_reach_a_provider(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(AssertionError, "non-loopback"):
                connection.connect(("203.0.113.1", 443))

    def test_loopback_clients_cannot_reach_the_operator_service(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(AssertionError, "offline tests denied"):
                connection.connect(("127.0.0.1", 8890))
            with self.assertRaisesRegex(AssertionError, "offline tests denied"):
                connection.bind(("127.0.0.1", 8890))

    def _import(self, fake_home: Path, altitude_home: Path | None) -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=str(fake_home))
        env.pop("ALTITUDE_HOME", None)
        if altitude_home is not None:
            env["ALTITUDE_HOME"] = str(altitude_home)
        return subprocess.run([sys.executable, "-c", "import unittest; from altitude import config; config.ensure_root()"],
                              cwd=REPO, env=env, capture_output=True, text=True)

    def test_a_unittest_process_refuses_the_live_default_and_writes_only_an_explicit_home(self):
        with tempfile.TemporaryDirectory(prefix="altitude-sentinel-") as tmp:
            fake_home = Path(tmp)
            refused = self._import(fake_home, None)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("refusing to use the live ~/.altitude state", refused.stderr)
            self.assertFalse((fake_home / ".altitude").exists(), "the refusal must precede ensure_root's first write")
            allowed = self._import(fake_home, fake_home / "isolated")
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue((fake_home / "isolated" / "projects.json").is_file())
            self.assertFalse((fake_home / ".altitude").exists())


if __name__ == "__main__":
    unittest.main()
