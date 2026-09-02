"""Recovery faults stop ordinary dispatch without recursively creating work."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_BOOT = Path(tempfile.mkdtemp(prefix="altitude-recovery-safety-bootstrap-"))
os.environ["ALTITUDE_HOME"] = str(_BOOT)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, incidents, monitor, recovery, server, state as S, tasks as T  # noqa: E402


class TestRecoveryFuse(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="altitude-recovery-safety-"))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.patches = ExitStack()
        for obj, name, value in (
            (config, "ROOT", self.root),
            (config, "MONITOR_DIR", self.root / "monitor"),
            (config, "PROJECTS_FILE", self.root / "projects.json"),
            (config, "INCIDENT_INDEX", self.root / "incidents.jsonl"),
            (incidents, "FAULTS", self.root / "monitor" / "faults.json"),
        ):
            self.patches.enter_context(mock.patch.object(obj, name, value))
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": str(self.repo)}})

    def tearDown(self):
        self.patches.close()

    def _resume_fault_case(self, stage: str, entry: str) -> None:
        """Exercise a real recovery incident only after the public resume lock unwinds."""
        task = T.new("altitude", f"{entry} {stage} resume", "request", actor="l3", source="chat")
        worktree = self.repo / ".claude" / "worktrees" / task["slug"]
        worktree.mkdir(parents=True)
        task.update({
            "state": "blocked" if entry == "blocked" else "running",
            "blocked_reason": "operator answer required" if entry == "blocked" else None,
            "worktree": str(worktree), "dispatch_id": f"{task['slug']}-1",
            "session_id": "session-old", "agent_id": "agent-old", "l2_token": "stable-token",
        })
        S.save_task("altitude", task)
        new = {"name": f"altitude/{task['dispatch_id']}", "id": "agent-new",
               "sessionId": "session-new", "state": "working", "startedAt": 2}

        with ExitStack() as stack:
            fetch = stack.enter_context(mock.patch.object(
                dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40))
            stack.enter_context(mock.patch.object(dispatch, "_validate_task_worktree"))
            stack.enter_context(mock.patch.object(dispatch, "wip_hold", return_value=None))
            stack.enter_context(mock.patch.object(engines, "usage_hold", return_value=None))
            stop = stack.enter_context(mock.patch.object(engines, "stop_l2_worker", return_value="stopped"))
            launch = stack.enter_context(mock.patch.object(
                engines, "resume_l2", return_value={"returncode": 0, "stdout": "", "stderr": ""}))
            stack.enter_context(mock.patch.object(engines, "claude_agents", return_value=[new]))
            if stage == "provenance":
                fetch.side_effect = dispatch.git_policy.GitPolicyError("origin ownership changed")
            elif stage == "stop":
                stop.side_effect = OSError("stop transport failed")
            elif stage == "launch":
                launch.side_effect = OSError("launcher failed")
            elif stage == "bind":
                stack.enter_context(mock.patch.object(S, "save_task", side_effect=OSError("state disk unavailable")))
            else:  # pragma: no cover - test helper misuse
                self.fail(f"unknown resume fault stage {stage}")

            expected_error = T.TransitionError if stage == "provenance" else RuntimeError
            expected_text = "resume refused by Git provenance gate" if stage == "provenance" else "resume of altitude/"
            with self.assertRaisesRegex(expected_error, expected_text):
                if entry == "direct":
                    dispatch.resume_session("altitude", task["slug"], "continue")
                elif entry == "message":
                    dispatch.message_l2("altitude", task["slug"], "steer")
                else:
                    dispatch.resume_blocked("altitude", task["slug"], "approved")

        if stage == "provenance":
            stop.assert_not_called()
            launch.assert_not_called()
            kind = "task-git-provenance"
        elif stage == "stop":
            self.assertEqual([call.args[1] for call in stop.call_args_list], ["agent-old"])
            launch.assert_not_called()
            kind = "l2-resume"
        elif stage == "launch":
            self.assertEqual([call.args[1] for call in stop.call_args_list], ["agent-old"])
            launch.assert_called_once()
            kind = "l2-resume"
        else:
            self.assertEqual([call.args[1] for call in stop.call_args_list], ["agent-old", "agent-new"])
            launch.assert_called_once()
            kind = "l2-resume"

        failed = [event for event in S.read_events("altitude", task["slug"])
                  if event.get("kind") == "resume-failed"]
        self.assertEqual(len(failed), 1)
        faults = [fault for fault in recovery.status()["faults"] if fault.get("kind") == kind]
        self.assertEqual(len(faults), 1)
        self.assertIsNotNone(faults[0].get("incident"))
        filed = [row for row in incidents.index() if row.get("title") == f"system fault: {kind}"]
        self.assertEqual(len(filed), 1)

    def test_direct_provenance_fault_unwinds_before_real_incident(self):
        self._resume_fault_case("provenance", "direct")

    def test_message_provenance_fault_unwinds_before_real_incident(self):
        self._resume_fault_case("provenance", "message")

    def test_direct_stop_fault_unwinds_before_real_incident(self):
        self._resume_fault_case("stop", "direct")

    def test_blocked_stop_fault_unwinds_before_real_incident(self):
        self._resume_fault_case("stop", "blocked")

    def test_direct_launch_fault_unwinds_before_real_incident(self):
        self._resume_fault_case("launch", "direct")

    def test_message_launch_fault_unwinds_before_real_incident(self):
        self._resume_fault_case("launch", "message")

    def test_direct_bind_fault_unwinds_before_real_incident_and_stops_replacement(self):
        self._resume_fault_case("bind", "direct")

    def test_blocked_bind_fault_unwinds_before_real_incident_and_stops_replacement(self):
        self._resume_fault_case("bind", "blocked")

    def test_resume_never_translates_or_suppresses_a_lock_order_error(self):
        task = T.new("altitude", "lock order resume", "request", actor="l3", source="chat")
        worktree = self.repo / ".claude" / "worktrees" / task["slug"]
        worktree.mkdir(parents=True)
        task.update({
            "state": "running", "worktree": str(worktree), "dispatch_id": f"{task['slug']}-1",
            "session_id": "session-old", "agent_id": "agent-old", "l2_token": "stable-token",
        })
        S.save_task("altitude", task)
        inversion = S.LockOrderError("synthetic lock inversion")

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(engines, "stop_l2_worker", side_effect=inversion):
            with self.assertRaisesRegex(S.LockOrderError, "synthetic lock inversion"):
                dispatch.resume_session("altitude", task["slug"], "continue")

        self.assertIsNone(recovery.status())
        self.assertFalse(any(event.get("kind") == "resume-failed"
                             for event in S.read_events("altitude", task["slug"])))

    def test_system_fault_holds_dispatch_and_creates_no_task(self):
        result = incidents.system_fault("test-health", "engine supervision failed", project="altitude")

        self.assertIsNotNone(result)
        self.assertEqual(S.list_tasks("altitude"), [], "fault evidence must not recursively create repair work")
        self.assertEqual(recovery.status()["faults"][-1]["kind"], "test-health")

        ordinary = T.new("altitude", "ordinary work", "request", actor="l3", source="chat")
        self.assertIn("recovery hold", dispatch.wip_hold("altitude", ordinary))

    def test_fault_episode_requests_one_deduplicated_l3_turn_without_a_task(self):
        first = incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        second = incidents.system_fault("test-health", "same mechanism failed again", project="altitude")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        attention = recovery.l3_attention_due("altitude")
        self.assertIsNotNone(attention)
        self.assertEqual(attention["revision"], 1)
        self.assertEqual([row["kind"] for row in attention["faults"]], ["test-health"])
        self.assertEqual(S.list_tasks("altitude"), [])

    def test_wake_is_durable_before_incident_rendering_can_fail(self):
        with mock.patch.object(incidents, "new_incident", side_effect=OSError("incident store unavailable")):
            with self.assertRaisesRegex(OSError, "incident store unavailable"):
                incidents.system_fault("test-health", "engine supervision failed", project="altitude")

        attention = recovery.l3_attention_due("altitude")
        self.assertIsNotNone(attention)
        self.assertEqual([row["kind"] for row in attention["faults"]], ["test-health"])
        self.assertEqual(S.list_tasks("altitude"), [])

    def test_different_faults_coalesce_into_one_successful_recovery_turn(self):
        incidents.system_fault("first-health", "first failure", project="altitude")
        incidents.system_fault("second-health", "second failure", project="altitude")
        calls = []

        def turn(project, prompt, **kwargs):
            self.assertTrue(kwargs["precheck"]())
            calls.append((project, prompt, kwargs["trigger"]))
            return {"error": None, "skipped": False, "completed": True}

        with mock.patch.object(server.l3, "turn", side_effect=turn):
            server.run_recovery_turn("altitude")
            server.run_recovery_turn("altitude")

        self.assertEqual(len(calls), 1)
        self.assertIn("first-health", calls[0][1])
        self.assertIn("second-health", calls[0][1])
        self.assertEqual(calls[0][2], "system-recovery")
        self.assertIsNone(recovery.l3_attention_due("altitude"))
        self.assertIsNotNone(recovery.status(), "a successful L3 turn must not clear the fuse")
        history = [row for row in S.read_project_log("altitude") if row.get("kind") == "recovery-turn-handled"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["revision"], 1)
        self.assertEqual(S.list_tasks("altitude"), [])

    def test_failed_recovery_turn_retries_the_same_wake_with_backoff(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        with mock.patch.object(server.l3, "turn", return_value={"error": "capacity", "skipped": False}):
            server.run_recovery_turn("altitude")

        held = recovery.status()
        attention = held["l3_attention"]
        self.assertEqual(attention["attempts"], 1)
        self.assertEqual(attention["handled_revision"], 0)
        self.assertIsNotNone(attention["next_attempt"])
        self.assertIsNone(recovery.l3_attention_due("altitude"), "backoff prevents a tight timer retry loop")
        self.assertFalse(any(row.get("kind") == "recovery-turn-handled" for row in S.read_project_log("altitude")))

        attention["next_attempt"] = "1970-01-01T00:00:00+00:00"
        S.write_json(recovery.hold_path(), held)

        retry = recovery.claim_l3_attention("altitude")
        self.assertIsNotNone(retry)
        self.assertTrue(recovery.l3_attention_is_current(
            "altitude", retry["episode"], retry["revision"], retry["claim"]))
        self.assertTrue(recovery.complete_l3_attention(
            "altitude", retry["episode"], retry["revision"], retry["claim"]))
        self.assertIsNone(recovery.l3_attention_due("altitude"))
        self.assertEqual(len([row for row in S.read_project_log("altitude")
                              if row.get("kind") == "recovery-turn-handled"]), 1)
        self.assertEqual(S.list_tasks("altitude"), [])

    def test_clear_cancels_a_stale_wake_and_a_new_episode_can_wake(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        old = recovery.claim_l3_attention("altitude")
        recovery.clear("operator verified the episode is stable", actor="burak")

        with mock.patch.object(server.l3, "turn") as turn:
            server.run_recovery_turn("altitude")
        turn.assert_not_called()
        self.assertFalse(recovery.l3_attention_is_current(
            "altitude", old["episode"], old["revision"], old["claim"]))

        # Fault dedupe spans episodes, so the repeated kind must still create a fresh wake after clearance.
        self.assertIsNone(incidents.system_fault("test-health", "failed after clearance", project="altitude"))
        new = recovery.l3_attention_due("altitude")
        self.assertIsNotNone(new)
        self.assertNotEqual(new["episode"], old["episode"])

    def test_recovery_turn_exception_does_not_file_a_recursive_workflow_fault(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        with mock.patch.object(server.l3, "turn", side_effect=RuntimeError("model unavailable")):
            server.run_recovery_turn("altitude")

        self.assertEqual(set(S.read_json(incidents.FAULTS)), {"test-health"})
        self.assertEqual(S.list_tasks("altitude"), [])
        self.assertEqual(recovery.status()["l3_attention"]["attempts"], 1)

    def test_fault_arriving_during_a_turn_joins_the_same_episode_without_another_turn(self):
        incidents.system_fault("first-health", "first failure", project="altitude")
        prompts = []

        def first_turn(project, prompt, **kwargs):
            self.assertTrue(kwargs["precheck"]())
            prompts.append(prompt)
            incidents.system_fault("second-health", "arrived during recovery", project="altitude")
            return {"error": None, "skipped": False, "completed": True}

        with mock.patch.object(server.l3, "turn", side_effect=first_turn):
            server.run_recovery_turn("altitude")

        attention = recovery.status()["l3_attention"]
        self.assertEqual(attention["revision"], 1)
        self.assertEqual(attention["handled_revision"], 1)
        self.assertEqual([row["kind"] for row in attention["faults"]], ["first-health", "second-health"])
        self.assertEqual(len(prompts), 1)
        self.assertIsNone(recovery.l3_attention_due("altitude"))
        self.assertEqual(S.list_tasks("altitude"), [])

    def test_clear_during_precheck_skips_without_recreating_the_episode(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")

        def clear_then_precheck(project, prompt, **kwargs):
            recovery.clear("operator cleared before the delayed turn", actor="burak")
            self.assertFalse(kwargs["precheck"]())
            return {"error": None, "skipped": True, "completed": False}

        with mock.patch.object(server.l3, "turn", side_effect=clear_then_precheck):
            server.run_recovery_turn("altitude")
        self.assertIsNone(recovery.status())
        self.assertFalse(any(row.get("kind") == "recovery-turn-handled" for row in S.read_project_log("altitude")))
        self.assertEqual(S.list_tasks("altitude"), [])

    def test_attention_claim_is_atomic_owner_fenced_and_recovers_when_stale(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        barrier = threading.Barrier(2)

        def claim(_):
            barrier.wait()
            return recovery.claim_l3_attention("altitude")

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, (1, 2)))
        winners = [row for row in claims if row]
        self.assertEqual(len(winners), 1)
        winner = winners[0]
        self.assertIsNone(recovery.l3_attention_due("altitude"))
        claimed = datetime.fromisoformat(recovery.status()["l3_attention"]["claimed"])
        self.assertIsNone(recovery.l3_attention_due(
            "altitude", now=claimed + timedelta(seconds=config.L3_CODEX_TURN_TIMEOUT)),
            "a live Codex L3 turn must retain its claim for its entire execution timeout")
        self.assertFalse(recovery.complete_l3_attention(
            "altitude", winner["episode"], winner["revision"], "not-the-owner"))

        held = recovery.status()
        held["l3_attention"]["claimed"] = "1970-01-01T00:00:00+00:00"
        S.write_json(recovery.hold_path(), held)
        replacement = recovery.claim_l3_attention("altitude")
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement["claim"], winner["claim"])
        self.assertFalse(recovery.complete_l3_attention(
            "altitude", winner["episode"], winner["revision"], winner["claim"]))
        self.assertTrue(recovery.complete_l3_attention(
            "altitude", replacement["episode"], replacement["revision"], replacement["claim"]))
        self.assertFalse(recovery.complete_l3_attention(
            "altitude", replacement["episode"], replacement["revision"], replacement["claim"]))

    def test_post_l3_lock_precheck_renews_a_claim_that_waited_to_start(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        claim = recovery.claim_l3_attention("altitude")
        held = recovery.status()
        held["l3_attention"]["claimed"] = "1970-01-01T00:00:00+00:00"
        S.write_json(recovery.hold_path(), held)

        self.assertTrue(recovery.l3_attention_is_current(
            "altitude", claim["episode"], claim["revision"], claim["claim"]))
        renewed = datetime.fromisoformat(recovery.status()["l3_attention"]["claimed"])
        self.assertGreater(renewed, datetime.fromisoformat("1970-01-01T00:00:00+00:00"))
        self.assertIsNone(recovery.l3_attention_due(
            "altitude", now=renewed + timedelta(seconds=config.L3_CODEX_TURN_TIMEOUT)))

    def test_empty_l3_result_is_not_acknowledged_as_a_completed_turn(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        with mock.patch.object(server.l3, "turn", return_value={}):
            server.run_recovery_turn("altitude")

        attention = recovery.status()["l3_attention"]
        self.assertEqual(attention["handled_revision"], 0)
        self.assertEqual(attention["attempts"], 1)
        self.assertNotIn("claim", attention)
        self.assertFalse(any(row.get("kind") == "recovery-turn-handled" for row in S.read_project_log("altitude")))

    def test_two_consumers_execute_only_the_atomic_claim_winner(self):
        incidents.system_fault("test-health", "engine supervision failed", project="altitude")
        entered, release = threading.Event(), threading.Event()
        calls = []

        def turn(project, prompt, **kwargs):
            calls.append(project)
            entered.set()
            self.assertTrue(release.wait(2))
            self.assertTrue(kwargs["precheck"]())
            return {"error": None, "skipped": False, "completed": True}

        with mock.patch.object(server.l3, "turn", side_effect=turn):
            first = threading.Thread(target=server.run_recovery_turn, args=("altitude",))
            second = threading.Thread(target=server.run_recovery_turn, args=("altitude",))
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            second.join(2)
            release.set()
            first.join(2)

        self.assertEqual(calls, ["altitude"])
        handled = [row for row in S.read_project_log("altitude") if row.get("kind") == "recovery-turn-handled"]
        self.assertEqual(len(handled), 1)
        self.assertIsNone(recovery.l3_attention_due("altitude"))

    def test_new_fault_kind_does_not_bypass_a_failed_turn_backoff(self):
        incidents.system_fault("first-health", "first failure", project="altitude")
        with mock.patch.object(server.l3, "turn", return_value={"error": "capacity", "skipped": False}):
            server.run_recovery_turn("altitude")
        before = recovery.status()["l3_attention"]

        incidents.system_fault("second-health", "second failure", project="altitude")
        after = recovery.status()["l3_attention"]

        self.assertEqual(after["revision"], 1)
        self.assertEqual(after["attempts"], before["attempts"])
        self.assertEqual(after["next_attempt"], before["next_attempt"])
        self.assertEqual([row["kind"] for row in after["faults"]], ["first-health", "second-health"])
        self.assertIsNone(recovery.l3_attention_due("altitude"))

    def test_one_explicit_repair_is_allowed_until_l3_clears(self):
        recovery.hold("runtime ownership is uncertain", kind="ownership", actor="l3")
        repair = T.new("altitude", "repair ownership", "diagnose and repair", actor="l3", source="recovery")

        self.assertEqual(repair["state"], "queued", "recovery delegation enters the direct queue")
        self.assertIsNone(recovery.dispatch_hold("altitude", repair))
        with self.assertRaisesRegex(T.TransitionError, "already has active repair task"):
            T.new("altitude", "second repair", "another repair", actor="l3", source="recovery")
        with self.assertRaisesRegex(ValueError, "only L3 or Burak"):
            recovery.clear("looks fine", actor="altd")

        cleared = recovery.clear("ownership reconciled\nand the repair PR verified", actor="l3")
        self.assertTrue(cleared["cleared"])
        self.assertIsNone(recovery.status())
        history_text = recovery.clearance_history_path().read_text()
        history = [json.loads(line) for line in history_text.splitlines()]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reason"], "ownership reconciled and the repair PR verified")
        self.assertEqual(history[0]["repair"]["slug"], repair["slug"])
        self.assertEqual(history[0]["faults"], [
            {"count": 1, "incident": None, "kind": "ownership"}
        ])
        self.assertNotIn("runtime ownership is uncertain", history_text,
                         "clearance history keeps evidence references, not raw fault details")
        self.assertEqual(recovery.clearance_history_path().stat().st_mode & 0o777, 0o600)
        ordinary = T.new("altitude", "ordinary after recovery", "request", actor="l3", source="chat")
        self.assertIsNone(recovery.dispatch_hold("altitude", ordinary))

    def test_concurrent_same_kind_faults_coalesce_incident_fyi_and_hold(self):
        barrier = threading.Barrier(2)

        def record(number):
            barrier.wait()
            return incidents.system_fault("parallel-health", f"failure {number}", project="altitude")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(record, (1, 2)))

        self.assertEqual(sum(result is not None for result in results), 1)
        fault = S.read_json(incidents.FAULTS)["parallel-health"]
        self.assertEqual(fault["count"], 2)
        self.assertIsNotNone(fault["incident"])
        rows = [row for row in incidents.index() if row.get("title") == "system fault: parallel-health"]
        self.assertEqual(len(rows), 1)
        inbox = [json.loads(line) for line in
                 (config.project_dir("altitude") / "inbox.jsonl").read_text().splitlines()]
        notices = [row for row in inbox if "SYSTEM FAULT [parallel-health]" in row.get("text", "")]
        self.assertEqual(len(notices), 1)
        held = [row for row in recovery.status()["faults"] if row.get("kind") == "parallel-health"]
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["count"], 2)
        self.assertEqual(held[0]["incident"], fault["incident"])

    def test_hold_publishes_before_a_prequeued_launch_can_cross_the_boundary(self):
        """Adversarial FIFO order: A owns barrier, B queues, then hold queues; B must still refuse."""
        ordinary = {"slug": "ordinary-b", "source": "chat"}
        condition = threading.Condition()
        owner = None
        queue = []
        a_inside = threading.Event()
        release_a = threading.Event()
        b_queued = threading.Event()
        b_spawned = threading.Event()
        b_held = threading.Event()
        published = threading.Event()
        hold_done = threading.Event()
        errors = []

        @contextmanager
        def fifo_launch_lock():
            nonlocal owner
            name = threading.current_thread().name
            with condition:
                queue.append(name)
                if name == "launch-b":
                    b_queued.set()
                while owner is not None or queue[0] != name:
                    condition.wait()
                queue.pop(0)
                owner = name
            try:
                yield
            finally:
                with condition:
                    owner = None
                    condition.notify_all()

        real_write_json = S.write_json

        def observed_write(path, value):
            real_write_json(path, value)
            if path == recovery.hold_path() and value.get("active"):
                published.set()

        def launch_a():
            try:
                with recovery.launch_permission("altitude", ordinary):
                    a_inside.set()
                    release_a.wait(5)
            except BaseException as exc:  # pragma: no cover - reported by assertion below
                errors.append(exc)

        def launch_b():
            try:
                with recovery.launch_permission("altitude", ordinary):
                    b_spawned.set()
            except recovery.LaunchHeld:
                b_held.set()
            except BaseException as exc:  # pragma: no cover - reported by assertion below
                errors.append(exc)

        def trip_hold():
            try:
                recovery.hold("launch ownership changed", kind="launch-race")
                hold_done.set()
            except BaseException as exc:  # pragma: no cover - reported by assertion below
                errors.append(exc)

        with mock.patch.object(recovery, "_launch_lock", fifo_launch_lock), \
                mock.patch.object(S, "write_json", side_effect=observed_write):
            a = threading.Thread(target=launch_a, name="launch-a")
            b = threading.Thread(target=launch_b, name="launch-b")
            hold = threading.Thread(target=trip_hold, name="fault-hold")
            a.start()
            self.assertTrue(a_inside.wait(2))
            b.start()
            self.assertTrue(b_queued.wait(2), "B must already be queued behind A")
            hold.start()
            self.assertTrue(published.wait(2), "the hold must publish while A still owns the barrier")
            self.assertFalse(hold_done.is_set(), "hold still settles A after publishing")
            self.assertFalse(b_spawned.is_set())
            release_a.set()
            for thread in (a, b, hold):
                thread.join(2)

        self.assertEqual(errors, [])
        self.assertTrue(b_held.is_set())
        self.assertFalse(b_spawned.is_set())
        self.assertTrue(hold_done.is_set())

    def test_engine_launch_guard_covers_popen_but_not_cli_wait(self):
        guard_active = False

        @contextmanager
        def spawn_guard():
            nonlocal guard_active
            guard_active = True
            try:
                yield
            finally:
                guard_active = False

        class FakeProcess:
            returncode = 0

            def communicate(self, timeout):
                self.assert_guard_released()
                return "started", ""

            def assert_guard_released(self):
                if guard_active:
                    raise AssertionError("spawn guard remained held while waiting for the CLI")

        def popen(*args, **kwargs):
            self.assertTrue(guard_active, "Popen is the guarded irreversible boundary")
            return FakeProcess()

        with mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(engines, "claude_agents", return_value=[]):
            result = engines.claude_bg("altitude/test", "prompt", cwd=self.repo,
                                       settings=self.root / "settings.json", spawn_guard=spawn_guard())
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "started")

    def test_same_name_claude_retry_binds_only_the_new_worker(self):
        old = {"id": "old", "name": "altitude/retry-1", "state": "stopped", "status": "exited",
               "startedAt": "2026-01-01T00:00:00Z"}
        new = {"id": "new", "name": "altitude/retry-1", "state": "working", "status": "busy",
               "sessionId": "new-session", "startedAt": "2026-01-02T00:00:00Z"}
        completed = subprocess.CompletedProcess([], 0, stdout="started", stderr="")
        with mock.patch.object(engines, "claude_agents", side_effect=[[old], [old, new]]), \
             mock.patch.object(engines, "_guarded_spawn", return_value=completed):
            result = engines.claude_bg("altitude/retry-1", "prompt", cwd=self.repo,
                                       settings=self.root / "settings.json")
        self.assertEqual(result["agent"]["id"], "new")

    def test_recovery_task_requires_an_active_hold_and_explicit_actor(self):
        with self.assertRaisesRegex(T.TransitionError, "requires an active recovery hold"):
            T.new("altitude", "unheld repair", "request", actor="l3", source="recovery")
        recovery.hold("manual hold", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "explicitly delegated"):
            T.new("altitude", "agent invented repair", "request", actor="altd", source="recovery")

    def test_recovery_task_needs_no_size_metadata(self):
        recovery.hold("manual hold", actor="l3")
        repair = T.new("altitude", "direct repair", "request", actor="l3", source="recovery")
        self.assertEqual(repair["state"], "queued")
        self.assertNotIn("class", repair)
        self.assertNotIn("envelope", repair)
        self.assertEqual(recovery.status()["repair"]["slug"], repair["slug"])

    def test_dispatch_skips_ordinary_work_and_reaches_claimed_repair(self):
        recovery.hold("runtime ownership is uncertain", kind="ownership", actor="l3")
        ordinary = T.new("altitude", "ordinary first", "request", actor="l3", source="chat")
        ordinary["state"] = "queued"
        S.save_task("altitude", ordinary)
        repair = T.new("altitude", "repair second", "request", actor="l3", source="recovery")
        started = []
        with mock.patch.object(dispatch, "run", side_effect=lambda project, slug: started.append(slug) or {
            "dispatch_id": slug, "agent": None
        }), mock.patch.object(engines, "claude_agents", return_value=[]), \
                mock.patch.object(monitor, "quota", return_value={"known": True}):
            server.dispatch_waiting("altitude")
        self.assertEqual(started, [repair["slug"]])
        project_hold = S.read_json(config.project_dir("altitude") / "hold.json")
        self.assertTrue(project_hold["reason"].startswith("recovery hold"))

    def test_unknown_claude_quota_does_not_trip_global_recovery(self):
        ordinary = T.new("altitude", "ordinary quota work", "request", actor="l3", source="chat")
        with mock.patch.object(engines, "claude_agents", return_value=[]), \
                mock.patch.object(monitor, "quota", return_value={"known": False}):
            held = dispatch.wip_hold("altitude", ordinary)
        self.assertIsNone(held)
        self.assertIsNone(recovery.status())

    def test_failed_task_write_rolls_back_the_repair_claim(self):
        recovery.hold("manual hold", actor="l3")
        with mock.patch.object(S, "save_task", side_effect=OSError("state store unavailable")):
            with self.assertRaisesRegex(OSError, "state store unavailable"):
                T.new("altitude", "unwritten repair", "request", actor="l3", source="recovery")
        self.assertIsNone(recovery.status()["repair"])

    def test_fault_during_git_prep_prevents_fresh_worker_launch(self):
        ordinary = T.new("altitude", "racy ordinary launch", "request", actor="l3", source="chat")
        ordinary["state"] = "queued"
        S.save_task("altitude", ordinary)

        def fault_then_return(*args, **kwargs):
            # A real monitor fault arrives on another thread/process while the Git
            # provenance mutex is held. Model that concurrency instead of nesting
            # RECOVERY under GIT_PUBLICATION in this callback's thread.
            errors = []
            worker = threading.Thread(
                target=lambda: _record_fault(errors),
            )
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            if errors:
                raise errors[0]
            return "a" * 40

        def _record_fault(errors):
            try:
                incidents.system_fault("fetch-race", "ownership changed during fetch", project="altitude")
            except BaseException as exc:
                errors.append(exc)

        crossed_spawn_boundary = mock.Mock()

        def guarded_launch(*args, spawn_guard, **kwargs):
            with spawn_guard:
                crossed_spawn_boundary()
            return {"stdout": "", "stderr": "", "returncode": 0, "agent": None}

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
                mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", side_effect=fault_then_return), \
                mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
                mock.patch.object(engines, "start_l2", side_effect=guarded_launch) as launch:
            with self.assertRaisesRegex(T.TransitionError, "recovery hold: fetch-race"):
                dispatch.run("altitude", ordinary["slug"])

        launch.assert_called_once()
        crossed_spawn_boundary.assert_not_called()
        current = S.load_task("altitude", ordinary["slug"])
        self.assertEqual(current["state"], "queued")
        self.assertIsNone(current.get("dispatching"))
        self.assertEqual(recovery.status()["faults"][-1]["kind"], "fetch-race")

    def test_resume_launch_obeys_hold_but_claimed_repair_can_resume(self):
        ordinary = T.new("altitude", "ordinary resume", "request", actor="l3", source="chat")
        ordinary.update({"state": "running", "worktree": str(self.repo), "dispatch_id": "ordinary-resume-1",
                         "session_id": "ordinary-session", "agent_id": "ordinary-agent"})
        S.save_task("altitude", ordinary)
        recovery.hold("manual hold", kind="manual", actor="l3")

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(engines, "stop_l2_worker") as stop, \
                mock.patch.object(engines, "claude_resume_bg") as resume:
            with self.assertRaisesRegex(T.TransitionError, "recovery hold: manual"):
                dispatch.resume_session("altitude", ordinary["slug"], "steer")
        stop.assert_not_called()
        resume.assert_not_called()

        repair = T.new("altitude", "claimed repair resume", "request", actor="l3", source="recovery")
        repair.update({"state": "blocked", "worktree": str(self.repo), "dispatch_id": "claimed-repair-resume-1",
                       "session_id": "repair-session"})
        S.save_task("altitude", repair)
        resumed = {"stdout": "", "stderr": "", "returncode": 0}
        live = [{"name": "altitude/claimed-repair-resume-1", "id": "agent-2", "sessionId": "session-2",
                 "state": "working", "startedAt": 2}]
        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(engines, "claude_resume_bg", return_value=resumed) as resume, \
                mock.patch.object(engines, "claude_agents", return_value=live):
            result = dispatch.resume_session("altitude", repair["slug"], "continue repair")
        resume.assert_called_once()
        self.assertEqual(result["agent"]["id"], "agent-2")

    def test_hold_appearing_after_stop_creates_exact_pending_resume(self):
        task = T.new("altitude", "post stop hold", "request", actor="l3", source="chat")
        task.update({
            "state": "running", "worktree": str(self.repo), "dispatch_id": "post-stop-hold-1",
            "session_id": "post-stop-session", "agent_id": "post-stop-agent", "l2_token": "stable-token",
        })
        S.save_task("altitude", task)
        held = "recovery hold: injected-race; explicit L3 clearance required"

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(recovery, "dispatch_hold", side_effect=[None, held]), \
                mock.patch.object(engines, "stop_l2_worker", return_value="stopped") as stop, \
                mock.patch.object(engines, "resume_l2") as launch:
            with self.assertRaisesRegex(T.TransitionError, "injected-race"):
                dispatch.resume_session("altitude", task["slug"], "exact steering prompt")

        stop.assert_called_once()
        launch.assert_not_called()
        pending = S.load_task("altitude", task["slug"])
        self.assertEqual(pending["state"], "blocked")
        self.assertEqual(pending["resume_answer"], "exact steering prompt")
        self.assertTrue(pending["resume_exact_prompt"])
        self.assertTrue(pending["resume_after"])
        self.assertIn("injected-race", pending["blocked_reason"])

        seen = {}

        def resumed(engine, name, session_id, prompt, **kwargs):
            seen["prompt"] = prompt
            return {"returncode": 0, "stdout": "", "stderr": "", "agent": {
                "id": "replacement", "sessionId": "replacement-session", "state": "working", "startedAt": 2,
            }}

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(recovery, "dispatch_hold", return_value=None), \
                mock.patch.object(engines, "stop_l2_worker", return_value="already stopped"), \
                mock.patch.object(engines, "resume_l2", side_effect=resumed), \
                mock.patch.object(engines, "claude_agents", return_value=[{
                    "id": "replacement", "sessionId": "replacement-session",
                    "name": "altitude/post-stop-hold-1", "state": "working", "startedAt": 2,
                }]):
            retried = dispatch.resume_due("altitude")

        self.assertEqual(retried, [task["slug"]])
        self.assertEqual(seen["prompt"], "exact steering prompt")
        resumed_task = S.load_task("altitude", task["slug"])
        self.assertEqual(resumed_task["state"], "running")
        self.assertNotIn("resume_exact_prompt", resumed_task)


if __name__ == "__main__":
    unittest.main()
