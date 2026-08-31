"""Verify and monitor read only dispatch-keyed counters."""
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

    def test_verify_reads_dispatch_key_only(self):
        self.write_counts("demo--task-1", subagent_launches=2, edits=3)
        self.write_counts("new-session", subagent_launches=9, edits=9)
        keyed = verify._spend({}, "demo", {"dispatch_id": "task-1", "session_id": "new-session"}, Path("."))
        self.assertEqual(keyed["spend"]["subagent_launches_hook"], 2)
        self.assertEqual(keyed["spend"]["edits_hook"], 3)

        self.write_counts("pre-change-session", subagent_launches=6)
        missing = verify._spend({}, "demo", {"dispatch_id": "task-2", "session_id": "pre-change-session"}, Path("."))
        self.assertIsNone(missing["spend"]["subagent_launches_hook"])

    def test_monitor_reads_dispatch_key_only(self):
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
        self.assertEqual((rows["keyed"]["l1_runs"], rows["keyed"]["edits"]), (0, 3))
        self.assertEqual((rows["unkeyed"]["l1_runs"], rows["unkeyed"]["edits"]), (0, 0))
        self.assertEqual((rows["legacy"]["l1_runs"], rows["legacy"]["edits"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
