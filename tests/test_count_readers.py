"""Verify and monitor read only the counter keyed by the task's current attempt."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from altitude import config, monitor, state as S, verify


class CountReaders(unittest.TestCase):
    def setUp(self):
        self.monitor_dir = Path(tempfile.mkdtemp(prefix="altitude-count-readers-"))
        self.addCleanup(shutil.rmtree, self.monitor_dir, ignore_errors=True)
        self.original_monitor_dir = config.MONITOR_DIR
        config.MONITOR_DIR = self.monitor_dir

    def tearDown(self):
        config.MONITOR_DIR = self.original_monitor_dir

    def write_counts(self, key, **counts):
        (self.monitor_dir / f"counts-{key}.json").write_text(json.dumps(counts))

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
        originals = config.load_projects, S.list_tasks, monitor.transcript_context_percent
        config.load_projects = lambda: {"demo": {"path": "."}}
        S.list_tasks = lambda project: tasks
        monitor.transcript_context_percent = lambda session_id, cwd: None
        try:
            rows = {row["slug"]: row for row in monitor.sessions() if row.get("kind") == "l2"}
        finally:
            config.load_projects, S.list_tasks, monitor.transcript_context_percent = originals
        self.assertEqual((rows["keyed"]["l1_runs"], rows["keyed"]["edits"]), (0, 3))
        self.assertEqual((rows["unkeyed"]["l1_runs"], rows["unkeyed"]["edits"]), (0, 0))
        self.assertEqual((rows["other"]["l1_runs"], rows["other"]["edits"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
