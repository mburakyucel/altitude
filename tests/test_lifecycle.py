"""Lifecycle self-test: a queued task runs directly through one L2 to done."""
import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, ".")
# Must happen before altitude.config is imported: config.py reads ALTITUDE_HOME into ROOT
# (and derives PROJECTS_FILE, MONITOR_DIR, INCIDENT_INDEX, DIGEST_FILE from it) at import time.
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="alt-test-")

from altitude import config, state as S, tasks as T

# Under `unittest discover`, another test module may have already imported altitude.config
# (against a different ALTITUDE_HOME) before this module ran. Reload it so ROOT and its
# derived paths are rebuilt against our throwaway home. This is safe because state.py and
# tasks.py do `from . import config` (they hold the module object, not copied values) and
# resolve paths lazily through config.project_dir() / config.PROJECTS_FILE.
importlib.reload(config)


class TestLifecycle(unittest.TestCase):
    """Single linear scenario. Kept as one test method: unittest does not guarantee an
    ordering across methods that would keep this state-mutating sequence valid."""

    def test_lifecycle_sequence(self):
        config.ensure_root()
        P = config.load_projects()
        P["demo"] = {"path": os.environ["ALTITUDE_HOME"], "stacks": ["python"]}
        config.save_projects(P)

        t = T.new("demo", "Add beta stage with alarm rollback", "Add a beta pipeline stage…")
        self.assertEqual(t["state"], "approved")
        self.assertNotIn("class", t)
        self.assertNotIn("envelope", t)
        self.assertEqual(T.decisions("demo"), [])
        T.brief("demo", t["slug"], "# Brief\n…")
        T.dispatch("demo", t["slug"], dispatch_id=f"{t['slug']}-1", session_id="sid", agent_id="aid", worktree="/wt", branch="b")
        self.assertEqual(S.load_task("demo", t["slug"])["attempt"], 1)

        T.block("demo", t["slug"], "Which rollback signal should I use?")
        self.assertEqual(T.decisions("demo")[0]["kind"], "blocked")

        T.resume("demo", t["slug"])
        T.report("demo", t["slug"], {"verdict": "ok", "prs": [140]})
        T.fyi("demo", t["slug"], "landed")
        T.done("demo", t["slug"], digest="Done.")

        self.assertEqual(S.task_dir("demo", t["slug"]).parent.name, "archive")
        self.assertGreaterEqual(len(S.read_events("demo", t["slug"])), 8)

        t2 = T.new("demo", "Add beta stage with alarm rollback", "again")
        self.assertTrue(t2["slug"].endswith("-2"))
        self.assertEqual(t2["state"], "approved")
        self.assertIn("Recently finished", (config.project_dir("demo") / "STATE.md").read_text())

        self.assertIsInstance(T.inbox("demo"), list)


if __name__ == "__main__":
    unittest.main()
