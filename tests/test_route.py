"""Optional L1/reviewer engine choice records its reason."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-route-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, route, monitor, state as S  # noqa: E402


class TestPickEngine(unittest.TestCase):
    def setUp(self):
        self._q, self._cx = monitor.quota, route.quota_codex
        self.claude, self.codex = {"known": False}, {"known": False}
        monitor.quota = lambda: self.claude
        route.quota_codex = lambda: self.codex

    def tearDown(self):
        monitor.quota, route.quota_codex = self._q, self._cx

    def test_run_override(self):
        self.assertEqual(route.pick_engine("l1", forced="claude")["engine"], "claude")
        with self.assertRaises(ValueError):
            route.pick_engine("l1", forced="gemini")

    def test_unknown_quotas_use_the_default_policy_out_loud(self):
        c = route.pick_engine("l1")
        self.assertEqual(c["engine"], config.L1_DEFAULT_ENGINE)
        self.assertIn("unknown", c["why"]); self.assertIn("default policy", c["why"])

    def test_both_known_picks_more_headroom(self):
        self.claude, self.codex = {"known": True, "five_hour": 80}, {"known": True, "primary_used": 20}
        self.assertEqual(route.pick_engine("l1")["engine"], "codex")
        self.claude, self.codex = {"known": True, "five_hour": 10}, {"known": True, "primary_used": 60}
        c = route.pick_engine("l1"); self.assertEqual(c["engine"], "claude"); self.assertIn("headroom", c["why"])

    def test_one_known_past_reserve_moves_work_to_the_other(self):
        self.claude = {"known": True, "five_hour": 85}
        self.assertEqual(route.pick_engine("l1")["engine"], "codex")
        self.claude, self.codex = {"known": False}, {"known": True, "primary_used": 90}
        self.assertEqual(route.pick_engine("l1")["engine"], "claude")

    def test_reviewer_takes_the_other_engine_from_the_author(self):
        c = route.pick_engine("reviewer", other_than="codex")
        self.assertEqual(c["engine"], "claude"); self.assertIn("other engine", c["why"])
        self.claude = {"known": True, "five_hour": 95}  # no room on the other seat: same engine, no crash
        self.assertEqual(route.pick_engine("reviewer", other_than="codex")["engine"], "codex")


if __name__ == "__main__":
    unittest.main()
