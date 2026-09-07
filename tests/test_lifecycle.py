"""Lifecycle self-test: a queued task runs directly through one L2 to done."""
import unittest

from tests.support import AltitudeCase
from altitude import config, state as S, tasks as T


class TestLifecycle(AltitudeCase):
    """Single linear scenario. Kept as one test method: unittest does not guarantee an
    ordering across methods that would keep this state-mutating sequence valid."""

    def test_lifecycle_sequence(self):
        t = T.new(self.project, "Add beta stage with alarm rollback", "Add a beta pipeline stage…")
        self.assertEqual(t["state"], "queued")
        self.assertEqual(T.decisions(self.project), [])
        T.brief(self.project, t["slug"], "# Brief\n…")
        T.dispatch(self.project, t["slug"], attempt=1, session_id="sid", agent_id="aid", worktree="/wt", branch="b")
        self.assertEqual(S.load_task(self.project, t["slug"])["attempt"], 1)

        T.block(self.project, t["slug"], "Which rollback signal should I use?")
        self.assertEqual(T.decisions(self.project)[0]["kind"], "stopped", "a block by altd without an L2 question")

        T.resume(self.project, t["slug"])
        T.report(self.project, t["slug"], {"verdict": "ok", "prs": [140]})
        T.fyi(self.project, t["slug"], "landed")
        T.done(self.project, t["slug"], digest="Done.")

        self.assertEqual(S.task_dir(self.project, t["slug"]).parent.name, "archive")
        self.assertGreaterEqual(len(S.read_events(self.project, t["slug"])), 8)

        t2 = T.new(self.project, "Add beta stage with alarm rollback", "again")
        self.assertTrue(t2["slug"].endswith("-2"))
        self.assertEqual(t2["state"], "queued")
        self.assertNotIn("Recently finished", (config.project_dir(self.project) / "STATE.md").read_text())
        T.reject(self.project, t2["slug"], "tracked in a GitHub issue")
        self.assertEqual(S.task_dir(self.project, t2["slug"]).parent.name, "archive")
        self.assertNotIn(t2["slug"], (config.project_dir(self.project) / "STATE.md").read_text())

        self.assertFalse((config.project_dir(self.project) / "inbox.jsonl").exists(),
                         "FYIs are chat rows; the project inbox file is gone (SPEC.md §5.2 note 3)")


if __name__ == "__main__":
    unittest.main()
