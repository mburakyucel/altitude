"""Send now reaches a running engine turn through its job's driver, without stopping the job or its command."""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from unittest import mock

from tests.support import REPO, AltitudeCase
from altitude import config, dispatch, engines, platform, server, state as S, tasks as T

FAKE = REPO / "tests" / "fake_engine.py"
TEXT = "Use the staging copy."


class TestEngineDriver(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.log, self.tool, self.sends = self.tmp / "engine.log", self.tmp / "tool", self.tmp / "worker.sends"
        for path in (self.log, self.tool):
            path.unlink(missing_ok=True)

    def drive(self, engine, *, tool=True, ask=False, task=None, helpers=False, end="completed", output=None,
              refuse=False):
        command = [sys.executable, str(FAKE), *(["app-server"] if engine == "codex" else [])]
        spec = {"engine": engine, "command": command, "input": engines.common._engine_input(engine, "Start the work.", ()),
                "cwd": str(self.repo), "sends": str(self.sends), "worker_id": "worker", "task": task}
        env = {**os.environ, "FAKE_ENGINE_LOG": str(self.log), **({"FAKE_ENGINE_TOOL": str(self.tool)} if tool else {}),
               **({"FAKE_ENGINE_ASK": "1"} if ask else {}),
               **({"FAKE_ENGINE_HELPERS": str(helpers)} if helpers else {}), "FAKE_ENGINE_END": end,
               **({"FAKE_ENGINE_REFUSE_STEER": "1"} if refuse else {})}
        proc = subprocess.Popen(engines.common._driver_command(), stdin=subprocess.PIPE, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        proc.stdin.write(engines.common._driver_input(spec).encode())
        proc.stdin.close()
        proc.stdin = None
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def finish(self, proc):
        stdout, stderr = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, stderr.decode())
        return [json.loads(line) for line in stdout.decode().splitlines()]

    def wait_for_tool(self, proc):
        deadline = time.monotonic() + 20
        while not self.tool.exists():
            self.assertIsNone(proc.poll(), "the turn ended before its command started")
            self.assertLess(time.monotonic(), deadline, "the fake command never started")
            time.sleep(0.02)

    def received(self):
        return [row for row in map(json.loads, self.log.read_text().splitlines()) if "environment" not in row]

    def test_send_now_reaches_a_turn_with_a_command_in_flight_on_both_engines(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.setUp()
                started = datetime.now(timezone.utc)
                proc = self.drive(engine, helpers=True)
                self.wait_for_tool(proc)
                os.kill(int(self.tool.with_suffix(".pid").read_text()), 0)
                self.assertFalse(self.tool.with_suffix(".completed").exists())
                engines.send_into_turn(self.sends, "m1", TEXT)
                events = self.finish(proc)
                finished = datetime.now(timezone.utc)
                self.assertEqual(self.tool.with_suffix(".completed").read_text(), "completed")
                marker = events.index({"type": "altitude.send", "message_id": "m1", "outcome": "delivered"})
                later = json.dumps(events[marker:])
                self.assertIn(f"Read: {TEXT}", later)
                self.assertNotIn(f"Read: {TEXT}", json.dumps(events[:marker]))
                self.assertEqual(engines.recover_send(self.sends, "m1"), "delivered")
                received = self.received()
                if engine == "claude":
                    self.assertEqual(self.tool.read_text(), "backgrounded")
                    line = next(row for row in received if row["type"] == "user"
                                and row["message"]["content"][0].get("text") == TEXT)
                    self.assertEqual((line["origin"], line["priority"]), ({"kind": "human"}, "next"))
                    self.assertTrue(any(row.get("request", {}).get("subtype") == "background_tasks" for row in received))
                    self.assertEqual(events[-1]["type"], "result")
                else:
                    steer = next(row for row in received if row.get("method") == "turn/steer")
                    self.assertEqual((steer["params"]["expectedTurnId"], steer["params"]["clientUserMessageId"],
                                      steer["params"]["input"][0]["text"]), ("turn-1", "m1", TEXT))
                    kinds = [event["type"] for event in events]
                    self.assertEqual(kinds[:3], ["thread.started", "turn.started", "item.started"])
                    self.assertEqual(events[0], {"type": "thread.started", "thread_id": "fake-thread",
                                                 "model": "fake-model"})
                    self.assertEqual(events[2]["item"]["type"], "command_execution")
                    for event in events:
                        if event["type"] in ("item.started", "item.completed"):
                            recorded = datetime.fromisoformat(event["timestamp"])
                            self.assertLessEqual(started, recorded)
                            self.assertLessEqual(recorded, finished)
                    self.assertEqual(events[-1], {"type": "turn.completed",
                                                  "usage": {"input_tokens": 5, "output_tokens": 2}})

    def test_a_message_at_the_turn_end_is_delivered_once_or_returned_for_the_next_turn(self):
        # The message is waiting before the turn can take it in: Claude reads it as the next turn of the same job,
        # Codex refuses the steer once its turn is over, and the message waits for the next turn instead.
        for engine, outcome in (("claude", "delivered"), ("codex", "returned")):
            with self.subTest(engine=engine):
                self.setUp()
                engines.send_into_turn(self.sends, "m1", TEXT)
                events = self.finish(self.drive(engine, tool=False))
                sends = [event for event in events if event["type"] == "altitude.send"]
                self.assertEqual(sends, [{"type": "altitude.send", "message_id": "m1", "outcome": outcome}])
                self.assertEqual(engines.recover_send(self.sends, "m1"), outcome)
                replies = [event for event in events if event["type"] == "stream_event"
                           and event["event"]["delta"]["text"] == f"Next turn: {TEXT}"]
                self.assertEqual(len(replies), 1 if engine == "claude" else 0)

    def test_helper_results_do_not_end_or_steer_the_waiting_owner(self):
        """#873: interleaved helper results must not close the server and discard the owner's wait."""
        proc = self.drive("codex", helpers="completed")
        self.wait_for_tool(proc)
        engines.send_into_turn(self.sends, "m1", TEXT)
        events = self.finish(proc)
        self.assertEqual(engines.recover_send(self.sends, "m1"), "delivered")
        steer = next(row for row in self.received() if row.get("method") == "turn/steer")
        self.assertEqual((steer["params"]["threadId"], steer["params"]["expectedTurnId"]),
                         ("fake-thread", "turn-1"))
        self.assertEqual(sum(row["type"] == "turn.started" for row in events), 1)
        self.assertEqual([row for row in events if row["type"] in ("error", "turn.failed")], [])
        self.assertNotIn("Helper", json.dumps(events))
        self.assertEqual(events[-1], {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}})
        self.assertIn(f"Read: {TEXT}", json.dumps(events))
        self.assertEqual(self.tool.with_suffix(".completed").read_text(), "completed")

    def test_helper_success_does_not_mask_owner_abort_failure_or_eof(self):
        for end in ("failed", "interrupted", "eof"):
            with self.subTest(end=end):
                self.setUp()
                proc = self.drive("codex", tool=False, helpers="completed", end=end)
                stdout, stderr = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 1, stderr.decode())
                events = [json.loads(line) for line in stdout.decode().splitlines()]
                self.assertNotIn("turn.completed", [row["type"] for row in events])
                failures = [row for row in events if row["type"] == "turn.failed"]
                self.assertEqual(failures, [] if end == "eof" else [
                    {"type": "turn.failed", "error": {"message": f"Codex turn {end}"}}])

    def test_helper_notifications_leave_owner_identity_usage_and_output_intact(self):
        driver = object.__new__(engines.codex._CodexDriver)
        driver.session_id, driver.turn = "owner", "owner-turn"
        driver.usage = {"input_tokens": 7}
        driver.emit = mock.Mock()
        for thread in ("helper", None):
            for method in ("turn/started", "turn/completed", "item/started", "item/completed",
                           "thread/tokenUsage/updated", "error"):
                driver.notify(method, {"threadId": thread, "turn": {"id": "other-turn", "status": "completed"},
                                       "item": {"type": "agentMessage", "text": "Other output"},
                                       "tokenUsage": {"total": {"inputTokens": 999}},
                                       "error": {"message": "Other failure"}, "willRetry": False})
        self.assertEqual((driver.session_id, driver.turn, driver.usage), ("owner", "owner-turn", {"input_tokens": 7}))
        driver.emit.assert_not_called()

    def test_item_times_record_notification_receipt_and_survive_projection(self):
        driver = object.__new__(engines.codex._CodexDriver)
        driver.session_id = "owner"
        driver.emit = mock.Mock()
        moments = [datetime(2026, 9, 3, 10, 0, second, tzinfo=timezone.utc) for second in (1, 5)]
        with mock.patch.object(engines.codex, "datetime") as clock:
            clock.now.side_effect = moments
            for method, status in (("item/started", "inProgress"), ("item/completed", "completed")):
                driver.notify(method, {"threadId": "owner", "item": {
                    "id": "command", "type": "commandExecution", "command": "echo fixture", "status": status,
                    "aggregatedOutput": "fixture" if status == "completed" else "", "durationMs": 4000}})
        events = [call.args[0] for call in driver.emit.call_args_list]
        self.assertEqual([event["timestamp"] for event in events], [moment.isoformat() for moment in moments])
        for event, moment in zip(events, moments):
            row, = engines.transcript_rows("codex", event, str(self.repo))
            self.assertEqual(row["at"], moment.isoformat())
            self.assertEqual(row["kind"], "command")

    def test_intermediate_and_final_messages_wait_for_the_owner_turn_completion(self):
        driver = object.__new__(engines.codex._CodexDriver)
        driver.session_id, driver.turn = "owner", "owner-turn"
        driver.completed, driver.usage = False, {}
        driver.lock, driver.done = threading.RLock(), threading.Event()
        driver.emit = mock.Mock()
        for item in ({"type": "agentMessage", "id": "comment", "phase": "commentary", "text": "Checking."},
                     {"type": "reasoning", "id": "reason", "summary": ["Inspect the fixture."]},
                     {"type": "commandExecution", "id": "tool", "command": "echo fixture", "status": "completed"},
                     {"type": "agentMessage", "id": "final", "phase": "final_answer", "text": "Finished."}):
            driver.notify("item/completed", {"threadId": "owner", "turnId": "owner-turn", "item": item})
            self.assertFalse(driver.completed)
            self.assertFalse(driver.done.is_set())
            self.assertEqual(driver.turn, "owner-turn")
        for total in (100, 200):
            driver.notify("thread/tokenUsage/updated", {"threadId": "owner", "turnId": "owner-turn",
                          "tokenUsage": {"total": {"inputTokens": total}, "last": {"inputTokens": 50}}})
        driver.notify("turn/completed", {"threadId": "owner", "turn": {"id": "owner-turn", "status": "completed"}})
        self.assertTrue(driver.completed)
        self.assertTrue(driver.done.is_set())
        self.assertIsNone(driver.turn)
        events = [call.args[0] for call in driver.emit.call_args_list]
        self.assertEqual([event["type"] for event in events], ["item.completed"] * 4 + ["turn.completed"])
        self.assertEqual(engines.common._agent_messages(events), ["Checking.", "Finished."])
        self.assertEqual(engines.common._event_usage(events), {"input_tokens": 200})

    def test_poll_keeps_waiting_owner_then_recovers_its_actual_clean_exit(self):
        self.private_ledgers()
        task = T.new(self.project, "Waiting owner", "Consume the helper review.")
        task.update(state="running", attempt=1, l2_engine="codex", agent_id="worker", session_id="fake-thread",
                    hold_merge="Review before merge")
        S.save_task(self.project, task)
        root = dispatch.l2_job_root(self.project, task["slug"])
        paths = engines.common._worker_paths(root, "worker")
        S.write_json(paths["record"], {"id": "worker", "engine": "codex", "unit": "fixture.service",
                                      "session_id": "fake-thread", "started_at": S.now()})
        paths["stderr"].write_text("")
        with paths["stdout"].open("wb") as output:
            proc = self.drive("codex", helpers=True, output=output)
            deadline = time.monotonic() + 20
            while '"command_execution"' not in paths["stdout"].read_text():
                self.assertIsNone(proc.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            with mock.patch.object(platform, "job_active", side_effect=lambda *_: proc.poll() is None), \
                 mock.patch.object(platform, "job_stop") as stop, \
                 mock.patch.object(server, "request_task_resume") as resume:
                self.assertEqual(dispatch.poll(self.project), [])
                saved = S.load_task(self.project, task["slug"])
                self.assertEqual((saved["state"], saved["agent_id"], saved["session_id"], saved["hold_merge"]),
                                 ("running", "worker", "fake-thread", "Review before merge"))
                self.assertFalse(saved.get("nudged"))
                self.assertEqual(T.pending(self.project, task["slug"]), [])
                stop.assert_not_called()
                engines.send_into_turn(self.sends, "m1", TEXT)
                _, stderr = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 0, stderr.decode())
                item, = dispatch.poll(self.project)
                self.assertTrue(item["died"])
                server.on_l2_finished(self.project, item)
                saved = S.load_task(self.project, task["slug"])
                self.assertTrue(saved["nudged"], "a genuine message-only owner exit still earns its one nudge")
                self.assertEqual(saved["hold_merge"], "Review before merge")
                resume.assert_called_once_with(self.project, task["slug"])

    def test_a_message_after_the_job_ended_is_withdrawn_for_the_next_turn(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.setUp()
                self.finish(self.drive(engine, tool=False))
                engines.send_into_turn(self.sends, "m1", TEXT)
                self.assertEqual(engines.recover_send(self.sends, "m1"), "returned")
                self.assertTrue((self.sends / "m1.returned").exists())
                self.assertFalse((self.sends / "m1.json").exists(), "no later driver can take it")

    def test_a_message_the_driver_was_writing_when_its_job_ended_is_unconfirmed(self):
        self.sends.mkdir()
        (self.sends / "m1.writing").write_text(json.dumps({"id": "m1", "text": TEXT}))
        self.assertEqual(engines.recover_send(self.sends, "m1"), "unconfirmed")
        self.assertEqual(engines.recover_send(self.sends, "never-written"), "returned")

    def test_a_delayed_initial_echo_cannot_acknowledge_a_later_message(self):
        driver = object.__new__(engines.claude._ClaudeDriver)
        driver.lock = threading.Lock()
        driver.waiting = {"initial": None, "send": "m1"}
        driver.ended_at = None
        driver.close, driver.settle = mock.Mock(), mock.Mock()
        driver.ended()
        for identity in ("initial", "unrelated", None):
            driver.acknowledge(identity)
        self.assertEqual(driver.waiting, {"send": "m1"})
        driver.close.assert_not_called()
        driver.settle.assert_not_called()
        driver.acknowledge("send")
        driver.acknowledge("send")
        driver.settle.assert_called_once_with("m1", "delivered")

    def test_a_completed_turn_with_no_steer_reply_closes_without_replaying_the_message(self):
        spec = {"engine": "codex", "command": [sys.executable, str(FAKE), "app-server"],
                "input": engines.common._engine_input("codex", "Start.", ()), "sends": str(self.sends),
                "cwd": str(self.repo)}
        engines.send_into_turn(self.sends, "m1", TEXT)
        env = {**os.environ, "FAKE_ENGINE_TOOL": str(self.tool), "FAKE_ENGINE_DROP_STEER_ACK": "1"}
        driver = engines.codex._CodexDriver(spec, env)
        self.addCleanup(lambda: driver.child.poll() is None and driver.child.kill())
        self.addCleanup(driver.child.stdout.close)
        events = []
        driver.emit = events.append
        timeout = threading.Timer(5, lambda: driver.child.poll() is None and driver.child.kill())
        timeout.start()
        try:
            with mock.patch.object(engines.common, "SEND_ECHO_WAIT", 0.05):
                self.assertEqual(driver.run(), 0)
        finally:
            timeout.cancel()
            timeout.join()
        self.assertEqual(self.tool.with_suffix(".completed").read_text(), "completed")
        self.assertIn({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}, events)
        self.assertIn({"type": "altitude.send", "message_id": "m1", "outcome": "unconfirmed"}, events)
        self.assertEqual(engines.recover_send(self.sends, "m1"), "unconfirmed")

    def test_codex_server_requests_are_refused_rather_than_left_waiting(self):
        self.finish(self.drive("codex", tool=False, ask=True))
        answer = next(row for row in self.received() if row.get("id") == "ask-1")
        self.assertNotIn("result", answer)
        self.assertIn("error", answer)

    def test_a_codex_thread_is_announced_only_once_its_turn_has_taken_the_prompt(self):
        """A launch counts its inbox delivered when it sees the thread, so a refused turn shows none."""
        script = self.tmp / "refuses-turn"
        script.write_text(f"#!{sys.executable}\n" + (
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    message = json.loads(line)\n"
            "    if 'id' not in message: continue\n"
            "    if message['method'] == 'turn/start':\n"
            "        reply = {'error': {'code': -32600, 'message': 'turn refused'}}\n"
            "    else:\n"
            "        reply = {'result': {'thread': {'id': 'thread-1'}}}\n"
            "    print(json.dumps({'id': message['id'], **reply}), flush=True)\n"))
        script.chmod(0o755)
        spec = {"engine": "codex", "command": [str(script)], "input": engines.common._engine_input("codex", "Start.", ()),
                "cwd": str(self.repo)}
        proc = subprocess.run(engines.common._driver_command(), input=engines.common._driver_input(spec).encode(),
                              capture_output=True, timeout=30)
        events = [json.loads(line) for line in proc.stdout.decode().splitlines()]
        self.assertEqual(events, [{"type": "error", "message": "turn/start: turn refused"}])
        self.assertNotEqual(proc.returncode, 0)

    def test_the_driver_settles_a_task_message_without_altd(self):
        """The driver writes the receipt itself, so a restart of Altitude mid-turn loses no delivery."""
        task = T.new(self.project, "Driver receipt", "Brief.")
        T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, TEXT)
        message = T.pending(self.project, task["slug"])[-1]
        with S.project_lock(self.project):
            current = S.load_task(self.project, task["slug"])
            current.update(state="running", agent_id="worker", session_id="fake-session")
            T.claim_send_now(self.project, current, [message], self.sends)
        proc = self.drive("claude", task={"project": self.project, "slug": task["slug"]})
        self.wait_for_tool(proc)
        self.finish(proc)
        current = S.load_task(self.project, task["slug"])
        self.assertNotIn("send_now", current)
        self.assertEqual(current["message_deliveries"][message["id"]]["state"], "delivered")
        self.assertEqual(current["message_deliveries"][message["id"]]["agent_id"], "worker")
        self.assertNotIn(message["id"], [row["id"] for row in T.pending(self.project, task["slug"])])

    def owner(self):
        """A running task whose worker is this driver's job, with an ordinary operator message waiting."""
        task = T.new(self.project, "Steered owner", "Brief.")
        with S.project_lock(self.project):
            current = S.load_task(self.project, task["slug"])
            current.update(state="running", agent_id="worker", session_id="fake-thread", attempt=1)
            S.save_task(self.project, current)
        record = engines.common._codex_paths(dispatch.l2_job_root(self.project, task["slug"]), "worker")["record"]
        record.parent.mkdir(parents=True, exist_ok=True)
        S.atomic_write(record, json.dumps({"id": "worker", "sends": str(self.sends)}))
        return {"project": self.project, "slug": task["slug"]}

    def test_an_ordinary_message_reaches_a_running_codex_turn_without_send_now(self):
        task = self.owner()
        proc = self.drive("codex", task=task)
        self.wait_for_tool(proc)
        message = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, TEXT)
        events = self.finish(proc)
        self.assertEqual(self.tool.with_suffix(".completed").read_text(), "completed")
        self.assertIn({"type": "altitude.send", "message_id": message["id"], "outcome": "delivered"}, events)
        steer = next(row for row in self.received() if row.get("method") == "turn/steer")
        self.assertEqual(steer["params"]["input"][0]["text"], T.render_inbox([message]))
        current = S.load_task(self.project, task["slug"])
        self.assertNotIn("send_now", current)
        self.assertEqual(current["message_deliveries"][message["id"]]["state"], "delivered")
        self.assertEqual(T.pending(self.project, task["slug"]), [])

    def test_a_refused_steer_leaves_the_message_for_the_next_turn_without_retrying(self):
        task = self.owner()
        proc = self.drive("codex", task=task, refuse=True)
        self.wait_for_tool(proc)
        message = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, TEXT)
        events = self.finish(proc)
        self.assertIn({"type": "altitude.send", "message_id": message["id"], "outcome": "returned"}, events)
        self.assertEqual(sum(row.get("method") == "turn/steer" for row in self.received()), 1)
        current = S.load_task(self.project, task["slug"])
        self.assertNotIn("send_now", current)
        self.assertEqual(current.get("message_deliveries") or {}, {})
        self.assertEqual(T.pending(self.project, task["slug"]), [message])  # the next turn's prompt carries it

    def test_a_claude_owner_keeps_its_hook_checkpoint(self):
        task = self.owner()
        message = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, TEXT)
        self.finish(self.drive("claude", tool=False, task=task))
        self.assertNotIn("send_now", S.load_task(self.project, task["slug"]))
        self.assertEqual(T.pending(self.project, task["slug"]), [message])
        self.assertFalse(self.sends.exists() and any(self.sends.iterdir()))

    def test_missing_launch_input_starts_no_engine(self):
        proc = subprocess.run(engines.common._driver_command(), input=b"", capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 125)
        self.assertIn(b"the engine was not started", proc.stderr)


