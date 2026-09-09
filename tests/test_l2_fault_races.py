"""#302: late worker-fault evidence cannot consume newer steering or another worker's lifecycle."""
import json
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, incidents, server, state as S, tasks as T


class TestL2FaultRaces(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.engine = FakeL2()
        self.engine.install(self)
        self.patch(server, "spawn", return_value=True)
        self.patch(server, "log")

    def launch(self, engine):
        self.register(self.project, routing=[[{"engine": engine, "model": "fixture-model"}]], wip=20)
        task = T.new(self.project, "Late worker evidence", "Keep steering and fault evidence distinct.",
                     paths=["README.md"])
        dispatch.run(self.project, task["slug"])
        return S.load_task(self.project, task["slug"])

    def finish(self, task, *, failed=False):
        self.engine.workers[task["agent_id"]].update(state="failed" if failed else "done", status="exited",
                                                    detail="fixture engine failure" if failed else "")
        return next(row for row in dispatch.poll(self.project) if row["task"]["slug"] == task["slug"])

    def interleave_fault(self, task, action):
        item = self.finish(task)
        original = incidents.system_fault
        expected = []

        def fault(*args, **kwargs):
            action(S.load_task(self.project, task["slug"]))
            expected.append(S.load_task(self.project, task["slug"]))
            return original(*args, **kwargs)

        with mock.patch.object(incidents, "system_fault", side_effect=fault):
            server.on_l2_finished(self.project, item)
        self.assertEqual(len(expected), 1)
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current, expected[0], "historical fault bookkeeping changed the newer lifecycle")
        evidence = S.read_json(incidents.FAULTS)[json.dumps([self.project, "l2-died"])]
        self.assertEqual(evidence["task"], task["slug"])
        self.assertIn(task["agent_id"], evidence["detail"])
        self.assertTrue(evidence["incident"], "stale fault evidence must remain recorded")
        return current

    def send(self, task):
        return T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "Apply this later correction.")

    def continue_task(self, task):
        self.send(task)
        dispatch.resume(self.project, task["slug"])
        return S.load_task(self.project, task["slug"])

    def test_message_after_clean_exit_block_keeps_its_wake_and_is_delivered(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                current = self.interleave_fault(task, self.send)
                self.assertEqual(current["state"], "blocked")
                self.assertTrue(current["resume_after"])
                self.assertNotIn("fault", current)
                self.assertIn(task["slug"], dispatch.resume_due(self.project))
                dispatch.resume(self.project, task["slug"])
                current = S.load_task(self.project, task["slug"])
                self.assertEqual(current["session_id"], task["session_id"])
                self.assertEqual(current["state"], "running")
                self.assertEqual(T.message_views(self.project, task["slug"], current, [])[0]["delivery"]["state"],
                                 "delivered")

    def test_fault_cannot_block_resumed_replacement_after_its_request_was_consumed(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                blocks = []

                def resume(blocked):
                    blocks.append(blocked["block_id"])
                    self.continue_task(blocked)

                current = self.interleave_fault(task, resume)
                self.assertEqual(current["state"], "running")
                self.assertNotEqual(current["agent_id"], task["agent_id"])
                self.assertEqual(current["session_id"], task["session_id"])
                self.assertEqual(current["block_id"], blocks[0])
                self.assertNotIn("resume_request", current)
                self.assertNotIn("fault", current)

    def test_explicit_resume_without_a_message_survives_late_fault_bookkeeping(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                current = self.interleave_fault(task, lambda blocked: dispatch.request_task_operation(
                    self.project, blocked["slug"], "resume", "Continue this session", actor=T.OPERATOR_MESSAGE_ROLE))
                self.assertNotIn("resume_request", current)
                self.assertEqual(current["daemon_request"]["status"], "pending")
                self.assertTrue(current["resume_after"])
                self.assertNotIn("fault", current)
                dispatch.run_task_operation(self.project, task["slug"])
                current = S.load_task(self.project, task["slug"])
                self.assertEqual(current["state"], "running")
                self.assertEqual(current["session_id"], task["session_id"])
                self.assertEqual(current["daemon_request"]["status"], "done")

    def test_new_question_survives_older_worker_fault_evidence(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                current = self.interleave_fault(task, lambda blocked: T.escalate(
                    self.project, blocked["slug"], "Which scope should the next turn follow?",
                    recommendation="Keep the smaller scope"))
                self.assertEqual(current["state"], "blocked")
                self.assertEqual(current["waiting_on"], T.OPERATOR_MESSAGE_ROLE)
                self.assertNotIn("fault", current)
                self.assertEqual(T.question_views(self.project, task["slug"])[-1]["status"], "open")
                self.assertNotIn(task["slug"], dispatch.resume_due(self.project))

    def test_stop_accepted_or_completed_on_replacement_retains_its_hold(self):
        for engine in config.ENGINES:
            for complete in (False, True):
                with self.subTest(engine=engine, complete=complete):
                    task = self.launch(engine)

                    def stop(blocked):
                        replacement = self.continue_task(blocked)
                        dispatch.request_task_operation(self.project, task["slug"], "stop", "Stop this turn",
                                                        actor=T.OPERATOR_MESSAGE_ROLE, generation=replacement["agent_id"])
                        if complete:
                            dispatch.run_task_operation(self.project, task["slug"])

                    current = self.interleave_fault(task, stop)
                    self.assertEqual(T.steering_view(current, S.read_events(self.project, task["slug"]))["state"],
                                     "stopped" if complete else "stopping")
                    self.assertNotIn("fault", current)
                    if not complete:
                        dispatch.run_task_operation(self.project, task["slug"])
                    self.assertEqual(S.load_task(self.project, task["slug"])["state"], "blocked")

    def test_prior_queue_resumes_clean_turn_but_never_erases_engine_failure(self):
        for engine in config.ENGINES:
            for failed in (False, True):
                with self.subTest(engine=engine, failed=failed):
                    task = self.launch(engine)
                    row = self.send(task)
                    server.on_l2_finished(self.project, self.finish(task, failed=failed))
                    current = S.load_task(self.project, task["slug"])
                    if failed:
                        self.assertEqual(current["fault"], "l2-died")
                        self.assertEqual(current["waiting_on"], "l3")
                        self.assertNotIn(task["slug"], dispatch.resume_due(self.project))
                        self.assertEqual([message["id"] for message in T.pending(self.project, task["slug"])], [row["id"]])
                    else:
                        self.assertNotIn("fault", current)
                        self.assertIn(task["slug"], dispatch.resume_due(self.project))
                        dispatch.resume(self.project, task["slug"])
                        current = S.load_task(self.project, task["slug"])
                        self.assertEqual(current["session_id"], task["session_id"])
                        self.assertEqual(current["state"], "running")
                        self.assertEqual(T.pending(self.project, task["slug"]), [])
