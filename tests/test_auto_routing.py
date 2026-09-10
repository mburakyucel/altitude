"""Configured Auto options flow through real routing, launch, session and failure handling."""
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, digest, dispatch, engines, incidents, l3, monitor, route, server, state as S, tasks as T


REJECTION = {"scope": "model", "why": "provider rejected the selected model as inaccessible"}


class TestAutoIntegration(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(engines, "installation", return_value={"available": None, "why": "installed; access unknown"})
        self.patch(engines, "usage_hold", return_value=None)
        self.patch(monitor, "quota", return_value={"known": False})
        self.patch(route, "quota_codex", return_value={"known": False})
        self.patch(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40)
        self.patch(dispatch, "_task_worktree", return_value=self.repo)
        self.patch(dispatch, "_validate_task_worktree")
        self.patch(engines, "worker_live", return_value=False)
        self.patch(engines, "stop_l2_worker")
        self.patch(server, "log")
        self.patch(incidents, "system_fault")

    def policy(self, value, **pins):
        self.register(self.project, routing=config.parse_routing(value), **pins)

    def task(self):
        return T.new(self.project, "Routed work", "Make the requested change.", paths=["README.md"])

    @staticmethod
    def launched(engine, name, prompt, **kwargs):
        return {"returncode": 0, "agent": {"id": "worker", "sessionId": "conversation",
                "state": "working", "engine_model": kwargs["model"]}}

    @staticmethod
    def rejected(*args, **kwargs):
        return {"returncode": 0, "rejection": REJECTION, "safe_to_retry": True,
                "agent": {"id": "rejected-worker", "sessionId": "rejected-conversation", "state": "failed"}}

    def test_fresh_dispatch_uses_configured_model_and_not_previous_observed_engine(self):
        self.policy("codex:operator-model > claude:opus")
        task = self.task()
        task.update(l2_engine="claude", engine_model="observed-old-model", launch_model="observed-old-model")
        S.save_task(self.project, task)
        with mock.patch.object(engines, "start_l2", side_effect=self.launched) as launch:
            dispatch.run(self.project, task["slug"])
        self.assertEqual((launch.call_args.args[0], launch.call_args.kwargs["model"]), ("codex", "operator-model"))
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["l2_engine"], saved["launch_model"], saved["attempt"]), ("codex", "operator-model", 1))
        self.assertIn("codex:operator-model", saved["routing"])

    def test_safe_fable_startup_rejection_falls_back_to_opus_once_before_binding(self):
        self.policy("claude:fable > claude:opus")
        task, calls = self.task(), []
        def launch(*args, **kwargs):
            calls.append((args[0], kwargs["model"]))
            return self.rejected() if kwargs["model"] == "fable" else self.launched(*args, **kwargs)
        with mock.patch.object(engines, "start_l2", side_effect=launch):
            dispatch.run(self.project, task["slug"])
        self.assertEqual(calls, [("claude", "fable"), ("claude", "opus")])
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["state"], saved["session_id"], saved["launch_model"], saved["attempt"]),
                         ("running", "conversation", "opus", 1))
        self.assertFalse(saved.get("engine"), "an Auto fallback must not manufacture an explicit engine pin")
        self.assertIn("claude:fable unavailable", saved["routing"])

    def test_previously_rejected_fable_is_skipped_for_claude_only_opus(self):
        self.policy("claude:fable > claude:opus")
        route.note_rejection({"engine": "claude", "model": "fable"}, REJECTION)
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched) as launch:
            dispatch.run(self.project, task["slug"])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual((launch.call_args.args[0], launch.call_args.kwargs["model"]), ("claude", "opus"))

    def test_queue_and_monitor_use_the_same_policy_as_fresh_dispatch(self):
        self.policy("claude:fable")
        task = self.task()
        route.note_rejection({"engine": "claude", "model": "fable"}, REJECTION)
        expected = route.pick_engine("l2", project=config.project(self.project))
        self.assertIsNone(expected["engine"])
        self.assertEqual(digest._waiting(self.project, task, None)["reason"], "engine hold: " + expected["why"])
        row = next(row for row in monitor.routing() if row["role"] == "l2" and row["project"] == self.project)
        self.assertEqual(row["why"], f"Project {self.project}: {expected['why']}")

    def test_fallback_brief_names_the_actual_provider_and_model(self):
        self.policy("claude:fable > codex:operator-model")
        task = self.task()
        def launch(*args, **kwargs):
            return self.rejected() if args[0] == "claude" else self.launched(*args, **kwargs)
        with mock.patch.object(engines, "start_l2", side_effect=launch) as execute:
            dispatch.run(self.project, task["slug"])
        self.assertIn("one L2 (codex, operator-model)", execute.call_args_list[-1].args[2])
        self.assertIn("one L2 (codex, operator-model)", (S.task_dir(self.project, task["slug"]) / "brief.md").read_text())

    def test_each_rejected_option_is_tried_once_and_no_option_leaves_actionable_hold(self):
        self.policy("claude:fable > claude:opus")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.rejected) as launch:
            with self.assertRaisesRegex(T.TransitionError, "no configured option available.*alt project set --routing"):
                dispatch.run(self.project, task["slug"])
        self.assertEqual([call.kwargs["model"] for call in launch.call_args_list], ["fable", "opus"])
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["state"], "queued")
        self.assertFalse(saved.get("dispatching"))
        self.assertFalse(saved.get("session_id"))

    def test_explicit_one_off_model_rejection_never_uses_auto_fallback(self):
        self.policy("claude:fable > claude:opus")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.rejected) as launch:
            with self.assertRaisesRegex(T.TransitionError, "forced claude:fable is unavailable"):
                dispatch.run(self.project, task["slug"], model="fable")
        self.assertEqual(launch.call_count, 1)
        with mock.patch.object(engines, "start_l2") as retry:
            with self.assertRaisesRegex(T.TransitionError, "forced claude:fable is unavailable"):
                dispatch.run(self.project, task["slug"])
        retry.assert_not_called()
        self.assertEqual(S.load_task(self.project, task["slug"])["model"], "fable")

    def test_project_model_pin_remains_strict_despite_auto_preferences(self):
        self.policy("codex > claude:opus", l2_model="fable")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.rejected) as launch:
            with self.assertRaisesRegex(T.TransitionError, "forced claude:fable is unavailable"):
                dispatch.run(self.project, task["slug"])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual((launch.call_args.args[0], launch.call_args.kwargs["model"]), ("claude", "fable"))

    def test_unsafe_startup_failure_does_not_replay_work_on_fallback(self):
        self.policy("claude:fable > claude:opus")
        task = self.task()
        failed = {**self.rejected(), "returncode": 1, "safe_to_retry": False, "stderr": "model rejected after tool work"}
        with mock.patch.object(engines, "start_l2", return_value=failed) as launch:
            with self.assertRaises(dispatch.DispatchFailure):
                dispatch.run(self.project, task["slug"])
        self.assertEqual(launch.call_count, 1)

    def test_preference_change_does_not_change_l2_resume_engine_model_or_conversation(self):
        self.policy("claude:opus")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched):
            dispatch.run(self.project, task["slug"])
        T.block(self.project, task["slug"], "Waiting for requested input")
        self.policy("codex:operator-model")
        def resumed(engine, name, sid, prompt, **kwargs):
            return {"returncode": 0, "agent": {"id": "resumed-worker", "sessionId": sid, "state": "working"}}
        with mock.patch.object(engines, "resume_l2", side_effect=resumed) as resume:
            dispatch.resume(self.project, task["slug"])
        self.assertEqual((resume.call_args.args[0], resume.call_args.args[2], resume.call_args.kwargs["model"]),
                         ("claude", "conversation", "opus"))
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["l2_engine"], saved["session_id"], saved["launch_model"], saved["attempt"]),
                         ("claude", "conversation", "opus", 1))

    def test_project_pin_parks_quota_failure_without_replacing_conversation(self):
        self.policy("claude:fable > codex", l2_engine="claude", l2_model="opus")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched):
            dispatch.run(self.project, task["slug"])
        running = S.load_task(self.project, task["slug"])
        with mock.patch.object(engines, "remove_l2_worker") as remove:
            server.on_l2_finished(self.project, {"task": running, "limited": engines._usage_limit("2099-01-01T00:00:00+00:00"),
                "agent": {"id": "worker", "state": "failed"}})
        remove.assert_not_called()
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["state"], saved["session_id"], saved["launch_model"]), ("blocked", "conversation", "opus"))

    def test_one_off_model_pin_survives_later_quota_failure(self):
        self.policy("claude:fable > codex")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched):
            dispatch.run(self.project, task["slug"], model="opus")
        running = S.load_task(self.project, task["slug"])
        with mock.patch.object(engines, "remove_l2_worker") as remove:
            server.on_l2_finished(self.project, {"task": running, "limited": engines._usage_limit("2099-01-01T00:00:00+00:00"),
                "agent": {"id": "worker", "state": "failed"}})
        remove.assert_not_called()
        self.assertEqual(S.load_task(self.project, task["slug"])["session_id"], "conversation")

    def test_one_off_model_pin_survives_asynchronous_model_rejection(self):
        self.policy("claude:fable > codex")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched):
            dispatch.run(self.project, task["slug"], model="opus")
        running = S.load_task(self.project, task["slug"])
        with mock.patch.object(engines, "remove_l2_worker") as remove:
            server.on_l2_finished(self.project, {"task": running, "rejection": REJECTION, "safe_to_retry": True,
                "agent": {"id": "worker", "state": "failed", "resumed": False}})
        remove.assert_not_called()
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["state"], saved["session_id"], saved["launch_model"]), ("blocked", "conversation", "opus"))

    def test_l3_same_provider_fallback_resumes_existing_session_and_logs_one_user_message(self):
        self.policy("claude:fable > claude:opus")
        l3.save_info(self.project, {"engine_last": "claude", "sessions": {"claude": {
            "session_id": "existing-session", "launch_model": "fable", "turns": 3,
            "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
        def answer(prompt, **kwargs):
            if kwargs["model"] == "fable":
                return {"session_id": kwargs["resume"], "error": REJECTION["why"],
                        "rejection": REJECTION, "safe_to_retry": True, "text": "", "tools": []}
            return {"session_id": kwargs["resume"], "text": "Opus answer", "tools": [], "error": None}
        with mock.patch.object(engines, "claude_print", side_effect=answer) as execute, \
             mock.patch.object(engines, "codex_exec") as other:
            result = l3.turn(self.project, "Continue coordinating")
        self.assertTrue(result["completed"])
        self.assertEqual([(call.kwargs["model"], call.kwargs["resume"]) for call in execute.call_args_list],
                         [("fable", "existing-session"), ("opus", "existing-session")])
        other.assert_not_called()
        self.assertEqual(len([row for row in l3.chat_history(self.project) if row["role"] == "user"]), 1)
        self.assertEqual(l3.info(self.project)["sessions"]["claude"]["session_id"], "existing-session")

    def test_l3_never_replays_rejection_after_tool_work(self):
        self.policy("claude:fable > codex")
        failure = {"session_id": "session", "text": "", "error": REJECTION["why"],
                   "rejection": REJECTION, "safe_to_retry": False, "tools": [{"name": "mcp__write"}]}
        with mock.patch.object(engines, "claude_print", return_value=failure) as execute, \
             mock.patch.object(engines, "codex_exec") as fallback:
            result = l3.turn(self.project, "Do the requested operation")
        self.assertEqual(execute.call_count, 1)
        fallback.assert_not_called()
        self.assertFalse(result["completed"])

    def test_l3_partial_rejection_retains_session_and_tool_history_on_each_engine(self):
        for engine, policy in (("claude", "claude:fable>codex"), ("codex", "codex>claude:opus")):
            with self.subTest(engine=engine):
                self.policy(policy)
                failure = {"session_id": f"partial-{engine}", "reported_session_id": f"partial-{engine}",
                           "text": "I performed the operation", "error": REJECTION["why"], "usage": {},
                           "rejection": REJECTION, "safe_to_retry": False, "tools": [{"name": "Bash", "command": "alt task status"}]}
                with mock.patch.object(engines, "claude_print", return_value=failure), \
                     mock.patch.object(engines, "codex_exec", return_value=failure):
                    result = l3.turn(self.project, f"Partial operation on {engine}")
                self.assertFalse(result["completed"])
                self.assertEqual(l3.info(self.project)["sessions"][engine]["session_id"], f"partial-{engine}")
                self.assertTrue(any(row.get("engine") == engine and row.get("tools") and
                                    row["text"] == "I performed the operation" for row in l3.chat_history(self.project)))

    def test_l3_project_pin_does_not_fallback_on_quota_limit(self):
        self.policy("claude:fable > codex", l3_engine="claude", l3_model="opus")
        failure = {"session_id": "session", "text": "", "error": "usage window exhausted", "tools": [],
                   "limited": engines._usage_limit("2099-01-01T00:00:00+00:00"), "safe_to_retry": True}
        with mock.patch.object(engines, "claude_print", return_value=failure) as execute, \
             mock.patch.object(engines, "codex_exec") as fallback:
            result = l3.turn(self.project, "Continue")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.kwargs["model"], "opus")
        fallback.assert_not_called()
        self.assertFalse(result["completed"])

    def test_l3_scoped_unknown_limit_falls_back_without_freezing_other_models(self):
        self.policy("claude:fable > claude:opus")
        limit = engines.usage_limit_in("You've reached your Fable limit. Switch to another model.")
        def answer(prompt, **kwargs):
            return {"session_id": "conversation", "text": "" if kwargs["model"] == "fable" else "Continued",
                    "error": limit["why"] if kwargs["model"] == "fable" else None, "tools": [],
                    "limited": limit if kwargs["model"] == "fable" else None, "safe_to_retry": True,
                    "usage": {}, "context_tokens": 1, "cost": 0, "turns": 1}
        with mock.patch.object(engines, "claude_print", side_effect=answer) as execute:
            result = l3.turn(self.project, "Continue the existing request")
        self.assertTrue(result["completed"])
        self.assertEqual([call.kwargs["model"] for call in execute.call_args_list], ["fable", "opus"])
        self.assertFalse(engines.usage_limit_path().exists())

    def test_model_limit_pinned_or_without_alternative_waits_without_a_reset_timer(self):
        for pin in ({}, {"l2_model": "fable"}):
            self.policy("claude:fable", **pin)
            task = self.task()
            running = {**task, "state": "running", "attempt": 1, "l2_engine": "claude", "agent_id": "worker",
                       "session_id": "session", "launch_model": "fable"}
            S.save_task(self.project, running)
            limit = engines.usage_limit_in("You've reached your Fable limit. Switch to another model.")
            with mock.patch.object(engines, "remove_l2_worker") as remove:
                server.on_l2_finished(self.project, {"task": running, "limited": limit})
            remove.assert_not_called()
            saved = S.load_task(self.project, task["slug"])
            self.assertEqual(saved["state"], "blocked")
            self.assertFalse(saved.get("resume_after") or saved.get("fault"))
            self.assertEqual(saved["session_id"], "session")
            self.assertIn("reset time unknown", saved["blocked_reason"])
            self.assertFalse(engines.usage_limit_path().exists())

    def test_limit_fallback_preserves_a_racing_resume_claim_and_its_messages(self):
        for phase in ("before-retirement", "before-requeue"):
            with self.subTest(phase=phase):
                self.policy("claude:fable > codex")
                task = self.task()
                running = {**task, "state": "running", "attempt": 1, "l2_engine": "claude", "agent_id": "worker",
                           "session_id": "session", "launch_model": "fable"}
                S.save_task(self.project, running)
                limit = engines.usage_limit_in("You've reached your Fable limit. Switch to another model.")
                claimed = {}
                def claim():
                    message = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "Preserve this new instruction")
                    claimed.update(claim=T.claim_resume(self.project, task["slug"]), message=message)
                record = engines.record_usage_limit
                requeue = T.requeue
                def observe(*args):
                    record(*args)
                    if phase == "before-retirement":
                        claim()
                def move(*args, **kwargs):
                    if phase == "before-requeue":
                        claim()
                    return requeue(*args, **kwargs)
                with mock.patch.object(engines, "record_usage_limit", side_effect=observe), \
                     mock.patch.object(T, "requeue", side_effect=move), \
                     mock.patch.object(engines, "remove_l2_worker") as remove:
                    server.on_l2_finished(self.project, {"task": running, "limited": limit})
                saved = S.load_task(self.project, task["slug"])
                self.assertEqual(saved["state"], "blocked")
                self.assertEqual(saved["resume_claim"]["id"], claimed["claim"]["id"])
                self.assertEqual([row["id"] for row in saved["resume_claim"]["messages"]], [claimed["message"]["id"]])
                self.assertEqual((saved["agent_id"], saved["session_id"]), ("worker", "session"))
                if phase == "before-retirement":
                    remove.assert_not_called()

    def test_l3_one_off_model_pin_stays_strict_and_keeps_error_in_conversation(self):
        self.policy("claude:fable > claude:opus")
        failure = {"session_id": "session", "text": "", "error": REJECTION["why"], "tools": [],
                   "rejection": REJECTION, "safe_to_retry": True}
        with mock.patch.object(engines, "claude_print", return_value=failure) as execute:
            result = l3.turn(self.project, "Use this model for this turn", model="fable")
        self.assertEqual(execute.call_count, 1)
        self.assertFalse(result["completed"])
        self.assertTrue(any(row["role"] == "error" and REJECTION["why"] in row["text"]
                            for row in l3.chat_history(self.project)))
