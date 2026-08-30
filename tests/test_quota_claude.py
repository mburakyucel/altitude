"""The Claude OAuth quota reader keeps account windows available without a live statusline."""
import json
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest.mock import patch

from altitude import config, monitor, quota_claude, server, state


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return json.dumps(self.value).encode()


class TestClaudeQuotaRead(unittest.TestCase):
    def test_oauth_usage_maps_windows_and_never_returns_the_token(self):
        with tempfile.TemporaryDirectory(prefix="altitude-claude-quota-") as tmp:
            credentials = Path(tmp) / "credentials.json"
            credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "secret-token"}}))
            seen = {}

            def opener(req, timeout):
                seen["authorization"] = req.get_header("Authorization")
                seen["beta"] = dict(req.header_items()).get("Anthropic-beta")
                seen["timeout"] = timeout
                return Response({"five_hour": {"utilization": 42.0, "resets_at": "2026-08-30T10:00:00Z"},
                                 "seven_day": {"used_percentage": 17}})

            result = quota_claude.read(credentials=credentials, opener=opener, timeout=3)

        self.assertTrue(result["known"])
        self.assertEqual((result["five_hour"], result["seven_day"]), (42.0, 17.0))
        self.assertEqual(result["resets_at"], "2026-08-30T10:00:00Z")
        self.assertEqual(seen, {"authorization": "Bearer secret-token", "beta": quota_claude.OAUTH_BETA,
                                "timeout": 3})
        self.assertNotIn("secret-token", json.dumps(result))

    def test_missing_credentials_and_invalid_response_are_explicitly_unknown(self):
        with tempfile.TemporaryDirectory(prefix="altitude-claude-quota-") as tmp:
            missing = quota_claude.read(credentials=Path(tmp) / "missing.json")
            credentials = Path(tmp) / "credentials.json"
            credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
            invalid = quota_claude.read(credentials=credentials,
                                        opener=lambda *args, **kwargs: Response({"five_hour": {"utilization": 420}}))

        self.assertFalse(missing["known"])
        self.assertIn("not found", missing["why"])
        self.assertFalse(invalid["known"])
        self.assertIn("invalid", invalid["why"])


class TestClaudeQuotaPersistence(unittest.TestCase):
    def tearDown(self):
        quota_claude._last_refresh_at = None

    def test_refresh_overwrites_stale_success_and_files_a_fault(self):
        failed = {"known": False, "why": "endpoint unavailable", "read_at": "2026-08-30T00:00:00+00:00"}
        with tempfile.TemporaryDirectory(prefix="altitude-claude-quota-") as tmp:
            root = Path(tmp)
            state.write_json(root / quota_claude.QUOTA_CLAUDE,
                             {"known": True, "five_hour": 1, "read_at": "2026-08-29T00:00:00+00:00"})
            faults = []
            with patch.object(config, "MONITOR_DIR", root), \
                 patch.object(quota_claude, "read", return_value=failed), \
                 patch("altitude.improve.system_fault", side_effect=lambda kind, why: faults.append((kind, why))):
                result = quota_claude.refresh()
            self.assertEqual(state.read_json(root / quota_claude.QUOTA_CLAUDE), failed)

        self.assertEqual(result, failed)
        self.assertEqual(faults, [("quota-claude", "endpoint unavailable")])

    def test_refresh_if_due_throttles_attempts(self):
        calls = []
        with patch.object(quota_claude, "refresh", side_effect=lambda: calls.append(True) or {"known": False}):
            quota_claude._last_refresh_at = None
            quota_claude.refresh_if_due()
            quota_claude.refresh_if_due()
        self.assertEqual(calls, [True])

    def test_partial_statusline_uses_fresh_oauth_for_missing_window(self):
        with tempfile.TemporaryDirectory(prefix="altitude-claude-quota-") as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc)
            state.write_json(root / "statusline-partial.json", {
                "_at": now.timestamp(),
                "rate_limits": {"five_hour": {"used_percentage": 72},
                                "seven_day": {"used_percentage": None}},
            })
            state.write_json(root / quota_claude.QUOTA_CLAUDE, {
                "known": True, "five_hour": 60, "seven_day": 24,
                "read_at": now.isoformat(), "resets_at": "2026-08-31T00:00:00Z",
            })
            with patch.object(config, "MONITOR_DIR", root):
                result = monitor.quota()

        self.assertEqual((result["five_hour"], result["seven_day"]), (72.0, 24.0))
        self.assertEqual(result["source"], "statusline+oauth-reader")
        self.assertTrue(result["known"])

    def test_future_status_and_reader_timestamps_are_not_fresh(self):
        with tempfile.TemporaryDirectory(prefix="altitude-claude-quota-future-") as tmp:
            root = Path(tmp)
            future = datetime.now(timezone.utc) + timedelta(seconds=301)
            state.write_json(root / "statusline-future.json", {
                "_at": future.timestamp(),
                "rate_limits": {"five_hour": {"used_percentage": 10}},
            })
            state.write_json(root / quota_claude.QUOTA_CLAUDE, {
                "known": True, "five_hour": 10, "seven_day": 20, "read_at": future.isoformat(),
            })
            with patch.object(config, "MONITOR_DIR", root):
                result = monitor.quota()
        self.assertFalse(result["known"])

    def test_server_tick_refreshes_both_account_readers(self):
        with patch.object(server.quota_codex, "refresh_if_due") as codex, \
             patch.object(server.quota_claude, "refresh_if_due") as claude, \
             patch.object(server, "drain_hook_faults"), \
             patch.object(server, "morning_digest"), \
             patch.object(server.config, "load_projects", return_value={}):
            server.tick()
        codex.assert_called_once_with()
        claude.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
