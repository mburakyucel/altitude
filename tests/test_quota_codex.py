"""The Codex app-server quota reader persists real account-wide limits."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from altitude import config, quota_codex, route, state

_route_quota_codex = route.quota_codex
_default_primary = object()


def transcript(*, primary=_default_primary, secondary=None) -> list[str]:
    if primary is _default_primary:
        primary = {"usedPercent": 1, "windowDurationMins": 10080, "resetsAt": 1788646278}
    limits = {
        "limitId": "codex",
        "limitName": None,
        "primary": primary,
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

    def test_null_primary_is_unknown(self):
        with patch.object(quota_codex, "_talk", return_value=transcript(primary=None)):
            result = quota_codex.read()
        self.assertFalse(result["known"])
        self.assertIn("primary.usedPercent", result["why"])

    def test_invalid_primary_used_percent_is_unknown(self):
        for used_percent in (float("nan"), -1, 150):
            with self.subTest(used_percent=used_percent):
                primary = {
                    "usedPercent": used_percent,
                    "windowDurationMins": 10080,
                    "resetsAt": 1788646278,
                }
                with patch.object(quota_codex, "_talk", return_value=transcript(primary=primary)):
                    result = quota_codex.read()
                self.assertFalse(result["known"])
                self.assertIn(repr(used_percent), result["why"])

    def test_missing_primary_reset_is_unknown(self):
        primary = {"usedPercent": 1, "windowDurationMins": 10080}
        with patch.object(quota_codex, "_talk", return_value=transcript(primary=primary)):
            result = quota_codex.read()
        self.assertFalse(result["known"])
        self.assertIn("primary.resetsAt", result["why"])

    def test_invalid_secondary_used_percent_is_dropped(self):
        secondary = {"usedPercent": "garbage", "windowDurationMins": 300, "resetsAt": 1788649878}
        with patch.object(quota_codex, "_talk", return_value=transcript(secondary=secondary)):
            result = quota_codex.read()
        self.assertTrue(result["known"])
        self.assertEqual(result["primary_used"], 1.0)
        self.assertIsNone(result["secondary_used"])
        self.assertIsNone(result["secondary_resets"])
        self.assertIsNone(result["secondary_window_minutes"])

    def test_output_over_byte_cap_is_unknown(self):
        failure = RuntimeError(
            f"Codex app-server output exceeded {quota_codex._MAX_OUTPUT_BYTES} bytes"
        )
        with patch.object(quota_codex, "_talk", side_effect=failure):
            result = quota_codex.read()
        self.assertFalse(result["known"])
        self.assertIn("exceeded", result["why"])

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

    def test_refresh_overwrites_stale_success_with_unknown(self):
        stale = {"known": True, "primary_used": 1.0, "read_at": "2026-08-30T00:00:00+00:00"}
        failed = {"known": False, "why": "Codex rate-limit read timed out"}
        with tempfile.TemporaryDirectory(prefix="altitude-codex-quota-") as tmp:
            with patch.object(config, "MONITOR_DIR", Path(tmp)):
                path = config.MONITOR_DIR / "quota-codex.json"
                state.write_json(path, stale)
                with patch.object(quota_codex, "read", return_value=failed):
                    result = quota_codex.refresh()
                self.assertEqual(state.read_json(path), failed)
        self.assertEqual(result, failed)

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
