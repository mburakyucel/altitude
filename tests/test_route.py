"""Weekly-first provider routing is explicit, comparable, and can return unavailable."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-route-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, monitor, route  # noqa: E402


def codex(weekly, short=0):
    return {"known": True, "primary_used": weekly, "primary_window_minutes": route.WEEK_MINUTES,
            "secondary_used": short, "secondary_window_minutes": route.SHORT_MINUTES}


class TestPickEngine(unittest.TestCase):
    def setUp(self):
        self._q, self._cx, self._hold = monitor.quota, route.quota_codex, engines.usage_hold
        self.claude, self.codex = {"known": False}, {"known": False}
        monitor.quota = lambda: self.claude
        route.quota_codex = lambda: self.codex
        engines.usage_hold = lambda: None

    def tearDown(self):
        monitor.quota, route.quota_codex, engines.usage_hold = self._q, self._cx, self._hold

    def test_override_is_visible_and_unavailable_override_does_not_fallback(self):
        choice = route.pick_engine("l2", forced="claude")
        self.assertEqual(choice["engine"], "claude")
        self.claude = {"known": True, "five_hour": 100, "seven_day": 10}
        choice = route.pick_engine("l2", forced="claude")
        self.assertIsNone(choice["engine"]); self.assertIn("forced claude", choice["why"])
        with self.assertRaises(ValueError):
            route.pick_engine("l2", forced="gemini")

    def test_unknown_quotas_use_codex_default_out_loud(self):
        choice = route.pick_engine("l2")
        self.assertEqual(choice["engine"], config.PRIMARY_DEFAULT_ENGINE)
        self.assertIn("unknown", choice["why"]); self.assertIn("default policy", choice["why"])

    def test_weekly_headroom_wins_over_short_window_percentage(self):
        self.claude = {"known": True, "five_hour": 5, "seven_day": 80}
        self.codex = codex(20, short=90)
        choice = route.pick_engine("l2")
        self.assertEqual(choice["engine"], "codex"); self.assertIn("weekly headroom", choice["why"])
        self.claude = {"known": True, "five_hour": 90, "seven_day": 10}
        self.codex = codex(60, short=5)
        self.assertEqual(route.pick_engine("l2")["engine"], "claude")

    def test_short_exhaustion_only_rules_out_that_provider(self):
        self.claude = {"known": True, "five_hour": 100, "seven_day": 5}
        self.codex = codex(80, short=20)
        choice = route.pick_engine("l2")
        self.assertEqual(choice["engine"], "codex"); self.assertIn("claude unavailable", choice["why"])

    def test_both_exhausted_returns_no_engine(self):
        self.claude = {"known": True, "five_hour": 100, "seven_day": 5}
        self.codex = codex(100, short=20)
        self.assertIsNone(route.pick_engine("l2")["engine"])




if __name__ == "__main__":
    unittest.main()
