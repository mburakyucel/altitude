"""Burak's chat messages queue while L3 is busy and run at the next turn boundary, in arrival order."""
import contextlib
import io
import json
import runpy
import socket
import threading
import unittest
from unittest import mock

from tests.support import ALT, AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, incidents, l3, server, state as S, tasks as T


def cli(argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runpy.run_path(str(ALT))["main"](argv)
    return json.loads(output.getvalue())


class TestTaskMessageResumeQueue(AltitudeCase):
    """I-20260904-062512: coordinator messages request, but never perform, a privileged resume."""

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        task = T.new(self.project, "Blocked conversation", "Continue it.")
        self.slug = task["slug"]
        self.worktree = self.repo / ".claude" / "worktrees" / self.slug
        self.worktree.mkdir(parents=True)
        task.update({"state": "blocked", "attempt": 1, "session_id": "thread-old", "agent_id": "agent-old",
                     "l2_engine": "codex", "worktree": str(self.worktree), "blocked_reason": "Need an answer.",
                     "waiting_on": "l3"})
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "state", frm="running", to="blocked", by="l2", reason="Need an answer.")
        self.setenv("ALTITUDE_ACTOR", "l3")

    def test_fault_waiting_messages_stay_quiet_and_operator_discussion_still_wakes(self):
        incidents.system_fault("fixture-fault", "Local operation still fails", project=self.project, task=self.slug)
        blocked = S.load_task(self.project, self.slug)
        messages = []
        for text in ("The receiving coordinator was notified.", "The upstream issue is closed.",
                     "An unrelated restart has finished; the local operation still fails."):
            row = cli(["--project", self.project, "task", "message", self.slug, text, "--summary", "Waiting status"])
            self.assertIs(row["wake"], False)
            messages.append(row)
            self.assertEqual(dispatch.resume_due(self.project), [])
            self.assertEqual(S.load_task(self.project, self.slug), blocked)
        self.assertEqual(T.pending(self.project, self.slug), messages)
        self.assertEqual(T.task_messages(self.project, self.slug), messages)
        self.assertFalse(any(event["kind"] == "resume-requested"
                             for event in S.read_events(self.project, self.slug)))

        human = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Explain what remains blocked.")
        self.assertEqual(dispatch.resume_due(self.project), [self.slug])
        self.assertEqual(S.load_task(self.project, self.slug)["resume_request"], human["id"])
        self.assertEqual(T.pending(self.project, self.slug), [*messages, human])

    def message_delivery(self, row):
        return next(message["delivery"] for message in T.message_views(self.project, self.slug, [])
                    if message["id"] == row["id"])

    def test_remove_middle_message_preserves_batch_order_and_later_arrivals(self):
        first, removed, last = [T.message(self.project, self.slug, "burak", text)
                                for text in ("first", "remove this", "last")]
        T.remove_message(self.project, self.slug, removed["id"])
        claim = T.claim_resume(self.project, self.slug)
        self.assertEqual([row["id"] for row in claim["messages"]], [first["id"], last["id"]])
        prompt = T.render_inbox(claim["messages"])
        self.assertNotIn(removed["text"], prompt)
        self.assertLess(prompt.index(first["id"]), prompt.index(last["id"]))
        late = T.message(self.project, self.slug, "burak", "later arrival")
        self.assertEqual(T.pending(self.project, self.slug), [late])
        self.assertTrue(self.message_delivery(late)["removable"])
        for row in (first, last):
            self.assertEqual(self.message_delivery(row)["state"], "sending")
            self.assertFalse(self.message_delivery(row)["removable"])
            with self.assertRaises(T.TransitionError):
                T.remove_message(self.project, self.slug, row["id"])
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launching")
        T.resume(self.project, self.slug, agent_id="replacement", session_id="thread-old",
                 expected_claim=claim["id"], input_delivered=True)
        self.assertEqual(T.take_inbox(self.project, self.slug), [late])
        self.assertEqual(T.take_inbox(self.project, self.slug), [])
        original = next(row for row in T.task_messages(self.project, self.slug) if row["id"] == removed["id"])
        self.assertEqual(original["text"], removed["text"])
        self.assertTrue(original["removed_at"])
        self.assertEqual(self.message_delivery(removed)["state"], "removed")

    def test_hook_and_remove_serialize_in_both_orders_without_losing_siblings(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "running"
        S.save_task(self.project, task)
        for first_operation in ("hook", "remove"):
            with self.subTest(first=first_operation):
                selected, sibling = [T.message(self.project, self.slug, "burak", text)
                                     for text in ("selected", "sibling")]
                locked, release, second_started = threading.Event(), threading.Event(), threading.Event()
                outcomes = {}
                original_pending = T._pending_rows

                def pause_with_lock(*args):
                    if threading.current_thread().name == "first-operation" and not locked.is_set():
                        locked.set()
                        release.wait(5)
                    return original_pending(*args)

                def run(operation, second=False):
                    if second:
                        second_started.set()
                    try:
                        outcomes[operation] = (T.take_inbox(self.project, self.slug) if operation == "hook"
                                               else T.remove_message(self.project, self.slug, selected["id"]))
                    except Exception as exc:
                        outcomes[operation] = exc

                second_operation = "remove" if first_operation == "hook" else "hook"
                with mock.patch.object(T, "_pending_rows", side_effect=pause_with_lock):
                    first = threading.Thread(target=run, args=(first_operation,), name="first-operation")
                    second = threading.Thread(target=run, args=(second_operation, True))
                    first.start()
                    try:
                        self.assertTrue(locked.wait(5))
                        second.start()
                        self.assertTrue(second_started.wait(5))
                    finally:
                        release.set()
                        first.join(5)
                        if second.ident:
                            second.join(5)
                self.assertFalse(first.is_alive() or second.is_alive())
                expected = [selected, sibling] if first_operation == "hook" else [sibling]
                self.assertEqual(outcomes["hook"], expected)
                if first_operation == "hook":
                    self.assertIsInstance(outcomes["remove"], T.TransitionError)
                else:
                    self.assertIsNone(outcomes["remove"])
                self.assertEqual(T.pending(self.project, self.slug), [])

    def test_failed_claim_removal_depends_on_whether_handoff_started(self):
        for phase in ("claimed", "launching", "launched"):
            with self.subTest(phase=phase):
                row = T.message(self.project, self.slug, "burak", phase)
                claim = T.claim_resume(self.project, self.slug)
                T.update_resume_claim(self.project, self.slug, claim["id"], phase=phase)
                with self.assertRaises(T.TransitionError):
                    T.remove_message(self.project, self.slug, row["id"])
                T.release_resume_claim(self.project, self.slug, claim["id"], consume_request=False)
                delivery = self.message_delivery(row)
                self.assertEqual(delivery["state"], "queued" if phase == "claimed" else "unconfirmed")
                self.assertEqual(delivery["removable"], phase == "claimed")
                if phase == "claimed":
                    T.remove_message(self.project, self.slug, row["id"])
                else:
                    with self.assertRaises(T.TransitionError):
                        T.remove_message(self.project, self.slug, row["id"])
                late = T.message(self.project, self.slug, "burak", "new after failure")
                self.assertTrue(self.message_delivery(late)["removable"])
                T.remove_message(self.project, self.slug, late["id"])
                T.take_inbox(self.project, self.slug)

    def test_stop_held_removal_preserves_worker_and_stop_authority(self):
        task = S.load_task(self.project, self.slug)
        task["stop_id"] = "stop-generation"
        S.save_task(self.project, task)
        selected, sibling = [T.message(self.project, self.slug, "burak", text) for text in ("selected", "sibling")]
        before = S.load_task(self.project, self.slug)
        T.remove_message(self.project, self.slug, selected["id"])
        after = S.load_task(self.project, self.slug)
        for key in ("stop_id", "state", "agent_id", "session_id", "resume_after", "resume_request"):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(T.take_inbox(self.project, self.slug), [])
        self.assertEqual(T.pending(self.project, self.slug), [sibling])
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_only_ordinary_operator_messages_are_removable_and_authority_is_retained(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "running"
        S.save_task(self.project, task)
        question = T.block(self.project, self.slug, "Which approach?", actor="l2", updates={"waiting_on": "burak"},
                           recommendation="Use the existing path", recommendation_label="Use existing")["questions"][-1]
        original = T.message(self.project, self.slug, "burak", "Use the existing path")
        T.resolve_question(self.project, self.slug, question["id"], question["revision"], original["id"],
                           expected_attempt=1, disposition="answered", reason="The operator selected the existing path")
        coordinator = T.message(self.project, self.slug, "l3", "Keep this coordination record")
        control = T.notify(self.project, self.slug, "Keep this control record", by="terminal",
                           attempt=S.load_task(self.project, self.slug)["attempt"])
        forged = T.message(self.project, self.slug, "burak", "Not an original operator message", by="l3")
        for row in (original, coordinator, control, forged):
            with self.subTest(row=row["text"]), self.assertRaises(T.TransitionError):
                T.remove_message(self.project, self.slug, row["id"])
        T.resume(self.project, self.slug)
        question = T.block(self.project, self.slug, "Continue?", actor="l2", updates={"waiting_on": "burak"},
                           recommendation="Continue", recommendation_label="Continue")["questions"][-1]
        T.accept_question(self.project, self.slug, question["id"], question["revision"])
        acceptance = S.load_task(self.project, self.slug)["questions"][-1]["acceptance_message"]
        with self.assertRaises(T.TransitionError):
            T.remove_message(self.project, self.slug, acceptance["id"])
        self.assertIn(acceptance["id"], [row["id"] for row in T.pending(self.project, self.slug)])

    def test_removed_original_message_cannot_resolve_question_or_cross_task_boundary(self):
        row = T.message(self.project, self.slug, "burak", "Withdraw this suggestion")
        question = T.question_views(self.project, self.slug)[-1]
        other = T.new(self.project, "Other owner", "Independent conversation")
        with self.assertRaises(T.TransitionError):
            T.remove_message(self.project, other["slug"], row["id"])
        self.assertIn(row["id"], [message["id"] for message in T.pending(self.project, self.slug)])
        T.remove_message(self.project, self.slug, row["id"])
        with self.assertRaisesRegex(T.TransitionError, "original message"):
            T.resolve_question(self.project, self.slug, question["id"], question["revision"], row["id"],
                               expected_attempt=1, disposition="answered", reason="Should not be authorized")
        self.assertEqual(T.question_views(self.project, self.slug)[-1]["status"], "open")
        self.assertEqual(next(message["text"] for message in T.task_messages(self.project, self.slug)
                              if message["id"] == row["id"]), row["text"])
        formatters = runpy.run_path(str(ALT))
        rendered = formatters["_messages_text"](T.task_messages(self.project, self.slug))
        self.assertIn("[Removed before delivery] " + row["text"], rendered)
        result = l3.search(self.project, row["text"])
        found = next(context for match in result["results"] for context in match["context"]
                     if context["text"] == row["text"])
        self.assertTrue(found["removed_at"])
        self.assertIn("[Removed before delivery]\n" + row["text"], formatters["_search_text"](result))

    def test_nonwaking_message_stays_quiet_after_a_later_operational_block(self):
        task = S.load_task(self.project, self.slug)
        task.update(state="running", blocked_reason=None)
        task.pop("waiting_on", None)
        S.save_task(self.project, task)
        row = T.message(self.project, self.slug, "l3", "Waiting status for the next checkpoint.", wake_blocked=False)
        self.assertIs(row["wake"], False)
        T.block(self.project, self.slug, "Worker stopped at its checkpoint", expected_state="running")
        self.assertEqual(T.pending(self.project, self.slug), [row])
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertNotIn("resume_after", S.load_task(self.project, self.slug))

    def test_verified_fault_resume_keeps_the_original_session_model_attempt_and_hold(self):
        make_repo(self.repo)
        self.quiet_engines()
        worker = FakeL2()
        worker.install(self)
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                queued = T.new(self.project, "Recover original session", "Continue after the local cause is repaired.",
                               engine=engine, model="fixture-original-model", paths=["README.md"],
                               hold_merge="Operator review remains required")
                slug = queued["slug"]
                dispatch.run(self.project, slug)
                original = S.load_task(self.project, slug)
                incidents.system_fault("fixture-fault", "Local operation failed", project=self.project, task=slug)
                message = T.message(self.project, slug, "l3", "Public delivery is available; local verification is pending.")
                self.assertEqual(dispatch.resume_due(self.project), [])
                reason = "Verified public delivery and observed the affected local operation succeed."
                request = dispatch.request_task_operation(self.project, slug, "resume", reason, actor="l3")
                self.assertEqual(request["request"]["reason"], reason)
                calls = len(worker.calls)
                with mock.patch.object(dispatch, "wip_hold", return_value="project at WIP cap"):
                    held = dispatch.run_task_operation(self.project, slug)
                    self.assertEqual(held["request"]["status"], "executing")
                    self.assertEqual(S.load_task(self.project, slug)["blocked_reason"],
                                     "system fault [fixture-fault]: Local operation failed")
                    dispatch.run_task_operation(self.project, slug)
                    self.assertEqual(sum(event["kind"] == "resume-held" for event in S.read_events(self.project, slug)), 1)
                self.assertIsNone(incidents.system_fault("fixture-fault", "Local operation failed",
                                                         project=self.project, task=slug))
                self.assertEqual(S.load_task(self.project, slug)["block_id"], request["request"]["block_id"],
                                 "rereading the unchanged saved blocker preserves the verified recovery request")
                self.register(self.project, routing=[[{"engine": engine, "model": "fixture-new-default"}]])
                result = dispatch.run_task_operation(self.project, slug)
                self.assertEqual(result["request"]["status"], "done")
                resumed = S.load_task(self.project, slug)
                self.assertEqual(resumed["state"], "running")
                for key in ("l2_engine", "session_id", "launch_model", "attempt", "worktree", "branch", "hold_merge"):
                    self.assertEqual(resumed[key], original[key], key)
                self.assertNotEqual(resumed["agent_id"], original["agent_id"])
                self.assertFalse(resumed.get("fault"))
                self.assertEqual(len(worker.calls), calls + 1)
                self.assertEqual((worker.calls[-1]["engine"], worker.calls[-1]["session_id"], worker.calls[-1]["model"]),
                                 (engine, original["session_id"], "fixture-original-model"))
                self.assertIn(message["text"], worker.calls[-1]["prompt"])
                self.assertEqual(T.pending(self.project, slug), [])
                self.assertEqual(dispatch.run_task_operation(self.project, slug)["request"]["id"], request["request"]["id"])
                self.assertEqual(len(worker.calls), calls + 1, "a repeated daemon receipt does not relaunch")

    def test_changed_fault_supersedes_a_queued_recovery_request(self):
        incidents.system_fault("fixture-fault", "First observed cause", project=self.project, task=self.slug)
        requested = dispatch.request_task_operation(self.project, self.slug, "resume", "First cause verified repaired", actor="l3")
        incidents.system_fault("fixture-fault", "Changed local cause", project=self.project, task=self.slug)
        changed = S.load_task(self.project, self.slug)
        self.assertNotEqual(changed["block_id"], requested["request"]["block_id"])
        with mock.patch.object(engines, "resume_l2") as launch:
            result = dispatch.run_task_operation(self.project, self.slug)
        launch.assert_not_called()
        self.assertEqual(result["request"]["status"], "refused")
        self.assertIn("block changed", result["request"]["note"])
        self.assertEqual(S.load_task(self.project, self.slug)["blocked_reason"], "system fault [fixture-fault]: Changed local cause")
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_changed_fault_discards_a_launched_claim_without_replacing_the_original_session(self):
        incidents.system_fault("fixture-fault", "First observed cause", project=self.project, task=self.slug)
        message = T.message(self.project, self.slug, "l3", "Waiting on a verified repair.")
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", owner_process={**claim["owner_process"], "pid": 99999999},
                              worker={"id": "unbound-replacement", "sessionId": "thread-old"})
        incidents.system_fault("fixture-fault", "Changed local cause", project=self.project, task=self.slug)
        with mock.patch.object(engines, "resume_l2") as launch, mock.patch.object(engines, "stop_l2_worker") as stop:
            with self.assertRaisesRegex(dispatch.ResumeFailure, "no longer current"):
                dispatch.resume(self.project, self.slug)
        launch.assert_not_called()
        stop.assert_called_once_with(S.load_task(self.project, self.slug)["l2_engine"], "unbound-replacement",
                                     job_root=dispatch.l2_job_root(self.project, self.slug))
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["session_id"], task["agent_id"], task["attempt"]),
                         ("blocked", "thread-old", "agent-old", 1))
        self.assertEqual(task["blocked_reason"], "system fault [fixture-fault]: Changed local cause")
        self.assertFalse(task.get("resume_claim"))
        self.assertEqual(T.pending(self.project, self.slug), [message])
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_l3_cli_saves_the_message_when_git_metadata_is_unavailable(self):
        unavailable = PermissionError(".git/FETCH_HEAD is read-only in the coordinator")
        with mock.patch.object(dispatch.git_policy, "fetch_origin", side_effect=unavailable) as fetch, \
             mock.patch.object(incidents, "system_fault") as fault:
            row = cli(["--project", self.project, "task", "message", self.slug, "Use the existing thread.", "--summary", "Use the existing thread"])

        fetch.assert_not_called()
        fault.assert_not_called()
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["attempt"], task["session_id"]),
                         ("blocked", 1, "thread-old"))
        self.assertTrue(task["resume_after"], "the inbox append also leaves a durable daemon request")
        self.assertEqual([message["id"] for message in T.pending(self.project, self.slug)], [row["id"]])
        self.assertEqual(dispatch.resume_due(self.project), [self.slug])

    def test_a_running_worker_gets_the_message_without_a_resume_request(self):
        task = S.load_task(self.project, self.slug)
        task.update({"state": "running", "blocked_reason": None})
        task.pop("waiting_on", None)
        S.save_task(self.project, task)

        with mock.patch.object(dispatch, "resume") as resume:
            row = cli(["--project", self.project, "task", "message", self.slug, "Keep going.", "--summary", "Keep going"])

        resume.assert_not_called()
        task = S.load_task(self.project, self.slug)
        self.assertNotIn("resume_after", task)
        self.assertEqual([message["id"] for message in T.pending(self.project, self.slug)], [row["id"]])
        self.assertEqual(dispatch.resume_due(self.project), [])
        T.block(self.project, self.slug, "turn ended without a report", expected_state="running")
        self.assertEqual(dispatch.resume_due(self.project), [self.slug],
                         "a running Codex message becomes due when its one-shot turn ends")
        self.assertEqual([lease["slug"] for lease in dispatch.leases(self.project)], [self.slug],
                         "the turn-boundary inbox retains its lease before the daemon claims it")

    def test_daemon_wakes_coalesce_and_late_message_is_not_lost_or_duplicated(self):
        first = T.message(self.project, self.slug, "l3", "First answer.", by="l3")
        started, release = threading.Event(), threading.Event()
        calls = []

        def resume_l2(engine, name, session_id, prompt, **_kwargs):
            calls.append((engine, name, session_id, prompt))
            return {"returncode": 0, "stdout": "", "stderr": "",
                    "agent": {"id": "agent-new", "sessionId": session_id, "state": "working"}}

        real_bind = T.resume

        def bind(*args, **kwargs):
            started.set()
            release.wait(10)
            return real_bind(*args, **kwargs)

        key = f"resume:{self.project}:{self.slug}"
        with server._bg_guard:
            server._bg.pop(key, None)
        self.addCleanup(lambda: release.set())
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch.object(engines, "worker_live", return_value=False), \
             mock.patch.object(engines, "resume_l2", side_effect=resume_l2), \
             mock.patch.object(T, "resume", side_effect=bind), \
             mock.patch.object(incidents, "system_fault") as fault:
            self.assertTrue(server.request_task_resume(self.project, self.slug))
            self.assertTrue(started.wait(5))
            claimed = S.load_task(self.project, self.slug)
            self.assertTrue(claimed["dispatching"])
            self.assertEqual(claimed["resume_claim"]["phase"], "launched")
            self.assertEqual(claimed["resume_claim"]["worker"]["id"], "agent-new")
            self.assertEqual(T.pending(self.project, self.slug), [],
                             "the provider prompt batch leaves the hook-visible inbox before launch")
            S.write_json(config.MONITOR_DIR / dispatch.RESTART_PENDING,
                         {"at": S.now(), "files": ["altitude/server.py"]})
            self.assertIn(f"{self.project}/{self.slug}", server.restart_status()["waiting_for"],
                          "a restart cannot cut across the daemon's provider launch")
            self.assertFalse(server.request_task_resume(self.project, self.slug),
                             "the message and daemon retry share one in-flight resume")
            self.assertEqual(dispatch.resume(self.project, self.slug), {"already_resuming": True},
                             "the durable claim also fences a second daemon process after restart")
            late = T.message(self.project, self.slug, "l3", "Late answer.", by="l3")
            self.assertFalse(server.request_task_resume(self.project, self.slug),
                             "a lease-release wake cannot start a second provider turn")
            release.set()
            with server._bg_guard:
                worker = server._bg[key]
            worker.join(5)

        fault.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ("codex", f"{self.project}/{self.slug}-1", "thread-old"))
        self.assertIn("First answer.", calls[0][3])
        self.assertNotIn("Late answer.", calls[0][3])
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["attempt"], task["session_id"]),
                         ("running", 1, "thread-old"))
        self.assertEqual([message["id"] for message in T.pending(self.project, self.slug)], [late["id"]])
        self.assertEqual([message["id"] for message in T.task_messages(self.project, self.slug)],
                         [task["questions"][0]["anchor_id"], first["id"], late["id"]])
        self.assertEqual(dispatch.resume(self.project, self.slug), {"already_running": True})
        self.assertEqual(len(calls), 1, "a stale daemon wake is an idempotent no-op")

    def test_restarted_daemon_adopts_the_persisted_launched_worker_without_resuming_again(self):
        message = T.message(self.project, self.slug, "l3", "Resume once.", by="l3")
        task = S.load_task(self.project, self.slug)
        task.pop("resume_after", None)
        task.pop("resume_request", None)
        S.save_task(self.project, task)  # the running-Codex turn-boundary path has inbox only
        claim = T.claim_resume(self.project, self.slug)
        self.assertEqual([lease["slug"] for lease in dispatch.leases(self.project)], [self.slug],
                         "an inbox-only in-flight resume retains its file lease")
        worker = {"id": "agent-replacement", "sessionId": "thread-old", "state": "working"}
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", owner_process={**claim["owner_process"], "pid": 99999999},
                              worker=worker)

        self.assertEqual(dispatch.resume_due(self.project), [self.slug])
        with mock.patch.object(dispatch, "wip_hold", return_value="WIP limit"), \
             mock.patch.object(engines, "window_hold", return_value="tomorrow"), \
             mock.patch.object(engines, "resume_l2") as launch:
            result = dispatch.resume(self.project, self.slug)

        launch.assert_not_called()
        self.assertEqual(result, {"agent": worker, "recovered": True})
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["agent_id"], task["session_id"], task["attempt"]),
                         ("running", "agent-replacement", "thread-old", 1))
        self.assertNotIn("resume_claim", task)
        self.assertIsNone(task["dispatching"])
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual([row["id"] for row in T.task_messages(self.project, self.slug)],
                         [task["questions"][0]["anchor_id"], message["id"]])

    def test_recreated_pid_namespace_recovers_launched_claim_without_replaying_its_batch(self):
        message = T.message(self.project, self.slug, "l3", "Exactly one delivery", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        worker = {"id": "persisted-worker", "sessionId": "thread-old", "state": "working"}
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", worker=worker,
                              owner_process={**claim["owner_process"], "namespace": "pid:[previous-container]"})
        with mock.patch.object(engines, "resume_l2") as launch:
            result = dispatch.resume(self.project, self.slug)
        launch.assert_not_called()
        self.assertTrue(result["recovered"])
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["agent_id"], task["session_id"], task["attempt"]), ("persisted-worker", "thread-old", 1))
        self.assertFalse(task.get("resume_claim"))
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual(sum(row["id"] == message["id"] for row in T.task_messages(self.project, self.slug)), 1)

        with mock.patch.object(engines, "worker", return_value=None), \
             mock.patch.object(engines, "resume_l2") as launch, \
             mock.patch.object(server, "request_task_resume") as resume, \
             mock.patch.object(incidents, "system_fault") as fault:
            dead = next(item for item in dispatch.poll(self.project) if item["task"]["slug"] == self.slug)
            self.assertTrue(dead["died"])
            server._on_l2_finished(self.project, dead)
            blocked = S.load_task(self.project, self.slug)
            self.assertEqual(blocked["state"], "blocked")
            self.assertIn("ended without a fresh report", blocked["blocked_reason"])
            self.assertEqual(dispatch.resume_due(self.project), [])
            self.assertEqual(sum(row["id"] == message["id"] for row in T.task_messages(self.project, self.slug)), 1)
            launch.assert_not_called()
            resume.assert_not_called()
            fault.assert_called_once()

    def test_restart_with_ambiguous_provider_launch_fails_closed_without_a_duplicate(self):
        message = T.message(self.project, self.slug, "l3", "Do not deliver me twice.", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launching", owner_process={**claim["owner_process"], "pid": 99999999})

        with mock.patch.object(engines, "resume_l2") as launch, \
             mock.patch.object(incidents, "system_fault") as fault:
            with self.assertRaisesRegex(dispatch.ResumeFailure, "worker ownership cannot be proven"):
                dispatch.resume(self.project, self.slug)

        launch.assert_not_called()
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "l2-resume-recovery")
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "blocked")
        self.assertNotIn("resume_claim", task)
        self.assertIsNone(task["dispatching"])
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)], [message["id"]])
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_new_question_supersedes_resume_before_launch_or_binding(self):
        for index, checkpoint in enumerate(("launching", "launched", "binding"), 1):
            with self.subTest(checkpoint=checkpoint):
                T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
                original_update, original_bind = T.update_resume_claim, T.resume

                def update(*args, **kwargs):
                    if kwargs.get("phase") == checkpoint:
                        T.escalate(self.project, self.slug, "A newer scope decision?")
                    return original_update(*args, **kwargs)

                def bind(*args, **kwargs):
                    if checkpoint == "binding":
                        T.escalate(self.project, self.slug, "A newer scope decision?")
                    return original_bind(*args, **kwargs)

                with mock.patch.object(dispatch, "wip_hold", return_value=None), \
                     mock.patch.object(engines, "window_hold", return_value=None), \
                     mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
                     mock.patch.object(dispatch, "_validate_task_worktree"), \
                     mock.patch.object(engines, "worker_live", return_value=False), \
                     mock.patch.object(engines, "resume_l2", return_value={"returncode": 0,
                         "agent": {"id": "replacement", "sessionId": "thread-old"}}) as launch, \
                     mock.patch.object(engines, "stop_l2_worker") as stop, \
                     mock.patch.object(T, "update_resume_claim", side_effect=update), \
                     mock.patch.object(T, "resume", side_effect=bind), \
                     mock.patch.object(incidents, "system_fault") as fault:
                    with self.assertRaisesRegex(T.TransitionError, "no longer current"):
                        dispatch.resume(self.project, self.slug)
                fault.assert_not_called()
                self.assertEqual(launch.call_count, int(checkpoint != "launching"))
                self.assertEqual(stop.call_count, int(checkpoint != "launching"))
                task = S.load_task(self.project, self.slug)
                self.assertEqual((task["state"], task["waiting_on"], task["blocked_reason"], task["agent_id"]),
                                 ("blocked", "burak", "A newer scope decision?", "agent-old"))
                self.assertFalse(task.get("resume_claim"))
                self.assertFalse(task.get("dispatching"))
                self.assertEqual(dispatch.resume_due(self.project), [])
                rows = T.pending(self.project, self.slug)
                self.assertEqual([row["text"] for row in rows if row.get("wake", True)], ["Earlier steering"] * index)
                [handoff] = [row for row in rows if row.get("wake") is False]
                self.assertEqual(handoff["text"], "A newer scope decision?")
                self.assertEqual(handoff["question_id"], task["questions"][-1]["id"])

    def test_restart_discards_a_launched_claim_superseded_by_a_new_question(self):
        earlier = T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", owner_process={**claim["owner_process"], "pid": 99999999},
                              worker={"id": "replacement", "sessionId": "thread-old"})
        T.escalate(self.project, self.slug, "A newer scope decision?")
        with mock.patch.object(engines, "resume_l2") as launch, mock.patch.object(engines, "stop_l2_worker") as stop:
            with self.assertRaisesRegex(dispatch.ResumeFailure, "no longer current"):
                dispatch.resume(self.project, self.slug)
        launch.assert_not_called()
        stop.assert_called_once_with("codex", "replacement", job_root=dispatch.l2_job_root(self.project, self.slug))
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["blocked_reason"]), ("blocked", "A newer scope decision?"))
        self.assertFalse(task.get("resume_claim"))
        rows = T.pending(self.project, self.slug)
        self.assertEqual([row for row in rows if row.get("wake", True)], [earlier])
        [handoff] = [row for row in rows if row.get("wake") is False]
        self.assertEqual(handoff["text"], "A newer scope decision?")
        self.assertEqual(handoff["question_id"], task["questions"][-1]["id"])
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_background_resume_cancellation_preserves_the_question(self):
        for checkpoint in ("claim", "hold", "provenance", "binding"):
            with self.subTest(checkpoint=checkpoint):
                T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
                original_bind = T.resume

                def wip(*args):
                    if checkpoint in ("claim", "hold"):
                        T.escalate(self.project, self.slug, "New question")
                    return "WIP limit" if checkpoint == "hold" else None

                def validate(*args, **kwargs):
                    if checkpoint == "provenance":
                        T.escalate(self.project, self.slug, "New question")
                        raise dispatch.git_policy.GitPolicyError("Fixture provenance failure")

                def bind(*args, **kwargs):
                    T.escalate(self.project, self.slug, "New question")
                    return original_bind(*args, **kwargs)

                with mock.patch.object(dispatch, "wip_hold", side_effect=wip), \
                     mock.patch.object(engines, "window_hold", return_value=None), \
                     mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
                     mock.patch.object(dispatch, "_validate_task_worktree", side_effect=validate), \
                     mock.patch.object(engines, "worker_live", return_value=False), \
                     mock.patch.object(engines, "resume_l2", return_value={"returncode": 0,
                         "agent": {"id": "replacement", "sessionId": "thread-old"}}), \
                     mock.patch.object(engines, "stop_l2_worker"), \
                     mock.patch.object(T, "resume", side_effect=bind), \
                     mock.patch.object(server, "log"), \
                     mock.patch.object(incidents, "system_fault", wraps=incidents.system_fault) as fault:
                    self.assertTrue(server.request_task_resume(self.project, self.slug, due=False))
                    with server._bg_guard:
                        worker = server._bg[f"resume:{self.project}:{self.slug}"]
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                self.assertEqual([call.args[0] for call in fault.call_args_list],
                                 ["task-git-provenance"] if checkpoint == "provenance" else [])
                task = S.load_task(self.project, self.slug)
                self.assertEqual((task["state"], task["waiting_on"], task["blocked_reason"]),
                                 ("blocked", "burak", "New question"))
                self.assertFalse(task.get("resume_claim"))
                self.assertFalse(task.get("fault"))
                self.assertEqual(dispatch.resume_due(self.project), [])

    def test_ambiguous_recovery_records_fault_without_replacing_newer_question(self):
        T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launching", owner_process={**claim["owner_process"], "pid": 99999999})
        T.escalate(self.project, self.slug, "New question")
        with mock.patch.object(engines, "resume_l2") as launch:
            with self.assertRaisesRegex(dispatch.ResumeFailure, "worker ownership cannot be proven"):
                dispatch.resume(self.project, self.slug)
        launch.assert_not_called()
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["waiting_on"], task["blocked_reason"]), ("burak", "New question"))
        self.assertFalse(task.get("fault"))
        self.assertFalse(task.get("resume_claim"))
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertIn("l2-resume-recovery", incidents.FAULTS.read_text())

    def test_stale_recovery_stop_failure_keeps_wait_and_records_uncertainty(self):
        T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", owner_process={**claim["owner_process"], "pid": 99999999},
                              worker={"id": "replacement", "sessionId": "thread-old"})
        T.escalate(self.project, self.slug, "New question")
        with mock.patch.object(engines, "stop_l2_worker", side_effect=OSError("Fixture stop failed")):
            with self.assertRaises(dispatch.ResumeFailure):
                dispatch.resume(self.project, self.slug)
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["waiting_on"], task["blocked_reason"]), ("burak", "New question"))
        self.assertFalse(task.get("resume_claim"))
        self.assertFalse(task.get("dispatching"))
        self.assertFalse(task.get("fault"))
        self.assertIn("Fixture stop failed", incidents.FAULTS.read_text())

    def test_explicit_resume_request_cannot_answer_a_later_question(self):
        first = dispatch.request_task_operation(self.project, self.slug, "resume", "Continue", actor="l3")
        T.escalate(self.project, self.slug, "New question")
        answer = T.message(self.project, self.slug, "burak", "New answer")
        with mock.patch.object(engines, "resume_l2") as launch:
            dispatch.run_task_operation(self.project, self.slug)
        launch.assert_not_called()
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["daemon_request"]["status"], "refused")
        self.assertEqual((task["waiting_on"], task["blocked_reason"]), ("burak", "New question"))
        self.assertEqual(task["resume_request"], answer["id"])
        self.assertTrue(task.get("resume_after"))
        second = dispatch.request_task_operation(self.project, self.slug, "resume", "Continue", actor="l3")
        self.assertFalse(second["idempotent"])
        self.assertNotEqual(first["request"]["id"], second["request"]["id"])

    def test_late_failure_cannot_consume_a_new_answer_or_retag_the_new_question(self):
        T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        original_release = T.release_resume_claim

        def release(*args, **kwargs):
            T.escalate(self.project, self.slug, "New question")
            T.message(self.project, self.slug, "burak", "New answer")
            return original_release(*args, **kwargs)

        with mock.patch.object(T, "release_resume_claim", side_effect=release):
            dispatch.record_resume_failure(self.project, self.slug, claim["id"], "Fixture failure", suppress_retry=True)
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["waiting_on"], task["blocked_reason"]), ("burak", "New question"))
        self.assertFalse(task.get("fault"))
        self.assertTrue(task.get("resume_after"))
        rows = T.pending(self.project, self.slug)
        self.assertEqual([row["text"] for row in rows if row.get("wake", True)], ["Earlier steering", "New answer"])
        [handoff] = [row for row in rows if row.get("wake") is False]
        self.assertEqual(handoff["text"], "New question")
        self.assertEqual(handoff["question_id"], task["questions"][-1]["id"])
        self.assertIn("l2-resume", incidents.FAULTS.read_text())

    def test_old_resume_error_cannot_borrow_a_new_claims_block_identity(self):
        T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
        old = T.claim_resume(self.project, self.slug)
        T.escalate(self.project, self.slug, "New question")
        T.release_resume_claim(self.project, self.slug, old["id"], consume_request=False)
        T.message(self.project, self.slug, "burak", "New answer")
        new = T.claim_resume(self.project, self.slug)
        dispatch.record_resume_failure(self.project, self.slug, old["id"], RuntimeError("Earlier launch failed"))
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["waiting_on"], task["blocked_reason"]), ("burak", "New question"))
        self.assertFalse(task.get("fault"))
        self.assertEqual(task["resume_claim"]["id"], new["id"])
        self.assertIn("Earlier launch failed", incidents.FAULTS.read_text())

    def test_terminal_precondition_failure_is_reported_once_and_not_retried_each_tick(self):
        T.message(self.project, self.slug, "l3", "Resume after checking the worktree.", by="l3")
        task = S.load_task(self.project, self.slug)
        task["worktree"] = str(self.repo / "missing-worktree")
        S.save_task(self.project, task)

        with mock.patch.object(incidents, "system_fault") as fault, \
             mock.patch.object(engines, "resume_l2") as launch:
            with self.assertRaisesRegex(dispatch.ResumeFailure, "worktree missing"):
                dispatch.resume(self.project, self.slug)
            self.assertEqual(dispatch.resume_due(self.project), [])
            self.assertFalse(server.request_task_resume(self.project, self.slug),
                             "a later tick does not retry a terminal precondition fault")

        launch.assert_not_called()
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "l2-resume")

    def test_old_worker_stop_failure_is_reported_once_and_not_retried_each_tick(self):
        T.message(self.project, self.slug, "l3", "Resume after stopping the idle worker.", by="l3")
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch.object(engines, "worker_live", return_value=True), \
             mock.patch.object(engines, "stop_l2_worker", side_effect=RuntimeError("worker would not stop")), \
             mock.patch.object(incidents, "system_fault") as fault, \
             mock.patch.object(engines, "resume_l2") as launch:
            with self.assertRaisesRegex(dispatch.ResumeFailure, "worker would not stop"):
                dispatch.resume(self.project, self.slug)
            self.assertEqual(dispatch.resume_due(self.project), [])
            self.assertFalse(server.request_task_resume(self.project, self.slug))

        launch.assert_not_called()
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "l2-resume")

    def test_post_save_bookkeeping_failure_keeps_the_authoritatively_bound_worker(self):
        message = T.message(self.project, self.slug, "l3", "Resume and keep the bound worker.", by="l3")
        launched = {"returncode": 0, "stdout": "", "stderr": "",
                    "agent": {"id": "agent-new", "sessionId": "thread-old", "state": "working"}}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "window_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch.object(engines, "worker_live", return_value=False), \
             mock.patch.object(engines, "resume_l2", return_value=launched), \
             mock.patch.object(engines, "stop_l2_worker") as stop, \
             mock.patch.object(S, "append_event", side_effect=OSError("events disk unavailable")), \
             mock.patch.object(incidents, "system_fault") as fault:
            result = dispatch.resume(self.project, self.slug)

        stop.assert_not_called()
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "l2-resume-bookkeeping")
        self.assertIn("events disk unavailable", result["bookkeeping_error"])
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["agent_id"], task["session_id"]),
                         ("running", "agent-new", "thread-old"))
        self.assertNotIn("resume_claim", task)
        self.assertIsNone(task["dispatching"])
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual([row["id"] for row in T.task_messages(self.project, self.slug)],
                         [task["questions"][0]["anchor_id"], message["id"]])


