"""A CI recovery result survives coordinator queue/turn crashes without waking the owner."""
from contextlib import contextmanager
from copy import deepcopy
import threading
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, l3, state as S, tasks as T


class TestCIRecheckDelivery(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.at = "2030-01-01T00:00:00+00:00"
        self.patch(S, "now", new=lambda: self.at)
        self.make_waiting("Owner")

    def make_waiting(self, title):
        task = T.new(self.project, title, "Finish the authorized recovery.")
        task.update(state="blocked", fault="ci-quota", blocked_reason="CI artifact quota refused.",
                    block_id="original-block", attempt=1, session_id="owner-session", agent_id="owner-agent",
                    l2_engine=config.ENGINES[0], hold_merge="operator review",
                    questions=[{"id": "decision", "status": "open", "question": "Keep the hold?",
                                "revision": 1, "audience": "operator"}])
        observation = {"conclusion": "failure", "artifact_upload": "unverified"}
        task["ci_recheck"] = {"id": "recheck-" + task["slug"], "status": "notifying", "reason": "Verify recovery.",
                              "baseline": observation, "observation": observation, "due_at": None,
                              "identity": T.ci_recheck_identity(task), "evidence": {
                                  "run": "https://github.com/example/product/actions/runs/42",
                                  "attempt": 2, **observation, "artifact_ids": []}}
        S.save_task(self.project, task)
        self.slug = task["slug"]
        self.original = deepcopy(task)
        return task

    def record(self):
        return S.load_task(self.project, self.slug)["ci_recheck"]

    @contextmanager
    def provider(self, engine=None, *, error=None, callback=None):
        engine = engine or config.ENGINES[0]

        def execute(_text, **kwargs):
            if callback:
                callback()
            sid = kwargs.get("resume") or "coordinator-session"
            return {"text": "I checked the evidence.", "session_id": sid, "reported_session_id": sid,
                    "usage": {"input_tokens": 10}, "context_tokens": 10, "cost": 0.0,
                    "error": error, "tools": []}

        seam = "claude_print" if engine == "claude" else "codex_exec"
        with mock.patch.object(l3, "_select", return_value={"engine": engine, "why": "fixture"}), \
             mock.patch.object(engines, seam, side_effect=execute) as call:
            yield call

    def queue(self):
        return l3.queue_ci_recheck(self.project, self.slug)

    def assert_owner_preserved(self):
        after = S.load_task(self.project, self.slug)
        for key, value in self.original.items():
            if key not in ("ci_recheck", "updated"):
                self.assertEqual(after.get(key), value, key)
        self.assertNotIn("resume_after", after)
        self.assertNotIn("daemon_request", after)

    def test_success_keeps_queue_until_terminal_evidence_on_each_engine(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                self.make_waiting("Owner " + engine)
                self.queue()

                def inspect_active():
                    record = self.record()
                    self.assertEqual(record["delivery"]["turn_id"], l3.active(self.project)["id"])
                    self.assertEqual(record["delivery"]["status"], "running")
                    self.assertEqual([row["id"] for row in l3.queued(self.project)], [record["id"]])
                    self.queue()  # The tick must not mistake the active turn for a crashed claim.
                    self.assertEqual(self.record()["delivery"]["attempts"], 1)

                with self.provider(engine, callback=inspect_active) as provider:
                    result = l3.deliver_queued(self.project)
                self.assertTrue(result["completed"])
                provider.assert_called_once()
                self.assertEqual(self.record()["status"], "done")
                self.assertEqual(self.record()["delivery"]["status"], "delivered")
                self.assertEqual(l3.queued(self.project), [])
                self.assertTrue(l3.chat_history(self.project)[-1]["completed"])
                self.assert_owner_preserved()

    def test_enqueue_after_task_receipt_crash_reconstructs_one_row(self):
        with mock.patch.object(l3, "_write_queue", side_effect=SystemExit("Before queue write")):
            with self.assertRaises(SystemExit):
                self.queue()
        receipt = deepcopy(self.record()["delivery"])
        self.assertEqual(l3.queued(self.project), [])
        self.queue()
        self.queue()
        self.assertEqual(len(l3.queued(self.project)), 1)
        self.assertEqual(self.record()["delivery"], receipt)

    def test_enqueue_persisted_before_interruption_is_not_duplicated(self):
        write = l3._write_queue

        def crash(path, rows):
            write(path, rows)
            raise SystemExit("After queue write")

        with mock.patch.object(l3, "_write_queue", side_effect=crash):
            with self.assertRaises(SystemExit):
                self.queue()
        self.queue()
        self.assertEqual(len(l3.queued(self.project)), 1)

    def test_queue_write_failures_have_a_visible_finite_budget(self):
        with mock.patch.object(l3, "_write_queue", side_effect=OSError("Queue storage unavailable")) as write:
            self.queue()
            self.queue()
            self.assertEqual(write.call_count, 1)
            self.assertEqual(self.record()["status"], "notifying")
            self.at = "2030-01-01T00:01:00+00:00"
            self.queue()
            self.queue()
            self.at = "2030-01-01T00:02:00+00:00"
            self.queue()
        self.assertEqual(write.call_count, 2)
        self.assertEqual(self.record()["status"], "failed")
        self.assertIn("Queue storage unavailable", self.record()["delivery"]["error"])
        self.assertEqual(self.record()["delivery"]["attempts"], 0)
        self.assert_owner_preserved()

    def test_queue_read_failures_save_deadline_and_exhaust_without_removing_other_messages(self):
        other = l3.queue_message(self.project, "Keep this ordinary message", trigger="chat", role="burak")
        for error in (OSError("Queue read unavailable"), ValueError("Queue JSON unavailable")):
            with self.subTest(error=type(error).__name__):
                self.make_waiting(type(error).__name__)
                self.at = "2030-01-01T00:00:00+00:00"
                with mock.patch.object(l3, "_queue_rows", side_effect=error) as read:
                    self.queue()
                    self.assertEqual(self.record()["delivery"]["deadline"], "2030-01-01T01:00:00+00:00")
                    self.queue()
                    self.assertEqual(read.call_count, 1)
                    self.at = "2030-01-01T00:01:00+00:00"
                    self.queue()
                    self.at = "2030-01-01T00:02:00+00:00"
                    self.queue()
                self.assertEqual(read.call_count, 2)
                self.assertEqual(self.record()["status"], "failed")
                self.assertEqual(l3.queued(self.project)[0]["id"], other["id"])
                self.assert_owner_preserved()

    def test_probe_without_notification_does_not_read_queue_or_create_delivery(self):
        task = S.load_task(self.project, self.slug)
        task["ci_recheck"]["status"] = "probing"
        S.save_task(self.project, task)
        with mock.patch.object(l3, "_queue_rows", side_effect=OSError("Queue unavailable")) as read:
            self.assertIsNone(self.queue())
        read.assert_not_called()
        self.assertNotIn("delivery", self.record())
        task["ci_recheck"]["status"] = "notifying"
        S.save_task(self.project, task)
        self.queue()
        self.assertTrue(self.record()["delivery"]["deadline"])

    def test_corrupt_queue_is_retained_with_bounded_delivery_failure(self):
        l3.queue_path(self.project).write_text("{broken queue\n")
        self.queue()
        self.at = "2030-01-01T00:01:00+00:00"
        self.queue()
        self.assertEqual(self.record()["status"], "failed")
        self.assertEqual(l3.queue_path(self.project).read_text(), "{broken queue\n")

    def test_terminal_chat_read_failure_is_bounded_without_replay(self):
        self.queue()
        task = S.load_task(self.project, self.slug)
        task["ci_recheck"]["delivery"].update(status="running", turn_id="earlier-turn", attempts=1,
                                            execution_started=self.at)
        S.save_task(self.project, task)
        with mock.patch.object(l3, "chat_history", side_effect=OSError("Chat storage unavailable")) as read:
            self.queue()
            self.queue()
            self.assertEqual(read.call_count, 1)
            self.at = "2030-01-01T00:01:00+00:00"
            self.queue()
            self.at = "2030-01-01T00:02:00+00:00"
            self.queue()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(self.record()["status"], "failed")
        self.assertEqual(self.record()["delivery"]["attempts"], 1)
        self.assertIn("Chat storage unavailable", self.record()["delivery"]["error"])

    def test_queue_to_turn_crash_consumes_claim_and_retries_only_once(self):
        self.queue()
        with self.provider(), mock.patch.object(l3, "turn", side_effect=SystemExit("Before chat")) as turn:
            with self.assertRaises(SystemExit):
                l3.deliver_queued(self.project)
            self.assertEqual(self.record()["delivery"]["attempts"], 1)
            self.assertTrue(self.record()["delivery"]["turn_id"])
            self.assertEqual(l3.chat_history(self.project), [])
            self.queue()
            self.assertIsNone(l3.deliver_queued(self.project))
            self.at = "2030-01-01T00:01:00+00:00"
            with self.assertRaises(SystemExit):
                l3.deliver_queued(self.project)
            self.queue()
            self.assertIsNone(l3.deliver_queued(self.project))
        self.assertEqual(turn.call_count, 2)
        self.assertEqual(self.record()["status"], "failed")
        self.assertIn("bounded", self.record()["delivery"]["error"])
        self.assertEqual(l3.queued(self.project), [])
        self.assert_owner_preserved()

    def test_claim_persisted_before_active_publication_is_recovered(self):
        self.queue()
        save = S.save_task

        def crash(project, task):
            save(project, task)
            if task["ci_recheck"]["delivery"]["status"] == "running":
                raise SystemExit("Claim written before active publication")

        with self.provider() as provider, mock.patch.object(S, "save_task", side_effect=crash):
            with self.assertRaises(SystemExit):
                l3.deliver_queued(self.project)
        provider.assert_not_called()
        self.assertIsNone(l3.active(self.project))
        self.assertEqual(self.record()["delivery"]["attempts"], 1)
        self.queue()
        self.at = "2030-01-01T00:01:00+00:00"
        with self.provider() as provider:
            l3.deliver_queued(self.project)
        provider.assert_called_once()
        self.assertEqual(self.record()["status"], "done")

    def test_provider_can_outlive_daemon_crash_and_is_never_replayed(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                self.make_waiting("Uncertain " + engine)
                self.queue()
                alive, release = threading.Event(), threading.Event()

                def still_running():
                    alive.set()
                    release.wait(5)

                worker = threading.Thread(target=still_running, daemon=True)
                self.addCleanup(release.set)

                def crash():
                    self.assertTrue(self.record()["delivery"]["execution_started"])
                    worker.start()
                    self.assertTrue(alive.wait(2))
                    raise SystemExit("Daemon dies while provider remains alive")

                with self.provider(engine, callback=crash) as provider:
                    with self.assertRaises(SystemExit):
                        l3.deliver_queued(self.project)
                self.assertTrue(worker.is_alive())
                self.assertIsNone(l3.active(self.project))
                self.queue()
                with self.provider(engine) as repeated:
                    self.assertIsNone(l3.deliver_queued(self.project))
                repeated.assert_not_called()
                provider.assert_called_once()
                self.assertTrue(provider.call_args.kwargs["durable_timeout"])
                self.assertEqual(self.record()["status"], "failed")
                self.assertEqual(self.record()["delivery"]["attempts"], 1)
                self.assertIn("uncertain", self.record()["delivery"]["error"])
                self.assertEqual(l3.queued(self.project), [])
                release.set()
                worker.join(2)
                self.assert_owner_preserved()

    def test_terminal_chat_repairs_restart_before_task_receipt_without_replay(self):
        self.queue()
        save = S.save_task

        def crash(project, task):
            if task["ci_recheck"]["status"] == "done":
                raise SystemExit("After terminal chat, before receipt")
            save(project, task)

        with self.provider() as provider, mock.patch.object(S, "save_task", side_effect=crash):
            with self.assertRaises(SystemExit):
                l3.deliver_queued(self.project)
        provider.assert_called_once()
        self.assertEqual(self.record()["status"], "notifying")
        # Reconciliation cannot rely on the normal last-60-row UI history window.
        for _ in range(65):
            l3.chat_log(self.project, "user", "Later discussion", trigger="chat")
        with self.provider() as provider:
            self.queue()
            self.assertIsNone(l3.deliver_queued(self.project))
        provider.assert_not_called()
        self.assertEqual(self.record()["status"], "done")
        self.assertEqual(l3.queued(self.project), [])

    def test_done_receipt_repairs_crash_before_queue_removal_without_engine(self):
        self.queue()
        with self.provider(), mock.patch.object(l3, "_write_queue", side_effect=SystemExit("Before dequeue")):
            with self.assertRaises(SystemExit):
                l3.deliver_queued(self.project)
        self.assertEqual(self.record()["status"], "done")
        self.assertEqual(len(l3.queued(self.project)), 1)
        with mock.patch.object(l3, "_select", return_value={"engine": None}):
            self.assertIsNone(l3.deliver_queued(self.project))
        self.assertEqual(l3.queued(self.project), [])

    def test_partial_error_output_is_not_success_and_delivery_exhausts(self):
        self.queue()
        with self.provider(error="Provider stopped after partial output") as provider:
            l3.deliver_queued(self.project)
            self.assertFalse(l3.chat_history(self.project)[-1]["completed"])
            self.assertEqual(self.record()["status"], "notifying")
            self.assertIsNone(l3.deliver_queued(self.project))
            self.at = "2030-01-01T00:01:00+00:00"
            l3.deliver_queued(self.project)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(self.record()["status"], "failed")
        self.assertEqual(l3.queued(self.project), [])

    def test_delivery_deadline_expires_without_an_engine(self):
        self.queue()
        self.at = "2030-01-01T01:00:00+00:00"
        with mock.patch.object(l3, "_select", return_value={"engine": None}):
            self.assertIsNone(l3.deliver_queued(self.project))
        self.assertEqual(self.record()["status"], "failed")
        self.assertEqual(self.record()["delivery"]["attempts"], 0)
        self.assertEqual(l3.queued(self.project), [])

    def test_each_engine_receives_only_time_remaining_to_delivery_deadline(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                self.at = "2030-01-01T00:00:00+00:00"
                self.make_waiting("Deadline " + engine)
                self.queue()
                self.at = "2030-01-01T00:59:30+00:00"
                with self.provider(engine) as provider:
                    l3.deliver_queued(self.project)
                self.assertEqual(provider.call_args.kwargs["timeout"], 30)
                self.assertTrue(provider.call_args.kwargs["durable_timeout"])
                self.assertEqual(self.record()["status"], "done")

    def test_stale_identity_invalidates_queued_action_without_turn(self):
        for changes in ({"block_id": "new-block"}, {"attempt": 2}, {"state": "running"},
                        {"daemon_request": {"id": "stop-request", "status": "pending"}},
                        {"resume_after": "2030-01-01T00:02:00+00:00"}):
            with self.subTest(changes=changes):
                self.make_waiting("Owner " + next(iter(changes)))
                self.queue()
                task = S.load_task(self.project, self.slug)
                task.update(changes)
                S.save_task(self.project, task)
                with self.provider() as provider:
                    self.assertIsNone(l3.deliver_queued(self.project))
                provider.assert_not_called()
                self.assertEqual(self.record()["status"], "invalidated")
                self.assertEqual(l3.queued(self.project), [])

    def test_retry_backoff_does_not_hold_ordinary_chat(self):
        self.queue()
        with self.provider(error="First turn failed"):
            l3.deliver_queued(self.project)
        l3.queue_message(self.project, "An ordinary question", trigger="chat", role="burak")
        with self.provider() as provider:
            l3.deliver_queued(self.project)
            self.assertIsNone(l3.deliver_queued(self.project))
        provider.assert_called_once()
        self.assertEqual(l3.chat_history(self.project)[-1]["trigger"], "chat")
        self.assertNotIn("completed", l3.chat_history(self.project)[-1])
        self.assertEqual(len(l3.queued(self.project)), 1)
        self.assertEqual(self.record()["delivery"]["attempts"], 1)

    def test_wrapper_error_does_not_prove_provider_termination_or_permit_replay(self):
        self.queue()
        def adapter_error():
            self.assertTrue(self.record()["delivery"]["execution_started"])
            raise RuntimeError("Session observation failed while external execution may still be alive")
        with self.provider(callback=adapter_error) as provider:
            result = l3.deliver_queued(self.project)
            self.assertFalse(result["completed"])
            self.assertEqual(l3.chat_history(self.project)[-1]["role"], "error")
            self.assertEqual(self.record()["status"], "failed")
            self.at = "2030-01-01T00:01:00+00:00"
            self.assertIsNone(l3.deliver_queued(self.project))
        provider.assert_called_once()
        self.assertIn("uncertain", self.record()["delivery"]["error"])

    def test_repeated_status_reconciliation_is_silent(self):
        self.queue()
        task = S.load_task(self.project, self.slug)
        rows = l3.queued(self.project)
        self.at = "2030-01-01T00:00:10+00:00"
        with mock.patch.object(S, "save_task") as save, mock.patch.object(l3, "_write_queue") as write:
            self.queue()
            self.queue()
        save.assert_not_called()
        write.assert_not_called()
        self.assertEqual(S.load_task(self.project, self.slug), task)
        self.assertEqual(l3.queued(self.project), rows)
        self.assertEqual(l3.chat_history(self.project), [])
