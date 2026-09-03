"""The test process never touches the operator's live state: the fixture isolates it, and config refuses it."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import REPO, SUITE
from altitude import config


class TestIsolation(unittest.TestCase):
    def test_suite_process_uses_throwaway_homes(self):
        self.assertEqual(Path.home(), SUITE / "home")
        self.assertEqual(config.ROOT, SUITE / "altitude")

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