class TestChatQueue(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.private_ledgers()
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.lines = []
        self.patch(server, "log", new=self.lines.append)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    # ---- helpers ------------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        raw = json.dumps(body).encode() if body is not None else b""
        head = f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        if body is not None:
            head += f"Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(head.encode() + b"\r\n" + raw)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        headers, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        return int(headers.split()[1]), payload

    def post_json(self, path: str, body: dict) -> tuple[int, dict]:
        status, payload = self.request("POST", path, body)
        return status, json.loads(payload)

    def queue_rows(self) -> list[dict]:
        return l3.queued(self.project)

    def chat_view(self) -> dict:
        return json.loads(self.request("GET", f"/api/chat/{self.project}")[1])

    @staticmethod
    def claude_result(text: str = "done") -> dict:
        return {"text": text, "session_id": "s1", "context_tokens": 100, "cost": 0.0,
                "usage": {}, "turns": 1, "error": None, "tools": []}

    def hold_l3(self):
        """Hold the project's turn lock the way a running turn does; the returned call gives it back."""
        held, done = threading.Event(), threading.Event()

        def hold():
            with l3.lock(self.project):
                held.set()
                done.wait(10)
        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.addCleanup(done.set)
        self.assertTrue(held.wait(5))

        def release():
            done.set()
            thread.join(5)
        return release

    # ---- the endpoint -------------------------------------------------------

    def test_a_message_sent_while_l3_is_busy_is_queued_with_its_position(self):
        self.hold_l3()
        status, first = self.post_json("/api/chat", {"project": self.project, "text": "first"})
        self.assertEqual(status, 200, first)
        self.assertEqual((first["queued"]["text"], first["queued"]["position"]), ("first", 1))
        status, second = self.post_json("/api/chat", {"project": self.project, "text": "second"})
        self.assertEqual((status, second["queued"]["position"]), (200, 2))

        rows = self.queue_rows()
        self.assertEqual([(r["text"], r["trigger"], r["role"]) for r in rows],
                         [("first", "chat", "burak"), ("second", "chat", "burak")])
        view = self.chat_view()
        self.assertEqual(view["queued"], rows)
        self.assertIsNone(view["active"], "a bare busy lock is not an L3 turn the Chat UI may infer")

    def test_a_free_l3_still_streams_the_answer(self):
        self.patch(l3, "turn", new=lambda project, text, *, trigger, on_text, on_start=None, on_split=None: (
            on_text("two tasks."), {"session_id": "s1", "engine": "claude", "error": None,
                                    "turn_id": "turn-1"})[1])
        status, payload = self.request("POST", "/api/chat", {"project": self.project, "text": "status?"})
        self.assertEqual(status, 200)
        self.assertIn(b'"two tasks."', payload)
        self.assertIn(b'"done"', payload)
        self.assertIn(b'"turn_id": "turn-1"', payload)
        self.assertNotIn(b'"queued"', payload)
        self.assertEqual(self.queue_rows(), [])

    def test_restart_after_stream_open_returns_the_persisted_queue_receipt(self):
        original_open = server.Handler._stream_open

        def restart_after_headers(handler):
            original_open(handler)
            restarting.return_value = True

        with mock.patch.object(config, "restart_in_progress", return_value=False) as restarting, \
             mock.patch.object(server.Handler, "_stream_open", new=restart_after_headers), \
             mock.patch.object(server, "request_l3_drain"):
            status, payload = self.request("POST", "/api/chat", {
                "project": self.project, "text": "Keep this through activation"})
        self.assertEqual(status, 200)
        lines = [json.loads(line) for line in payload.decode().splitlines() if line.startswith("{")]
        self.assertEqual(len(lines), 1)
        receipt = lines[0]["queued"]
        waiting = self.chat_view()["queued"]
        self.assertEqual([row["id"] for row in waiting], [receipt["id"]])
        self.assertEqual(receipt["position"], 1)
        with self.deliverable(), mock.patch.object(l3, "turn", return_value={"completed": True}) as turn:
            server.drain_l3_queue(self.project)
            server.drain_l3_queue(self.project)
        turn.assert_called_once_with(self.project, "Keep this through activation", trigger="chat")

    def test_busy_queue_receipt_survives_a_failed_immediate_drain_start(self):
        release = self.hold_l3()
        with mock.patch.object(server, "spawn", side_effect=RuntimeError("fixture thread unavailable")):
            status, receipt = self.post_json("/api/chat", {
                "project": self.project, "text": "Run when a worker is available"})
        self.assertEqual(status, 200, receipt)
        self.assertEqual([row["id"] for row in self.chat_view()["queued"]], [receipt["queued"]["id"]])
        self.assertTrue(any("fixture thread unavailable" in line for line in self.lines))
        release()
        with self.deliverable(), mock.patch.object(l3, "turn", return_value={"completed": True}) as turn:
            server.drain_l3_queue(self.project)
            server.drain_l3_queue(self.project)
        turn.assert_called_once_with(self.project, "Run when a worker is available", trigger="chat")

    def test_task_message_receipt_survives_a_failed_immediate_wake(self):
        task = T.new(self.project, "Message acceptance", "Keep the accepted message.")
        task.update(state="blocked", attempt=1, blocked_reason="Await a message.")
        S.save_task(self.project, task)
        with mock.patch.object(server, "request_task_resume", side_effect=RuntimeError("fixture wake unavailable")):
            status, receipt = self.post_json("/api/l2/message", {
                "project": self.project, "slug": task["slug"], "text": "Continue after the wake recovers"})
        self.assertEqual(status, 200, receipt)
        message_id = receipt["message"]["id"]
        self.assertEqual([row["id"] for row in T.task_messages(self.project, task["slug"])], [message_id])
        self.assertEqual([row["id"] for row in T.pending(self.project, task["slug"])], [message_id])
        self.assertEqual(S.load_task(self.project, task["slug"])["resume_request"], message_id)
        self.assertIn(task["slug"], dispatch.resume_due(self.project))
        self.assertTrue(any("fixture wake unavailable" in line for line in self.lines))

    def test_task_message_500_is_uncertain_when_event_write_fails_after_acceptance(self):
        task = T.new(self.project, "Uncertain task receipt", "Keep recovery honest.")
        task.update(state="running", attempt=1)
        S.save_task(self.project, task)
        with mock.patch.object(S, "append_event", side_effect=OSError("fixture event write unavailable")):
            status, response = self.post_json("/api/l2/message", {
                "project": self.project, "slug": task["slug"], "text": "Already in the inbox"})
        # #298: HTTP 500 alone cannot authorize an unsent Retry; persistence can precede the error.
        self.assertEqual(status, 500, response)
        messages = T.task_messages(self.project, task["slug"])
        self.assertEqual(len(messages), 1)
        self.assertEqual([row["id"] for row in T.pending(self.project, task["slug"])], [messages[0]["id"]])

    def test_get_chat_exposes_one_server_owned_active_turn_until_completion(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def provider(_prompt, **_kwargs):
            started.set()
            release.wait(10)
            return self.claude_result()

        result = []
        with self.deliverable(), mock.patch.object(engines, "claude_print", side_effect=provider), \
             mock.patch.object(server, "request_l3_drain"):
            worker = threading.Thread(
                target=lambda: result.append(server.server_l3_turn(
                    self.project, "private incident evidence", trigger="incident")), daemon=True)
            worker.start()
            self.assertTrue(started.wait(5))
            first = self.chat_view()["active"]
            running = self.chat_view()
            second = running["active"]
            self.assertEqual(set(first), {"id", "started_at", "trigger"})
            self.assertEqual(first, second, "the active identity changed during one turn")
            self.assertTrue(running["busy"])
            self.assertEqual(first["trigger"], "incident")
            self.assertNotIn("private incident evidence", json.dumps(first))
            release.set()
            worker.join(5)

        self.assertEqual(result[0]["text"], "done")
        final = self.chat_view()
        self.assertIsNone(final["active"])
        self.assertFalse(final["busy"])
        self.assertEqual(final["history"][-1]["turn_id"], first["id"])

    def test_queue_rows_become_one_active_folded_turn_then_terminal_history(self):
        for text in ("first", "second"):
            l3.queue_message(self.project, text, trigger="chat", role="burak")
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def provider(_prompt, **_kwargs):
            started.set()
            release.wait(10)
            return self.claude_result("both answered")

        with self.deliverable(), mock.patch.object(engines, "claude_print", side_effect=provider):
            worker = threading.Thread(target=server.drain_l3_queue, args=(self.project,), daemon=True)
            worker.start()
            self.assertTrue(started.wait(5))
            active_view = self.chat_view()
            self.assertEqual(active_view["queued"], [])
            self.assertEqual(active_view["active"]["trigger"], "chat")
            self.assertEqual(active_view["history"][-1]["text"], "first\n\nsecond")
            self.assertEqual(active_view["history"][-1]["turn_id"], active_view["active"]["id"])
            turn_id = active_view["active"]["id"]
            release.set()
            worker.join(5)

        final = self.chat_view()
        self.assertIsNone(final["active"])
        self.assertEqual((final["history"][-1]["role"], final["history"][-1]["text"]),
                         ("assistant", "both answered"))
        self.assertEqual(final["history"][-1]["turn_id"], turn_id)

    def test_queue_claim_and_active_publication_are_one_api_handoff(self):
        l3.queue_message(self.project, "start this", trigger="chat", role="burak")
        dequeued, release_handoff = threading.Event(), threading.Event()
        provider_started, release_provider = threading.Event(), threading.Event()
        view_done = threading.Event()
        self.addCleanup(release_handoff.set)
        self.addCleanup(release_provider.set)
        original_write_queue = l3._write_queue

        def pause_after_dequeue(path, rows):
            original_write_queue(path, rows)
            dequeued.set()
            release_handoff.wait(10)

        def provider(_prompt, **_kwargs):
            provider_started.set()
            release_provider.wait(10)
            return self.claude_result()

        views = []

        def load_view():
            views.append(self.chat_view())
            view_done.set()

        with self.deliverable(), mock.patch.object(l3, "_write_queue", side_effect=pause_after_dequeue), \
             mock.patch.object(engines, "claude_print", side_effect=provider):
            worker = threading.Thread(target=server.drain_l3_queue, args=(self.project,), daemon=True)
            worker.start()
            self.assertTrue(dequeued.wait(5))
            reader = threading.Thread(target=load_view, daemon=True)
            reader.start()
            try:
                self.assertFalse(view_done.wait(0.5),
                                 "GET observed the queue after removal but before active publication")
            finally:
                release_handoff.set()
            self.assertTrue(view_done.wait(5))
            self.assertEqual(views[0]["queued"], [])
            self.assertIsNotNone(views[0]["active"])
            self.assertEqual(views[0]["history"][-1]["text"], "start this")
            self.assertEqual(views[0]["history"][-1]["turn_id"], views[0]["active"]["id"])
            self.assertTrue(views[0]["busy"])
            self.assertTrue(provider_started.wait(5))
            release_provider.set()
            worker.join(5)
            reader.join(5)

        self.assertIsNone(self.chat_view()["active"])

    def test_queued_text_stays_durable_and_visible_before_turn_execution(self):
        text = "Please inspect the sample project"
        l3.queue_message(self.project, text, trigger="chat", role="burak")
        selecting, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        run_turn = l3.turn

        def paused_turn(*args, **kwargs):
            selecting.set()
            release.wait(10)
            return run_turn(*args, **kwargs)

        with self.deliverable(), mock.patch.object(l3, "turn", side_effect=paused_turn), mock.patch.object(
                engines, "claude_print", return_value=self.claude_result()) as provider:
            worker = threading.Thread(target=l3.deliver_queued, args=(self.project,), daemon=True)
            worker.start()
            try:
                self.assertTrue(selecting.wait(5))
                view = self.chat_view()
                self.assertIn(text, [row["text"] for row in view["queued"] + view["history"]],
                              "returning Chat loses the accepted bubble during the queue handoff")
                durable = l3.queued(self.project) + l3.chat_history(self.project)
                self.assertEqual(sum(row["text"] == text for row in durable), 1)
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertIsNone(l3.deliver_queued(self.project))
            provider.assert_called_once()
        view = self.chat_view()
        self.assertEqual(view["queued"], [])
        self.assertIsNone(view["active"])
        self.assertEqual([row["text"] for row in view["history"]], [text, "done"])

    def test_history_admission_failure_restores_the_ordered_queue_before_a_turn_runs(self):
        rows = [l3.queue_message(self.project, text, trigger="chat", role="burak")
                for text in ("first", "second")]
        with self.deliverable(), mock.patch.object(l3, "chat_log", side_effect=OSError("History unavailable")), \
             mock.patch.object(engines, "claude_print") as provider:
            with self.assertRaisesRegex(OSError, "History unavailable"):
                l3.deliver_queued(self.project)
            provider.assert_not_called()
        view = self.chat_view()
        self.assertEqual([row["id"] for row in view["queued"]], [row["id"] for row in rows])
        self.assertEqual(view["history"], [])
        self.assertIsNone(view["active"])
        self.assertTrue(l3.drop_queued(self.project, rows[0]["id"]))
        with self.deliverable(), mock.patch.object(
                engines, "claude_print", return_value=self.claude_result()) as provider:
            l3.deliver_queued(self.project)
            self.assertIsNone(l3.deliver_queued(self.project))
            provider.assert_called_once()
        self.assertEqual([row["text"] for row in self.chat_view()["history"]], ["second", "done"])

    def test_queued_provider_failure_retains_one_user_row_and_terminal_error(self):
        l3.queue_message(self.project, "inspect sample", trigger="chat", role="burak", slug="sample-task")
        with self.deliverable(), mock.patch.object(
                engines, "claude_print", side_effect=RuntimeError("Provider unavailable")) as provider:
            with self.assertRaisesRegex(RuntimeError, "Provider unavailable"):
                l3.deliver_queued(self.project)
            self.assertIsNone(l3.deliver_queued(self.project))
            provider.assert_called_once()
        view = self.chat_view()
        self.assertEqual(view["queued"], [])
        self.assertIsNone(view["active"])
        user, error = view["history"]
        self.assertEqual((user["role"], user["text"], error["role"]), ("user", "inspect sample", "error"))
        self.assertEqual(user["turn_id"], error["turn_id"])
        self.assertEqual((user["slug"], error["slug"]), ("sample-task", "sample-task"))

    def test_provider_exception_clears_the_active_turn(self):
        with self.deliverable(), mock.patch.object(
                engines, "claude_print", side_effect=RuntimeError("provider failed")):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                l3.turn(self.project, "hello", trigger="report-landed")
        self.assertIsNone(l3.active(self.project))
        terminal = l3.chat_history(self.project)[-1]
        self.assertEqual((terminal["role"], terminal["trigger"]), ("error", "report-landed"))
        self.assertIn("provider failed", terminal["text"])
        self.assertTrue(terminal["turn_id"])

    def test_client_disconnect_does_not_clear_or_cancel_the_active_turn(self):
        started, release, completed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def provider(_prompt, **_kwargs):
            started.set()
            release.wait(10)
            return self.claude_result("finished without the page")

        body = json.dumps({"project": self.project, "text": "hello"}).encode()
        host, port = self.httpd.server_address
        with self.deliverable(), mock.patch.object(engines, "claude_print", side_effect=provider), \
             mock.patch.object(server, "request_l3_drain", side_effect=lambda _project: completed.set()):
            with socket.create_connection((host, port), timeout=5) as sock:
                sock.sendall(b"POST /api/chat HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                             + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                self.assertIn(b"200", sock.recv(128))
                self.assertTrue(started.wait(5))
            self.assertIsNotNone(l3.active(self.project), "closing Chat canceled its active server turn")
            release.set()
            self.assertTrue(completed.wait(5))

        self.assertIsNone(l3.active(self.project))
        self.assertEqual(l3.chat_history(self.project)[-1]["text"], "finished without the page")

    def test_a_queued_message_is_removed_only_before_its_turn_starts(self):
        self.hold_l3()
        _, ack = self.post_json("/api/chat", {"project": self.project, "text": "never mind"})
        message_id = ack["queued"]["id"]
        status, out = self.post_json("/api/chat/remove", {"project": self.project, "id": message_id})
        self.assertEqual((status, out["queued"]), (200, []))
        self.assertEqual(self.queue_rows(), [])

        status, out = self.post_json("/api/chat/remove", {"project": self.project, "id": message_id})
        self.assertEqual(status, 409)
        self.assertIn("already started", out["error"])

        server_row = l3.queue_message(self.project, "report landed", trigger="report-landed")
        status, _ = self.post_json("/api/chat/remove", {"project": self.project, "id": server_row["id"]})
        self.assertEqual(status, 409, "Burak may take back chat, not queued server work")
        self.assertEqual([row["id"] for row in self.queue_rows()], [server_row["id"]])

    # ---- draining -----------------------------------------------------------

    def deliverable(self):
        return mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "test"})

    @contextlib.contextmanager
    def native_chat_turn(self):
        with l3._active_turn(self.project, "chat") as turn:
            turn["sends"] = l3._sends_root(self.project) / turn["id"]
            yield turn

    @staticmethod
    def driver_settles(options, outcome, text):
        """What the engine driver does with each Send now drop at this point of the turn (`engines._Driver`): claim it
        by renaming, write it to the engine, then name the outcome and report it with the reply streamed so far.
        `writing` is a driver that ended mid-write; None leaves the drop unclaimed, as a turn that ends first does."""
        for drop in sorted(options["sends"].glob("*.json")):
            if outcome is None:
                continue
            message = json.loads(drop.read_text())
            claimed = drop.rename(drop.with_suffix(".writing"))
            if outcome != "writing":
                claimed.rename(drop.with_suffix(f".{outcome}"))
                options["on_send"](message["id"], outcome, text)

    def test_native_group_excludes_kept_chat_already_in_history(self):
        kept = l3.queue_message(self.project, "Kept instruction", trigger="chat", role=config.OPERATOR_ACTOR)
        kept.update(turn_id="kept-turn", retry_at="2099-01-01T00:00:00+00:00")
        l3._write_queue(l3.queue_path(self.project), [kept])
        l3.chat_log(self.project, "user", kept["text"], trigger="chat", turn_id=kept["turn_id"])
        selected = l3.queue_message(self.project, "New instruction", trigger="chat", role=config.OPERATOR_ACTOR)
        with self.deliverable(), self.native_chat_turn() as turn:
            l3.send_now(self.project, selected["id"])
            rows = self.queue_rows()
            self.assertEqual([row["id"] for row in rows], [selected["id"], kept["id"]])
            self.assertNotIn("sending", rows[1])
            self.assertEqual(json.loads((turn["sends"] / f"{selected['id']}.json").read_text())["text"],
                             selected["text"])
            self.assertFalse(l3.drop_queued(self.project, kept["id"]))

    def test_kept_chat_send_now_clears_delay_without_native_relogging(self):
        kept = l3.queue_message(self.project, "Kept instruction", trigger="chat", role=config.OPERATOR_ACTOR)
        kept.update(turn_id="kept-turn", retry_at="2099-01-01T00:00:00+00:00")
        l3._write_queue(l3.queue_path(self.project), [kept])
        l3.chat_log(self.project, "user", kept["text"], trigger="chat", turn_id=kept["turn_id"])
        later = l3.queue_message(self.project, "Later instruction", trigger="chat", role=config.OPERATOR_ACTOR)
        with self.deliverable(), self.native_chat_turn() as turn:
            l3.send_now(self.project, kept["id"])
            rows = self.queue_rows()
            self.assertEqual([row["id"] for row in rows], [kept["id"], later["id"]])
            self.assertTrue(rows[0]["send_now"])
            self.assertNotIn("retry_at", rows[0])
            self.assertNotIn("sending", rows[0])
            self.assertNotIn("send_now", rows[1])
            self.assertFalse(list(turn["sends"].glob("*.json")))
            self.assertEqual(sum(row.get("turn_id") == "kept-turn" for row in l3.chat_history(self.project, None)), 1)

    @contextlib.contextmanager
    def chat_turn_with_send_now(self, engine, outcome):
        """The operator starts a chat turn from the page, queues three messages and sends the middle one now while the
        turn runs; the fake engine then settles it with `outcome`. Yields what the page and the records saw, with the
        engine fakes still in place for the turns that follow."""
        reached, go = threading.Event(), threading.Event()
        self.addCleanup(go.set)
        prompts, called, stream, seen = [], [], [], {}
        l3.save_info(self.project, {"engine_last": engine, "sessions": {engine: {
            "session_id": "s1", "context_percent": 12, "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
        before = len(l3.chat_history(self.project, None))

        def provider(adapter, prompt, **options):
            prompts.append(prompt)
            called.append(adapter)
            options["on_start"](None)
            if len(prompts) == 1:
                seen["sends"] = options.get("sends")
                reached.set()
                go.wait(10)
                if seen["sends"]:
                    self.driver_settles(options, outcome, "Reply so far")
            return {**self.claude_result("Final reply"), "reported_session_id": "s1"}

        with mock.patch.object(l3, "_select", return_value={"engine": engine, "why": "fixture"}), \
             mock.patch.object(engines, "claude_print", side_effect=lambda *a, **o: provider("claude", *a, **o)), \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **o: provider("codex", *a, **o)), \
             mock.patch.object(engines.platform, "job_stop", side_effect=AssertionError("Send now cannot stop a job")), \
             mock.patch.object(server, "request_l3_drain"):
            page = threading.Thread(target=lambda: stream.append(self.request(
                "POST", "/api/chat", {"project": self.project, "text": "Current instruction"})), daemon=True)
            page.start()
            try:
                self.assertTrue(reached.wait(5))
                turn = l3.active(self.project)
                native = engines.coordinator_native_send(engine)
                self.assertEqual(seen["sends"], l3._sends_root(self.project) / turn["id"] if native else None)
                older, selected, later = [l3.queue_message(self.project, text, trigger="chat", role=config.OPERATOR_ACTOR)
                                          for text in ("Older instruction", "Urgent correction", "Later instruction")]
                body = {"project": self.project, "id": selected["id"]}
                status, sent = self.post_json("/api/chat/send-now", body)
                self.assertEqual((status, sent["status"]), (200, "sending"))
                self.assertEqual(self.post_json("/api/chat/send-now", body)[1]["status"], "sending")
                rows = self.queue_rows()
                self.assertEqual([row["id"] for row in rows], [older["id"], selected["id"], later["id"]])
                if native:
                    self.assertTrue(all((row["send_now"], row["sending"], row["send_group"])
                                        == (True, turn["id"], older["id"]) for row in rows))
                    self.assertEqual(self.chat_view()["queued"][0]["send_now_reason"], "Sending into the current turn")
                    self.assertEqual([path.name for path in seen["sends"].iterdir()], [f"{older['id']}.json"])
                    self.assertEqual(json.loads((seen["sends"] / f"{older['id']}.json").read_text()),
                                     {"id": older["id"], "text": "Older instruction\n\nUrgent correction\n\nLater instruction"})
                else:
                    self.assertTrue(all(row["send_now"] and "sending" not in row for row in rows))
                    self.assertNotIn("sends", turn)
                    self.assertFalse((l3._sends_root(self.project) / turn["id"]).exists())
                    self.assertEqual(self.chat_view()["queued"][0]["send_now_reason"], "Runs next after this turn")
                for member in (older, later):
                    self.assertEqual(self.post_json("/api/chat/send-now", {"project": self.project,
                                                                         "id": member["id"]})[1]["status"], "sending")
                self.assertEqual(called, [engine], "the coordinator turn runs through its engine's streaming adapter")
                if native:
                    self.assertEqual(self.post_json("/api/chat/remove", body)[0], 409,
                                     "a message handed to the running turn cannot be taken back")
                self.assertIsNone(l3.deliver_queued(self.project), "no second turn while this one runs")
            finally:
                go.set()
                page.join(10)
            self.assertFalse(page.is_alive())
            if seen["sends"]:
                self.assertFalse(seen["sends"].exists(), "the turn's sends folder goes with the turn")
            status, payload = stream[0]
            self.assertEqual(status, 200)
            lines = [json.loads(line) for line in payload.decode().splitlines() if line.startswith("{")]
            yield {"turn": turn, "lines": lines, "history": l3.chat_history(self.project, None)[before:],
                   "prompts": prompts, "older": older, "selected": selected, "later": later,
                   "drain": lambda: l3.deliver_queued(self.project)}

    def assert_delivered_once(self, seen):
        selected = seen["selected"]
        admitted = [row for row in l3.chat_history(self.project, None) if selected["id"] in row.get("queue_ids", [])]
        self.assertEqual(len(admitted), 1, "the message reaches the conversation exactly once")
        self.assertEqual(self.post_json("/api/chat/send-now", {"project": self.project, "id": selected["id"]})[1]["status"],
                         "delivered")

    def test_send_now_joins_the_whole_group_to_the_running_chat_turn_once_on_native_engine(self):
        for engine in filter(engines.coordinator_native_send, config.ENGINES):
            with self.subTest(engine=engine), self.chat_turn_with_send_now(engine, "delivered") as seen:
                first, lines = seen["turn"]["id"], seen["lines"]
                self.assertEqual([next(iter(line)) for line in lines], ["turn", "turn", "turn", "turn", "done"])
                members = [seen[key] for key in ("older", "selected", "later")]
                self.assertEqual([line["user"] for line in lines[1:4]], [row["text"] for row in members])
                receipts = [row["delivery"] for row in seen["history"] if row.get("queue_ids")]
                self.assertEqual([receipt["state"] for receipt in receipts], ["delivered"] * 3)
                self.assertTrue(all(receipt["at"] for receipt in receipts))
                self.assertEqual([line["delivery"] for line in lines[1:4]], receipts)
                following = [line["turn"]["id"] for line in lines[1:4]]
                self.assertEqual(len(set([first, *following])), 4)
                self.assertEqual(lines[4]["done"]["turn_id"], following[-1])
                self.assertEqual([(row["role"], row["text"], row["turn_id"], row.get("queue_ids"))
                                  for row in seen["history"]],
                                 [("user", "Current instruction", first, None),
                                  ("assistant", "Reply so far", first, None),
                                  *[("user", row["text"], turn_id, [row["id"]])
                                    for row, turn_id in zip(members, following)],
                                  ("assistant", "Final reply", following[-1], None)])
                self.assertEqual(self.queue_rows(), [])
                self.assertIsNone(seen["drain"]())
                self.assertEqual(len(seen["prompts"]), 1, "the delivered group is not rerun as a turn")
                self.assert_delivered_once(seen)

    def test_send_now_the_engine_may_have_read_is_logged_once_and_never_rerun(self):
        for engine in filter(engines.coordinator_native_send, config.ENGINES):
            for outcome in ("unconfirmed", "writing"):
                with self.subTest(engine=engine, outcome=outcome), self.chat_turn_with_send_now(engine, outcome) as seen:
                    first, selected = seen["turn"]["id"], seen["selected"]
                    rows = [(row["role"], row["text"], row.get("queue_ids")) for row in seen["history"]]
                    users = [("user", seen[key]["text"], [seen[key]["id"]]) for key in ("older", "selected", "later")]
                    receipts = [row["delivery"] for row in seen["history"] if row.get("queue_ids")]
                    self.assertEqual([receipt["state"] for receipt in receipts], ["unconfirmed"] * 3)
                    self.assertTrue(all(receipt["at"] for receipt in receipts))
                    if outcome == "unconfirmed":  # reported mid-turn: the reply so far ends there, as for delivered
                        self.assertEqual([line["delivery"] for line in seen["lines"][1:4]], receipts)
                        self.assertEqual([next(iter(line)) for line in seen["lines"]],
                                         ["turn", "turn", "turn", "turn", "done"])
                        self.assertEqual(rows, [("user", "Current instruction", None),
                                                ("assistant", "Reply so far", None),
                                                *users,
                                                ("assistant", "Final reply", None)])
                    else:  # the turn ended with the drop half-written: it joins the conversation after the reply
                        self.assertEqual([next(iter(line)) for line in seen["lines"]], ["turn", "done"])
                        self.assertEqual(rows, [("user", "Current instruction", None),
                                                ("assistant", "Final reply", None),
                                                *users])
                        self.assertEqual(seen["history"][1]["turn_id"], first)
                        self.assertNotEqual(seen["history"][2]["turn_id"], first)
                    self.assertEqual(self.queue_rows(), [])
                    self.assertIsNone(seen["drain"]())
                    self.assertEqual(len(seen["prompts"]), 1)
                    self.assert_delivered_once(seen)

    def test_send_now_the_turn_did_not_take_runs_next_once(self):
        for engine in config.ENGINES:
            for outcome in ("returned", None):
                with self.subTest(engine=engine, outcome=outcome), self.chat_turn_with_send_now(engine, outcome) as seen:
                    selected = seen["selected"]
                    self.assertEqual([next(iter(line)) for line in seen["lines"]], ["turn", "done"])
                    self.assertEqual([(row["role"], row["text"]) for row in seen["history"]],
                                     [("user", "Current instruction"), ("assistant", "Final reply")])
                    rows = self.queue_rows()
                    self.assertEqual([row["id"] for row in rows],
                                     [seen["older"]["id"], selected["id"], seen["later"]["id"]])
                    self.assertTrue(rows[0]["send_now"])
                    self.assertNotIn("sending", rows[0])
                    self.assertEqual(self.chat_view()["queued"][0]["send_now_reason"], "Runs next")
                    seen["drain"]()
                    self.assertTrue(seen["prompts"][1].endswith("Older instruction\n\nUrgent correction\n\nLater instruction"))
                    self.assertIsNone(seen["drain"]())
                    self.assertEqual(len(seen["prompts"]), 2)
                    delivered = [row for row in l3.chat_history(self.project, None)
                                 if any(seen[key]["id"] in row.get("queue_ids", [])
                                        for key in ("older", "selected", "later"))]
                    self.assertEqual([row["text"] for row in delivered],
                                     [seen[key]["text"] for key in ("older", "selected", "later")])
                    self.assertEqual(len({row["turn_id"] for row in delivered}), 3)
                    self.assert_delivered_once(seen)

    def test_restart_settles_the_send_now_claim_of_a_turn_that_is_gone(self):
        for left in ("json", "writing", "delivered", "unconfirmed"):
            with self.subTest(left=left), self.deliverable():
                selected = l3.queue_message(self.project, "Urgent correction", trigger="chat", role=config.OPERATOR_ACTOR)
                sibling = l3.queue_message(self.project, "Later instruction", trigger="chat", role=config.OPERATOR_ACTOR)
                # The daemon stops mid-turn: the turn never settles its sends.
                with mock.patch.object(l3, "_settle_sends"), l3.lock(self.project), \
                     self.native_chat_turn() as turn:
                    l3.send_now(self.project, selected["id"])
                sends = l3._sends_root(self.project) / turn["id"]
                drop = sends / f"{selected['id']}.json"
                if left != "json":  # its driver had claimed or settled the message before the daemon stopped
                    drop.rename(drop.with_suffix(f".{left}"))
                self.assertEqual(self.queue_rows()[0]["sending"], turn["id"])
                before = len(l3.chat_history(self.project, None))
                recover, outcomes = engines.recover_send, []

                def recorded(folder, message_id):
                    outcomes.append((recover(folder, message_id), sorted(path.name for path in folder.iterdir())))
                    return outcomes[-1][0]

                with mock.patch.object(engines, "recover_send", side_effect=recorded), \
                     mock.patch.object(l3, "turn", return_value={"completed": True}) as execute:
                    for _ in range(3):
                        l3.deliver_queued(self.project)
                history = l3.chat_history(self.project, None)[before:]
                if left == "json":
                    self.assertEqual(outcomes, [("returned", [f"{selected['id']}.returned"])] * 2,
                                     "an unclaimed drop is withdrawn so no live driver can take it later")
                    self.assertEqual([call.args[1] for call in execute.call_args_list],
                                     ["Urgent correction\n\nLater instruction"])
                    self.assertEqual([row.get("queue_ids") for row in history], [[selected["id"]], [sibling["id"]]])
                else:
                    outcome = "unconfirmed" if left == "writing" else left
                    self.assertEqual(outcomes, [(outcome, [f"{selected['id']}.{left}"]) ] * 2)
                    self.assertEqual(execute.call_count, 0)
                    self.assertEqual([row["delivery"]["state"] for row in history], [outcome] * 2)
                    self.assertTrue(all(row["delivery"]["at"] for row in history))
                    self.assertEqual([(row["role"], row["text"], row.get("queue_ids")) for row in history],
                                     [("user", "Urgent correction", [selected["id"]]),
                                      ("user", "Later instruction", [sibling["id"]])])
                self.assertEqual(self.queue_rows(), [])
                self.assertFalse(sends.exists())

    def test_send_now_runs_next_when_the_running_turn_cannot_take_it(self):
        for trigger in ("incident", "block", "report-landed", "ci-recheck", "upstream-issue", "restart"):
            with self.subTest(trigger=trigger), self.deliverable(), mock.patch.object(server, "request_l3_drain"):
                system = l3.queue_message(self.project, "System work", trigger="incident")
                selected = l3.queue_message(self.project, "Urgent operator input", trigger="chat", role=config.OPERATOR_ACTOR)
                with l3.lock(self.project), l3._active_turn(self.project, trigger) as turn:
                    self.assertNotIn("sends", turn)
                    status, sent = self.post_json("/api/chat/send-now", {"project": self.project, "id": selected["id"]})
                    self.assertEqual((status, sent["status"]), (200, "sending"))
                    self.assertEqual(l3.active(self.project), turn)
                    self.assertNotIn("sending", self.queue_rows()[0])
                    self.assertFalse(l3._sends_root(self.project).exists(), "system work is never written into")
                    self.assertEqual(self.chat_view()["queued"][0]["send_now_reason"], "Runs next after system work")
                    self.assertIsNone(l3.deliver_queued(self.project))
                with mock.patch.object(l3, "turn", return_value={"completed": True}) as execute:
                    l3.deliver_queued(self.project)
                    execute.assert_called_once_with(self.project, selected["text"], trigger="chat")
                    self.assertEqual([row["id"] for row in self.queue_rows()], [system["id"]])
                    l3.deliver_queued(self.project)
                    self.assertEqual(execute.call_count, 2)

        from tests.test_images import upload
        with self.deliverable(), mock.patch.object(server, "request_l3_drain"):
            pictured = l3.queue_message(self.project, "Look at this", trigger="chat", role=config.OPERATOR_ACTOR,
                                        uploads=[upload()], request_id="image-request")
            with l3.lock(self.project), self.native_chat_turn() as turn:
                status, sent = self.post_json("/api/chat/send-now", {"project": self.project, "id": pictured["id"]})
                self.assertEqual((status, sent["status"]), (200, "sending"))
                self.assertNotIn("sending", self.queue_rows()[0])
                self.assertFalse((l3._sends_root(self.project) / turn["id"]).exists(),
                                 "an image message is not written into the running turn")
                self.assertEqual(self.chat_view()["queued"][0]["send_now_reason"], "Runs next after this turn")
            with mock.patch.object(l3, "turn", return_value={"completed": True}) as execute:
                l3.deliver_queued(self.project)
            self.assertEqual(execute.call_args.args[1], "Look at this")
            self.assertEqual(execute.call_args.kwargs["image_message"]["id"], pictured["id"])

    def test_send_now_refusals_preserve_rows_and_write_nothing_into_the_turn(self):
        with self.deliverable(), mock.patch.object(server, "request_l3_drain"), \
             l3.lock(self.project), self.native_chat_turn() as turn:
            sends = turn["sends"]
            selected = l3.queue_message(self.project, "Keep this", trigger="chat", role=config.OPERATOR_ACTOR)
            system = l3.queue_message(self.project, "System work", trigger="incident")
            other = l3.queue_message(self.project, "And this", trigger="chat", role=config.OPERATOR_ACTOR)
            rows = self.queue_rows()
            for message_id in (system["id"], "missing"):
                self.assertEqual(self.post_json("/api/chat/send-now", {"project": self.project, "id": message_id})[0], 409)
            for refusal, reason in (
                    (mock.patch.object(l3, "_select", return_value={"engine": None, "why": "No eligible engine"}),
                     "No engine"),
                    (mock.patch.object(config, "is_managed", return_value=False), "no longer managed"),
                    (mock.patch.object(config, "restart_in_progress", return_value=True), "restarting")):
                with self.subTest(reason=reason), refusal:
                    status, answer = self.post_json("/api/chat/send-now", {"project": self.project, "id": selected["id"]})
                    self.assertEqual(status, 409)
                    self.assertIn(reason, answer["error"])
            self.assertEqual(self.queue_rows(), rows)
            self.assertFalse(sends.exists())
            self.assertEqual(self.post_json("/api/chat/send-now", {"project": self.project, "id": selected["id"]})[0], 200)
            status, answer = self.post_json("/api/chat/send-now", {"project": self.project, "id": other["id"]})
            self.assertEqual((status, answer["status"]), (200, "sending"))
            late = l3.queue_message(self.project, "Arrived after the group", trigger="chat", role=config.OPERATOR_ACTOR)
            status, answer = self.post_json("/api/chat/send-now", {"project": self.project, "id": late["id"]})
            self.assertEqual(status, 409)
            self.assertIn("Another message is being sent now", answer["error"])
            self.assertEqual([path.name for path in sends.iterdir()], [f"{selected['id']}.json"])
            self.assertNotIn("send_now", next(row for row in self.queue_rows() if row["id"] == late["id"]))

    def test_send_now_image_group_keeps_order_and_all_members_ahead_of_system_work(self):
        from tests.test_images import upload
        with self.deliverable(), mock.patch.object(server, "request_l3_drain"):
            system = l3.queue_message(self.project, "System work", trigger="incident")
            first = l3.queue_message(self.project, "First input", trigger="chat", role=config.OPERATOR_ACTOR)
            pictured = l3.queue_message(self.project, "Image input", trigger="chat", role=config.OPERATOR_ACTOR,
                                       uploads=[upload()], request_id="ordered-image-request")
            last = l3.queue_message(self.project, "Last input", trigger="chat", role=config.OPERATOR_ACTOR)
            with l3.lock(self.project), self.native_chat_turn() as turn:
                l3.send_now(self.project, last["id"])
                rows = self.queue_rows()
                self.assertEqual([row["id"] for row in rows], [first["id"], pictured["id"], last["id"], system["id"]])
                self.assertTrue(all(row.get("send_now") for row in rows[:3]))
                self.assertTrue(all("sending" not in row for row in rows))
                self.assertFalse(turn["sends"].exists())
            late = l3.queue_message(self.project, "Later arrival", trigger="chat", role=config.OPERATOR_ACTOR)
            before = len(l3.chat_history(self.project, None))
            with mock.patch.object(l3, "turn", return_value={"completed": True}) as execute:
                for expected in (first, pictured, last, system, late):
                    l3.deliver_queued(self.project)
                    self.assertEqual(execute.call_args.args[1], expected["text"])
                self.assertIsNone(l3.deliver_queued(self.project))
            history = l3.chat_history(self.project, None)[before:]
            self.assertEqual([row["text"] for row in history if row["role"] == "user"],
                             [row["text"] for row in (first, pictured, last, system, late)])
            self.assertEqual([row["queue_ids"] for row in history if row["role"] == "user"],
                             [[row["id"]] for row in (first, pictured, last, system, late)])

    def test_send_now_preserves_linked_conversation_boundaries(self):
        with self.deliverable():
            other = l3.queue_message(self.project, "Project input", trigger="chat", role=config.OPERATOR_ACTOR)
            first = l3.queue_message(self.project, "Linked first", trigger="chat", role=config.OPERATOR_ACTOR,
                                     slug="linked-task")
            last = l3.queue_message(self.project, "Linked last", trigger="chat", role=config.OPERATOR_ACTOR,
                                    slug="linked-task")
            with l3.lock(self.project), self.native_chat_turn() as turn:
                l3.send_now(self.project, last["id"])
                self.assertFalse(turn["sends"].exists(), "a different conversation waits for its own turn")
                self.assertEqual([row["id"] for row in self.queue_rows()], [first["id"], last["id"], other["id"]])
            with mock.patch.object(l3, "turn", return_value={"completed": True}) as execute:
                l3.deliver_queued(self.project)
                execute.assert_called_once_with(self.project, "Linked first\n\nLinked last", trigger="chat", slug="linked-task")
            self.assertEqual([row["id"] for row in self.queue_rows()], [other["id"]])

    def test_send_now_group_history_failure_restores_all_members_without_partial_bubbles(self):
        rows = [l3.queue_message(self.project, text, trigger="chat", role=config.OPERATOR_ACTOR)
                for text in ("First", "Second", "Third")]
        with self.deliverable():
            l3.send_now(self.project, rows[-1]["id"])
            history_path = config.project_dir(self.project) / "chat.jsonl"
            atomic_write = S.atomic_write

            def fail_history(path, contents, *args, **kwargs):
                if path == history_path:
                    self.assertEqual([json.loads(line)["text"] for line in contents.splitlines()],
                                     [row["text"] for row in rows])
                    raise OSError("History batch unavailable")
                return atomic_write(path, contents, *args, **kwargs)

            with mock.patch.object(S, "atomic_write", side_effect=fail_history), \
                 mock.patch.object(l3, "turn") as execute:
                with self.assertRaisesRegex(OSError, "History batch unavailable"):
                    l3.deliver_queued(self.project)
                execute.assert_not_called()
            self.assertEqual([row["id"] for row in self.queue_rows()], [row["id"] for row in rows])
            self.assertEqual(l3.chat_history(self.project, None), [])
            with mock.patch.object(l3, "turn", return_value={"completed": True}) as execute:
                l3.deliver_queued(self.project)
                self.assertIsNone(l3.deliver_queued(self.project))
                execute.assert_called_once_with(self.project, "First\n\nSecond\n\nThird", trigger="chat")
            self.assertEqual([row["queue_ids"] for row in l3.chat_history(self.project, None)],
                             [[row["id"]] for row in rows])

    def test_restart_after_native_member_history_write_does_not_duplicate_its_receipt(self):
        with self.deliverable():
            rows = [l3.queue_message(self.project, text, trigger="chat", role=config.OPERATOR_ACTOR)
                    for text in ("First", "Second")]
            with mock.patch.object(l3, "_settle_sends"), l3.lock(self.project), \
                 self.native_chat_turn() as turn:
                l3.send_now(self.project, rows[-1]["id"])
                drop = turn["sends"] / f"{rows[0]['id']}.json"
                drop.rename(drop.with_suffix(".delivered"))
                with mock.patch.object(l3, "_write_queue", side_effect=OSError("Queue write unavailable")):
                    with self.assertRaisesRegex(OSError, "Queue write unavailable"):
                        l3._split_turn(self.project, turn, rows[0]["id"], "", engine=config.ENGINES[0],
                                       delivery={"state": "delivered", "at": S.now()})
            with mock.patch.object(l3, "turn") as execute:
                self.assertIsNone(l3.deliver_queued(self.project))
                execute.assert_not_called()
            self.assertEqual([row["queue_ids"] for row in l3.chat_history(self.project, None)],
                             [[row["id"]] for row in rows])
            self.assertEqual(self.queue_rows(), [])

            self.assertEqual([row["delivery"]["state"] for row in l3.chat_history(self.project, None)],
                             ["delivered"] * 2)

    def test_send_now_group_is_not_repeated_in_fresh_session_history(self):
        rows = [l3.queue_message(self.project, text, trigger="chat", role=config.OPERATOR_ACTOR)
                for text in ("First unique instruction", "Second unique instruction")]
        with self.deliverable(), mock.patch.object(engines, "claude_print", return_value=self.claude_result()) as provider:
            l3.send_now(self.project, rows[-1]["id"])
            l3.deliver_queued(self.project)
            provider.assert_called_once()
            prompt = provider.call_args.args[0]
            for row in rows:
                self.assertEqual(prompt.count(row["text"]), 1)

    def test_send_now_priority_survives_restart_without_holding_quiet_point(self):
        selected = l3.queue_message(self.project, "Run this first", trigger="chat", role=config.OPERATOR_ACTOR)
        with self.deliverable():
            l3.send_now(self.project, selected["id"])
        S.write_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {"at": S.now(), "files": ["altitude/l3.py"]})
        self.assertEqual(server.restart_status()["waiting_for"], [])
        with self.deliverable(), mock.patch.object(l3, "turn", return_value={"completed": True}) as execute:
            server.drain_l3_queue(self.project)
            server.drain_l3_queue(self.project)
            execute.assert_called_once_with(self.project, selected["text"], trigger="chat")

    def test_turn_boundary_send_survives_restart_without_a_native_claim(self):
        with self.chat_turn_with_send_now("codex", None) as seen:
            queued = self.queue_rows()
            self.assertTrue(all(row["send_now"] and "sending" not in row for row in queued))
            self.assertFalse(l3._sends_root(self.project).exists())
            # Recovery has no native outcome to settle: the original durable rows own delivery.
            l3._finish_sends(self.project)
            self.assertEqual(self.queue_rows(), queued)
            seen["drain"]()
            self.assertIsNone(seen["drain"]())
            self.assert_delivered_once(seen)
            self.assertEqual(len(seen["prompts"]), 2)

    def test_send_now_remains_removable_before_claim_when_engine_becomes_unavailable(self):
        row = l3.queue_message(self.project, "Withdraw priority", trigger="chat", role=config.OPERATOR_ACTOR)
        with self.deliverable():
            l3.send_now(self.project, row["id"])
        with mock.patch.object(l3, "_select", return_value={"engine": None, "why": "Unavailable"}):
            self.assertIsNone(l3.deliver_queued(self.project))
            self.assertTrue(l3.drop_queued(self.project, row["id"]))
        self.assertEqual(self.queue_rows(), [])

    def test_direct_turn_waiting_at_boundary_cannot_overtake_selected_message(self):
        selected = l3.queue_message(self.project, "Selected input", trigger="chat", role=config.OPERATOR_ACTOR)
        with self.deliverable(), mock.patch.object(engines, "claude_print") as provider:
            l3.send_now(self.project, selected["id"])
            response = l3.turn(self.project, "New arrival", trigger="chat")
            self.assertIn("queued", response)
            provider.assert_not_called()
        self.assertEqual([row["text"] for row in self.queue_rows()], ["Selected input", "New arrival"])

    def test_upstream_notification_uses_existing_queue_and_chat_without_task_association(self):
        url = "https://github.com/fictional/altitude/issues/42"
        row = l3.queue_upstream_issue(self.project, url, checkout=self.repo)
        waiting = self.chat_view()
        self.assertEqual(waiting["queued"][0]["id"], row["message_id"])
        self.assertNotIn("slug", waiting["queued"][0])
        with self.deliverable(), mock.patch.object(engines, "claude_print", return_value=self.claude_result()):
            server.drain_l3_queue(self.project)
        view = self.chat_view()
        self.assertEqual(view["queued"], [])
        self.assertEqual([r["trigger"] for r in view["history"]], ["upstream-issue", "upstream-issue"])
        self.assertIn(url, view["history"][0]["text"])
        self.assertTrue(all("slug" not in r and "tasks" not in r for r in view["history"]))
        self.assertEqual(S.list_tasks(self.project), [])
        self.assertEqual(l3.queue_upstream_issue(self.project, url, checkout=self.repo)["status"], "received")
        self.assertEqual(l3.queued(self.project), [])

    def test_upstream_enqueue_failure_after_persistence_reuses_the_waiting_row(self):
        url = "https://github.com/fictional/altitude/issues/42"
        write = l3._write_queue
        def persisted(*args):
            write(*args)
            raise OSError("Interruption after atomic enqueue")
        with mock.patch.object(l3, "_write_queue", side_effect=persisted):
            with self.assertRaises(OSError):
                l3.queue_upstream_issue(self.project, url, checkout=self.repo)
        first = l3.queued(self.project)[0]
        retried = l3.queue_upstream_issue(self.project, url.upper(), checkout=self.repo)
        self.assertEqual(retried["message_id"], first["id"])
        self.assertEqual(len(l3.queued(self.project)), 1)

    def test_upstream_claim_receipt_failure_preserves_queue_and_no_second_notification(self):
        url = "https://github.com/fictional/altitude/issues/42"
        row = l3.queue_upstream_issue(self.project, url, checkout=self.repo)
        with self.deliverable(), mock.patch.object(S, "project_log", side_effect=OSError("Receipt unavailable")):
            with self.assertRaises(OSError):
                l3.deliver_queued(self.project)
        self.assertEqual(l3.queued(self.project)[0]["id"], row["message_id"])
        # A saved claim with failed dequeue also leaves the same pending row recoverable.
        with self.deliverable(), mock.patch.object(l3, "_write_queue", side_effect=OSError("Dequeue unavailable")):
            with self.assertRaises(OSError):
                l3.deliver_queued(self.project)
        self.assertEqual(l3.queue_upstream_issue(self.project, url, checkout=self.repo), row)
        with self.deliverable(), mock.patch.object(l3, "turn", return_value={"completed": True}) as turn:
            l3.deliver_queued(self.project)
            l3.deliver_queued(self.project)
        turn.assert_called_once()
        self.assertEqual(l3.queue_upstream_issue(self.project, url, checkout=self.repo)["status"], "received")

    def test_upstream_claim_preserves_history_on_exit_before_execution(self):
        url = "https://github.com/fictional/altitude/issues/42"
        row = l3.queue_upstream_issue(self.project, url, checkout=self.repo)
        with self.deliverable(), mock.patch.object(l3, "turn", side_effect=SystemExit("Daemon exit before execution")):
            with self.assertRaises(SystemExit):
                l3.deliver_queued(self.project)
        self.assertEqual(l3.queued(self.project), [])
        self.assertIn(url, l3.chat_history(self.project)[0]["text"])
        retried = l3.queue_upstream_issue(self.project, url, checkout=self.repo)
        self.assertEqual(retried["status"], "received")
        self.assertEqual(retried["message_id"], row["message_id"])
        self.assertEqual(l3.queued(self.project), [])

    def test_consecutive_chat_messages_fold_into_one_turn_and_server_rows_keep_their_own(self):
        for text, trigger in (("first", "chat"), ("second", "chat"), ("a fault", "incident"), ("third", "chat")):
            l3.queue_message(self.project, text, trigger=trigger, role="burak" if trigger == "chat" else "server")
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, self.deliverable():
            server.drain_l3_queue(self.project)
        self.assertEqual([(c.args[1], c.kwargs["trigger"]) for c in turn.call_args_list],
                         [("first\n\nsecond", "chat"), ("a fault", "incident"), ("third", "chat")])
        self.assertEqual(self.queue_rows(), [])

    def test_remove_between_selection_and_claim_preserves_siblings_and_conversation_boundaries(self):
        first, removed, last = [l3.queue_message(self.project, text, trigger="chat", role="burak", slug="owner-a")
                                for text in ("first", "remove this", "last")]
        l3.queue_message(self.project, "other owner", trigger="chat", role="burak", slug="owner-b")
        l3.queue_message(self.project, "system authority", trigger="incident", role="server")
        original_active = l3._active_turn

        @contextlib.contextmanager
        def remove_before_claim(*args, **kwargs):
            self.assertTrue(l3.drop_queued(self.project, removed["id"]))
            with original_active(*args, **kwargs) as active:
                yield active

        with self.deliverable(), mock.patch.object(l3, "_active_turn", side_effect=remove_before_claim), \
             mock.patch.object(l3, "turn") as turn:
            self.assertIsNone(l3.deliver_queued(self.project))
        turn.assert_not_called()
        self.assertEqual([row["text"] for row in self.queue_rows()],
                         ["first", "last", "other owner", "system authority"])

        def during_turn(_project, prompt, **_kwargs):
            if prompt == "first\n\nlast":
                self.assertFalse(l3.drop_queued(self.project, first["id"]))
                self.assertFalse(l3.drop_queued(self.project, last["id"]))
                l3.queue_message(self.project, "later arrival", trigger="chat", role="burak", slug="owner-a")
            return {"completed": True}

        with self.deliverable(), mock.patch.object(l3, "turn", side_effect=during_turn) as turn:
            l3.deliver_queued(self.project)
            self.assertEqual([row["text"] for row in self.queue_rows()],
                             ["other owner", "system authority", "later arrival"])
            server.drain_l3_queue(self.project)
        self.assertEqual([(call.args[1], call.kwargs["trigger"], call.kwargs.get("slug"))
                          for call in turn.call_args_list],
                         [("first\n\nlast", "chat", "owner-a"), ("other owner", "chat", "owner-b"),
                          ("system authority", "incident", None), ("later arrival", "chat", "owner-a")])
        self.assertEqual(self.queue_rows(), [])

    def test_the_queue_drains_at_the_turn_boundary_not_at_the_next_tick(self):
        drained = threading.Event()
        calls = []

        def fake_turn(project, text, *, trigger, on_text=None, on_start=None, on_split=None):
            calls.append(text)
            if on_text:  # the turn Burak started from the page; he types again while it runs
                l3.queue_message(project, "while you were busy", trigger="chat", role="burak")
                on_text("working on it")
            else:
                drained.set()
            return {"session_id": "s1", "engine": "claude", "error": None, "completed": True}

        with mock.patch.object(l3, "turn", new=fake_turn), self.deliverable():
            self.request("POST", "/api/chat", {"project": self.project, "text": "status?"})
            self.assertTrue(drained.wait(10), "the queue waited for the next tick instead of the turn boundary")
        self.assertEqual(calls, ["status?", "while you were busy"])
        self.assertEqual(self.queue_rows(), [])

    def test_a_drain_request_arriving_as_the_loop_empties_is_not_lost(self):
        first_check, release, second_check = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def deliver(project):
            calls.append(project)
            if len(calls) == 1:
                first_check.set()
                release.wait(10)
            else:
                second_check.set()
            return None

        with mock.patch.object(l3, "deliver_queued", new=deliver), mock.patch.object(l3, "busy", return_value=False):
            self.assertTrue(server.request_l3_drain(self.project))
            self.assertTrue(first_check.wait(5))
            self.assertFalse(server.request_l3_drain(self.project), "the existing keyed loop owns the second request")
            release.set()
            self.assertTrue(second_check.wait(5), "the request was dropped while the drain loop exited")

    def test_every_server_turn_requests_a_drain_even_when_the_turn_errors(self):
        with mock.patch.object(l3, "turn", side_effect=RuntimeError("provider failed")), \
             mock.patch.object(server, "request_l3_drain") as request:
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                server.server_l3_turn(self.project, "hello", trigger="chat")
        request.assert_called_once_with(self.project)

    # ---- restarts -----------------------------------------------------------

    def test_a_pending_queue_neither_holds_the_restart_nor_loses_a_message(self):
        S.write_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {"at": S.now(), "files": ["altitude/l3.py"]})
        l3.queue_message(self.project, "before the restart", trigger="chat", role="burak")
        self.assertEqual(server.restart_status()["waiting_for"], [],
                         "a queued message is not a turn in flight and must not hold the restart")

        release = self.hold_l3()  # only a turn actually running holds it
        self.assertEqual(server.restart_status()["waiting_for"], [f"{self.project} L3"])
        release()

        # The queue is a file: the restarted process reads the same rows and runs each exactly once.
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, self.deliverable():
            server.drain_l3_queue(self.project)
            server.drain_l3_queue(self.project)
        turn.assert_called_once_with(self.project, "before the restart", trigger="chat")

    # ---- what the conversation reads (SPEC.md §3.3, §3.4, §5.2) --------------

    def test_the_stream_names_its_turn_before_the_first_text(self):
        # A poll that lands mid-stream already shows the server's rows for this turn; without the id
        # the page had to guess which rows were its own and doubled the operator's bubble.
        def provider(_prompt, **kwargs):
            kwargs["on_start"](4242)
            kwargs["on_text"]("two tasks.")
            return self.claude_result("two tasks.")

        with self.deliverable(), mock.patch.object(engines, "claude_print", side_effect=provider):
            status, payload = self.request("POST", "/api/chat", {"project": self.project, "text": "status?"})
        self.assertEqual(status, 200)
        lines = [json.loads(line) for line in payload.decode().splitlines() if line.startswith("{")]  # chunk sizes between
        self.assertEqual([next(iter(line)) for line in lines], ["turn", "t", "done"])
        self.assertEqual(set(lines[0]["turn"]), {"id", "started_at", "trigger", "provider_started"})
        self.assertEqual(lines[0]["turn"]["trigger"], "chat")
        self.assertEqual(lines[2]["done"]["turn_id"], lines[0]["turn"]["id"])
        rows = self.chat_view()["history"]
        self.assertEqual([r["turn_id"] for r in rows[-2:]], [lines[0]["turn"]["id"]] * 2)

    def test_a_task_created_through_the_verb_broker_names_itself_on_the_turns_assistant_row(self):
        # SPEC.md §5.2 note 4: the page shows the task under the reply that created it, so the reply's
        # row must say which task that was; a task created outside any turn belongs to no row.
        created = {}

        def provider(_prompt, **_kwargs):
            reply = server.l3_verb_request(self.project, {
                "kind": "alt", "args": ["task", "new", "--title", "Fold the system lines", "-"],
                "stdin": "Render every system turn as one line."})
            self.assertEqual(reply["returncode"], 0, reply["stderr"])
            created["slug"] = json.loads(reply["stdout"])["slug"]
            return self.claude_result("Created one task for it.")

        with self.deliverable(), mock.patch.object(engines, "claude_print", side_effect=provider), \
             mock.patch.object(server, "request_l3_drain"):
            server.server_l3_turn(self.project, "fold the system lines", trigger="chat")
        user, assistant = self.chat_view()["history"][-2:]
        self.assertEqual(assistant["tasks"], [created["slug"]])
        self.assertNotIn("tasks", user)
        self.assertEqual(S.load_task(self.project, created["slug"])["state"], "queued")

        self.assertFalse(l3.note_task(self.project, "made-by-hand"), "no turn is running")
        with self.deliverable(), mock.patch.object(engines, "claude_print",
                                                   return_value=self.claude_result("nothing new")):
            server.server_l3_turn(self.project, "anything new?", trigger="chat")
        self.assertNotIn("tasks", self.chat_view()["history"][-1])

    def test_the_report_prompt_is_label_value_rows_and_keeps_the_report_behind_the_task(self):
        # SPEC.md §5.2 note 1: the excerpt made the stored row unreadable in the expanded system card.
        task = T.new(self.project, "Persist paths", "Keep them.")
        verdict = {"verdict": "ok", "problems": [], "signals": ["one flaky test retried"], "prs": [178],
                   "spend": {"turns": 14, "subagent_launches": 2, "cost": None},
                   "report": {"landed": {"prs": [{"number": 178, "merged": True}], "deploy": "healthy"},
                              "review": [{"summary": "secret evidence text"}]}}
        with mock.patch.object(l3, "turn", return_value={}) as turn:
            server.report_turn(self.project, task, verdict)
        header = turn.call_args.args[1]
        self.assertEqual(turn.call_args.kwargs["trigger"], "report-landed")
        self.assertEqual(header.splitlines()[:7], [
            f"Report landed for {task['slug']}.", f"Task: {task['slug']}", "Verdict: ok", "Problems: none",
            "Post-mortem signals: one flaky test retried", "PRs: #178 merged", "Spend: 14 turns, 2 subagent launches"])
        self.assertNotIn("secret evidence text", header)
        self.assertNotIn("Report excerpt", header)
        self.assertIn(f"`alt task report {task['slug']}`", header)


if __name__ == "__main__":
    unittest.main()
