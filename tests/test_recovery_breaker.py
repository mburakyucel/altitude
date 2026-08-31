"""The automatic-recovery circuit breaker is durable, bounded, and generation fenced."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_IMPORT_HOME = Path(tempfile.mkdtemp(prefix="altitude-recovery-breaker-import-"))
os.environ["ALTITUDE_HOME"] = str(_IMPORT_HOME)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, recovery_breaker as breaker, server  # noqa: E402


BASE = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


class TestRecoveryBreaker(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="altitude-recovery-breaker-")
        self.addCleanup(tmp.cleanup)
        self.monitor = Path(tmp.name) / "monitor"
        self.monitor.mkdir()
        old_monitor = config.MONITOR_DIR
        config.MONITOR_DIR = self.monitor
        breaker.acquire_reset_authority()
        self.addCleanup(setattr, config, "MONITOR_DIR", old_monitor)
        limits = mock.patch.multiple(
            config,
            RECOVERY_BREAKER_TICK_LIMIT=10,
            RECOVERY_BREAKER_WINDOW_LIMIT=20,
            RECOVERY_BREAKER_WINDOW_SECONDS=900,
            RECOVERY_BREAKER_FAILURE_LIMIT=3,
            AGENT_POLL_SECONDS=30,
        )
        limits.start()
        self.addCleanup(limits.stop)

    def reserve(self, number=1, *, at=BASE, kind="resume"):
        return breaker.reserve(kind, project="altitude", slug=f"task-{number}",
                               evidence_generation=f"dispatch-{number}", at=at)

    def spawn_started(self, *, slug: str, evidence: str, crash: bool):
        env = dict(os.environ)
        env["ALTITUDE_HOME"] = str(self.monitor.parent)
        body = "os._exit(23)" if crash else "time.sleep(60)"
        code = f"""
import os, time
from altitude import recovery_breaker as breaker
permit = breaker.reserve("resume", project="altitude", slug={slug!r}, evidence_generation={evidence!r})
with breaker.launch_gate(permit, deadline_seconds=0.001):
    print("started", flush=True)
    {body}
