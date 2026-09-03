"""The edit hook accumulates counts across resumed sessions in one dispatch."""
import json
import os
import subprocess
import sys
import unittest

from tests.support import REPO, AltitudeCase

HOOK = REPO / "hooks" / "edit_count.py"


class EditCountHook(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"  # the hook writes counts under $ALTITUDE_HOME/monitor

    def run_hook(self, session_id, file_path, key=None):
        env = dict(os.environ, ALTITUDE_HOME=str(self.home))
        env.pop("ALTITUDE_SESSION_KEY", None)
        if key:
            env["ALTITUDE_SESSION_KEY"] = key
        payload = {"session_id": session_id, "tool_name": "Edit", "tool_input": {"file_path": file_path}}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=env)

    def counts(self, key):
        return json.loads((self.home / "monitor" / f"counts-{key}.json").read_text())

    def test_dispatch_key_accumulates_edits_across_sessions(self):
        key = "demo--task-1"
        self.assertEqual(self.run_hook("old-session", "a.py", key).returncode, 0)
        self.assertEqual(self.run_hook("new-session", "b.py", key).returncode, 0)
        self.assertEqual(self.counts(key)["edits"], 2)
        self.assertEqual(self.counts(key)["files"], ["a.py", "b.py"])
        self.assertFalse((self.home / "monitor" / "counts-old-session.json").exists())
        self.assertFalse((self.home / "monitor" / "counts-new-session.json").exists())

    def test_first_keyed_edit_does_not_import_session_count(self):
        mon = self.home / "monitor"
        mon.mkdir(parents=True, exist_ok=True)
        session_counts = mon / "counts-old-session.json"
        session_counts.write_text(json.dumps({"edits": 4, "files": ["old.py"]}))

        result = self.run_hook("old-session", "new.py", "demo--task-1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counts("demo--task-1")["edits"], 1)
        self.assertEqual(self.counts("demo--task-1")["files"], ["new.py"])
        self.assertEqual(json.loads(session_counts.read_text())["edits"], 4)

    def test_without_dispatch_key_edits_remain_per_session(self):
        self.assertEqual(self.run_hook("session-a", "a.py").returncode, 0)
        self.assertEqual(self.run_hook("session-b", "b.py").returncode, 0)
        self.assertEqual(self.counts("session-a")["edits"], 1)
        self.assertEqual(self.counts("session-b")["edits"], 1)


if __name__ == "__main__":
    unittest.main()
