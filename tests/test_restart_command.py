"""The operator restart stays one guarded, repository-owned command."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO

SCRIPT = REPO / "scripts" / "restart_altitude.py"


def load_script():
    spec = importlib.util.spec_from_file_location("restart_altitude", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class TestRestartCommand(unittest.TestCase):
    def test_make_exposes_one_operator_command(self):
        makefile = (REPO / "Makefile").read_text()
        restart = makefile.split("restart:", 1)[1].split("\ninstall-service:", 1)[0]
        self.assertIn("python3 scripts/restart_altitude.py", restart)
        self.assertIn("`make restart`", (REPO / "README.md").read_text())
        helper = SCRIPT.read_text()
        self.assertIn("fetch_and_require_exact_base", helper)
        self.assertIn("require_idle()", helper)
        self.assertIn('unit_environment().get("ALTITUDE_HOME"', helper)

    def test_generated_web_bundle_is_runtime_state_not_tracked_source(self):
        ignored = (REPO / ".gitignore").read_text().splitlines()
        self.assertIn("web/dist/", ignored)
        self.assertIn("web/.dist-*/", ignored)
        tracked = subprocess.run(
            ["git", "ls-files", "web/dist"], cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        self.assertEqual(tracked, [])

    def test_bundle_validation_rejects_a_missing_bundle(self):
        restart = load_script()
        with self.assertRaises(restart.RestartError):
            restart.validate_bundle(REPO / "tests" / "does-not-exist")

    def test_failed_restart_restores_previous_bundle(self):
        restart = load_script()
        with tempfile.TemporaryDirectory(prefix="altitude-restart-test-") as tmp:
            web = Path(tmp)
            dist, staging = web / "dist", web / ".dist-next-test"
            dist.mkdir(); (dist / "version").write_text("old")
            staging.mkdir(); (staging / "version").write_text("new")
            attempts = 0

            def run(command, **_kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise restart.RestartError("simulated restart failure")
                return mock.Mock(returncode=0)

            with mock.patch.object(restart, "WEB", web), mock.patch.object(restart, "DIST", dist), \
                    mock.patch.object(restart, "unit_properties", return_value={"MainPID": "10"}), \
                    mock.patch.object(restart, "run", side_effect=run), \
                    mock.patch.object(restart, "diagnostics"):
                with self.assertRaises(restart.RestartError):
                    restart.publish_and_restart(staging)
            self.assertEqual((dist / "version").read_text(), "old")
            self.assertFalse(staging.exists())


if __name__ == "__main__":
    unittest.main()
