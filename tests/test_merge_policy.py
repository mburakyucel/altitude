"""R-014 temporarily supersedes merge defaults; task holds remain additional blockers."""
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

    def test_l_class_stops_at_reviewed_pr_while_r014_bootstrap_is_incomplete(self):
        T.new("altitude", "merge-l", "L", "req")
        b = dispatch.build_brief("altitude", "merge-l")
        self.assertIn("R-014 landing block", b)
        self.assertIn("trusted remote landing integration pending", b)
        self.assertNotIn("R-014 landing block plus a Burak hold", b)

    def test_hold_is_the_exception_and_says_why(self):
        T.new("altitude", "merge-hold", "S", "req", hold_merge="rewrites the deploy workflow")
        b = dispatch.build_brief("altitude", "merge-hold")
        self.assertIn("R-014 landing block plus a Burak hold", b)
        self.assertIn("rewrites the deploy workflow", b)
        T.set_hold_merge("altitude", "merge-hold", None, actor="l3")
        self.assertIsNone(S.load_task("altitude", "merge-hold")["hold_merge"])
        self.assertNotIn("R-014 landing block plus a Burak hold", dispatch.build_brief("altitude", "merge-hold"))
        T.set_hold_merge("altitude", "merge-hold", "  spends money  ", actor="l3")
        self.assertEqual(S.load_task("altitude", "merge-hold")["hold_merge"], "spends money")


if __name__ == "__main__":
    unittest.main()
