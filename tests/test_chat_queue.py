"""Burak's chat messages queue while L3 is busy and run at the next turn boundary, in arrival order."""
import contextlib
import io
import json
import runpy
import socket
import threading
import unittest
from unittest import mock

from tests.support import ALT, AltitudeCase
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

    def test_l3_cli_saves_the_message_when_git_metadata_is_unavailable(self):
        unavailable = PermissionError(".git/FETCH_HEAD is read-only in the coordinator")
        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", side_effect=unavailable) as fetch, \
             mock.patch.object(incidents, "system_fault") as fault:
            row = cli(["--project", self.project, "task", "message", self.slug, "Use the existing thread."])

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
            row = cli(["--project", self.project, "task", "message", self.slug, "Keep going."])

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
             mock.patch.object(dispatch, "settle_deploy_checkout"), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
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
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", owner_pid=99999999,
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

    def test_restart_with_ambiguous_provider_launch_fails_closed_without_a_duplicate(self):
        message = T.message(self.project, self.slug, "l3", "Do not deliver me twice.", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launching", owner_pid=99999999)

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
                     mock.patch.object(dispatch, "settle_deploy_checkout"), \
                     mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
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
                self.assertIn(task["questions"][-1]["id"], handoff["question_context"])

    def test_restart_discards_a_launched_claim_superseded_by_a_new_question(self):
        earlier = T.message(self.project, self.slug, "l3", "Earlier steering", by="l3")
        claim = T.claim_resume(self.project, self.slug)
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", owner_pid=99999999,
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
        self.assertIn(task["questions"][-1]["id"], handoff["question_context"])
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
                     mock.patch.object(dispatch, "settle_deploy_checkout"), \
                     mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
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
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launching", owner_pid=99999999)
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
        T.update_resume_claim(self.project, self.slug, claim["id"], phase="launched", owner_pid=99999999,
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
        self.assertIn(task["questions"][-1]["id"], handoff["question_context"])
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
             mock.patch.object(dispatch, "settle_deploy_checkout"), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
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
             mock.patch.object(dispatch, "settle_deploy_checkout"), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
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
        head = f"{method} {path} HTTP/1.0\r\nHost: x\r\n"
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
        self.patch(l3, "turn", new=lambda project, text, *, trigger, on_text, on_start=None: (
            on_text("two tasks."), {"session_id": "s1", "engine": "claude", "error": None,
                                    "turn_id": "turn-1"})[1])
        status, payload = self.request("POST", "/api/chat", {"project": self.project, "text": "status?"})
        self.assertEqual(status, 200)
        self.assertIn(b'"two tasks."', payload)
        self.assertIn(b'"done"', payload)
        self.assertIn(b'"turn_id": "turn-1"', payload)
        self.assertNotIn(b'"queued"', payload)
        self.assertEqual(self.queue_rows(), [])

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
            self.assertTrue(views[0]["busy"])
            self.assertTrue(provider_started.wait(5))
            release_provider.set()
            worker.join(5)
            reader.join(5)

        self.assertIsNone(self.chat_view()["active"])

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
                sock.sendall(b"POST /api/chat HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
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

    def test_consecutive_chat_messages_fold_into_one_turn_and_server_rows_keep_their_own(self):
        for text, trigger in (("first", "chat"), ("second", "chat"), ("a fault", "incident"), ("third", "chat")):
            l3.queue_message(self.project, text, trigger=trigger, role="burak" if trigger == "chat" else "server")
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, self.deliverable():
            server.drain_l3_queue(self.project)
        self.assertEqual([(c.args[1], c.kwargs["trigger"]) for c in turn.call_args_list],
                         [("first\n\nsecond", "chat"), ("a fault", "incident"), ("third", "chat")])
        self.assertEqual(self.queue_rows(), [])

    def test_the_queue_drains_at_the_turn_boundary_not_at_the_next_tick(self):
        drained = threading.Event()
        calls = []

        def fake_turn(project, text, *, trigger, on_text=None, on_start=None):
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
        self.assertEqual(set(lines[0]["turn"]), {"id", "started_at", "trigger"})
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
