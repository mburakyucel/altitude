"""Decision 48: PRs merge by default for every class; `hold_merge` (with a reason) is the exception and travels in the brief."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-merge-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, dispatch  # noqa: E402

REPO = _TMP / "repo"


class TestMergePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        REPO.mkdir()
        config.save_projects({"altitude": {"name": "altitude", "path": str(REPO), "stacks": ["python"]}})

    def test_l_class_merges_by_default(self):
        T.new("altitude", "merge-l", "L", "req")
        b = dispatch.build_brief("altitude", "merge-l")
        self.assertIn("Merge when the applicable checks and any appropriate review are complete", b)
        self.assertIn("Review is optional", b)
        self.assertNotIn("Held for Burak", b)

    def test_hold_is_the_exception_and_says_why(self):
        T.new("altitude", "merge-hold", "S", "req", hold_merge="rewrites the deploy workflow")
        b = dispatch.build_brief("altitude", "merge-hold")
        self.assertIn("Held for Burak", b); self.assertIn("rewrites the deploy workflow", b)
        T.set_hold_merge("altitude", "merge-hold", None, actor="l3")
        self.assertIsNone(S.load_task("altitude", "merge-hold")["hold_merge"])
        self.assertNotIn("Held for Burak", dispatch.build_brief("altitude", "merge-hold"))
        T.set_hold_merge("altitude", "merge-hold", "  spends money  ", actor="l3")
        self.assertEqual(S.load_task("altitude", "merge-hold")["hold_merge"], "spends money")


if __name__ == "__main__":
    unittest.main()