"""
        child = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parent.parent), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(child.stdout.readline().strip(), "started")
        return child

    def test_missing_state_is_closed_and_public_snapshot_hides_permits(self):
        state = breaker.public_state(at=BASE)
        self.assertEqual((state["mode"], state["generation"]), (breaker.CLOSED, 1))
        self.assertFalse(breaker.state_path().exists())
        permit = self.reserve()
        state = breaker.public_state(at=BASE)
        self.assertEqual(state["tick"], {"bucket": int(BASE.timestamp()) // 30, "used": 1, "limit": 10})
        self.assertNotIn(permit["token"], json.dumps(state))

    def test_tick_limit_opens_before_the_n_plus_one_action(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_TICK_LIMIT", 2):
            first, second = self.reserve(1), self.reserve(2)
            with self.assertRaises(breaker.BreakerOpen):
                self.reserve(3)
        raw = json.loads(breaker.state_path().read_text())
        self.assertEqual((raw["mode"], raw["generation"], len(raw["events"])), (breaker.OPEN, 2, 2))
        self.assertEqual([event["state"] for event in raw["events"]], ["invalidated", "invalidated"])
        self.assertNotIn("dispatch-3", [event["evidence_generation"] for event in raw["events"]])
        with self.assertRaises(breaker.BreakerOpen):
            with breaker.launch_gate(first):
                self.fail("a stale body must never execute")
        self.assertEqual(second["generation"], 1)

    def test_rolling_window_survives_separate_calls_and_tick_buckets(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_WINDOW_LIMIT", 2):
            for number, offset in ((1, 0), (2, 31)):
                permit = self.reserve(number, at=BASE + timedelta(seconds=offset))
                breaker.settle(permit, outcome="success", at=BASE + timedelta(seconds=offset + 1))
            with self.assertRaises(breaker.BreakerOpen):
                self.reserve(3, at=BASE + timedelta(seconds=62))
        state = breaker.public_state(at=BASE + timedelta(seconds=62))
        self.assertEqual(state["reason"]["code"], "window-limit")
        self.assertEqual(state["window"]["used"], 2)

    def test_old_window_entries_expire_but_future_clock_entries_fail_closed(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_WINDOW_LIMIT", 1):
            old = self.reserve(1, at=BASE)
            breaker.settle(old, outcome="success", at=BASE)
            current = self.reserve(2, at=BASE + timedelta(seconds=901))
            breaker.settle(current, outcome="success", at=BASE + timedelta(seconds=901))
            with self.assertRaises(breaker.BreakerOpen):
                self.reserve(3, at=BASE + timedelta(seconds=900))

    def test_failure_family_threshold_opens_and_idempotence_does_not_double_count(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_FAILURE_LIMIT", 2):
            first = self.reserve(1)
            one = breaker.settle(first, outcome="error", error_family="resume-crash", at=BASE)
            again = breaker.settle(first, outcome="error", error_family="resume-crash", at=BASE)
            self.assertFalse(one["opened"])
            self.assertTrue(again["idempotent"])
            second = self.reserve(2, at=BASE + timedelta(seconds=31))
            result = breaker.settle(second, outcome="error", error_family="resume-crash",
                                    at=BASE + timedelta(seconds=31))
        self.assertTrue(result["opened"])
        state = breaker.public_state(at=BASE + timedelta(seconds=31))
        self.assertEqual(state["mode"], breaker.OPEN)
        self.assertEqual(state["failure_families"]["counts"], {"resume-crash": 2})
        self.assertNotIn(first["token"], json.dumps(state["reason"]))

    def test_distinct_failure_families_do_not_combine(self):
        for number, family in enumerate(("git", "engine", "git", "engine"), 1):
            at = BASE + timedelta(seconds=31 * number)
            permit = self.reserve(number, at=at)
            result = breaker.settle(permit, outcome="error", error_family=family, at=at)
            self.assertFalse(result["opened"])
        self.assertEqual(breaker.public_state(at=at)["mode"], breaker.CLOSED)

    def test_launch_gate_consumes_once_and_settled_evidence_can_retry(self):
        permit = self.reserve()
        with breaker.launch_gate(permit, at=BASE) as started:
            self.assertEqual(started["state"], "started")
        with self.assertRaises(breaker.BreakerPermitError):
            with breaker.launch_gate(permit, at=BASE):
                pass
        self.assertTrue(breaker.settle(permit, outcome="success", at=BASE)["settled"])
        retry = breaker.reserve("resume", project="altitude", slug="task-1",
                                evidence_generation="dispatch-1", at=BASE + timedelta(seconds=31))
        self.assertNotEqual(retry["token"], permit["token"])

    def test_server_action_error_settles_a_failure_family(self):
        def fail():
            raise RuntimeError("engine failed")

        with self.assertRaisesRegex(RuntimeError, "engine failed"):
            server._automatic_call("dispatch", "altitude", "task-1", {"attempt": 1}, fail)
        raw = json.loads(breaker.state_path().read_text())
        self.assertEqual((raw["events"][0]["state"], raw["events"][0]["error_family"]),
                         ("settled", "dispatch:RuntimeError"))

    def test_launch_gate_persistence_failure_never_reaches_action(self):
        permit = self.reserve()
        target = mock.Mock()
        with mock.patch.object(breaker, "_save_locked", side_effect=breaker.BreakerStateError("disk full")):
            with self.assertRaisesRegex(breaker.BreakerStateError, "disk full"):
                with breaker.launch_gate(permit, at=BASE):
                    target()
        target.assert_not_called()
        raw = json.loads(breaker.state_path().read_text())
        self.assertEqual(raw["events"][0]["state"], "reserved")

    def test_crashed_owner_is_reconciled_and_same_evidence_can_retry(self):
        child = self.spawn_started(slug="crash", evidence="crash-1", crash=True)
        _, stderr = child.communicate(timeout=5)
        self.assertEqual((child.returncode, stderr), (23, ""))
        started = json.loads(breaker.state_path().read_text())["events"][0]
        self.assertEqual((started["state"], started["owner_pid"]), ("started", child.pid))
        self.assertTrue(started["owner_start"])
        self.assertTrue(started["deadline_at"])
        self.assertEqual(len(started["action_identity"]), 64)

        result = breaker.reconcile()
        self.assertEqual(result["reconciled"], 1)
        settled = json.loads(breaker.state_path().read_text())["events"][0]
        self.assertEqual((settled["state"], settled["outcome"]), ("settled", "owner-dead"))
        retry = breaker.reserve("resume", project="altitude", slug="crash",
                                evidence_generation="crash-1")
        self.assertNotEqual(retry["token"], started["token"])
        history = breaker.history_path().read_text()
        self.assertIn('"event": "logical-action-owner-dead"', history)
        self.assertNotIn(started["token"], history)

    def test_live_owner_survives_expired_deadline_and_blocks_reset_until_death(self):
        child = self.spawn_started(slug="live", evidence="live-1", crash=False)
        try:
            raw = json.loads(breaker.state_path().read_text())["events"][0]
            after_deadline = datetime.fromisoformat(raw["deadline_at"]) + timedelta(seconds=1)
            state = breaker.public_state(at=after_deadline)
            action = state["recent_actions"][0]
            self.assertEqual((action["state"], action["owner_live"]), ("started", True))
            self.assertTrue(action["deadline_expired"])
            with mock.patch.object(config, "RECOVERY_BREAKER_WINDOW_LIMIT", 1):
                with self.assertRaises(breaker.BreakerOpen):
                    breaker.reserve("resume", project="altitude", slug="other",
                                    evidence_generation="other-1")
            with self.assertRaisesRegex(breaker.BreakerPermitError, "logical action is still started"):
                breaker.reset(2, "live action must survive")
        finally:
            child.terminate()
            _, stderr = child.communicate(timeout=5)
        self.assertEqual(stderr, "")
        closed = breaker.reset(2, "exact owner is now dead")
        self.assertEqual((closed["mode"], closed["generation"]), (breaker.CLOSED, 3))

    def test_reset_refuses_started_old_generation_until_exact_settlement(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_TICK_LIMIT", 1):
            active = self.reserve(1)
            with breaker.launch_gate(active, at=BASE):
                pass
            with self.assertRaises(breaker.BreakerOpen):
                self.reserve(2)
        with self.assertRaisesRegex(breaker.BreakerPermitError, "logical action is still started"):
            breaker.reset(2, "must not abandon started work", at=BASE)
        settlement = breaker.settle(active, outcome="success", at=BASE)
        self.assertEqual((settlement["settled"], settlement["stale"], settlement["historical"]),
                         (True, True, True))
        closed = breaker.reset(2, "logical action settled", at=BASE)
        self.assertEqual((closed["mode"], closed["generation"]), (breaker.CLOSED, 3))

    def test_stale_or_mismatched_generation_cannot_settle_current_state(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_TICK_LIMIT", 1):
            old = self.reserve(1)
            with self.assertRaises(breaker.BreakerOpen):
                self.reserve(2)
        opened = breaker.public_state(at=BASE)
        self.assertEqual(opened["generation"], 2)
        self.assertTrue(breaker.settle(old, outcome="success", at=BASE)["stale"])
        with self.assertRaises(breaker.BreakerPermitError):
            breaker.reset(1, "wrong generation", at=BASE)
        closed = breaker.reset(2, "operator inspected the recovery loop", at=BASE)
        self.assertEqual((closed["mode"], closed["generation"], closed["window"]["used"]),
                         (breaker.CLOSED, 3, 0))
        history = [json.loads(line) for line in breaker.history_path().read_text().splitlines()]
        self.assertEqual([record["event"] for record in history], ["opened", "reset"])

    def test_second_process_cannot_acquire_daemon_reset_authority(self):
        env = dict(os.environ)
        env["ALTITUDE_HOME"] = str(self.monitor.parent)
        code = ("from altitude import recovery_breaker as b; "
                "b.acquire_reset_authority()")
        child = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parent.parent),
                               env=env, capture_output=True, text=True)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("another daemon process owns", child.stderr)

    def test_reset_refuses_a_process_without_daemon_authority(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_TICK_LIMIT", 0):
            with self.assertRaises(breaker.BreakerOpen):
                self.reserve()
        with mock.patch.object(breaker, "_RESET_PID", os.getpid() + 1):
            with self.assertRaisesRegex(breaker.BreakerPermitError, "daemon process authority"):
                breaker.reset(2, "forged actor must not reset", at=BASE)
        self.assertEqual(breaker.public_state(at=BASE)["mode"], breaker.OPEN)

    def test_corrupt_state_is_open_until_exact_manual_reset(self):
        breaker.state_path().write_text("{not json")
        state = breaker.public_state(at=BASE)
        self.assertEqual((state["mode"], state["generation"], state["corrupt"]), (breaker.OPEN, 0, True))
        with self.assertRaises(breaker.BreakerStateError):
            self.reserve()
        with self.assertRaises(breaker.BreakerPermitError):
            breaker.reset(1, "incorrect generation", at=BASE)
        closed = breaker.reset(0, "operator archived corrupt evidence", at=BASE)
        self.assertEqual((closed["mode"], closed["generation"], closed["corrupt"]),
                         (breaker.CLOSED, 1, False))
        history = breaker.history_path().read_text()
        self.assertIn('"corrupt": true', history)
        self.assertNotIn("{not json", history)

    def test_concurrent_reservations_cannot_cross_the_threshold(self):
        with mock.patch.object(config, "RECOVERY_BREAKER_TICK_LIMIT", 4):
            def attempt(number):
                try:
                    self.reserve(number)
                    return "accepted"
                except breaker.BreakerOpen:
                    return "refused"

            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(attempt, range(1, 13)))
        self.assertEqual(results.count("accepted"), 4)
        self.assertEqual(results.count("refused"), 8)
        raw = json.loads(breaker.state_path().read_text())
        self.assertEqual((raw["mode"], len(raw["events"])), (breaker.OPEN, 4))

    def test_server_automatic_call_settles_and_open_state_never_calls_target(self):
        target = mock.Mock(return_value={"ok": True})
        result = server._automatic_call("dispatch", "altitude", "task-1", {"attempt": 1}, target)
        self.assertEqual(result, {"ok": True})
        target.assert_called_once_with()
        raw = json.loads(breaker.state_path().read_text())
        self.assertEqual((raw["events"][0]["kind"], raw["events"][0]["state"],
                          raw["events"][0]["outcome"]), ("dispatch", "settled", "success"))

        breaker.state_path().write_text("{broken")
        refused = mock.Mock()
        with mock.patch.object(server, "log"):
            held = server._automatic_call("dispatch", "altitude", "task-2", {"attempt": 1}, refused)
        self.assertIs(held, server._BREAKER_BLOCKED)
        refused.assert_not_called()

    def test_open_tick_keeps_evidence_closeout_and_merge_but_suppresses_launches(self):
        tasks = [
            {"slug": "needs-size", "state": "requested", "class": None},
            {"slug": "needs-proposal", "state": "requested", "class": "M"},
            {"slug": "needs-dispatch", "state": "approved", "class": "M"},
        ]
        spawned = []

        def list_tasks(project, include_archive=False):
            return [] if include_archive else list(tasks)

        with mock.patch.object(server.quota_codex, "refresh_if_due"), \
             mock.patch.object(server.quota_claude, "refresh_if_due"), \
             mock.patch.object(server, "drain_hook_faults"), \
             mock.patch.object(server, "_breaker_open", return_value=True), \
             mock.patch.object(server.config, "load_projects", return_value={"altitude": {}}), \
             mock.patch.object(server.l3, "lease_info"), \
             mock.patch.object(server.T, "migrate_operational_parks", return_value=[]), \
             mock.patch.object(server.dispatch, "poll", return_value=[{"task": {"slug": "landed"}}]), \
             mock.patch.object(server.merge_coordinator, "pending", return_value=[("landed", 91)]), \
             mock.patch.object(server, "spawn", side_effect=lambda key, *args: spawned.append(key) or True), \
             mock.patch.object(server, "resume_stranded_reports") as closeout, \
             mock.patch.object(server.dispatch, "resume_due") as due, \
             mock.patch.object(server.dispatch, "resume_recoverable") as recoverable, \
             mock.patch.object(server, "resume_stranded_blockers") as blocker_scan, \
             mock.patch.object(server.S, "list_tasks", side_effect=list_tasks), \
             mock.patch.object(server, "dispatch_waiting") as dispatch_waiting, \
             mock.patch.object(server.mechanize, "run_due") as mechanize_due, \
             mock.patch.object(server, "weekly_audit") as audit, \
             mock.patch.object(server, "morning_digest"), \
             mock.patch.object(server, "log"):
            server.tick()

        self.assertEqual(spawned, ["finished:altitude:landed", "merge:altitude:landed:91"])
        closeout.assert_called_once_with("altitude")
        blocker_scan.assert_called_once_with("altitude")
        due.assert_not_called(); recoverable.assert_not_called(); dispatch_waiting.assert_not_called()
        mechanize_due.assert_not_called(); audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
