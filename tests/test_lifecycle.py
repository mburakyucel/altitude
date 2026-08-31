"""Lifecycle self-test: one linear, state-mutating scenario against a throwaway ALTITUDE_HOME,
covering new -> propose -> approve -> brief -> dispatch -> block -> resume -> report -> fyi ->
done, then a second task exercising auto_approve and the STATE.md digest."""
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

        t = T.new("demo", "Add beta stage with alarm rollback", "L", "Add a beta pipeline stage…")
        self.assertEqual(t["state"], "requested")
        self.assertEqual(t["envelope"]["subagent_launches"], 20)

        T.propose(
            "demo", t["slug"], "# Proposal\n…", {"estimate": {"turns": 90}},
            question="Ship beta stage with CloudWatch alarm rollback (~$3/mo)?",
            options=["Approve: alarm rollback", "Approve: manual rollback", "Revise", "Park"],
        )

        with self.assertRaises(T.TransitionError):
            T.approve("demo", t["slug"], 0, actor="l3")
        self.assertEqual(len(T.decisions("demo")), 1)

        T.approve("demo", t["slug"], 0)
        T.brief("demo", t["slug"], "# Brief\n…")
        T.dispatch("demo", t["slug"], dispatch_id=f"{t['slug']}-1", session_id="sid", agent_id="aid", worktree="/wt", branch="b")
        self.assertEqual(S.load_task("demo", t["slug"])["attempt"], 1)

        T.block("demo", t["slug"], "envelope reached")
        self.assertEqual(T.decisions("demo"), [], "operational blockers stay with Altitude")
        T.needs_user("demo", t["slug"], "Approve more than twice the class envelope")
        with self.assertRaisesRegex(T.TransitionError, "L3-only"):
            T.needs_user("demo", t["slug"], "bypass", actor="l2")
        self.assertEqual(T.decisions("demo")[0]["kind"], "blocked")

        T.resume("demo", t["slug"])
        T.report("demo", t["slug"], {"verdict": "ok", "prs": [140]})
        T.fyi("demo", t["slug"], "landed")
        with self.assertRaisesRegex(T.TransitionError, "trusted remote landing integration"):
            T.done("demo", t["slug"], actor="l3", digest="must not archive")
        with self.assertRaisesRegex(T.TransitionError, "trusted remote landing integration"):
            T.done("demo", t["slug"], actor="altd", digest="must not archive")
        T.done("demo", t["slug"], actor="burak", digest="Done.")

        self.assertEqual(S.task_dir("demo", t["slug"]).parent.name, "archive")
        self.assertGreaterEqual(len(S.read_events("demo", t["slug"])), 9)

        t2 = T.new("demo", "Add beta stage with alarm rollback", "S", "again")
        self.assertTrue(t2["slug"].endswith("-2"))
        T.auto_approve("demo", t2["slug"], "docs-only")
        self.assertIn("Recently finished", (config.project_dir("demo") / "STATE.md").read_text())

        self.assertIsInstance(T.inbox("demo"), list)


if __name__ == "__main__":
    unittest.main()
