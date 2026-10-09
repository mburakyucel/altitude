"""Send now hands a queued operator message to the running owner's driver, which writes it into the current turn.

The owner's job is never stopped for it. The driver's outcome settles the row: delivered and unconfirmed rows leave
the inbox, a returned row waits for the next user turn, and a claim its driver never settled is recovered from the
drop file before another worker can take the inbox, so no message is delivered twice."""
import json
import os
import socket
import subprocess
import sys
import threading
from unittest import mock

from tests.support import REPO, AltitudeCase, make_repo
from tests.fakes import FakeL2
from tests.test_images import upload
from altitude import config, dispatch, engines, platform, server, state as S, tasks as T

SENDING = "Sending into the current turn."
HOOK = REPO / "hooks" / "inbox.py"
READ_RECORD = engines.worker_sends  # FakeL2 replaces it; the driverless case reads real ownership records


def give_driver(job_root, worker_id: str) -> None:
    """The ownership record `engines.start_l2` writes for a worker whose job runs the Send now driver."""
    paths = engines._codex_paths(job_root, worker_id)
    paths["record"].parent.mkdir(parents=True, exist_ok=True)
    S.atomic_write(paths["record"], json.dumps({"id": worker_id, "sends": str(paths["sends"])}))


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

    def send(self, task, text, **kwargs):
        return T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, text, **kwargs)

    def delivery(self, task, message):
        return next(row["delivery"] for row in T.message_views(self.project, task["slug"], [])
                    if row["id"] == message["id"])

    def request(self, task, message):
        return dispatch.request_send_now(self.project, task["slug"], message["id"])

    def sends(self, task):
        return engines.worker_sends(task["agent_id"], job_root=dispatch.l2_job_root(self.project, task["slug"]))

    def ids(self, rows):
        return [row["id"] for row in rows]

    def claimed(self, engine):
        """A running owner with three queued messages, the middle one handed to its driver."""
        task = self.launch(engine)
        first, selected, last = [self.send(task, text) for text in ("earlier", "selected", "later")]
        self.assertEqual(self.request(task, selected), {"status": "sending", "idempotent": False})
        return task, first, selected, last

    def no_stop(self):
        return (mock.patch.object(engines, "stop_l2_worker", side_effect=AssertionError("Send now stopped the job")),
                mock.patch.object(platform, "job_stop", side_effect=AssertionError("Send now stopped the unit")))

    # ---- the claim -----------------------------------------------------------------------------------------

    def test_send_now_hands_the_message_to_the_running_turn_without_stopping_it(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                first, selected, last = [self.send(task, text) for text in ("earlier", "selected", "later")]
                self.assertTrue(self.delivery(task, selected)["send_now"])
                stop_worker, stop_unit = self.no_stop()
                with stop_worker, stop_unit, mock.patch.object(dispatch, "run_task_operation") as operation:
                    self.assertEqual(self.request(task, selected), {"status": "sending", "idempotent": False})
                    self.assertEqual(self.request(task, selected), {"status": "sending", "idempotent": True})
                    with self.assertRaisesRegex(T.TransitionError, "Another message is being sent now"):
                        self.request(task, last)
                    operation.assert_not_called()
                sends = self.sends(task)
                self.assertEqual(sorted(path.name for path in sends.iterdir()), [f"{selected['id']}.json"])
                self.assertEqual(json.loads((sends / f"{selected['id']}.json").read_text()),
                                 {"id": selected["id"], "text": "selected"})
                current = S.load_task(self.project, task["slug"])
                claim = current["send_now"]
                self.assertEqual(set(claim), {"id", "agent_id", "sends", "at"})
                self.assertEqual((claim["id"], claim["agent_id"], claim["sends"]),
                                 (selected["id"], task["agent_id"], str(sends)))
                self.assertEqual(current["state"], "running")
                self.assertEqual(current["agent_id"], task["agent_id"])
                self.assertIsNone(current.get("daemon_request"))
                self.assertNotIn("stop_id", current)
                self.assertEqual(self.worker.workers[task["agent_id"]]["state"], "working")
                self.assertEqual(self.ids(T.pending(self.project, task["slug"])),
                                 self.ids([first, selected, last]))
                self.assertEqual(self.delivery(task, selected), {
                    "state": "sending", "at": None, "removable": False,
                    "send_now": False, "send_now_pending": True, "send_now_reason": SENDING})
                with self.assertRaisesRegex(T.TransitionError, "can no longer be removed"):
                    T.remove_message(self.project, task["slug"], selected["id"])
                other = self.delivery(task, last)
                self.assertEqual((other["state"], other["removable"], other["send_now"], other["send_now_pending"]),
                                 ("queued", True, False, False))
                self.assertIn("Another message is being sent now", other["send_now_reason"])
                T.remove_message(self.project, task["slug"], last["id"])  # siblings stay removable

    # ---- the driver's outcome ------------------------------------------------------------------------------

    def test_delivered_message_leaves_the_inbox_and_never_joins_the_next_resume(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task, first, selected, last = self.claimed(engine)
                T.settle_send_now(self.project, task["slug"], selected["id"], "delivered",
                                  agent_id=task["agent_id"], session_id=task["session_id"])
                current = S.load_task(self.project, task["slug"])
                self.assertNotIn("send_now", current)
                receipt = current["message_deliveries"][selected["id"]]
                self.assertEqual((receipt["state"], receipt["agent_id"], receipt["session_id"]),
                                 ("delivered", task["agent_id"], task["session_id"]))
                self.assertTrue(receipt["at"])
                inbox = (S.task_dir(self.project, task["slug"]) / "inbox.jsonl").read_text()
                self.assertNotIn(selected["id"], inbox)
                self.assertEqual(self.ids(T.pending(self.project, task["slug"])), self.ids([first, last]))
                self.assertEqual(self.delivery(task, selected)["state"], "delivered")
                self.assertTrue(self.delivery(task, first)["send_now"])
                self.assertEqual(self.request(task, selected), {"status": "delivered", "idempotent": True})
                # The turn ends; the next resume carries the siblings only.
                T.block(self.project, task["slug"], "Turn ended", resume_pending=True)
                dispatch.resume(self.project, task["slug"])
                self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([first, last]))
                self.assertEqual(T.pending(self.project, task["slug"]), [])
                self.assertEqual(self.delivery(task, selected)["state"], "delivered")

    def test_returned_message_stays_queued_and_wakes_the_next_turn(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                selected = self.send(task, "selected")
                self.request(task, selected)
                T.settle_send_now(self.project, task["slug"], selected["id"], "returned", agent_id=task["agent_id"])
                current = S.load_task(self.project, task["slug"])
                self.assertNotIn("send_now", current)
                self.assertNotIn(selected["id"], current.get("message_deliveries") or {})
                self.assertEqual(T.pending(self.project, task["slug"]), [selected])
                delivery = self.delivery(task, selected)
                self.assertEqual((delivery["state"], delivery["removable"], delivery["send_now"]),
                                 ("queued", True, True))
                blocked = T.block(self.project, task["slug"], "Turn ended", resume_pending=True)
                self.assertTrue(blocked["resume_after"])
                self.assertEqual(blocked["resume_request"], selected["id"])
                dispatch.resume(self.project, task["slug"])
                self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([selected]))
                self.assertEqual(T.pending(self.project, task["slug"]), [])

    def test_unconfirmed_message_leaves_the_inbox_and_is_never_redelivered(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task, first, selected, last = self.claimed(engine)
                T.settle_send_now(self.project, task["slug"], selected["id"], "unconfirmed", agent_id=task["agent_id"])
                current = S.load_task(self.project, task["slug"])
                self.assertNotIn("send_now", current)
                receipt = current["message_deliveries"][selected["id"]]
                self.assertEqual((receipt["state"], receipt["at"], receipt["agent_id"]),
                                 ("unconfirmed", None, task["agent_id"]))
                self.assertEqual(self.ids(T.pending(self.project, task["slug"])), self.ids([first, last]))
                delivery = self.delivery(task, selected)
                self.assertEqual((delivery["state"], delivery["removable"]), ("unconfirmed", False))
                T.block(self.project, task["slug"], "Turn ended", resume_pending=True)
                dispatch.resume(self.project, task["slug"])
                self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([first, last]))

    def test_a_settlement_for_another_message_leaves_the_claim(self):
        task, first, selected, _ = self.claimed(config.ENGINES[0])
        T.settle_send_now(self.project, task["slug"], first["id"], "returned", agent_id=task["agent_id"])
        self.assertEqual(S.load_task(self.project, task["slug"])["send_now"]["id"], selected["id"])

    # ---- Stop, a lost job, and recovery ------------------------------------------------------------------

    def test_stop_with_a_message_in_flight_recovers_it_once_from_the_drop(self):
        # (drop name the driver left, receipt expected, whether the continuation carries the message)
        cases = ((None, None, True), ("writing", "unconfirmed", False), ("delivered", "delivered", False))
        for engine in config.ENGINES:
            for left, receipt, carried in cases:
                with self.subTest(engine=engine, left=left):
                    task, first, selected, last = self.claimed(engine)
                    sends = self.sends(task)
                    if left:
                        os.rename(sends / f"{selected['id']}.json", sends / f"{selected['id']}.{left}")
                    with mock.patch.object(engines, "stop_l2_worker", wraps=self.worker.stop_l2_worker) as stop:
                        dispatch.stop(self.project, task["slug"], reason="Operator stop")
                    stop.assert_called_once()
                    self.assertEqual(stop.call_args.args[1], task["agent_id"])
                    stopped = S.load_task(self.project, task["slug"])
                    self.assertEqual(stopped["state"], "blocked")
                    self.assertTrue(stopped["stop_id"])
                    self.assertEqual(stopped["send_now"]["id"], selected["id"])  # the block keeps the claim
                    self.assertEqual(self.delivery(task, selected)["state"], "sending")
                    claim = T.claim_resume(self.project, task["slug"])
                    expected = [first, selected, last] if carried else [first, last]
                    self.assertEqual(self.ids(claim["messages"]), self.ids(expected))
                    current = S.load_task(self.project, task["slug"])
                    self.assertNotIn("send_now", current)
                    if receipt:
                        state = current["message_deliveries"][selected["id"]]
                        self.assertEqual((state["state"], state["agent_id"]), (receipt, task["agent_id"]))
                        self.assertTrue((sends / f"{selected['id']}.{left}").exists())
                    else:
                        self.assertNotIn(selected["id"], current.get("message_deliveries") or {})
                        # A driver still running can no longer take it.
                        self.assertFalse((sends / f"{selected['id']}.json").exists())
                        self.assertTrue((sends / f"{selected['id']}.returned").exists())
                    T.release_resume_claim(self.project, task["slug"], claim["id"], consume_request=False)
                    self.assertEqual(self.ids(T.pending(self.project, task["slug"])), self.ids(expected))
                    again = T.claim_resume(self.project, task["slug"])
                    self.assertEqual(self.ids(again["messages"]), self.ids(expected))
                    T.release_resume_claim(self.project, task["slug"], again["id"], consume_request=False)

    def test_continuation_after_stop_delivers_a_withdrawn_message_exactly_once(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task, first, selected, last = self.claimed(engine)
                stopped = dispatch.stop(self.project, task["slug"], reason="Operator stop")
                calls = len(self.worker.calls)
                dispatch.request_task_operation(self.project, task["slug"], "resume", "Continue",
                                                actor=T.OPERATOR_MESSAGE_ROLE, stop_id=stopped["stop_id"],
                                                deliver_reason=False)
                dispatch.run_task_operation(self.project, task["slug"])
                self.assertEqual(len(self.worker.calls), calls + 1)
                self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([first, selected, last]))
                current = S.load_task(self.project, task["slug"])
                self.assertEqual(current["state"], "running")
                self.assertNotIn("send_now", current)
                self.assertEqual(T.pending(self.project, task["slug"]), [])
                self.assertEqual(self.delivery(task, selected)["state"], "delivered")
                # The new worker has its own driver, and the settled message is not offered again.
                self.assertNotEqual(self.sends(S.load_task(self.project, task["slug"])), self.sends(task))
                self.assertEqual(self.request(task, selected), {"status": "delivered", "idempotent": True})

    def test_a_lost_job_blocked_by_the_monitor_recovers_the_claim_at_resume(self):
        task, first, selected, last = self.claimed(config.ENGINES[0])
        sends = self.sends(task)
        os.rename(sends / f"{selected['id']}.json", sends / f"{selected['id']}.delivered")
        blocked = T.block(self.project, task["slug"], "Worker exited", resume_pending=True)
        self.assertEqual(blocked["send_now"]["id"], selected["id"])
        self.assertTrue(blocked["resume_after"])
        dispatch.resume(self.project, task["slug"])
        self.assertEqual(self.worker.calls[-1]["prompt"], T.render_inbox([first, last]))
        self.assertEqual(self.delivery(task, selected)["state"], "delivered")

    def test_a_report_after_the_driver_died_continues_only_for_a_message_the_owner_never_read(self):
        for left, continues in (("delivered", False), ("json", True)):
            with self.subTest(left=left):
                task, _, selected, _ = self.claimed(config.ENGINES[0])
                T.take_inbox(self.project, task["slug"], {row["id"] for row in T.pending(self.project, task["slug"])
                                                          if row["id"] != selected["id"]})
                sends = self.sends(task)
                if left == "delivered":
                    os.rename(sends / f"{selected['id']}.json", sends / f"{selected['id']}.delivered")
                if continues:
                    with self.assertRaisesRegex(T.TransitionError, "pending messages require continuation"):
                        T.report(self.project, task["slug"], {"verdict": "ok", "problems": []})
                    current = S.load_task(self.project, task["slug"])
                    self.assertEqual(current["state"], "blocked")
                    self.assertEqual([row["id"] for row in T.pending(self.project, task["slug"])], [selected["id"]])
                else:
                    current = T.report(self.project, task["slug"], {"verdict": "ok", "problems": []})
                    self.assertEqual(current["state"], "reported")
                    self.assertEqual(current["message_deliveries"][selected["id"]]["state"], "delivered")
                self.assertNotIn("send_now", current)

    def test_ending_the_task_settles_an_unsettled_claim(self):
        task, _, selected, _ = self.claimed(config.ENGINES[0])
        sends = self.sends(task)
        os.rename(sends / f"{selected['id']}.json", sends / f"{selected['id']}.delivered")
        rejected = T.reject(self.project, task["slug"], "Operator ended this task")
        self.assertEqual(rejected["state"], "rejected")
        self.assertNotIn("send_now", rejected)
        self.assertEqual(rejected["message_deliveries"][selected["id"]]["state"], "delivered")

    # ---- refusals -------------------------------------------------------------------------------------------

    def assert_refused(self, task, message, expected):
        before = S.load_task(self.project, task["slug"])
        stop_worker, stop_unit = self.no_stop()
        with stop_worker, stop_unit, self.assertRaisesRegex(T.TransitionError, expected):
            self.request(task, message)
        self.assertEqual(S.load_task(self.project, task["slug"]), before)
        sends = self.sends(before) if before.get("agent_id") else None
        self.assertFalse(sends and sends.exists() and any(sends.iterdir()), "a refusal wrote a drop")

    def test_owner_states_that_cannot_take_a_message_refuse_without_mutation(self):
        for engine in config.ENGINES:
            for change, expected in (({"state": "blocked"}, "needs a running owner"),
                                     ({"state": "blocked", "waiting_on": "operator"}, "waiting for an answer"),
                                     ({"state": "blocked", "fault": "fixture fault"}, "faulted"),
                                     ({"stop_id": "explicit-stop"}, "stopped or stopping"),
                                     ({"daemon_request": {"id": "r1", "operation": "stop", "status": "pending"}},
                                      "Another owner action"),
                                     ({"dispatching": "2026-10-09T00:00:00Z"}, "Another owner action")):
                with self.subTest(engine=engine, change=change):
                    task = self.launch(engine)
                    message = self.send(task, "Queued before the change")
                    original = S.load_task(self.project, task["slug"])
                    task = {**original, **change}
                    S.save_task(self.project, task)
                    self.assert_refused(task, message, expected)
                    delivery = self.delivery(task, message)
                    self.assertFalse(delivery["send_now"])
                    self.assertFalse(delivery["send_now_pending"])
                    self.assertIn(expected, delivery["send_now_reason"])
                    # Leave no ready resume holding the next launch's machine slot.
                    S.save_task(self.project, original)
                    T.remove_message(self.project, task["slug"], message["id"])

    def test_a_worker_launched_without_a_driver_refuses(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "No driver here")
        job_root = dispatch.l2_job_root(self.project, task["slug"])
        record = engines._codex_paths(job_root, task["agent_id"])["record"]
        self.patch(engines, "worker_sends", new=READ_RECORD)
        self.assert_refused(task, message, "started before Send now")  # no ownership record
        self.assertIn("next turn", self.delivery(task, message)["send_now_reason"])
        record.parent.mkdir(parents=True, exist_ok=True)
        S.atomic_write(record, json.dumps({"id": task["agent_id"]}))
        self.assert_refused(task, message, "started before Send now")  # a record without a `sends` folder
        give_driver(job_root, task["agent_id"])
        self.assertEqual(self.request(task, message), {"status": "sending", "idempotent": False})

    def test_a_message_with_images_waits_for_the_next_turn(self):
        task = self.launch(config.ENGINES[0])
        message = self.send(task, "See the screenshot", uploads=[upload()])
        self.assert_refused(task, message, dispatch.IMAGE_SEND_NOW)
        delivery = self.delivery(task, message)
        self.assertEqual((delivery["send_now"], delivery["send_now_reason"], delivery["removable"]),
                         (False, dispatch.IMAGE_SEND_NOW, True))

    def test_only_a_queued_operator_message_can_be_sent_now(self):
        task = self.launch(config.ENGINES[0])
        coordinator = T.message(self.project, task["slug"], "l3", "Coordinator note")
        self.assert_refused(task, coordinator, "Only queued operator messages")
        self.assert_refused(task, {"id": "missing"}, "Only queued operator messages")
        removed = self.send(task, "Remove me")
        T.remove_message(self.project, task["slug"], removed["id"])
        self.assert_refused(task, removed, "removed")
        picked = self.send(task, "Already picked up")
        self.assertEqual(T.take_inbox(self.project, task["slug"], ids={picked["id"]}), [picked])
        self.assertEqual(self.request(task, picked), {"status": "delivered", "idempotent": True})
        self.assertNotIn("send_now", S.load_task(self.project, task["slug"]))

    # ---- the HTTP endpoint ----------------------------------------------------------------------------------

    def test_endpoint_answers_synchronously_and_starts_no_task_operation(self):
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.patch(server, "log", new=lambda *_: None)
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def post(body):
            raw = json.dumps(body).encode()
            head = (f"POST /api/l2/send-now HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(raw)}\r\n\r\n").encode()
            with socket.create_connection(httpd.server_address, timeout=10) as sock:
                sock.sendall(head + raw)
                data = b"".join(iter(lambda: sock.recv(65536), b""))
            headers, _, payload = data.partition(b"\r\n\r\n")
            return int(headers.split()[1]), json.loads(payload)

        task = self.launch(config.ENGINES[0])
        selected, other = self.send(task, "selected"), self.send(task, "other")
        body = {"project": self.project, "slug": task["slug"]}
        with mock.patch.object(server, "spawn", side_effect=AssertionError("Send now spawned work")):
            self.assertEqual(post({**body, "id": selected["id"]}), (200, {"status": "sending", "idempotent": False}))
            self.assertEqual(post({**body, "id": selected["id"]}), (200, {"status": "sending", "idempotent": True}))
            status, refusal = post({**body, "id": other["id"]})
        self.assertEqual(status, 409)
        self.assertIn("Another message is being sent now", refusal["error"])
        self.assertTrue((self.sends(task) / f"{selected['id']}.json").exists())


class TestSendNowInboxHook(AltitudeCase):
    """The inbox hook neither lists the message the driver is writing nor ends the turn for it."""

    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Hooked task", "request")
        self.slug = task["slug"]
        task.update({"state": "running", "attempt": 1, "session_id": "sid", "agent_id": "aid"})
        S.save_task(self.project, task)
        give_driver(dispatch.l2_job_root(self.project, self.slug), "aid")

    def run_hook(self, event):
        env = dict(os.environ, ALTITUDE_HOME=str(config.ROOT), ALTITUDE_PROJECT=self.project, ALTITUDE_TASK=self.slug)
        payload = {"hook_event_name": event, "session_id": "sid",
                   **({"tool_name": "Bash"} if event == "PostToolUse" else {"stop_hook_active": False})}
        done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_the_claimed_message_is_not_waiting_and_does_not_end_the_turn(self):
        selected = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Use the staging copy.")
        self.assertEqual(dispatch.request_send_now(self.project, self.slug, selected["id"])["status"], "sending")

        self.assertEqual(self.run_hook("PostToolUse"), "")
        self.assertEqual(self.run_hook("Stop"), "")
        self.assertIsNone(S.load_task(self.project, self.slug).get("turn_released"))
        self.assertEqual(T.pending(self.project, self.slug), [selected])

        other = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Say macOS is supported.")
        context = json.loads(self.run_hook("PostToolUse"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"1 message from Operator (message id {other['id']}) waits", context)
        self.assertNotIn(selected["id"], context)
        self.assertEqual(self.run_hook("Stop"), "")
        self.assertIsNotNone(S.load_task(self.project, self.slug).get("turn_released"))
        self.assertEqual(S.load_task(self.project, self.slug)["send_now"]["id"], selected["id"])
