"""Verify and monitor read only the counter keyed by the task's current attempt."""
import json
import unittest
from pathlib import Path

from tests.support import AltitudeCase
from altitude import config, monitor, state as S, verify


class CountReaders(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()

    def write_counts(self, key, **counts):
        (config.MONITOR_DIR / f"counts-{key}.json").write_text(json.dumps(counts))

    def test_verify_reads_attempt_key_only(self):
        self.write_counts("demo--task-1", edits=3)
        self.write_counts("new-session", edits=9)
        keyed = verify._spend({}, "demo", {"slug": "task", "attempt": 1, "session_id": "new-session"}, Path("."))
        self.assertEqual(keyed["spend"]["edits_hook"], 3)

        self.write_counts("unrelated-session", edits=6)
        missing = verify._spend({}, "demo", {"slug": "task", "attempt": 2, "session_id": "unrelated-session"}, Path("."))
        self.assertIsNone(missing["spend"]["edits_hook"])

    def test_monitor_reads_attempt_key_only(self):
        tasks = [
            {"slug": "keyed", "state": "running", "attempt": 1, "session_id": "new-session"},
            {"slug": "unkeyed", "state": "blocked", "session_id": "session-without-dispatch"},
            {"slug": "other", "state": "reported", "attempt": 1, "session_id": "unrelated-session"},
        ]
        self.write_counts("demo--keyed-1", edits=3)
        self.write_counts("new-session", edits=9)
        self.write_counts("session-without-dispatch", edits=5)
        self.write_counts("unrelated-session", edits=7)
        self.patch(config, "load_projects", return_value={"demo": {"path": "."}})
        self.patch(S, "list_tasks", new=lambda project: tasks)

        rows = {row["slug"]: row for row in monitor.sessions() if row.get("kind") == "l2"}

        self.assertEqual(rows["keyed"]["edits"], 3)
        self.assertEqual(rows["unkeyed"]["edits"], 0)
        self.assertEqual(rows["other"]["edits"], 0)


if __name__ == "__main__":
    unittest.main()
