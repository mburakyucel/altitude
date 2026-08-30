"""Verify and monitor prefer dispatch-keyed counts while retaining legacy session counts."""
import json
import tempfile
import unittest
from pathlib import Path

from altitude import config, monitor, state as S, verify


class CountReaders(unittest.TestCase):
    def setUp(self):
        self.monitor_dir = Path(tempfile.mkdtemp(prefix="altitude-count-readers-"))
        self.original_monitor_dir = config.MONITOR_DIR
        config.MONITOR_DIR = self.monitor_dir

    def tearDown(self):
        config.MONITOR_DIR = self.original_monitor_dir

    def write_counts(self, key, **counts):
        (self.monitor_dir / f"counts-{key}.json").write_text(json.dumps(counts))

    def test_verify_reads_dispatch_key_and_session_fallbacks(self):
        self.write_counts("demo--task-1", subagent_launches=2, edits=3)
        self.write_counts("new-session", subagent_launches=9, edits=9)
        keyed = verify._spend({}, "demo", {"dispatch_id": "task-1", "session_id": "new-session"}, Path("."))
        self.assertEqual(keyed["spend"]["subagent_launches_hook"], 2)
        self.assertEqual(keyed["spend"]["edits_hook"], 3)

        self.write_counts("old-session", subagent_launches=4, edits=5)
        unkeyed = verify._spend({}, "demo", {"session_id": "old-session"}, Path("."))
        self.assertEqual(unkeyed["spend"]["subagent_launches_hook"], 4)
        self.assertEqual(unkeyed["spend"]["edits_hook"], 5)

        self.write_counts("pre-change-session", subagent_launches=6)
        legacy = verify._spend({}, "demo", {"dispatch_id": "task-2", "session_id": "pre-change-session"}, Path("."))
        self.assertEqual(legacy["spend"]["subagent_launches_hook"], 6)

    def test_monitor_reads_dispatch_key_and_session_fallbacks(self):
        tasks = [
            {"slug": "keyed", "state": "running", "dispatch_id": "keyed-1", "session_id": "new-session"},
            {"slug": "unkeyed", "state": "blocked", "session_id": "old-session"},
            {"slug": "legacy", "state": "reported", "dispatch_id": "legacy-1", "session_id": "pre-change-session"},
        ]
        self.write_counts("demo--keyed-1", subagent_launches=2, edits=3)
        self.write_counts("new-session", subagent_launches=9, edits=9)
        self.write_counts("old-session", subagent_launches=4, edits=5)
        self.write_counts("pre-change-session", subagent_launches=6, edits=7)
        originals = config.load_projects, S.list_tasks, monitor.transcript_context_percent
        config.load_projects = lambda: {"demo": {"path": "."}}
        S.list_tasks = lambda project: tasks
        monitor.transcript_context_percent = lambda session_id, cwd: None
        try:
            rows = {row["slug"]: row for row in monitor.sessions() if row.get("kind") == "l2"}
        finally:
            config.load_projects, S.list_tasks, monitor.transcript_context_percent = originals
        self.assertEqual((rows["keyed"]["subagent_launches"], rows["keyed"]["edits"]), (2, 3))
        self.assertEqual((rows["unkeyed"]["subagent_launches"], rows["unkeyed"]["edits"]), (4, 5))
        self.assertEqual((rows["legacy"]["subagent_launches"], rows["legacy"]["edits"]), (6, 7))


if __name__ == "__main__":
    unittest.main()
