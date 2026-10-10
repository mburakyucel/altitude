"""Approved #302 journeys through HTTP/storage/Git; only external workers are fixtures."""
import fcntl
import http.client
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, server, state as S, tasks as T


class TestL2Steering(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.engine = FakeL2()
        self.engine.install(self)
        # Requests persist normally. The test drives each daemon checkpoint explicitly.
        self.patch(server, "spawn", return_value=True)
        self.patch(server, "log")
        self.patch(server.monitor, "sessions", return_value=[])
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, body=None, status=200):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            connection.request("GET" if body is None else "POST", path,
                               body=json.dumps(body) if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = json.loads(response.read())
            self.assertEqual(response.status, status, data)
            return data
        finally:
            connection.close()

    def launch(self, engine):
        self.register(self.project, routing=[[{"engine": engine, "model": "fixture-model"}]], wip=20)
        task = T.new(self.project, f"Steer {engine}", "Keep this focused.", paths=["README.md"])
        dispatch.run(self.project, task["slug"])
        return S.load_task(self.project, task["slug"])

    def view(self, task):
        return self.request(f"/api/task/{self.project}/{task['slug']}")

    def send(self, task, text, **fields):
        return self.request("/api/l2/message", {"project": self.project, "slug": task["slug"],
                                                "text": text, **fields})["message"]

    def action(self, task, action, status=200, **fields):
        return self.request("/api/task/action", {"project": self.project, "slug": task["slug"],
                                                 "action": action, **fields}, status)

    def stop(self, task):
        self.action(task, "stop", generation=task["agent_id"])
        dispatch.run_task_operation(self.project, task["slug"])
        stopped = self.view(task)
        self.assertEqual(stopped["steering"]["state"], "stopped")
        return stopped

    def archive_during_live_status_read(self, slug):
        """Reject and archive the task after a reader resolved its live folder but before it reads status."""
        live_status = S.tasks_dir(self.project) / slug / "status.json"
        read_json = S.read_json
        archived = []

        def read(path, *args, **kwargs):
            if path == live_status and not archived:
                archived.append(True)
                T.reject(self.project, slug, "The operator ended this task.")
            return read_json(path, *args, **kwargs)

        return mock.patch.object(S, "read_json", side_effect=read), archived

    def test_poll_crossing_archive_reads_the_archived_task(self):
        task = T.new(self.project, "Archive while polling", "Keep the archived conversation readable.")
        crossing, archived = self.archive_during_live_status_read(task["slug"])
        with crossing, mock.patch.object(server, "log") as log:
            settled = self.view(task)
            self.assertFalse(any("Traceback" in str(call) for call in log.call_args_list))
        self.assertEqual(archived, [True])
        self.assertEqual(settled["state"], "rejected")
        self.assertEqual(settled["files"]["request"].strip(), "Keep the archived conversation readable.")
        self.assertTrue(any(event.get("reason") == "The operator ended this task." for event in settled["events"]))

    def test_action_response_crossing_archive_returns_the_archived_state(self):
        task = T.new(self.project, "Archive while responding", "Report the archived state.")
        crossing, archived = self.archive_during_live_status_read(task["slug"])
        # The action's own daemon runner finishes the archive while the response reads the task.
        with mock.patch.object(server, "request_daemon_task_operation", return_value={"queued": True}) as operation, \
                crossing, mock.patch.object(server, "log") as log:
            result = self.action(task, "reject", reason="The fixture exercise is complete.")
            self.assertFalse(any("Traceback" in str(call) for call in log.call_args_list))
        operation.assert_called_once_with(self.project, task["slug"], "reject", "The fixture exercise is complete.",
                                          actor=config.OPERATOR_ACTOR)
        self.assertEqual(archived, [True])
        self.assertEqual(result, {"ok": True, "state": "rejected"})
        self.assertTrue(S.task_dir(self.project, task["slug"]).is_relative_to(S.archive_dir(self.project)))

    def test_text_submission_identity_reaches_receipt_conversation_and_inbox(self):
        task = T.new(self.project, "Message identity", "Keep each accepted send visible once.")
        identity = "12345678-1234-4234-8234-123456789abc"
        with mock.patch.object(server, "require_image_capability", side_effect=AssertionError("Text needs no image capability")):
            row = self.send(task, "Inspect the sample", request_id=identity)
        self.assertEqual(row["id"], identity.replace("-", ""))
        self.assertEqual([m["id"] for m in self.view(task)["messages"]], [row["id"]])
        self.assertEqual([m["id"] for m in T.pending(self.project, task["slug"])], [row["id"]])
        for invalid in (None, "not-a-uuid", 12, {}):
            with self.subTest(identity=invalid):
                self.request("/api/l2/message", {"project": self.project, "slug": task["slug"],
                                                "text": "Invalid identity", "request_id": invalid}, status=400)
        second = self.send(task, "Inspect the sample")
        self.assertNotEqual(second["id"], row["id"])
        self.assertEqual([m["id"] for m in self.view(task)["messages"]], [row["id"], second["id"]])

    def test_missing_task_is_not_found_but_corrupt_state_still_fails(self):
        with mock.patch.object(server, "log") as log:
            self.request(f"/api/task/{self.project}/missing-task", status=404)
            self.assertFalse(any("Traceback" in str(call) for call in log.call_args_list))
            task = T.new(self.project, "Corrupt polling", "Keep failures visible.")
            S.status_path(self.project, task["slug"]).write_text("{corrupt}\n")
            result = self.request(f"/api/task/{self.project}/{task['slug']}", status=500)
            self.assertIn("corrupt JSON", result["error"])
            self.assertTrue(any("Traceback" in str(call) for call in log.call_args_list))

    def test_archive_waits_for_task_documents_and_events_snapshot(self):
        for name in ("request.md", "events.log"):
            with self.subTest(name=name):
                task = T.new(self.project, f"Archive during {name}", "Keep this request readable.")
                target = S.task_dir(self.project, task["slug"]) / name
                read_text = Path.read_text
                activity = server.transcript.activity
                attempted = threading.Event()
                errors = []

                def archive():
                    attempted.set()
                    try:
                        T.reject(self.project, task["slug"], "The operator ended this task.")
                    except Exception as exc:
                        errors.append(exc)

                writer = threading.Thread(target=archive)

                def read(path, *args, **kwargs):
                    if path == target and not writer.ident:
                        with open(config.project_dir(self.project) / ".lock") as lock:
                            with self.assertRaises(BlockingIOError):
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        writer.start()
                        self.assertTrue(attempted.wait(5))
                    return read_text(path, *args, **kwargs)

                def after_archive(project, slug):
                    writer.join(5)
                    self.assertFalse(writer.is_alive(), "activity runs after releasing the snapshot lock")
                    return activity(project, slug)

                try:
                    with mock.patch.object(Path, "read_text", new=read), \
                            mock.patch.object(server.transcript, "activity", new=after_archive), \
                            mock.patch.object(server, "log") as log:
                        snapshot = self.view(task)
                        self.assertFalse(any("Traceback" in str(call) for call in log.call_args_list))
                finally:
                    if writer.ident:
                        writer.join(5)
                self.assertTrue(attempted.is_set())
                self.assertFalse(writer.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(snapshot["state"], "queued")
                self.assertEqual(snapshot["files"]["request"].strip(), "Keep this request readable.")
                self.assertFalse(any(event.get("kind") == "state" for event in snapshot["events"]))
                settled = self.view(task)
                self.assertEqual(settled["state"], "rejected")
                self.assertEqual(settled["files"]["request"].strip(), "Keep this request readable.")
                self.assertTrue(any(event.get("reason") == "The operator ended this task." for event in settled["events"]))

    def test_missing_question_field_is_a_logged_failure_not_a_missing_task(self):
        task = T.new(self.project, "Malformed question", "Keep state errors visible.")
        task = T.block(self.project, task["slug"], "Which retry policy?", actor="l2")
        del task["questions"][0]["id"]
        S.save_task(self.project, task)
        with mock.patch.object(server, "log") as log:
            result = self.request(f"/api/task/{self.project}/{task['slug']}", status=500)
            self.assertEqual(result["error"], "'id'")
            self.assertTrue(any("KeyError: 'id'" in call.args[0] for call in log.call_args_list))

    def test_stop_holds_old_racing_and_stale_tab_sends_then_correction_keeps_session_and_edits(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                path = Path(task["worktree"]) / "README.md"
                path.write_text("Uncommitted operator work.\n")
                first = self.send(task, "Use the smaller change.")
                self.action(task, "stop", generation=task["agent_id"])
                stopping = self.view(task)
                self.assertEqual(stopping["steering"]["state"], "stopping")
                racing = self.send(task, "Sent while Stop was pending.")
                self.assertEqual(T.take_inbox(self.project, task["slug"]), [])
                self.assertEqual(dispatch.resume_due(self.project), [])
                dispatch.run_task_operation(self.project, task["slug"])
                stopped = self.view(task)
                self.assertEqual(stopped["steering"]["state"], "stopped")
                stale = self.send(task, "Ordinary send from another tab.")
                self.assertEqual(dispatch.resume(self.project, task["slug"]), {"waiting": True})
                self.assertNotIn(task["slug"], dispatch.resume_due(self.project))
                self.action(task, "resume", status=409)  # no observed Stop identity
                correction = self.send(task, "Continue with this correction.", stop_id=stopped["steering"]["stop_id"])
                self.assertEqual(self.view(task)["steering"]["state"], "resuming")
                late = []
                self.engine.on_resume = lambda: late.append(self.send(task, "Arrived after the resume claim."))
                dispatch.resume(self.project, task["slug"])
                self.engine.on_resume = None
                resumed = self.view(task)
                self.assertEqual(resumed["steering"]["state"], "running")
                self.assertEqual(resumed["session_id"], task["session_id"])
                self.assertEqual((resumed["attempt"], resumed["launch_model"]), (task["attempt"], task["launch_model"]))
                self.assertNotEqual(resumed["agent_id"], task["agent_id"])
                self.assertEqual(path.read_text(), "Uncommitted operator work.\n")
                rows = resumed["messages"]
                self.assertEqual([row["id"] for row in rows], [first["id"], racing["id"], stale["id"], correction["id"], late[0]["id"]])
                self.assertEqual([row["delivery"]["state"] for row in rows], ["delivered"] * 4 + ["queued"])
                prompt = self.engine.calls[-1]["prompt"]
                positions = [prompt.index(row["text"]) for row in rows[:-1]]
                self.assertEqual(positions, sorted(positions))
                self.assertNotIn(late[0]["text"], prompt)
                self.action(task, "stop", status=409, generation=task["agent_id"])
                self.assertEqual(self.view(task)["steering"]["state"], "running")

    def test_clean_turn_delivers_queue_but_error_or_explicit_question_does_not_auto_resume(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                self.send(task, "Turn boundary steering.")
                self.engine.workers[task["agent_id"]].update(state="done", status="exited")
                item = next(row for row in dispatch.poll(self.project) if row["task"]["slug"] == task["slug"])
                server.on_l2_finished(self.project, item)
                self.assertFalse(S.load_task(self.project, task["slug"]).get("fault"))
                dispatch.resume(self.project, task["slug"])
                resumed = self.view(task)
                self.assertEqual(resumed["messages"][0]["delivery"]["state"], "delivered")
                self.assertEqual(resumed["session_id"], task["session_id"])
                self.send(task, "Another message before a question.")
                T.block(self.project, task["slug"], "Which approach?", actor="l2", updates={"waiting_on": "l3"})
                self.engine.workers[resumed["agent_id"]].update(state="done", status="exited")
                self.assertNotIn(task["slug"], [row["task"]["slug"] for row in dispatch.poll(self.project)])
                self.assertNotIn(task["slug"], dispatch.resume_due(self.project))

                failed = self.launch(engine)
                self.send(failed, "A message cannot erase engine failure.")
                self.engine.workers[failed["agent_id"]].update(state="failed", status="exited", detail="fixture CLI failure")
                item = next(row for row in dispatch.poll(self.project) if row["task"]["slug"] == failed["slug"])
                server.on_l2_finished(self.project, item)
                self.assertEqual(S.load_task(self.project, failed["slug"])["fault"], "l2-died")
                self.assertNotIn(failed["slug"], dispatch.resume_due(self.project))
                self.assertEqual(self.view(failed)["messages"][0]["delivery"]["state"], "queued")

    def hook(self, task, event):
        """The real inbox hook, as the Claude session's settings run it."""
        env = dict(os.environ, ALTITUDE_HOME=str(config.ROOT), ALTITUDE_PROJECT=self.project, ALTITUDE_TASK=task["slug"])
        payload = {"hook_event_name": event, "session_id": task["session_id"],
                   **({"tool_name": "Bash"} if event == "PostToolUse" else {"stop_hook_active": False})}
        done = subprocess.run([sys.executable, str(config.HOOKS / "inbox.py")], input=json.dumps(payload), text=True,
                              capture_output=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout) if done.stdout else {}

    def finish_turn(self, task):
        self.engine.workers[task["agent_id"]].update(state="done", status="exited")
        item = next(row for row in dispatch.poll(self.project) if row["task"]["slug"] == task["slug"])
        server.on_l2_finished(self.project, item)
        self.assertFalse(S.load_task(self.project, task["slug"]).get("fault"))
        dispatch.resume(self.project, task["slug"])
        return self.engine.calls[-1]

    def test_operator_words_reach_a_running_claude_owner_as_its_next_user_turn(self):
        # #612: the engine's action classifier reads user turns, never hook context, as the user's intent.
        task = self.launch("claude")
        words = self.send(task, "Say macOS is supported.")
        T.message(self.project, task["slug"], "l3", "Rebase before landing.")

        context = self.hook(task, "PostToolUse")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Rebase before landing.", context)
        self.assertNotIn("Say macOS is supported.", context)
        self.assertIn(f"message id {words['id']}) waits for this session's next user turn", context)
        self.assertEqual(self.hook(task, "Stop"), {}, "the turn ends instead of holding the owner in it")
        # Coordination arriving between the stop and the resume joins the batch, as every resume prompt does.
        late = T.message(self.project, task["slug"], "l3", "Late coordination.")

        resumed = self.finish_turn(task)

        self.assertEqual((resumed["engine"], resumed["session_id"]), ("claude", task["session_id"]))
        self.assertIn(T.render_inbox([words]), resumed["prompt"])
        self.assertIn(T.render_inbox([late]), resumed["prompt"])
        self.assertNotIn("Rebase before landing.", resumed["prompt"], "hook context is not repeated as user input")
        delivered = {row["id"]: row["delivery"]["state"] for row in self.view(task)["messages"]}
        self.assertEqual(delivered[words["id"]], "delivered")
        self.assertIsNone(S.load_task(self.project, task["slug"]).get("turn_released"))

    def test_removing_the_waiting_message_after_the_turn_ends_resumes_without_a_fault(self):
        task = self.launch("claude")
        row = self.send(task, "Actually, never mind.")
        self.assertEqual(self.hook(task, "Stop"), {})
        T.remove_message(self.project, task["slug"], row["id"])

        resumed = self.finish_turn(task)

        self.assertEqual(resumed["session_id"], task["session_id"])
        self.assertNotIn("never mind", resumed["prompt"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

    def exit_turn(self, task, **worker):
        """End the current worker's turn without a report and return the fault calls it raised."""
        current = S.load_task(self.project, task["slug"])
        self.engine.workers[current["agent_id"]].update(status="exited", **{"state": "done", **worker})
        item = next(row for row in dispatch.poll(self.project) if row["task"]["slug"] == task["slug"])
        with mock.patch.object(server.incidents, "system_fault") as fault:
            server.on_l2_finished(self.project, item)
        return fault.call_args_list

    def nudges(self, task):
        return [row for row in self.view(task)["messages"] if row["role"] == "system" and row["text"] == T.NUDGE]

    def test_message_only_turn_is_nudged_once_then_a_second_one_is_a_dead_worker(self):
        # #787: a clean exit with only a message resumes the same session once before the l2-died fault.
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                self.assertEqual(self.exit_turn(task), [])
                nudged = S.load_task(self.project, task["slug"])
                self.assertEqual((nudged["state"], nudged.get("fault"), nudged["nudged"]), ("blocked", None, True))
                self.assertIn("only a message", nudged["blocked_reason"])
                self.assertIn(task["slug"], dispatch.resume_due(self.project))
                [row] = self.nudges(task)
                self.assertEqual((row["by"], row["resume"]), ("altitude", True))
                self.assertNotIn("delivery", row, "the nudge is a system row, not an operator message")
                self.assertEqual(T.removable_messages(self.project, task["slug"], nudged), set())

                dispatch.resume(self.project, task["slug"])
                resumed = self.engine.calls[-1]
                self.assertEqual((resumed["engine"], resumed["session_id"]), (engine, task["session_id"]))
                self.assertEqual(resumed["prompt"], T.render_inbox([row]))
                self.assertTrue(resumed["prompt"].startswith("Resumed by Altitude"))
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

                faults = self.exit_turn(task)
                self.assertEqual([call.args[0] for call in faults], ["l2-died"])
                dead = S.load_task(self.project, task["slug"])
                self.assertEqual((dead["state"], dead.get("resume_after"), dead.get("nudged")), ("blocked", None, None))
                self.assertNotIn(task["slug"], dispatch.resume_due(self.project))
                self.assertEqual(len(self.nudges(task)), 1)

    def test_unclean_exit_faults_without_a_nudge(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                faults = self.exit_turn(task, state="failed", detail="fixture CLI failure")
                self.assertEqual([call.args[0] for call in faults], ["l2-died"])
                self.assertEqual(S.load_task(self.project, task["slug"]).get("resume_after"), None)
                self.assertEqual(self.nudges(task), [])

    def test_queued_message_takes_precedence_over_the_nudge(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                words = self.send(task, "Keep the old format.")
                self.assertEqual(self.exit_turn(task), [])
                self.assertIsNone(S.load_task(self.project, task["slug"]).get("nudged"))
                dispatch.resume(self.project, task["slug"])
                self.assertEqual(self.engine.calls[-1]["prompt"], T.render_inbox([words]))
                self.assertEqual(self.nudges(task), [])

                # After a nudge, steering continues the session but does not earn a second nudge.
                self.assertEqual(self.exit_turn(task), [])
                dispatch.resume(self.project, task["slug"])
                later = self.send(task, "Also keep the old tests.")
                self.assertEqual(self.exit_turn(task), [])
                self.assertTrue(S.load_task(self.project, task["slug"])["nudged"])
                dispatch.resume(self.project, task["slug"])
                self.assertEqual(self.engine.calls[-1]["prompt"], T.render_inbox([later]))
                self.assertEqual([call.args[0] for call in self.exit_turn(task)], ["l2-died"])
                self.assertEqual(len(self.nudges(task)), 1)

    def test_owner_block_or_report_after_the_nudge_clears_it(self):
        for engine in config.ENGINES:
            for finish in ("block", "report"):
                with self.subTest(engine=engine, finish=finish):
                    task = self.launch(engine)
                    self.assertEqual(self.exit_turn(task), [])
                    dispatch.resume(self.project, task["slug"])
                    if finish == "block":
                        T.block(self.project, task["slug"], "Which approach?", actor="l2", updates={"waiting_on": "l3"})
                        T.message(self.project, task["slug"], "l3", "Take the smaller approach.")
                        dispatch.resume(self.project, task["slug"])
                    else:
                        T.report(self.project, task["slug"], {"verdict": "ok", "prs": []})
                        T.continue_report(self.project, S.load_task(self.project, task["slug"]), actor="l3",
                                          reason="Continue the delivery", check_pr=False)
                        dispatch.resume(self.project, task["slug"])
                    self.assertIsNone(S.load_task(self.project, task["slug"]).get("nudged"))
                    self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
                    self.assertEqual(self.exit_turn(task), [], "a fresh message-only turn is nudged again")
                    self.assertEqual(len(self.nudges(task)), 2)
                    dispatch.resume(self.project, task["slug"])

    def test_stop_failure_never_confirms_and_resume_failure_restores_exact_queue(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                row = self.send(task, "Keep this exactly once.")
                self.action(task, "stop", generation=task["agent_id"])
                with mock.patch.object(engines, "stop_l2_worker", side_effect=RuntimeError("fixture termination failed")):
                    with self.assertRaisesRegex(RuntimeError, "termination failed"):
                        dispatch.run_task_operation(self.project, task["slug"])
                failed = self.view(task)
                self.assertEqual(failed["steering"]["state"], "stop_unconfirmed")
                self.action(task, "resume", status=409, stop_id=failed["steering"]["stop_id"])
                self.send(task, "Correction before confirmed stop.", stop_id=failed["steering"]["stop_id"])
                self.assertNotIn(task["slug"], dispatch.resume_due(self.project))
                # Explicit trusted retry can obtain the missing termination evidence.
                dispatch.stop(self.project, task["slug"])
                stopped = self.view(task)
                self.action(task, "resume", stop_id=stopped["steering"]["stop_id"])
                self.engine.outcomes.append(RuntimeError("fixture resume input failed"))
                with self.assertRaises(dispatch.ResumeFailure):
                    dispatch.run_task_operation(self.project, task["slug"])
                failed_resume = self.view(task)
                self.assertEqual(failed_resume["steering"]["state"], "stopped")
                self.assertEqual(failed_resume["session_id"], task["session_id"])
                # Continue sends no authored reason, so the conversation keeps only the operator's two messages.
                self.assertEqual([row["delivery"]["state"] for row in failed_resume["messages"]], ["unconfirmed", "unconfirmed"])
                self.assertFalse(any(row["delivery"]["removable"] for row in failed_resume["messages"]))
                with self.assertRaises(T.TransitionError):
                    T.remove_message(self.project, task["slug"], row["id"])
                self.assertEqual(T.pending(self.project, task["slug"])[0]["id"], row["id"])

    def test_consumption_without_handoff_evidence_is_unconfirmed_and_saved_wake_failure_is_accepted(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                row = self.send(task, "Checkpoint delivery requires evidence.")
                T.take_inbox(self.project, task["slug"])
                self.assertEqual(self.view(task)["messages"][0]["delivery"]["state"], "unconfirmed")
                stopped = self.stop(task)
                with mock.patch.object(server, "request_task_resume", side_effect=RuntimeError("wake unavailable")):
                    saved = self.send(task, "Saved despite wake failure.", stop_id=stopped["steering"]["stop_id"])
                self.assertEqual(saved["delivery"]["state"], "queued")
                self.assertEqual([m["id"] for m in T.task_messages(self.project, task["slug"])], [row["id"], saved["id"]])
                self.assertIn(task["slug"], dispatch.resume_due(self.project))
                dispatch.resume(self.project, task["slug"])
                self.assertEqual(self.view(task)["messages"][-1]["delivery"]["state"], "delivered")

    def test_answer_to_open_question_during_stop_stays_held(self):
        task = self.launch(config.ENGINES[0])
        question = T.block(self.project, task["slug"], "Use the small option?", actor="l2",
                           recommendation="Use the small option")["questions"][-1]
        dispatch.stop(self.project, task["slug"])
        T.accept_question(self.project, task["slug"], question["id"], question["revision"])
        self.assertNotIn(task["slug"], dispatch.resume_due(self.project))
        self.assertEqual(T.take_inbox(self.project, task["slug"]), [])
        self.assertEqual(T.question_views(self.project, task["slug"])[0]["status"], "open")
        self.assertIsNotNone(T.question_views(self.project, task["slug"])[0]["response"])
        self.assertEqual(self.view(task)["steering"]["state"], "stopped")

    def test_status_recheck_can_confirm_termination_without_retrying_stop_or_rewriting_task(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                self.send(task, "Retain the queued correction.")
                self.action(task, "stop", generation=task["agent_id"])
                with mock.patch.object(engines, "stop_l2_worker", side_effect=RuntimeError("termination check failed")):
                    with self.assertRaises(RuntimeError):
                        dispatch.run_task_operation(self.project, task["slug"])
                before = S.load_task(self.project, task["slug"])
                with mock.patch.object(engines, "worker_termination", return_value=None), \
                        mock.patch.object(engines, "stop_l2_worker") as stop:
                    unknown = self.view(task)
                    self.assertEqual(unknown["steering"]["state"], "stop_unconfirmed")
                    self.action(task, "resume", status=409, stop_id=before["stop_id"])
                    stop.assert_not_called()
                with mock.patch.object(engines, "worker_termination", return_value=True), \
                        mock.patch.object(engines, "stop_l2_worker") as stop:
                    confirmed = self.view(task)
                    self.assertEqual(confirmed["steering"]["state"], "stopped")
                    self.assertEqual(S.load_task(self.project, task["slug"]), before)
                    self.action(task, "resume", stop_id=before["stop_id"])
                    stop.assert_not_called()
                self.engine.workers[task["agent_id"]].update(state="done", status="exited")
                dispatch.run_task_operation(self.project, task["slug"])
                resumed = self.view(task)
                self.assertEqual(resumed["session_id"], task["session_id"])
                self.assertEqual(resumed["messages"][0]["delivery"]["state"], "delivered")

    def test_old_continue_cannot_wake_a_replacement_workers_new_question(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                stopped = self.stop(task)
                self.action(task, "resume", stop_id=stopped["steering"]["stop_id"])
                dispatch.run_task_operation(self.project, task["slug"])
                blocked = T.block(self.project, task["slug"], "Choose the new approach?", actor="l2",
                                  recommendation="Keep the new scope small")
                calls = len(self.engine.calls)
                self.action(task, "resume", status=409, stop_id=stopped["steering"]["stop_id"])
                self.request("/api/l2/message", {"project": self.project, "slug": task["slug"],
                                                  "text": "Correction to an old stopped session.",
                                                  "stop_id": stopped["steering"]["stop_id"]}, status=409)
                self.assertEqual(S.load_task(self.project, task["slug"]), blocked)
                self.assertNotIn(task["slug"], dispatch.resume_due(self.project))
                self.assertEqual(len(self.engine.calls), calls)
                self.assertEqual(T.question_views(self.project, task["slug"])[-1]["status"], "open")
