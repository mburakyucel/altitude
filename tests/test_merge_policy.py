"""PRs merge by default; `hold_merge` is the explicit exception."""
import unittest

from tests.support import AltitudeCase
from altitude import state as S, tasks as T, dispatch


class TestMergePolicy(AltitudeCase):
    def test_tasks_merge_by_default(self):
        T.new(self.project, "merge-l", "req")
        b = dispatch.build_brief(self.project, "merge-l")
        self.assertIn("Merge when the applicable checks and any appropriate review are complete", b)
        self.assertNotIn("Held for operator review", b)

    def test_hold_is_the_exception_and_says_why(self):
        T.new(self.project, "merge-hold", "req", hold_merge="rewrites the deploy workflow")
        b = dispatch.build_brief(self.project, "merge-hold")
        self.assertIn("Held for operator review", b); self.assertIn("rewrites the deploy workflow", b)
        self.assertIn("after the operator approves it in this task's chat, merge with `alt land --merge --approval", b)
        with self.assertRaisesRegex(T.TransitionError, "only the operator"):
            T.set_hold_merge(self.project, "merge-hold", None, actor="l3")
        T.set_hold_merge(self.project, "merge-hold", None, actor="burak")
        self.assertIsNone(S.load_task(self.project, "merge-hold")["hold_merge"])
        self.assertNotIn("Held for operator review", dispatch.build_brief(self.project, "merge-hold"))
        T.set_hold_merge(self.project, "merge-hold", "  spends money  ", actor="l3")
        self.assertEqual(S.load_task(self.project, "merge-hold")["hold_merge"], "spends money")


if __name__ == "__main__":
    unittest.main()