class TestChatTurns(AltitudeCase):
    """The chat entry points stream a delivered message's split point to their caller."""

    def setUp(self):
        super().setUp()
        self.fake_engines()
        self.tool = self.tmp / "tool"
        os.environ["FAKE_ENGINE_TOOL"] = str(self.tool)
        self.addCleanup(os.environ.pop, "FAKE_ENGINE_TOOL", None)
        self.patch(platform, "job_command", side_effect=lambda unit, command, env, **kw: command)
        # macOS refuses a Seatbelt profile inside another one, as a confined worker or validation run would apply.
        self.patch(platform, "confined", side_effect=lambda command, writable: command)
        self.patch(engines.codex, "_codex_session_model", return_value={"engine_model": "fake-model"})

    def test_a_delivered_message_splits_the_native_coordinator_reply(self):
        self.tool.unlink(missing_ok=True)
        sends, calls = self.tmp / "claude.sends", []

        def on_send(message_id, outcome, text):
            calls.append((message_id, outcome, text))

        def started(_pid):
            def send():  # once the turn's command is running
                while not self.tool.exists():
                    time.sleep(0.02)
                engines.send_into_turn(sends, "m1", TEXT)
            threading.Thread(target=send, daemon=True).start()

        result = engines.claude_print("Start the work.", cwd=self.repo, timeout=60, sends=sends, on_send=on_send,
                                     on_start=started)
        self.assertIsNone(result["error"], result)
        self.assertEqual(calls[0][:2], ("m1", "delivered"))
        self.assertEqual(calls[0][2], "Waiting on the command.")
        self.assertEqual(result["text"], f"Read: {TEXT}")

    def test_a_codex_exec_that_exits_at_once_reports_its_own_error(self):
        script = self.tmp / "signed-out"
        script.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('codex: not signed in\\n')\nsys.exit(3)\n")
        script.chmod(0o755)
        with mock.patch.object(config, "CODEX_BIN", str(script)):
            result = engines.codex_exec("hello", cwd=self.repo, timeout=30)
        self.assertEqual(result["returncode"], 3)
        self.assertEqual(result["error"], "codex: not signed in")

    def test_turns_without_send_now_close_the_engine_input(self):
        """A fixture that reads its whole input still answers: nothing can join a turn without a sends folder."""
        script = self.tmp / "reads-all"
        script.write_text(f"#!{sys.executable}\nimport json, sys\nsys.stdin.read()\n"
                          "print(json.dumps({'type': 'result', 'result': 'whole', 'session_id': 's'}))\n")
        script.chmod(0o755)
        with mock.patch.object(config, "CLAUDE_BIN", str(script)):
            self.assertEqual(engines.claude_print("hello", cwd=self.repo, timeout=30)["text"], "whole")
