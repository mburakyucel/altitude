"""Send now reaches a running engine turn through its job's driver, without stopping the job or its command."""
import json
import os
import subprocess
import sys
import threading
import time
from unittest import mock

from tests.support import REPO, AltitudeCase
from altitude import config, engines, platform, state as S, tasks as T

FAKE = REPO / "tests" / "fake_engine.py"
TEXT = "Use the staging copy."


class TestEngineDriver(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.log, self.tool, self.sends = self.tmp / "engine.log", self.tmp / "tool", self.tmp / "worker.sends"
        for path in (self.log, self.tool):
            path.unlink(missing_ok=True)

    def drive(self, engine, *, tool=True, ask=False, task=None):
        command = [sys.executable, str(FAKE), *(["app-server"] if engine == "codex" else [])]
        spec = {"engine": engine, "command": command, "input": engines._engine_input(engine, "Start the work.", ()),
                "cwd": str(self.repo), "sends": str(self.sends), "worker_id": "worker", "task": task}
        env = {**os.environ, "FAKE_ENGINE_LOG": str(self.log), **({"FAKE_ENGINE_TOOL": str(self.tool)} if tool else {}),
               **({"FAKE_ENGINE_ASK": "1"} if ask else {})}
        proc = subprocess.Popen(engines._driver_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        proc.stdin.write(engines._driver_input(spec).encode())
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
                proc = self.drive(engine)
                self.wait_for_tool(proc)
                os.kill(int(self.tool.with_suffix(".pid").read_text()), 0)
                self.assertFalse(self.tool.with_suffix(".completed").exists())
                engines.send_into_turn(self.sends, "m1", TEXT)
                events = self.finish(proc)
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
        driver = object.__new__(engines._ClaudeDriver)
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
                "input": engines._engine_input("codex", "Start.", ()), "sends": str(self.sends),
                "cwd": str(self.repo)}
        engines.send_into_turn(self.sends, "m1", TEXT)
        env = {**os.environ, "FAKE_ENGINE_TOOL": str(self.tool), "FAKE_ENGINE_DROP_STEER_ACK": "1"}
        driver = engines._CodexDriver(spec, env)
        self.addCleanup(lambda: driver.child.poll() is None and driver.child.kill())
        self.addCleanup(driver.child.stdout.close)
        events = []
        driver.emit = events.append
        timeout = threading.Timer(5, lambda: driver.child.poll() is None and driver.child.kill())
        timeout.start()
        try:
            with mock.patch.object(engines, "SEND_ECHO_WAIT", 0.05):
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
        spec = {"engine": "codex", "command": [str(script)], "input": engines._engine_input("codex", "Start.", ()),
                "cwd": str(self.repo)}
        proc = subprocess.run(engines._driver_command(), input=engines._driver_input(spec).encode(),
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

    def test_missing_launch_input_starts_no_engine(self):
        proc = subprocess.run(engines._driver_command(), input=b"", capture_output=True, timeout=30)
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
        self.patch(engines, "_codex_session_model", return_value={"engine_model": "fake-model"})

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
