"""`alt project` and `alt state` are the operator's commands; phase 4 lost their parsers, so pin them."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-alt-operator-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config  # noqa: E402

ALT = Path(__file__).resolve().parent.parent / "bin" / "alt"


def _alt(*args):
    env = {**os.environ, "ALTITUDE_HOME": str(config.ROOT)}
    env.pop("ALTITUDE_ACTOR", None)
    return subprocess.run([sys.executable, str(ALT), *args], capture_output=True, text=True, env=env)


class TestOperatorCommands(unittest.TestCase):
    def test_project_add_list_remove_and_state(self):
        repo = _TMP / "op-proj"
        repo.mkdir(exist_ok=True)
        added = _alt("project", "add", "op", "--path", str(repo), "--l2-engine", "codex")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout)["l2_engine"], "codex")
        listed = _alt("project", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["op"]["path"], str(repo))
        state = _alt("--project", "op", "state")
        self.assertEqual(state.returncode, 0, state.stderr)
        self.assertIn("# STATE — op", state.stdout)
        removed = _alt("project", "remove", "op")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertNotIn("op", json.loads(_alt("project", "list").stdout))


if __name__ == "__main__":
    unittest.main()
