"""The edit hook counts each edit of a task's current attempt through altd, across resumed sessions."""
import json
import os
import subprocess
import sys
import unittest

from tests.support import REPO, AltitudeCase
from altitude import config, state as S, tasks as T

HOOK = REPO / "hooks" / "edit_count.py"


class EditCountHook(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Counted task", "request")
        self.slug = task["slug"]
        task.update({"state": "running", "attempt": 1, "agent_id": "aid"})
        S.save_task(self.project, task)

    def run_hook(self, session_id, file_path, *, attempt="1", task=True):
        env = dict(os.environ, ALTITUDE_HOME=str(config.ROOT), ALTITUDE_PROJECT=self.project, ALTITUDE_ATTEMPT=attempt,
                   **({"ALTITUDE_TASK": self.slug} if task else {}))
        payload = {"session_id": session_id, "tool_name": "Edit", "tool_input": {"file_path": file_path}}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=env, timeout=60)

    def counts(self):
        return S.read_json(S.counts_path(self.project, S.load_task(self.project, self.slug)), None)

    def test_an_attempt_accumulates_edits_across_sessions(self):
        self.assertEqual(self.run_hook("old-session", "a.py").returncode, 0)
        self.assertEqual(self.run_hook("new-session", "b.py").returncode, 0)
        self.assertEqual(self.counts(), {"edits": 2, "files": ["a.py", "b.py"]})

    def test_a_new_attempt_starts_its_own_count(self):
        self.run_hook("session", "a.py")
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 2
        S.save_task(self.project, task)
        self.assertEqual(self.run_hook("session", "b.py", attempt="2").returncode, 0)
        self.assertEqual(self.counts(), {"edits": 1, "files": ["b.py"]})

    def test_a_stale_attempt_or_a_session_outside_a_task_counts_nothing(self):
        stale = self.run_hook("session", "a.py", attempt="0")
        self.assertEqual(stale.returncode, 0)
        self.assertIn("did not count this edit", stale.stderr)
        self.assertEqual((self.run_hook("session", "a.py", task=False).returncode), 0)
        self.assertIsNone(self.counts())


if __name__ == "__main__":
    unittest.main()
