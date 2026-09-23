"""Unattended quota acquisition reaches fresh-task routing through real persisted readings."""
import json
import subprocess
import time
import unittest
from datetime import datetime

from tests.support import AltitudeCase
from tests.test_quota_codex import transcript
from altitude import config, engines, monitor, quota_codex, route, state


RESET = "2099-01-01T00:00:00+00:00"


def limit(kind, percent):
    return {"kind": kind, "percent": percent, "resets_at": RESET,
            "severity": "normal", "is_active": True}


def usage(*rows):
    return "\n".join(json.dumps(event) for event in (
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "usage_report": {"rate_limits": {"limits": list(rows)}}},
        {"type": "result", "is_error": False, "num_turns": 0},
    ))


class QuotaFixture(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(engines, "_last_quota_refresh", None)
        self.version = "2.1.277 (Claude Code)\n"
        self.output = usage(limit("weekly_all", 5), limit("session", 1))
        self.failure = None
        self.process = self.patch(engines.subprocess, "run", side_effect=self.native_read)

    def native_read(self, command, **kwargs):
        self.assertEqual(command[0], config.CLAUDE_BIN)
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, self.version, "")
        self.assertEqual(command[1:3], ["--print", "/usage"])
        self.assertTrue(kwargs["check"])
        self.assertLessEqual(kwargs["timeout"], 20)
        if self.failure:
            raise self.failure
        return subprocess.CompletedProcess(command, 0, self.output, "")


