"""Selected queued input uses real Stop/resume ownership and deterministic engine workers."""
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, incidents, project_setup, state as S, tasks as T


class TestSendNowL2(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.patch(config, "WIP_PER_MACHINE", 30)
        self.worker = FakeL2()
        self.worker.install(self)

    def launch(self, engine):
        self.register(self.project, routing=[[{"engine": engine, "model": "fixture-model"}]], wip=30)
        task = T.new(self.project, f"Send now {engine}", "Preserve the current work.")
        dispatch.run(self.project, task["slug"])
        return S.load_task(self.project, task["slug"])

    def send(self, task, text):
        return T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, text)

    def delivery(self, task, message):
        return next(row["delivery"] for row in T.message_views(self.project, task["slug"], [])
                    if row["id"] == message["id"])

    def request(self, task, message):
        return dispatch.request_send_now(self.project, task["slug"], message["id"])

    def execute(self, task):
        return dispatch.run_task_operation(self.project, task["slug"])

    def test_selected_only_preserves_session_siblings_and_work_for_both_engines(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                task.update(hold_merge="Operator review required", machine_access={"fixture": "retained"})
                S.save_task(self.project, task)
                work = Path(task["worktree"]) / "README.md"
                work.write_text("Uncommitted work stays here.\n")
                first, selected, last = [self.send(task, text) for text in ("earlier", "selected", "later")]
                self.assertTrue(self.delivery(task, selected)["send_now"])
                request = self.request(task, selected)
                self.assertEqual(self.request(task, selected)["request"]["id"], request["request"]["id"])
                self.assertEqual(T.take_inbox(self.project, task["slug"]), [])
                self.assertTrue(self.delivery(task, selected)["send_now_pending"])
                with self.assertRaises(T.TransitionError):
                    T.remove_message(self.project, task["slug"], selected["id"])
                late = []
                self.worker.on_resume = lambda: late.append(self.send(task, "during launch"))
                self.execute(task)
                self.worker.on_resume = None
                current = S.load_task(self.project, task["slug"])
                self.assertEqual(current["state"], "running")
                for key in ("session_id", "attempt", "launch_model", "branch", "worktree", "hold_merge", "machine_access"):
                    self.assertEqual(current[key], task[key])
                self.assertNotEqual(current["agent_id"], task["agent_id"])
                self.assertEqual(work.read_text(), "Uncommitted work stays here.\n")
                self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([selected]))
                self.assertEqual(T.pending(self.project, task["slug"]), [first, last, late[0]])
                self.assertEqual(self.delivery(task, selected)["state"], "delivered")
                calls = len(self.worker.calls)
                self.assertTrue(self.request(task, selected)["idempotent"])
                self.execute(task)
                self.assertEqual(len(self.worker.calls), calls)
                self.assertEqual(T.take_inbox(self.project, task["slug"]), [first, last, late[0]])
                self.assertEqual(T.take_inbox(self.project, task["slug"]), [])
                self.assertEqual(len(T.task_messages(self.project, task["slug"])), 4)

    def test_hook_or_removal_winning_admission_never_interrupts(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                picked = self.send(task, "Already picked up")
                self.assertEqual(T.take_inbox(self.project, task["slug"]), [picked])
                self.assertEqual(self.request(task, picked), {"queued": False, "idempotent": True})
                removed = self.send(task, "Remove me")
                T.remove_message(self.project, task["slug"], removed["id"])
                with self.assertRaisesRegex(T.TransitionError, "removed"):
                    self.request(task, removed)
                self.assertNotIn("stop_id", S.load_task(self.project, task["slug"]))
                self.assertEqual(self.worker.workers[task["agent_id"]]["state"], "working")

    def test_question_fault_stop_and_missing_session_are_denied_without_mutation(self):
        for engine in config.ENGINES:
            for change, expected in (({"state": "blocked", "waiting_on": "operator"}, "question"),
                                     ({"state": "blocked", "fault": "fixture fault"}, "faulted"),
                                     ({"stop_id": "explicit-stop"}, "stopped"),
                                     ({"session_id": None}, "saved session")):
                with self.subTest(engine=engine, change=change):
                    task = self.launch(engine)
                    message = self.send(task, "Queued before the wait")
                    task = S.load_task(self.project, task["slug"])
                    task.update(change)
                    S.save_task(self.project, task)
                    with self.assertRaisesRegex(T.TransitionError, expected):
                        self.request(task, message)
                    self.assertEqual(S.load_task(self.project, task["slug"]), task)
                    self.assertFalse(self.delivery(task, message)["send_now"])
                    self.assertIn(expected, self.delivery(task, message)["send_now_reason"])

    def test_engine_setup_launch_and_admission_holds_do_not_stop_owner(self):
        @contextmanager
        def held():
            yield "Fixture admission paused"

        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Wait for available launch")
        checks = [mock.patch.object(dispatch, "resume_engine_hold", return_value="Engine unavailable"),
                  mock.patch.object(config, "provider_admission", held),
                  project_setup.operation_lock(self.project), dispatch.launch_lock()]
        for check in checks:
            with self.subTest(check=type(check).__name__), check:
                with self.assertRaises(T.TransitionError):
                    self.request(task, message)
                self.assertNotIn("stop_id", S.load_task(self.project, task["slug"]))
        self.assertEqual(self.worker.workers[task["agent_id"]]["state"], "working")

    def test_delivery_projection_never_acquires_transient_admission_or_launch_locks(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Keep the row stable during unrelated launches")
        with mock.patch.object(config, "provider_admission", side_effect=AssertionError("view acquired admission")), \
                mock.patch.object(project_setup, "operation_lock", side_effect=AssertionError("view acquired setup")), \
                mock.patch.object(dispatch, "launch_lock", side_effect=AssertionError("view acquired launch")):
            self.assertTrue(self.delivery(task, message)["send_now"])
        with project_setup.operation_lock(self.project), dispatch.launch_lock():
            self.assertTrue(self.delivery(task, message)["send_now"])
            with self.assertRaises(T.TransitionError):
                self.request(task, message)

    def test_post_stop_preclaim_error_preserves_completed_stop_and_selected_priority(self):
        for engine in config.ENGINES:
            for error in (RuntimeError("Resume preflight unavailable"), T.TransitionError("Resume preflight changed")):
                with self.subTest(engine=engine, error=type(error).__name__):
                    task = self.launch(engine)
                    sibling, selected = [self.send(task, text) for text in ("earlier sibling", "selected")]
                    self.request(task, selected)
                    # Stop admission passes; the ordinary resume preflight then fails before claiming input.
                    with mock.patch.object(dispatch, "resume_engine_hold", side_effect=[None, error]):
                        with self.assertRaisesRegex(type(error), "Resume preflight"):
                            self.execute(task)
                    current = S.load_task(self.project, task["slug"])
                    self.assertEqual(current["daemon_request"]["status"], "done")
                    self.assertEqual(current["send_now"], selected["id"])
                    self.assertEqual(current["resume_request"], selected["id"])
                    self.assertTrue(current["resume_after"])
                    self.assertEqual(T.pending(self.project, task["slug"]), [sibling, selected])
                    self.assertTrue(self.delivery(task, selected)["send_now_pending"])
                    with mock.patch.object(engines, "stop_l2_worker", wraps=self.worker.stop_l2_worker) as stop:
                        dispatch.resume(self.project, task["slug"])
                        stop.assert_not_called()
                    self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([selected]))
                    self.assertEqual(T.pending(self.project, task["slug"]), [sibling])

    def test_engine_hold_arriving_before_execution_releases_interruption_request(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Deliver when possible")
        self.request(task, message)
        with mock.patch.object(dispatch, "resume_engine_hold", return_value="Engine unavailable"):
            self.assertEqual(self.execute(task)["request"]["status"], "refused")
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current["state"], "running")
        self.assertNotIn("stop_id", current)
        self.assertTrue(self.delivery(task, message)["removable"])
        self.assertEqual(self.worker.workers[task["agent_id"]]["state"], "working")
        retried = self.request(task, message)
        self.assertTrue(retried["queued"])
        self.assertNotEqual(retried["request"]["id"], current["daemon_request"]["id"])
        self.execute(task)
        self.assertEqual(self.delivery(task, message)["state"], "delivered")

    def test_stop_restart_recovers_selected_message_once(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                sibling, selected = [self.send(task, text) for text in ("sibling", "selected")]
                request = self.request(task, selected)["request"]
                current = S.load_task(self.project, task["slug"])
                current["daemon_request"]["status"] = "executing"
                S.save_task(self.project, current)
                dispatch.stop(self.project, task["slug"], daemon_request_id=request["id"],
                              expected_agent_id=task["agent_id"], expected_session_id=task["session_id"])
                # A replacement daemon sees the committed Stop before the continuation was scheduled.
                self.execute(task)
                calls = len(self.worker.calls)
                self.execute(task)
                self.assertEqual(len(self.worker.calls), calls)
                self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([selected]))
                self.assertEqual(T.pending(self.project, task["slug"]), [sibling])

    def test_restart_after_stop_receipt_keeps_durable_resume_without_replaying_stop(self):
        task = self.launch(config.ENGINES[0])
        sibling, selected = [self.send(task, text) for text in ("sibling", "selected")]
        self.request(task, selected)
        with mock.patch.object(dispatch, "_resume", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.execute(task)
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current["daemon_request"]["status"], "done")
        self.assertIn(task["slug"], dispatch.resume_due(self.project))
        with mock.patch.object(engines, "stop_l2_worker", wraps=self.worker.stop_l2_worker) as stop:
            self.execute(task)
            dispatch.resume(self.project, task["slug"])
            stop.assert_not_called()
        self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([selected]))
        self.assertEqual(T.pending(self.project, task["slug"]), [sibling])

    def test_capacity_lock_spans_stop_and_resume(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Keep this owner's slot")
        self.request(task, message)
        observed = []
        original_stop = self.worker.stop_l2_worker

        def check_capacity():
            with dispatch.launch_lock(wait=False) as ready:
                observed.append(ready)

        def stop(*args, **kwargs):
            check_capacity()
            return original_stop(*args, **kwargs)

        self.worker.on_resume = check_capacity
        with mock.patch.object(engines, "stop_l2_worker", side_effect=stop):
            self.execute(task)
        self.assertEqual(observed, [False, False])
        with dispatch.launch_lock(wait=False) as ready:
            self.assertTrue(ready)

    def test_held_continuation_releases_operation_slot_and_stop_cancels_wake(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Retain for resume")
        self.request(task, message)
        original_stop = self.worker.stop_l2_worker

        def stop_then_hold(*args, **kwargs):
            result = original_stop(*args, **kwargs)
            self.patch(dispatch, "resume_engine_hold", return_value="Engine temporarily unavailable")
            return result

        with mock.patch.object(engines, "stop_l2_worker", side_effect=stop_then_hold):
            self.assertIn("held", self.execute(task))
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current["daemon_request"]["status"], "done")
        self.assertTrue(current["resume_after"])
        delivery = self.delivery(task, message)
        self.assertTrue(delivery["send_now_pending"])
        self.assertIn("unavailable", delivery["send_now_reason"])
        dispatch.request_task_operation(self.project, task["slug"], "stop", "Keep this owner stopped",
                                        actor=T.OPERATOR_MESSAGE_ROLE, generation=current["agent_id"])
        self.execute(task)
        current = S.load_task(self.project, task["slug"])
        self.assertNotIn("resume_after", current)
        self.assertNotIn("send_now", current)
        self.assertEqual(T.pending(self.project, task["slug"]), [message])

    def test_new_question_supersedes_held_continuation(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Earlier steering")
        self.request(task, message)
        original_resume = dispatch._resume
        with mock.patch.object(dispatch, "_resume", return_value={"held": "fixture boundary"}):
            self.execute(task)
        current = T.escalate(self.project, task["slug"], "Which design should we use?")
        self.assertNotIn("resume_after", current)
        self.assertNotIn("send_now", current)
        self.assertNotIn("stop_id", current)
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertEqual(original_resume(self.project, task["slug"]), {"waiting": True})
        self.assertFalse(self.delivery(task, message)["send_now_pending"])

    def test_reject_remains_available_during_held_continuation(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Keep for continuation")
        self.request(task, message)
        with mock.patch.object(dispatch, "_resume", return_value={"held": "fixture admission pause"}):
            self.execute(task)
        dispatch.request_task_operation(self.project, task["slug"], "reject", "End this task",
                                        actor=T.OPERATOR_MESSAGE_ROLE)
        self.execute(task)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_fault_during_native_stop_supersedes_wake_and_coordinator_discussion_stays_quiet(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                message = self.send(task, "Earlier steering")
                self.request(task, message)
                original_stop = self.worker.stop_l2_worker

                def stop_then_fault(*args, **kwargs):
                    result = original_stop(*args, **kwargs)
                    incidents.system_fault("fixture-send-now", "A newer failure needs recovery",
                                           project=self.project, task=task["slug"])
                    return result

                calls = len(self.worker.calls)
                with mock.patch.object(engines, "stop_l2_worker", side_effect=stop_then_fault):
                    self.assertEqual(self.execute(task)["request"]["status"], "refused")
                current = S.load_task(self.project, task["slug"])
                self.assertEqual(current["fault"], "fixture-send-now")
                self.assertNotIn("resume_after", current)
                self.assertNotIn("send_now", current)
                self.assertNotIn("stop_id", current)
                coordinator = T.message(self.project, task["slug"], "l3", "Recovery is still pending")
                self.assertFalse(coordinator["wake"])
                self.assertEqual(dispatch.resume_due(self.project), [])
                self.assertEqual(len(self.worker.calls), calls)
                with self.assertRaisesRegex(T.TransitionError, "faulted"):
                    self.request(task, message)

    def test_question_during_native_stop_supersedes_delivery_and_retains_answer_path(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                message = self.send(task, "Steering before new question")
                self.request(task, message)
                original_stop = self.worker.stop_l2_worker

                def stop_then_question(*args, **kwargs):
                    result = original_stop(*args, **kwargs)
                    T.escalate(self.project, task["slug"], "Which direction should this task take?")
                    return result

                calls = len(self.worker.calls)
                with mock.patch.object(engines, "stop_l2_worker", side_effect=stop_then_question):
                    self.assertEqual(self.execute(task)["request"]["status"], "refused")
                current = S.load_task(self.project, task["slug"])
                self.assertNotIn("stop_id", current)
                self.assertNotIn("send_now", current)
                self.assertNotIn("resume_after", current)
                self.assertEqual(len(self.worker.calls), calls)
                self.assertEqual(current["waiting_on"], T.OPERATOR_MESSAGE_ROLE)
                self.send(task, "Answer to the new question")
                self.assertIn(task["slug"], dispatch.resume_due(self.project))
                dispatch.resume(self.project, task["slug"])

    def test_unconfirmed_stop_never_launches_replacement(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "Do not duplicate")
        self.request(task, message)
        calls = len(self.worker.calls)
        with mock.patch.object(engines, "stop_l2_worker", side_effect=RuntimeError("Termination unknown")):
            with self.assertRaisesRegex(RuntimeError, "Termination unknown"):
                self.execute(task)
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(len(self.worker.calls), calls)
        self.assertEqual(current["daemon_request"]["status"], "failed")
        self.assertNotIn("resume_after", current)
        self.assertEqual(T.pending(self.project, task["slug"]), [message])
