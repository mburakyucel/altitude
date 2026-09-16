"""Approved #302 journeys through HTTP/storage/Git; only external workers are fixtures."""
import fcntl
import http.client
import json
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

    def test_poll_crossing_archive_is_not_a_failed_request(self):
        task = T.new(self.project, "Archive while polling", "Keep the archived conversation readable.")
        slug = task["slug"]
        live_status = S.status_path(self.project, slug)
        status_path = S.status_path
        archived = []

        def resolve(project, requested_slug):
            path = status_path(project, requested_slug)
            if path == live_status and not archived:
                archived.append(True)
                T.reject(project, requested_slug, "The operator ended this task.")
            return path

        with mock.patch.object(S, "status_path", side_effect=resolve), mock.patch.object(server, "log") as log:
            missing = self.request(f"/api/task/{self.project}/{slug}", status=404)
            self.assertEqual(missing, {"error": "Task is not available."})
            self.assertFalse(any("Traceback" in str(call) for call in log.call_args_list))
        self.assertEqual(archived, [True])
        settled = self.view(task)
        self.assertEqual(settled["state"], "rejected")
        self.assertEqual(settled["files"]["request"].strip(), "Keep the archived conversation readable.")
        self.assertTrue(any(event.get("reason") == "The operator ended this task." for event in settled["events"]))

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
