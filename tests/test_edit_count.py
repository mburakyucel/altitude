"""The edit hook accumulates counts across resumed sessions in one dispatch."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "edit_count.py"


class EditCountHook(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="altitude-edit-count-")

    def run_hook(self, session_id, file_path, key=None):
        env = dict(os.environ, ALTITUDE_HOME=self.home)
        env.pop("ALTITUDE_SESSION_KEY", None)
        if key:
            env["ALTITUDE_SESSION_KEY"] = key
        payload = {"session_id": session_id, "tool_name": "Edit", "tool_input": {"file_path": file_path}}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=env)

    def counts(self, key):
        path = Path(self.home) / "monitor" / f"counts-{key}.json"
        return json.loads(path.read_text())

    def test_dispatch_key_accumulates_edits_across_sessions(self):
        key = "demo--task-1"
        self.assertEqual(self.run_hook("old-session", "a.py", key).returncode, 0)
        self.assertEqual(self.run_hook("new-session", "b.py", key).returncode, 0)
        self.assertEqual(self.counts(key)["edits"], 2)
        self.assertEqual(self.counts(key)["files"], ["a.py", "b.py"])
        self.assertFalse((Path(self.home) / "monitor" / "counts-old-session.json").exists())
        self.assertFalse((Path(self.home) / "monitor" / "counts-new-session.json").exists())

    def test_without_dispatch_key_edits_remain_per_session(self):
        self.assertEqual(self.run_hook("session-a", "a.py").returncode, 0)
        self.assertEqual(self.run_hook("session-b", "b.py").returncode, 0)
        self.assertEqual(self.counts("session-a")["edits"], 1)
        self.assertEqual(self.counts("session-b")["edits"], 1)


if __name__ == "__main__":
    unittest.main()
