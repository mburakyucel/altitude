"""Decision 45: engine choice — forced > task > role > quota headroom > default policy, always with a reason."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-route-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, route, monitor, engines, state as S  # noqa: E402


class TestPickEngine(unittest.TestCase):
    def setUp(self):
        self._q, self._cx, self._hold = monitor.quota, route.quota_codex, engines.usage_hold
        self.claude, self.codex = {"known": False}, {"known": False}
        monitor.quota = lambda: self.claude
        route.quota_codex = lambda: self.codex
        engines.usage_hold = lambda: None

    def tearDown(self):
        monitor.quota, route.quota_codex, engines.usage_hold = self._q, self._cx, self._hold

    def test_future_codex_quota_timestamp_is_not_fresh(self):
        with tempfile.TemporaryDirectory(prefix="altitude-route-future-") as tmp:
            original = config.MONITOR_DIR
            config.MONITOR_DIR = Path(tmp)
            try:
                future = datetime.now(timezone.utc) + timedelta(seconds=301)
                S.write_json(config.MONITOR_DIR / route.QUOTA_CODEX, {
                    "known": True, "primary_used": 10, "read_at": future.isoformat(),
                })
                self.assertFalse(self._cx()["known"])
            finally:
                config.MONITOR_DIR = original

    def test_forced_and_task_and_role(self):
        self.assertEqual(route.pick_engine("l1", forced="claude")["engine"], "claude")
        self.assertEqual(route.pick_engine("l1", task={"slug": "x", "engine": "codex"})["engine"], "codex")
        self.assertEqual(route.pick_engine("l1", forced="claude", task={"slug": "x", "engine": "codex"})["engine"], "claude", "command line beats task")
        self.assertEqual(route.pick_engine("l3")["engine"], "claude")
        self.assertEqual(route.pick_engine("critic")["engine"], "codex")
        with self.assertRaises(ValueError):
            route.pick_engine("l1", forced="gemini")

    def test_l3_uses_weekly_ratio_with_hysteresis(self):
        self.claude = {"known": True, "five_hour": 1, "seven_day": 60}
        self.codex = {"known": True, "primary_used": 20, "primary_window_minutes": 10080}
        choice = route.pick_l3_engine(previous="claude")
        self.assertEqual(choice["engine"], "codex")
        self.assertIn("2.00x", choice["why"])

        self.codex["primary_used"] = 45  # 55/40 = 1.375: inside the hysteresis band
        self.assertEqual(route.pick_l3_engine(previous="codex")["engine"], "codex")
        self.assertEqual(route.pick_l3_engine(previous="claude")["engine"], "claude")

        self.codex["primary_used"] = 55  # 45/40 = 1.125: switch back clearly
        self.assertEqual(route.pick_l3_engine(previous="codex")["engine"], "claude")

    def test_l3_uses_secondary_codex_weekly_window_and_hard_hold_wins(self):
        self.claude = {"known": True, "seven_day": 60}
        self.codex = {"known": True, "primary_used": 5, "primary_window_minutes": 300,
                      "secondary_used": 20, "secondary_window_minutes": 10080}
        self.assertEqual(route.pick_l3_engine(previous="claude")["engine"], "codex")
        engines.usage_hold = lambda: "2099-01-01T00:00:00+00:00"
        choice = route.pick_l3_engine(forced="claude", previous="claude")
        self.assertEqual(choice["engine"], "codex")
        self.assertIn("exhausted", choice["why"])

    def test_l3_keeps_previous_engine_when_weekly_ratio_is_unknown(self):
        self.assertEqual(route.pick_l3_engine(previous="codex")["engine"], "codex")
        self.assertEqual(route.pick_l3_engine(previous=None)["engine"], "claude")
        self.assertEqual(route.pick_l3_engine(forced="codex")["engine"], "codex")

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
