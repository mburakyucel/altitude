"""The Codex app-server quota reader persists real account-wide limits for decision 45."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from altitude import config, quota_codex, route, state

_route_quota_codex = route.quota_codex


def transcript(*, secondary=None) -> list[str]:
    limits = {
        "limitId": "codex",
        "limitName": None,
        "primary": {"usedPercent": 1, "windowDurationMins": 10080, "resetsAt": 1788646278},
        "secondary": secondary,
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "individualLimit": None,
        "spendControlReached": False,
        "planType": "pro",
        "rateLimitReachedType": None,
    }
    return [
        json.dumps({"id": 1, "result": {"userAgent": "codex", "codexHome": "/tmp/codex"}}),
        json.dumps({"method": "configWarning", "params": {"message": "fixture warning"}}),
        json.dumps({"method": "remoteControl/status/changed", "params": {"status": "disconnected"}}),
        json.dumps({"id": 2, "result": {"rateLimits": limits,
                                         "rateLimitsByLimitId": {"other": {}},
                                         "rateLimitResetCredits": {"unused": True}}}),
    ]


class TestRead(unittest.TestCase):
    def test_interleaved_transcript_maps_primary_limit(self):
        with patch.object(quota_codex, "_talk", return_value=transcript()):
            result = quota_codex.read()
        self.assertTrue(result["known"])
        self.assertEqual(result["primary_used"], 1.0)
        self.assertEqual(result["primary_resets"], datetime.fromtimestamp(1788646278, timezone.utc).isoformat())
        self.assertIsNone(result["secondary_used"])

    def test_secondary_limit_is_mapped_when_present(self):
        secondary = {"usedPercent": 23, "windowDurationMins": 300, "resetsAt": 1788649878}
        with patch.object(quota_codex, "_talk", return_value=transcript(secondary=secondary)):
            result = quota_codex.read()
        self.assertEqual(result["secondary_used"], 23.0)
        self.assertEqual(result["secondary_resets"], datetime.fromtimestamp(1788649878, timezone.utc).isoformat())
        self.assertEqual(result["secondary_window_minutes"], 300)

    def test_json_rpc_error_message_is_returned_as_unknown(self):
        lines = [json.dumps({"id": 2, "error": {"code": -32000, "message": "account unavailable"}})]
        with patch.object(quota_codex, "_talk", return_value=lines):
            result = quota_codex.read()
        self.assertFalse(result["known"])
        self.assertIn("account unavailable", result["why"])

    def test_binary_missing_and_timeout_are_unknown(self):
        for failure in (FileNotFoundError("codex"), TimeoutError("late")):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(quota_codex, "_talk", side_effect=failure):
                    result = quota_codex.read()
                self.assertFalse(result["known"])
                self.assertIn("why", result)


class TestRefresh(unittest.TestCase):
    def test_refresh_writes_the_router_file(self):
        reading = {"known": True, "primary_used": 1.0, "read_at": "2026-08-30T00:00:00+00:00"}
        with tempfile.TemporaryDirectory(prefix="altitude-codex-quota-") as tmp:
            with patch.object(config, "MONITOR_DIR", Path(tmp)):
                with patch.object(quota_codex, "read", return_value=reading):
                    result = quota_codex.refresh()
                path = config.MONITOR_DIR / "quota-codex.json"
                self.assertEqual(state.read_json(path), reading)
                with patch.object(route, "quota_codex", _route_quota_codex):
                    self.assertEqual(route.quota_codex(), reading)
        self.assertEqual(result, reading)

    def test_refresh_if_due_throttles_attempts(self):
        calls = []
        with patch.object(quota_codex, "refresh", side_effect=lambda: calls.append(True) or {"known": False}):
            quota_codex._last_refresh_at = None
            quota_codex.refresh_if_due()
            quota_codex.refresh_if_due()
        self.assertEqual(calls, [True])
        quota_codex._last_refresh_at = None


if __name__ == "__main__":
    unittest.main()
