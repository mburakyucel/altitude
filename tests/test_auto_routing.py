"""Configured Auto options flow through real routing, launch, session and failure handling."""
from unittest import mock
import time

from tests.support import AltitudeCase, set_project_setting
from altitude import config, digest, dispatch, engines, incidents, l3, monitor, route, server, state as S, tasks as T
from altitude.engines import codex as quota_codex


REJECTION = {"scope": "model", "why": "provider rejected the selected model as inaccessible"}
RESET_EPOCH = 4070926800  # 2099-01-01T05:00:00Z
RESET_ISO = "2099-01-01T05:00:00+00:00"


class TestAutoIntegration(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(engines, "installation", return_value={"available": None, "why": "installed; access unknown"})
        self.patch(engines, "usage_hold", return_value=None)
        self.read_quota, self.read_other_quota = monitor.quota, route.quota_codex
        self.quota = self.patch(monitor, "quota", return_value={"known": False})
        self.other_quota = self.patch(route, "quota_codex", return_value={"known": False})
        self.patch(dispatch.git_policy, "fetch_origin", return_value="a" * 40)
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
        self.assertIn("codex (operator-model) owns this", execute.call_args_list[-1].args[2])
        self.assertIn("codex (operator-model) owns this", (S.task_dir(self.project, task["slug"]) / "brief.md").read_text())

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

    def test_project_default_model_is_an_auto_preference_and_not_a_pin(self):
        self.policy("codex > claude", l2_model="fable")
        task = self.task()
        def launch(*args, **kwargs):
            return self.rejected() if args[0] == "codex" else self.launched(*args, **kwargs)
        with mock.patch.object(engines, "start_l2", side_effect=launch) as execute:
            dispatch.run(self.project, task["slug"])
        self.assertEqual([(call.args[0], call.kwargs["model"]) for call in execute.call_args_list],
                         [("codex", None), ("claude", "fable")])
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["l2_engine"], saved["launch_model"], saved["routing_pinned"]), ("claude", "fable", False))
        self.assertFalse(saved.get("engine"))

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

    def test_global_pair_applies_at_dispatch_and_never_reinterprets_null_resume(self):
        original = config.machine_settings()
        for name in ("events.jsonl", "model_defaults_codex-request.json"):
            path = config.ROOT / name
            if path.exists():
                self.addCleanup(path.write_bytes, path.read_bytes())
            else:
                self.addCleanup(path.unlink, missing_ok=True)
        settings_path = config.ROOT / "settings.json"
        if settings_path.exists():
            self.addCleanup(S.write_json, settings_path, original)
        else:
            self.addCleanup(settings_path.unlink, missing_ok=True)
        self.policy("codex")
        S.write_json(config.ROOT / "settings.json", {"model_defaults_codex": {"model": None, "effort": "native"}})
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched) as launch:
            dispatch.run(self.project, task["slug"])
        self.assertEqual((launch.call_args.kwargs["model"], launch.call_args.kwargs["effort"]), (None, None))
        T.block(self.project, task["slug"], "Waiting for requested input")
        queued = self.task()
        server.save_model_defaults({"engine": "codex", "value": {"model": "gpt-6.1-sol", "effort": "ultra"},
                                    "expected": {"model": None, "effort": "native"}})
        with mock.patch.object(engines, "resume_l2", return_value={"returncode": 0, "agent": {
                "id": "resumed", "sessionId": "conversation", "state": "working"}}) as resume:
            dispatch.resume(self.project, task["slug"])
        self.assertEqual((resume.call_args.kwargs["model"], resume.call_args.kwargs["effort"]), (None, None))
        with mock.patch.object(engines, "start_l2", side_effect=self.launched) as launch:
            dispatch.run(self.project, queued["slug"])
        self.assertEqual((launch.call_args.kwargs["model"], launch.call_args.kwargs["effort"]), ("gpt-6.1-sol", "ultra"))

    def test_named_choice_applies_to_queued_tasks_but_preserves_bound_sessions(self):
        self.policy("claude:opus")
        task = self.task()
        # Choosing after queueing still applies at the first launch.
        choice = {"engine": "codex", "model": "gpt-6.1-sol"}
        self.patch(config, "machine_settings", return_value={"new_tasks": choice})
        with mock.patch.object(engines, "start_l2", side_effect=self.launched) as launch:
            dispatch.run(self.project, task["slug"])
        self.assertEqual((launch.call_args.args[0], launch.call_args.kwargs["model"]),
                         ("codex", "gpt-6.1-sol"))
        T.block(self.project, task["slug"], "Waiting for requested input")
        choice["model"] = "another-model"
        with mock.patch.object(engines, "resume_l2", return_value={"returncode": 0,
                               "agent": {"id": "resumed-worker", "sessionId": "conversation", "state": "working"}}) as resume:
            dispatch.resume(self.project, task["slug"])
        self.assertEqual((resume.call_args.args[0], resume.call_args.args[2], resume.call_args.kwargs["model"]),
                         ("codex", "conversation", "gpt-6.1-sol"))
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["launch_model"], saved["attempt"]), ("gpt-6.1-sol", 1))

    def test_quota_refresh_changes_fresh_dispatch_without_changing_running_or_resumed_sessions(self):
        self.policy("codex,claude:opus")
        self.quota.side_effect, self.other_quota.side_effect = self.read_quota, self.read_other_quota
        self.patch(engines.common, "_last_quota_refresh", None)
        native = self.patch(engines.claude, "_claude_quota", return_value={
            "known": True, "at": time.time(), "seven_day": 5, "five_hour": 1})
        self.patch(quota_codex, "read_quota", return_value={
            "known": True, "read_at": S.now(), "primary_used": 60,
            "primary_window_minutes": route.WEEK_MINUTES})
        engines.refresh_quotas(min_interval=0)
        first = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched):
            dispatch.run(self.project, first["slug"])
        running = S.load_task(self.project, first["slug"])
        self.assertEqual(running["l2_engine"], "claude")
        native.return_value = {"known": True, "at": time.time(), "seven_day": 90, "five_hour": 1}
        engines.refresh_quotas(min_interval=0)
        self.assertEqual(S.load_task(self.project, first["slug"]), running)
        second = T.new(self.project, "Next fresh task", "Use current quota")
        with mock.patch.object(engines, "start_l2", side_effect=self.launched):
            dispatch.run(self.project, second["slug"])
        self.assertEqual(S.load_task(self.project, second["slug"])["l2_engine"], "codex")
        T.message(self.project, first["slug"], T.OPERATOR_MESSAGE_ROLE, "Preserve this session", wake_blocked=False)
        T.block(self.project, first["slug"], "Ready to resume")
        with mock.patch.object(engines, "resume_l2", side_effect=lambda engine, name, sid, prompt, **kwargs: {
                "returncode": 0, "agent": {"id": "resumed-worker", "sessionId": sid}}) as resume:
            dispatch.resume(self.project, first["slug"])
        self.assertEqual((resume.call_args.args[0], resume.call_args.args[2]), ("claude", running["session_id"]))
        saved = S.load_task(self.project, first["slug"])
        for key in ("l2_engine", "session_id", "launch_model", "attempt"):
            self.assertEqual(saved[key], running[key])

    def test_project_pin_parks_quota_failure_without_replacing_conversation(self):
        self.policy("claude:fable > codex", l2_engine="claude", l2_model="opus")
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=self.launched):
            dispatch.run(self.project, task["slug"])
        running = S.load_task(self.project, task["slug"])
        with mock.patch.object(engines, "remove_l2_worker") as remove:
            server.on_l2_finished(self.project, {"task": running, "limited": engines.common._usage_limit("2099-01-01T00:00:00+00:00"),
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
            server.on_l2_finished(self.project, {"task": running, "limited": engines.common._usage_limit("2099-01-01T00:00:00+00:00"),
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

    def test_a_limit_met_mid_turn_on_l3_s_chosen_engine_answers_on_the_other_and_says_so(self):
        self.policy("claude:fable > codex", l3_choice={"engine": "claude", "model": "opus"})
        failure = {"session_id": "session", "text": "", "error": "usage window exhausted", "tools": [],
                   "limited": engines.common._usage_limit("2099-01-01T00:00:00+00:00"), "safe_to_retry": True}
        with mock.patch.object(engines, "claude_print", return_value=failure) as execute, \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("Codex answer")) as other:
            result = l3.turn(self.project, "Continue")
        self.assertEqual((execute.call_count, execute.call_args.kwargs["model"], other.call_count), (1, "opus", 1))
        self.assertTrue(result["completed"])
        self.assertEqual(self.notices(), ["Claude's usage limit is reached until 2099-01-01T00:00:00+00:00, "
                                          "so Codex is answering for now."])

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

    def test_sign_in_rejection_waits_for_the_operator_and_clears_on_their_successful_retry(self):
        self.policy("claude:opus")
        sign_in = engines.rejection("claude", {"error": "Failed to authenticate: OAuth session expired and could not be refreshed"})
        route.note_rejection({"engine": "claude", "model": "opus"}, sign_in)
        signed_in = []
        def answer(prompt, **kwargs):
            if signed_in:
                return {"session_id": "conversation", "text": "Answered", "tools": [], "error": None,
                        "usage": {}, "context_tokens": 1, "cost": 0, "turns": 1}
            return {"session_id": None, "text": "", "error": "Not logged in", "rejection": sign_in,
                    "safe_to_retry": True, "tools": []}
        with mock.patch.object(engines, "claude_print", side_effect=answer) as execute:
            held = l3.turn(self.project, "Report landed", trigger="report-landed")
            self.assertTrue(held["held"])
            self.assertIn("sign in, then Retry or Resume", held["error"])
            # Still signed out: the operator's Retry tries once and the rejection is recorded again,
            # so automatic turns keep waiting instead of launching.
            failed = l3.turn(self.project, "Are you there?")
            self.assertFalse(failed["completed"])
            self.assertEqual(execute.call_count, 1)
            self.assertTrue(l3.turn(self.project, "Report landed", trigger="report-landed")["held"])
            self.assertEqual(execute.call_count, 1)
            signed_in.append(True)
            self.assertTrue(l3.turn(self.project, "Are you there?")["completed"])
            self.assertEqual(execute.call_count, 2)
            self.assertIsNone(route.choice_unavailable("l2", {"engine": "claude"}))
            self.assertNotIn("held", l3.turn(self.project, "Report landed", trigger="report-landed"))
            self.assertEqual(execute.call_count, 3)

    @staticmethod
    def revoked(*args, **kwargs):
        """What claude_print returns for the CLI's revoked sign-in stream (tests.test_engine_availability)."""
        from tests.test_engine_availability import REVOKED, revoked_stream
        stdout = revoked_stream()
        return {"session_id": "session", "text": "", "error": REVOKED, "tools": [], "turns": 1,
                "rejection": engines.rejection("claude", {"raw_stdout": stdout}),
                "safe_to_retry": engines.common._safe_output("claude", stdout)}

    @staticmethod
    def answered(text):
        return {"session_id": "conversation", "reported_session_id": "conversation", "text": text, "tools": [],
                "error": None, "usage": {}, "context_tokens": 1, "cost": 0, "turns": 1}

    def notices(self):
        return [row["text"] for row in l3.chat_history(self.project) if row["role"] == "system"]

    def assert_no_provider_text(self):
        self.assertFalse([row for row in l3.chat_history(self.project) if "OAuth token revoked" in row["text"]])

    def test_a_revoked_sign_in_answers_on_the_other_engine_and_says_so_once_per_lapse(self):
        self.policy("claude:opus > codex")
        notice = ("Claude's sign-in has lapsed, so Codex is answering for now. "
                  "To sign in, run `claude auth login` on the computer running Altitude.")
        signed_in = []
        def claude(*args, **kwargs):
            return self.answered("Claude answer") if signed_in else self.revoked()
        with mock.patch.object(engines, "claude_print", side_effect=claude) as execute, \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("Codex answer")) as other:
            self.assertTrue(l3.turn(self.project, "It says activation failed")["completed"])
            self.assertTrue(l3.turn(self.project, "What failed?")["completed"])
            self.assertEqual((execute.call_count, other.call_count), (1, 2))
            self.assertEqual(self.notices(), [notice])
            replies = [row for row in l3.chat_history(self.project) if row["role"] == "assistant"]
            self.assertEqual([(row["engine"], row["text"]) for row in replies],
                             [("codex", "Codex answer"), ("codex", "Codex answer")])
            self.assert_no_provider_text()
            # The next usage check reads Claude again: the lapse ends, and a later lapse is told again.
            signed_in.append(True)
            route.signed_in("claude")
            self.assertEqual(l3.turn(self.project, "Back?")["engine"], "claude")
            signed_in.clear()
            self.assertTrue(l3.turn(self.project, "And now?")["completed"])
        self.assertEqual(self.notices(), [notice, notice])
        self.assert_no_provider_text()

    def test_a_revoked_sign_in_without_another_engine_keeps_the_message_and_tells_once(self):
        self.policy("claude:opus")
        with mock.patch.object(engines, "claude_print", side_effect=self.revoked) as execute:
            first = l3.turn(self.project, "It says activation failed")
            self.assertTrue(l3.turn(self.project, "Report landed", trigger="report-landed")["held"])
            second = l3.turn(self.project, "Oauth token for what?")
        # Each operator message retries the signed-out engine once; system turns keep waiting.
        self.assertEqual(execute.call_count, 2)
        for result in (first, second):
            self.assertTrue(result["undelivered"])
            self.assertFalse(result["completed"])
        self.assertEqual([row["text"] for row in l3.queued(self.project)],
                         ["It says activation failed", "Oauth token for what?"])
        self.assertEqual(self.notices(), [
            "Claude's sign-in has lapsed and no other engine can answer, so your message waits and sends once "
            "you are signed in again. To sign in, run `claude auth login` on the computer running Altitude."])
        self.assertFalse([row for row in l3.chat_history(self.project) if row["role"] in ("assistant", "error")])
        self.assert_no_provider_text()
        notice = next(row for row in l3.chat_history(self.project) if row["role"] == "system")
        self.assertEqual((notice["trigger"], notice["heads_up"]), ("fyi", True))

    def test_a_rejection_long_after_the_last_evidence_is_a_new_lapse(self):
        sign_in = engines.rejection("claude", {"error": "authentication_failed"})
        route.note_rejection({"engine": "claude"}, sign_in)
        self.assertTrue(route.tell_sign_in("claude", self.project))
        route.note_rejection({"engine": "claude"}, sign_in)
        self.assertFalse(route.tell_sign_in("claude", self.project))
        path = route._rejection_path({"engine": "claude"}, "sign-in")
        S.write_json(path, {**S.read_json(path), "at": "2000-01-01T00:00:00+00:00"})
        route.note_rejection({"engine": "claude"}, sign_in)
        self.assertTrue(route.tell_sign_in("claude", self.project))

    def test_a_lapse_seen_by_the_usage_check_moves_the_next_message_without_a_failed_turn(self):
        self.policy("claude:opus > codex")
        route.note_rejection({"engine": "claude"}, engines.rejection("claude", {"error": "authentication_failed"}))
        with mock.patch.object(engines, "claude_print") as execute, \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("Codex answer")):
            self.assertTrue(l3.turn(self.project, "Status?")["completed"])
        execute.assert_not_called()
        self.assertEqual(self.notices(), ["Claude's sign-in has lapsed, so Codex is answering for now. "
                                          "To sign in, run `claude auth login` on the computer running Altitude."])

    def exhausted(self, *, codex=False):
        """Claude's five-hour window at its limit with a reported reset; Codex at 1% of its week unless also out."""
        self.quota.return_value = {"known": True, "at": time.time(), "five_hour": 100, "seven_day": 40,
                                   "five_hour_resets": RESET_EPOCH, "seven_day_resets": RESET_EPOCH + 86400}
        self.other_quota.return_value = {"known": True, "read_at": S.now(),
                                         "primary_used": 100 if codex else 1, "primary_window_minutes": route.SHORT_MINUTES,
                                         "primary_resets": "2099-01-01T06:00:00+00:00",
                                         "secondary_used": 1, "secondary_window_minutes": route.WEEK_MINUTES}

    def test_an_operator_message_queued_on_l3_s_exhausted_engine_is_answered_on_the_other_with_one_line(self):
        # 2026-10-12: L3 on Claude (Fable), Claude's five-hour window used up, Codex at 1%, the operator's message
        # queued behind it. The stored L3 Only key that held it there no longer decides anything.
        self.register(self.project, l3_engine="claude", l3_choice={"engine": "claude", "model": "fable"})
        l3.save_info(self.project, {"engine_last": "claude"})
        self.exhausted()
        waiting = l3.queue_message(self.project, "Codex is available, is my message going?", trigger="chat",
                                   role=config.OPERATOR_ACTOR)
        with mock.patch.object(engines, "claude_print") as execute, \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("Yes, on Codex.")):
            self.assertTrue(l3.deliver_queued(self.project)["completed"])
            self.assertTrue(l3.turn(self.project, "Thanks")["completed"])
        execute.assert_not_called()
        self.assertEqual(l3.queued(self.project), [])
        replies = [(row["engine"], row["text"]) for row in l3.chat_history(self.project) if row["role"] == "assistant"]
        self.assertEqual(replies, [("codex", "Yes, on Codex."), ("codex", "Yes, on Codex.")])
        self.assertEqual(self.notices(), [f"Claude's usage limit is reached until {RESET_ISO}, so Codex is answering for now."])
        user = next(row for row in l3.chat_history(self.project) if row["role"] == "user")
        self.assertEqual((user["text"], user["queue_ids"]), (waiting["text"], [waiting["id"]]))

    def test_an_l3_choice_changed_while_a_message_waits_sends_it_on_the_new_choice(self):
        self.policy("claude:fable")
        self.exhausted()
        with mock.patch.object(engines, "claude_print") as execute, \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("Answered on Codex")) as other:
            held = l3.turn(self.project, "Are you there?")
            self.assertTrue(held["undelivered"])
            self.assertIsNone(l3.deliver_queued(self.project))
            other.assert_not_called()
            set_project_setting(self.project, "l3_choice", {"engine": "codex"})
            self.assertTrue(l3.deliver_queued(self.project)["completed"])
        execute.assert_not_called()
        self.assertEqual(l3.queued(self.project), [])
        replies = [row for row in l3.chat_history(self.project) if row["role"] == "assistant"]
        self.assertEqual([(row["engine"], row["text"], row["turn_id"]) for row in replies],
                         [("codex", "Answered on Codex", held["turn_id"])])

    def test_with_no_engine_able_to_run_the_waiting_message_gets_one_line_with_the_reset(self):
        self.policy("claude:fable > codex")
        self.exhausted(codex=True)
        with mock.patch.object(engines, "claude_print") as execute, mock.patch.object(engines, "codex_exec") as other:
            first = l3.turn(self.project, "Is the release out?")
            for _ in range(3):
                self.assertIsNone(l3.deliver_queued(self.project))
            second = l3.turn(self.project, "Hello?")
        execute.assert_not_called(); other.assert_not_called()
        self.assertTrue(first["undelivered"] and second["undelivered"])
        self.assertEqual([row["text"] for row in l3.queued(self.project)], ["Is the release out?", "Hello?"])
        self.assertEqual(self.notices(), ["Claude and Codex's usage limits are reached, so your message waits until "
                                          "2099-01-01T05:00:00+00:00."])
        self.assertFalse([row for row in l3.chat_history(self.project) if row["role"] in ("assistant", "error")])
        notice = next(row for row in l3.chat_history(self.project) if row["role"] == "system")
        self.assertEqual((notice["trigger"], notice["heads_up"]), ("fyi", True))

    def test_one_engine_at_its_limit_says_the_message_waits_until_its_reset(self):
        self.policy("claude:fable")
        self.exhausted()
        l3.queue_message(self.project, "Is the release out?", trigger="chat", role=config.OPERATOR_ACTOR)
        self.assertIsNone(l3.deliver_queued(self.project))
        self.assertEqual(self.notices(), [f"Claude's usage limit is reached until {RESET_ISO} and no other engine can "
                                          "answer, so your message waits until then."])

    def test_each_change_in_what_can_answer_gets_its_own_line_and_an_unchanged_one_is_not_repeated(self):
        self.policy("claude:fable > codex", l3_choice={"engine": "claude"})
        elsewhere = f"Claude's usage limit is reached until {RESET_ISO}, so Codex is answering for now."
        waits = f"Claude and Codex's usage limits are reached, so your message waits until {RESET_ISO}."
        with mock.patch.object(engines, "claude_print") as execute, \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("On Codex")):
            self.exhausted()
            self.assertTrue(l3.turn(self.project, "One")["completed"])
            self.exhausted(codex=True)
            self.assertTrue(l3.turn(self.project, "Two")["undelivered"])
            self.exhausted()
            self.assertTrue(l3.deliver_queued(self.project)["completed"])
            self.assertTrue(l3.turn(self.project, "Three")["completed"])
        execute.assert_not_called()
        self.assertEqual(self.notices(), [elsewhere, waits, elsewhere])

    def test_automatic_work_hands_off_with_the_line_while_the_chosen_engine_is_limited_and_returns_to_its_own_conversation(self):
        self.policy("claude:fable > codex", l3_choice={"engine": "claude"})
        claude = {"session_id": "claude-conversation", "text": "On Claude", "tools": [], "error": None, "usage": {},
                  "context_tokens": 1, "cost": 0, "turns": 1}
        with mock.patch.object(engines, "claude_print", return_value=claude) as execute, \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("On Codex")) as other:
            self.assertTrue(l3.turn(self.project, "Morning", trigger="system")["completed"])
            self.exhausted()
            self.assertEqual(l3.turn(self.project, "A report landed", trigger="system")["engine"], "codex")
            self.quota.return_value = {"known": True, "at": time.time(), "five_hour": 10, "seven_day": 40}
            self.assertEqual(l3.turn(self.project, "CI finished", trigger="system")["engine"], "claude")
        self.assertEqual([call.kwargs.get("resume") for call in execute.call_args_list], [None, "claude-conversation"])
        other.assert_called_once()
        self.assertEqual(self.notices(), [f"Claude's usage limit is reached until {RESET_ISO}, so Codex is answering for now."])

    def test_a_waiting_line_names_the_earliest_reported_reset_when_another_engine_reports_none(self):
        self.policy("claude:fable > codex")
        self.exhausted(codex=True)
        self.other_quota.return_value = {**self.other_quota.return_value, "primary_resets": None}
        self.assertTrue(l3.turn(self.project, "Is the release out?")["undelivered"])
        self.assertEqual(self.notices(), [f"Claude and Codex's usage limits are reached, so your message waits until "
                                          f"{RESET_ISO}."])

    def test_a_limit_that_ends_and_returns_is_announced_again(self):
        self.policy("claude:fable > codex", l3_choice={"engine": "claude"})
        line = f"Claude's usage limit is reached until {RESET_ISO}, so Codex is answering for now."
        claude = {"session_id": "claude-conversation", "text": "On Claude", "tools": [], "error": None, "usage": {},
                  "context_tokens": 1, "cost": 0, "turns": 1}
        with mock.patch.object(engines, "claude_print", return_value=claude), \
             mock.patch.object(engines, "codex_exec", side_effect=lambda *a, **k: self.answered("On Codex")):
            for limited in (True, True, False, True):
                if limited:
                    self.exhausted()
                else:
                    self.quota.return_value = {"known": True, "at": time.time(), "five_hour": 10, "seven_day": 40}
                self.assertTrue(l3.turn(self.project, "Next")["completed"])
        self.assertEqual(self.notices(), [line, line])

    def test_a_turn_pinned_to_a_signed_out_engine_stops_with_the_plain_reason(self):
        self.policy("claude:opus > codex")
        with mock.patch.object(engines, "claude_print", side_effect=self.revoked), \
             mock.patch.object(engines, "codex_exec") as other:
            result = l3.turn(self.project, "Use Claude for this", engine="claude")
        other.assert_not_called()
        self.assertFalse(result["completed"])
        errors = [row["text"] for row in l3.chat_history(self.project) if row["role"] == "error"]
        self.assertEqual(errors, ["provider rejected authentication or account access; sign in, then Retry or Resume"])
        self.assertEqual(self.notices(), ["Claude's sign-in has lapsed, so this turn stopped; Retry it once you are "
                                          "signed in again. To sign in, run `claude auth login` on the computer "
                                          "running Altitude."])
        self.assert_no_provider_text()

    def test_operator_retry_withdraws_only_the_engine_it_attempts(self):
        self.policy("claude:opus > codex")
        for engine in config.ENGINES:
            route.note_rejection({"engine": engine}, engines.rejection(engine, {"error": "authentication_error"}))
        answer = {"session_id": "conversation", "text": "Answered", "tools": [], "error": None,
                  "usage": {}, "context_tokens": 1, "cost": 0, "turns": 1}
        with mock.patch.object(engines, "claude_print", return_value=answer) as execute, \
             mock.patch.object(engines, "codex_exec") as other:
            self.assertTrue(l3.turn(self.project, "Signed in again")["completed"])
        execute.assert_called_once()
        other.assert_not_called()
        self.assertIsNone(route.choice_unavailable("l2", {"engine": "claude"}))
        self.assertIn("sign in, then Retry or Resume", route.choice_unavailable("l2", {"engine": "codex"}))

    def test_operator_retry_keeps_a_usage_limit_cooldown(self):
        self.policy("claude:opus")
        route.note_limit("claude", engines.usage_limit_in("You've hit your usage limit"))
        with mock.patch.object(engines, "claude_print") as execute:
            result = l3.turn(self.project, "Are you there?")
        execute.assert_not_called()
        self.assertIn("engine hold", result["error"])
        self.assertIn("usage window exhausted", result["error"])
