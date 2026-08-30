"""The test process must never inherit Altitude's live state root.

Many older test modules assign ALTITUDE_HOME immediately before their own Altitude imports.  That is not process
isolation: unittest discovery imports all modules into one interpreter, and altitude.config caches ROOT and its
derived paths on the first import.  Import config here, first in the repository's sorted discovery order, against
one suite-level throwaway home.  config.py independently refuses the live default so safety does not depend on
this filename or discovery order.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SUITE_HOME = Path(tempfile.mkdtemp(prefix="altitude-test-suite-"))
os.environ["ALTITUDE_HOME"] = str(SUITE_HOME)

# Freeze the process-wide path constants before any per-module ALTITUDE_HOME assignment can race discovery.
from altitude import config as _suite_config  # noqa: E402,F401


class TestStateIsolation(unittest.TestCase):
    def run_import(self, fake_home: Path, altitude_home: Path | None) -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=str(fake_home))
        if altitude_home is None:
            env.pop("ALTITUDE_HOME", None)
        else:
            env["ALTITUDE_HOME"] = str(altitude_home)
        return subprocess.run(
            [sys.executable, "-c", "import unittest; from altitude import config; config.ensure_root()"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_unittest_process_refuses_the_live_default_before_writing(self):
        with tempfile.TemporaryDirectory(prefix="altitude-live-sentinel-") as tmp:
            fake_home = Path(tmp)
            result = self.run_import(fake_home, None)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing to use the live ~/.altitude state", result.stderr)
            self.assertFalse((fake_home / ".altitude").exists(), "the refusal must precede ensure_root's first write")

    def test_unittest_process_can_write_only_to_an_explicit_isolated_home(self):
        with tempfile.TemporaryDirectory(prefix="altitude-home-sentinel-") as tmp:
            fake_home = Path(tmp)
            isolated = fake_home / "isolated"
            result = self.run_import(fake_home, isolated)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((isolated / "projects.json").is_file())
            self.assertFalse((fake_home / ".altitude").exists())


if __name__ == "__main__":
    unittest.main()