class TestNativeQuota(QuotaFixture):
    def test_live_rows_include_zero_and_observation_time_without_a_session(self):
        self.output = usage(limit("weekly_all", 0), limit("session", 12))
        before = time.time()
        reading = engines._claude_quota()
        self.assertTrue(reading["known"])
        self.assertEqual((reading["seven_day"], reading["five_hour"]), (0, 12))
        self.assertEqual(reading["seven_day_resets"], datetime.fromisoformat(RESET).timestamp())
        self.assertGreaterEqual(reading["at"], before)
        self.assertLessEqual(reading["at"], time.time())
        command = self.process.call_args.args[0]
        for flag in ("--safe-mode", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(self.process.call_args.kwargs["cwd"], config.HOME)

    def test_partial_windows_are_not_fabricated_and_model_scopes_are_not_account_quota(self):
        for kind, field, absent in (("weekly_all", "seven_day", "five_hour"),
                                    ("session", "five_hour", "seven_day")):
            with self.subTest(kind=kind):
                self.output = usage(limit(kind, 0), limit("weekly_opus", 100))
                reading = engines._claude_quota()
                self.assertTrue(reading["known"])
                self.assertEqual(reading[field], 0)
                self.assertNotIn(absent, reading)
        self.output = usage(limit("weekly_opus", 20))
        self.assertFalse(engines._claude_quota()["known"])

    def test_unsupported_or_unrecognized_version_never_invokes_print(self):
        for version in ("2.1.276 (Claude Code)", "2.0.99", "unknown", ""):
            with self.subTest(version=version):
                self.version = version
                self.process.reset_mock()
                self.assertFalse(engines._claude_quota()["known"])
                self.assertEqual(self.process.call_args_list[0].args[0][1:], ["--version"])
                self.assertEqual(self.process.call_count, 1)

    def test_missing_executable_and_version_probe_timeout_are_unknown(self):
        for failure in (FileNotFoundError("private executable path"),
                        subprocess.TimeoutExpired("private command", 5)):
            with self.subTest(failure=type(failure).__name__):
                self.process.side_effect = failure
                reading = engines._claude_quota()
                self.assertFalse(reading["known"])
                self.assertNotIn("private", reading["why"])

    def test_usage_timeout_and_nonzero_exit_do_not_persist_private_errors(self):
        for failure in (subprocess.TimeoutExpired("private command", 20),
                        subprocess.CalledProcessError(1, "private command", stderr="private credentials")):
            with self.subTest(failure=type(failure).__name__):
                self.failure = failure
                reading = engines._claude_quota()
                self.assertFalse(reading["known"])
                self.assertNotIn("private", reading["why"])
                self.assertNotIn("at", reading)

    def test_missing_error_or_malformed_report_is_unknown(self):
        for output in ("", "not json", "[]", "null", usage(),
                       json.dumps({"type": "result", "is_error": True, "result": "private failure"}),
                       json.dumps({"type": "assistant", "usage_report": {"rate_limits": []}}),
                       json.dumps({"type": "assistant", "usage_report": {"rate_limits": {"limits": [None]}}})):
            with self.subTest(output=output):
                self.output = output
                reading = engines._claude_quota()
                self.assertFalse(reading["known"])
                self.assertNotIn("private", reading["why"])
                self.assertNotIn("at", reading)

    def test_invalid_percent_reset_duplicate_and_cached_rows_are_unknown(self):
        invalid = [dict(limit("weekly_all", value)) for value in (-1, 101, "bad", None, True, float("nan"))]
        invalid.append({**limit("weekly_all", 5), "resets_at": "bad"})
        for missing in ("severity", "is_active"):
            cached = limit("weekly_all", 5)
            del cached[missing]
            invalid.append(cached)
        for row in invalid:
            with self.subTest(row=row):
                self.output = usage(row)
                self.assertFalse(engines._claude_quota()["known"])
        self.output = usage(limit("weekly_all", 5), limit("weekly_all", 10))
        self.assertFalse(engines._claude_quota()["known"])
        self.output = usage(limit("weekly_all", 5)) + '\n' + json.dumps({"type": "result", "is_error": True})
        self.assertFalse(engines._claude_quota()["known"])


class TestRefreshRouting(QuotaFixture):
    def setUp(self):
        super().setUp()
        self.talk = self.patch(quota_codex, "_talk", return_value=transcript(primary={
            "usedPercent": 60, "windowDurationMins": route.WEEK_MINUTES, "resetsAt": 4070908800},
            secondary={"usedPercent": 1, "windowDurationMins": route.SHORT_MINUTES, "resetsAt": 4070908800}))
        self.installation = self.patch(engines, "installation", return_value={
            "available": None, "why": "fixture executable installed"})
        self.project_config = {"routing": config.parse_routing("codex,claude:fable>claude:opus")}

    def refresh(self):
        engines.refresh_quotas(min_interval=0)

    def choice(self, task=None):
        return route.pick_task(self.project_config, task or {})

    def test_both_readers_persist_current_evidence_and_weekly_headroom_selects_fresh_tasks(self):
        self.output = usage(limit("weekly_all", 5), limit("session", 90))
        self.refresh()
        stored = state.read_json(config.MONITOR_DIR / route.QUOTA_CLAUDE)
        self.assertEqual(stored, monitor.quota())
        self.assertEqual(state.read_json(config.MONITOR_DIR / route.QUOTA_CODEX), route.quota_codex())
        self.assertTrue(route.quota_codex()["known"])
        self.assertEqual(self.choice()["engine"], "claude")
        self.assertIn("weekly headroom", self.choice()["why"])
        self.output = usage(limit("weekly_all", 90), limit("session", 1))
        self.refresh()
        self.assertEqual(self.choice()["engine"], "codex")
        self.assertEqual(route.pick_engine("l3", project=self.project_config)["engine"], "codex")

    def test_partial_quota_keeps_weekly_comparison_unknown(self):
        self.output = usage(limit("session", 1))
        self.refresh()
        self.assertTrue(monitor.quota()["known"])
        self.assertEqual(self.choice()["engine"], "codex")
        self.assertIn("unknown or not comparable", self.choice()["why"])

    def test_failed_refresh_replaces_stale_success_and_reacquisition_restores_balancing(self):
        self.refresh()
        for filename in (route.QUOTA_CLAUDE, route.QUOTA_CODEX):
            path = config.MONITOR_DIR / filename
            reading = state.read_json(path)
            reading.update(at=0, read_at="2000-01-01T00:00:00+00:00")
            state.write_json(path, reading)
        for reading in (monitor.quota(), route.quota_codex()):
            self.assertFalse(reading["known"])
            self.assertTrue(reading["stale"])
        self.assertEqual(self.choice()["engine"], "codex")
        self.failure = subprocess.TimeoutExpired("fixture", 20)
        self.talk.side_effect = TimeoutError("fixture")
        self.refresh()
        for reading in (monitor.quota(), route.quota_codex()):
            self.assertFalse(reading["known"])
            self.assertNotIn("seven_day", reading)
            self.assertNotIn("primary_used", reading)
        self.assertIn("unknown", self.choice()["why"])
        self.failure = None
        self.talk.side_effect = None
        self.refresh()
        self.assertEqual(self.choice()["engine"], "claude")
        self.assertTrue(monitor.quota()["known"])
        self.assertTrue(route.quota_codex()["known"])

    def test_one_reader_failure_does_not_prevent_the_other_seats_update(self):
        self.talk.side_effect = TimeoutError("fixture")
        self.refresh()
        self.assertFalse(route.quota_codex()["known"])
        self.assertTrue(monitor.quota()["known"])
        self.talk.side_effect = None
        self.failure = subprocess.TimeoutExpired("fixture", 20)
        self.refresh()
        self.assertTrue(route.quota_codex()["known"])
        self.assertFalse(monitor.quota()["known"])

    def test_successful_and_failed_attempts_share_the_refresh_interval(self):
        clock = self.patch(engines.time, "monotonic", return_value=1000)
        for failed in (False, True):
            with self.subTest(failed=failed):
                engines._last_quota_refresh = None
                self.process.reset_mock()
                self.talk.reset_mock()
                self.failure = subprocess.TimeoutExpired("fixture", 20) if failed else None
                self.talk.side_effect = TimeoutError("fixture") if failed else None
                clock.return_value = 1000
                engines.refresh_quotas()
                attempts = self.process.call_count
                clock.return_value = 1299
                engines.refresh_quotas()
                self.assertEqual(self.process.call_count, attempts)
                self.assertEqual(self.talk.call_count, 1)
                clock.return_value = 1300
                engines.refresh_quotas()
                self.assertEqual(self.process.call_count, attempts * 2)
                self.assertEqual(self.talk.call_count, 2)

    def test_acquired_headroom_does_not_override_pins_exhaustion_or_installation(self):
        self.refresh()
        self.assertEqual(self.choice({"engine": "codex"})["engine"], "codex")
        self.output = usage(limit("weekly_all", 5), limit("session", 100))
        self.refresh()
        self.assertEqual(self.choice()["engine"], "codex")
        self.assertIsNone(self.choice({"engine": "claude"})["engine"])
        self.output = usage(limit("weekly_all", 5), limit("session", 1))
        self.refresh()
        self.installation.side_effect = lambda engine: {
            "available": False if engine == "claude" else None, "why": "executable missing"}
        self.assertEqual(self.choice()["engine"], "codex")
        self.assertIsNone(self.choice({"engine": "claude"})["engine"])

    def test_rejection_and_all_exhausted_still_exclude_seats(self):
        self.refresh()
        route.note_rejection({"engine": "claude"}, {"scope": "engine", "why": "authentication required"})
        self.assertEqual(self.choice()["engine"], "codex")
        self.talk.return_value = transcript(primary={
            "usedPercent": 100, "windowDurationMins": route.WEEK_MINUTES, "resetsAt": 4070908800})
        self.refresh()
        self.assertIsNone(self.choice()["engine"])


if __name__ == "__main__":
    unittest.main()
