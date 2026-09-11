"""An open review PR keeps its same owner reachable without reusing stale completion evidence."""
import copy
import os
from contextlib import contextmanager
from datetime import datetime

from tests.support import fyi_rows
from tests.test_task_chat import ChatCase
from altitude import dispatch, engines, incidents, l3, server, state as S, tasks as T, verify


class TestReportedContinuation(ChatCase):
    def setUp(self):
        super().setUp()
        self.quiet_launch()
        self.private_ledgers()
        self.patch(server, "log")
        self.gh = self.patch(verify, "gh", return_value={"state": "OPEN"})
        self.report = {"landed": {"prs": [{"number": 47, "merged": False}],
                                  "main_runs": [], "deploy": "not-applicable"},
                       "review": [], "blocked": "", "follow_ups": ["Operator review remains held."]}
        self.report_path = S.task_dir(self.project, self.slug) / "report.json"
        S.write_json(self.report_path, self.report)
        task = self.task()
        task.update(state="reported", attempt=3, l2_engine="codex", prs=[47],
                    paths=["tests/", "README.md"], hold_merge="Operator UX review",
                    hold_merge_id="original-hold", worker_started_at="2020-01-01T00:00:00+00:00")
        task["verified"] = {"verdict": "ok", "problems": [], "signals": [], "spend": {},
                            "prs": [47], "owner": T.report_owner(task)}
        S.save_task(self.project, task)
        self.original = copy.deepcopy(task)

    def task(self):
        return S.load_task(self.project, self.slug)

    def send(self, text="Resolve the conflicts; keep the review hold.", role="burak"):
        return T.message(self.project, self.slug, role, text)

    def launch(self, callback=None):
        def resume(engine, name, session_id, prompt, **kwargs):
            if callback:
                callback(prompt)
            return {"returncode": 0, "agent": {"id": "next-worker", "sessionId": session_id}}
        return self.patch(engines, "resume_l2", side_effect=resume)

    def assert_owner_preserved(self):
        for key in ("attempt", "session_id", "l2_engine", "worktree", "branch", "prs", "paths",
                    "hold_merge", "hold_merge_id"):
            self.assertEqual(self.task().get(key), self.original.get(key), key)

    def assert_no_fault_effects(self):
        self.assertNotIn("fault", self.task())
        self.assertEqual(S.read_json(incidents.FAULTS, {}), {})
        self.assertEqual(incidents.index(self.project), [])
        self.assertEqual(fyi_rows(self.project), [])
        self.assertFalse(l3.queue_path(self.project).exists())

    def test_message_preserves_owner_hold_and_historical_report(self):
        row = self.send()
        self.assertEqual(self.task()["state"], "blocked")
        self.assertEqual(self.task()["resume_request"], row["id"])
        self.assertNotIn("verified", self.task())
        self.assert_owner_preserved()
        historical = [e for e in S.read_events(self.project, self.slug) if e["kind"] == "report-superseded"]
        self.assertEqual(len(historical), 1)
        self.assertEqual(historical[0]["report"], self.report)
        self.assertEqual(historical[0]["verified"], self.original["verified"])
        worker = self.launch()
        dispatch.resume(self.project, self.slug)
        self.assertEqual(self.task()["state"], "running")
        self.assert_owner_preserved()
        self.assertEqual(worker.call_args.args[:3],
                         ("codex", f"{self.project}/{self.slug}-3", "session-old"))
        self.assertEqual(worker.call_args.args[3].count(row["text"]), 1)
        self.assertEqual(T.pending(self.project, self.slug), [])

    def test_coordinator_resume_uses_same_daemon_path_and_is_idempotent(self):
        request = dispatch.request_task_operation(self.project, self.slug, "resume", "Resolve conflicts", actor="l3")
        repeated = dispatch.request_task_operation(self.project, self.slug, "resume", "Resolve conflicts", actor="l3")
        self.assertEqual(request["request"]["id"], repeated["request"]["id"])
        worker = self.launch()
        dispatch.run_task_operation(self.project, self.slug)
        dispatch.run_task_operation(self.project, self.slug)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(self.task()["state"], "running")
        self.assert_owner_preserved()

    def test_closed_or_unreadable_pr_refuses_before_saving_message(self):
        for response in ({"state": "CLOSED"}, {"state": "MERGED"}, None):
            with self.subTest(response=response):
                self.gh.return_value = response
                with self.assertRaises(T.TransitionError):
                    self.send()
                self.assertEqual(self.task(), self.original)
                self.assertEqual(T.task_messages(self.project, self.slug), [])
                self.assertEqual(T.pending(self.project, self.slug), [])
        self.gh.side_effect = verify.VerifierFault("fixture unavailable")
        with self.assertRaises(T.TransitionError):
            self.send()
        self.assertEqual(self.task(), self.original)

    def test_missing_identity_and_terminal_tasks_are_not_resurrected(self):
        for change in ({"agent_id": None}, {"session_id": None}, {"worktree": None},
                       {"prs": [99]}, {"state": "done"}, {"state": "rejected"}):
            with self.subTest(change=change):
                task = {**self.original, **change}
                S.save_task(self.project, task)
                with self.assertRaises(T.TransitionError):
                    self.send()
                self.assertEqual(self.task()["state"], task["state"])
                self.assertEqual(T.pending(self.project, self.slug), [])
                self.assertEqual(T.task_messages(self.project, self.slug), [])

    def test_distinct_sends_and_message_during_launch_each_deliver_once(self):
        first = self.send("Keep the hold.")
        second = self.send("Keep the hold.")
        late = []
        def during_launch(prompt):
            self.assertEqual(prompt.count("Keep the hold."), 2)
            late.append(self.send("Also explain the diagnosis."))
        worker = self.launch(during_launch)
        dispatch.resume(self.project, self.slug)
        self.assertEqual(worker.call_count, 1)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)], [late[0]["id"]])
        self.assertEqual([row["id"] for row in T.task_messages(self.project, self.slug)],
                         [first["id"], second["id"], late[0]["id"]])

    def test_failed_launch_restores_batch_and_newer_message(self):
        first = self.send()
        late = []
        def refuse(*args, **kwargs):
            late.append(self.send("Preserve this additional instruction."))
            return {"returncode": 1, "stderr": "fixture refused"}
        self.patch(engines, "resume_l2", side_effect=refuse)
        self.patch(incidents, "system_fault")
        with self.assertRaises(dispatch.ResumeFailure):
            dispatch.resume(self.project, self.slug)
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)], [first["id"], late[0]["id"]])
        self.assertEqual(self.task()["resume_request"], late[0]["id"])
        self.assert_owner_preserved()

    def test_uncertain_launch_preserves_message_without_relaunching(self):
        row = self.send()
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launching")
        self.patch(dispatch, "_claim_owner_live", return_value=False)
        fault = self.patch(incidents, "system_fault")
        worker = self.launch()
        with self.assertRaisesRegex(dispatch.ResumeFailure, "ownership cannot be proven"):
            dispatch.resume(self.project, self.slug)
        worker.assert_not_called()
        fault.assert_called_once()
        self.assertEqual([r["id"] for r in T.pending(self.project, self.slug)], [row["id"]])
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assert_owner_preserved()

    def test_capacity_wait_cannot_archive_old_report_and_eventually_resumes(self):
        self.send()
        hold = self.patch(dispatch, "wip_hold", return_value="WIP limit reached")
        worker = self.launch()
        self.assertTrue(dispatch.resume(self.project, self.slug).get("held"))
        worker.assert_not_called()
        with self.assertRaises(T.TransitionError):
            T.done(self.project, self.slug, digest="old report says done")
        hold.return_value = None
        self.assertIn(self.slug, dispatch.resume_due(self.project))
        dispatch.resume(self.project, self.slug)
        self.assertEqual(worker.call_count, 1)
        self.assert_owner_preserved()

    def test_old_report_cannot_complete_or_promote_waiting_continuation(self):
        self.send()
        with self.assertRaises(T.TransitionError):
            T.report(self.project, self.slug, self.original["verified"])
        with self.assertRaises(T.TransitionError):
            T.done(self.project, self.slug, digest="stale close")
        spawn = self.patch(server, "spawn")
        server.resume_stranded_reports(self.project)
        spawn.assert_not_called()
        self.assertEqual(self.task()["state"], "blocked")
        self.assertIsNone(self.task().get("l3_handled"))

    def test_old_l3_report_callback_cannot_stamp_resumed_work(self):
        def discuss(*args, **kwargs):
            self.send()
            self.launch()
            dispatch.resume(self.project, self.slug)
            return {"completed": True}
        self.patch(server, "server_l3_turn", side_effect=discuss)
        server.report_turn(self.project, self.original, self.original["verified"])
        self.assertEqual(self.task()["state"], "running")
        self.assertIsNone(self.task().get("l3_handled"))

    def test_stale_report_callback_does_not_start_l3_turn(self):
        self.send()
        turn = self.patch(server, "server_l3_turn")
        server.report_turn(self.project, self.original, self.original["verified"])
        turn.assert_not_called()

    def test_verifier_fault_after_continuation_has_no_fault_side_effect(self):
        def check(*args):
            self.send()
            raise verify.VerifierFault("old verification failed")
        self.patch(verify, "_verify", side_effect=check)
        self.assertEqual(verify.verify(self.project, self.slug)["verdict"], "fault")
        self.assert_no_fault_effects()
        self.assertEqual(self.task()["state"], "blocked")

    def test_verifier_success_after_continuation_cannot_replace_current_work(self):
        def check(*args):
            self.send()
            return {"verdict": "ok", "prs": [47]}
        self.patch(verify, "_verify", side_effect=check)
        result = verify.verify(self.project, self.slug)
        with self.assertRaises(T.TransitionError):
            T.report(self.project, self.slug, result)
        self.assertNotIn("verified", self.task())

    def running(self):
        task = self.task()
        task["state"] = "running"
        task.pop("verified", None)
        S.save_task(self.project, task)
        return task

    def test_message_after_worker_exit_still_reaches_owner(self):
        snapshot = self.running()
        row = self.send()
        verifier = self.patch(verify, "verify")
        server.on_l2_finished(self.project, {"task": snapshot, "agent": {"state": "done"}})
        verifier.assert_not_called()
        self.assertIn(self.slug, dispatch.resume_due(self.project))
        worker = self.launch()
        dispatch.resume(self.project, self.slug)
        self.assertIn(row["text"], worker.call_args.args[3])
        self.assertEqual(self.task()["state"], "running")

    def test_message_arriving_during_verification_keeps_continuation_due(self):
        snapshot = self.running()
        real_verify = verify.verify
        def check(*args):
            result = real_verify(*args)
            self.send()
            return result
        self.patch(verify, "verify", side_effect=check)
        turn = self.patch(server, "report_turn")
        server.on_l2_finished(self.project, {"task": snapshot, "agent": {"state": "done"}})
        turn.assert_not_called()
        self.assertEqual(self.task()["state"], "blocked")
        self.assertIn(self.slug, dispatch.resume_due(self.project))
        self.assertNotIn("verified", self.task())

    def test_old_poll_snapshot_cannot_finish_new_turn_with_same_worker_identity(self):
        snapshot = self.running()
        T.report(self.project, self.slug, verify.verify(self.project, self.slug))
        self.send()
        self.patch(engines, "resume_l2", return_value={"returncode": 0, "agent": {
            "id": "agent-old", "sessionId": "session-old"}})
        dispatch.resume(self.project, self.slug)
        verifier = self.patch(verify, "verify")
        server.on_l2_finished(self.project, {"task": snapshot, "agent": {"state": "done"}})
        verifier.assert_not_called()
        self.assertEqual(self.task()["state"], "running")

    def test_message_arriving_during_failed_verification_keeps_continuation_due(self):
        snapshot = self.running()
        def check(*args):
            self.send()
            raise verify.VerifierFault("superseded report check failed")
        self.patch(verify, "_verify", side_effect=check)
        server.on_l2_finished(self.project, {"task": snapshot, "agent": {"state": "done"}})
        self.assert_no_fault_effects()
        self.assertEqual(self.task()["state"], "blocked")
        self.assertIn(self.slug, dispatch.resume_due(self.project))
        self.assertNotIn("fault", self.task())

    def pending_operation_survives_message(self, operation, final):
        self.running()
        request = dispatch.request_task_operation(self.project, self.slug, operation,
                                                  "Operator requested this action", actor="burak")
        self.send("One more detail for later.")
        self.patch(engines, "stop_l2_worker", return_value="stopped")
        self.patch(engines, "remove_l2_worker", return_value="removed")
        result = dispatch.run_task_operation(self.project, self.slug)
        self.assertEqual(result["request"]["id"], request["request"]["id"])
        self.assertEqual(result["request"]["status"], "done")
        self.assertEqual(self.task()["state"], final)

    def test_running_message_preserves_pending_stop(self):
        self.pending_operation_survives_message("stop", "blocked")

    def test_running_message_preserves_pending_reject(self):
        self.pending_operation_survives_message("reject", "rejected")

    def test_old_verifier_cannot_block_new_same_agent_turn_with_pending_message(self):
        old = self.running()
        resumed = []
        def check(*args):
            T.report(self.project, self.slug, self.original["verified"])
            self.send("Continue from the first result.")
            self.patch(engines, "resume_l2", return_value={"returncode": 0, "agent": {
                "id": "agent-old", "sessionId": "session-old"}})
            dispatch.resume(self.project, self.slug)
            self.send("Additional instruction for the active new turn.")
            resumed.append(self.task())
            return {"verdict": "ok", "problems": [], "signals": [], "spend": {}, "prs": [47]}
        self.patch(verify, "_verify", side_effect=check)
        server.on_l2_finished(self.project, {"task": old, "agent": {"state": "done"}})
        self.assertEqual(self.task(), resumed[0])
        self.assertEqual(self.task()["state"], "running")
        self.assertEqual(len(T.pending(self.project, self.slug)), 1)

    def test_running_message_at_fault_mutation_boundary_supersedes_incident(self):
        owner = T.report_owner(self.running())
        original_block = T.block
        after_message = []
        def message_then_block(*args, **kwargs):
            self.send("This follow-up wins before the fault mutation.")
            after_message.append(self.task())
            return original_block(*args, **kwargs)
        self.patch(T, "block", side_effect=message_then_block)
        result = incidents.system_fault("verifier", "obsolete check failed", project=self.project,
                                        task=self.slug, expected_owner=owner)
        self.assertIsNone(result)
        self.assertEqual(self.task(), after_message[0])
        self.assert_no_fault_effects()

    def test_blocked_message_at_fault_mutation_boundary_supersedes_incident(self):
        T.block(self.project, self.slug, "Paused for review", actor="altd")
        owner = T.report_owner(self.task())
        original_lock = S.project_lock
        after_message = []
        @contextmanager
        def message_then_lock(project):
            if not after_message:
                after_message.append(None)  # Do not intercept the message's own project lock.
                self.send("Continue the paused owner.")
                after_message[0] = self.task()
            with original_lock(project):
                yield
        self.patch(S, "project_lock", new=message_then_lock)
        result = incidents.system_fault("verifier", "obsolete blocked check failed", project=self.project,
                                        task=self.slug, expected_owner=owner)
        self.assertIsNone(result)
        self.assertEqual(self.task(), after_message[0])
        self.assertIn(self.slug, dispatch.resume_due(self.project))
        self.assert_no_fault_effects()

    def test_resume_claim_supersedes_old_fault_before_worker_start_is_updated(self):
        self.send()
        owner = T.report_owner(self.task())
        claim = T.claim_resume(self.project, self.slug)
        claimed = self.task()
        self.assertEqual(T.pending(self.project, self.slug), [])
        result = incidents.system_fault("verifier", "pre-claim check failed", project=self.project,
                                        task=self.slug, expected_owner=owner)
        self.assertIsNone(result)
        self.assertEqual(self.task(), claimed)
        self.assertEqual(self.task()["resume_claim"]["id"], claim["id"])
        self.assert_no_fault_effects()

    def test_explicit_resume_request_owns_blocked_task_before_old_fault_returns(self):
        T.block(self.project, self.slug, "Paused for review", actor="altd")
        owner = T.report_owner(self.task())
        dispatch.request_task_operation(self.project, self.slug, "resume", "Continue this owner", actor="l3")
        requested = self.task()
        result = incidents.system_fault("verifier", "pre-request check failed", project=self.project,
                                        task=self.slug, expected_owner=owner)
        self.assertIsNone(result)
        self.assertEqual(self.task(), requested)
        self.assert_no_fault_effects()

    def test_old_report_turn_does_not_fault_new_report_after_continuation(self):
        original_lock = S.project_lock
        interleaved = []
        @contextmanager
        def continue_before_report_read(project):
            if not interleaved:
                interleaved.append(None)
                self.send()
                self.launch()
                dispatch.resume(self.project, self.slug)
                self.report_path.write_text("{unfinished current report")
                interleaved[0] = self.task()
            with original_lock(project):
                yield
        self.patch(S, "project_lock", new=continue_before_report_read)
        turn = self.patch(server, "server_l3_turn")
        server.report_turn(self.project, self.original, self.original["verified"])
        self.assertEqual(self.task(), interleaved[0])
        self.assertEqual(self.task()["state"], "running")
        turn.assert_not_called()
        self.assert_no_fault_effects()

    def test_unrewritten_report_is_stale_when_resumed_worker_exits(self):
        self.send()
        self.launch()
        dispatch.resume(self.project, self.slug)
        # The file remains accessible as evidence, but cannot describe this turn's outcome.
        os.utime(self.report_path, (1, 1))
        self.patch(engines, "worker", return_value={"id": "next-worker", "state": "done"})
        result = dispatch.poll(self.project)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].get("died"))
        self.assertIn("report predates follow-up work; refresh report.json",
                      verify.verify(self.project, self.slug)["problems"])

    def test_new_report_can_conclude_current_continuation(self):
        self.send()
        self.launch()
        dispatch.resume(self.project, self.slug)
        S.write_json(self.report_path, self.report)
        current = self.task()
        stamp = datetime.fromisoformat(current["worker_started_at"]).timestamp() + 1
        os.utime(self.report_path, (stamp, stamp))
        result = verify.verify(self.project, self.slug)
        reported = T.report(self.project, self.slug, result)
        self.assertEqual(reported["state"], "reported")
        self.assertEqual(reported["verified"]["owner"], T.report_owner(reported))
        self.assert_owner_preserved()
