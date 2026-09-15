"""Owners assess freshness; withdrawal never supplies an operator answer or cancels required work."""
import builtins
import os
from unittest import mock

from tests.support import AltitudeCase, add_worktree, make_repo
from altitude import dispatch, engines, l3, server, state as S, tasks as T


class ConversationDecisions(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        task = T.new(self.project, "Index retention", "Keep rollback available.", hold_merge="Operator review")
        task.update(state="running", attempt=1, agent_id="owner", session_id="conversation")
        S.save_task(self.project, task)
        self.slug = task["slug"]
        self.ask()

    def ask(self, text="How long should we keep the old index?", *, waiting="burak"):
        task = S.load_task(self.project, self.slug)
        task["state"] = "running"
        S.save_task(self.project, task)
        return T.block(self.project, self.slug, text, actor="l2", updates={"waiting_on": waiting},
                       recommendation="Keep it for seven days.", recommendation_label="Use 7 days & resume",
                       recommendation_why="Allows rollback without indefinite storage.")["questions"][-1]

    def current(self):
        return T.question_views(self.project, self.slug)[-1]

    def test_message_readers_see_complete_records_during_publication(self):
        for name in ("conversation.jsonl", "inbox.jsonl"):
            with self.subTest(name=name):
                path = S.task_dir(self.project, self.slug) / name
                T._append_jsonl(path, {"id": "earlier", "text": "Keep the requested work."})
                before = T._rows(path, name)
                row = {"id": "new-guidance", "text": "Required revision. " * 1000}
                reads = []

                def split_write(stream):
                    write = stream.write

                    def interrupted(text):
                        middle = len(text) // 2
                        write(text[:middle])
                        stream.flush()
                        reads.append(T._rows(path, name))
                        return middle + write(text[middle:])

                    stream.write = interrupted
                    return stream

                real_open, real_fdopen = builtins.open, os.fdopen
                with S.project_lock(self.project), \
                     mock.patch.object(T, "open", side_effect=lambda *a, **k: split_write(real_open(*a, **k)),
                                       create=True), \
                     mock.patch.object(S.os, "fdopen", side_effect=lambda *a, **k: split_write(real_fdopen(*a, **k))):
                    T._append_jsonl(path, row)
                self.assertEqual(reads, [before])
                self.assertEqual(T._rows(path, name), before + [row])

    def test_acceptance_recovers_after_inbox_publication_fails(self):
        guidance = T.message(self.project, self.slug, "burak", "Keep the requested revisions.")
        question = self.current()
        path = S.task_dir(self.project, self.slug) / "inbox.jsonl"
        prefix = path.read_bytes()
        replace = os.replace

        def fail_inbox(source, destination):
            if destination == path:
                raise OSError("inbox publication interrupted")
            return replace(source, destination)

        with mock.patch.object(S.os, "replace", side_effect=fail_inbox), self.assertRaises(OSError):
            T.accept_question(self.project, self.slug, question["id"], 1)
        self.assertEqual(path.read_bytes(), prefix)
        receipt = self.current()["response"]
        for _ in range(2):
            T.accept_question(self.project, self.slug, question["id"], 1)
        self.assertEqual(self.current()["response"], receipt)
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)],
                         [guidance["id"], receipt["message_id"]])
        self.assertTrue(path.read_bytes().startswith(prefix))

    def resolve(self, row, *, question=None, **kwargs):
        question = question or self.current()
        return T.resolve_question(self.project, self.slug, question["id"], question["revision"], row["id"],
                                  expected_attempt=1, disposition=kwargs.pop("disposition", "answered"),
                                  reason=kwargs.pop("reason", "Keep it for fourteen days."), **kwargs)

    def test_discussion_keeps_the_same_question_after_wake_and_owner_reply(self):
        question = self.current()
        message = T.message(self.project, self.slug, "burak", "Can we roll back after day seven?")
        T.resume(self.project, self.slug)
        T.message(self.project, self.slug, "l2", "We would need a rebuild. Fourteen days would extend that window.",
                  expected_attempt=1)
        [card] = T.decisions(self.project)
        self.assertEqual((card["id"], card["revision"], card["status"]), (question["id"], 1, "open"))
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["agent_id"], task["session_id"], task["attempt"]), ("owner", "conversation", 1))
        prompt = T.render_inbox(T.pending(self.project, self.slug))
        self.assertIn(message["id"], prompt)
        self.assertIn(question["id"], prompt)
        self.assertIn("does not authorize", prompt)
        self.assertEqual(task["hold_merge"], "Operator review")
        state = S.regen_state_md(self.project).split("## Tasks")[0]
        self.assertIn(question["id"], state)
        self.assertIn("Keep it for seven days.", state)

    def test_simple_contextual_answer_resolves_without_keyword_or_second_confirmation(self):
        message = T.message(self.project, self.slug, "burak", "14 days")
        self.assertEqual(self.current()["status"], "open")
        result = self.resolve(message)
        self.assertEqual(result["resolution"]["message_id"], message["id"])
        self.assertEqual(result["resolution"]["text"], "Keep it for fourteen days.")
        self.assertEqual(T.decisions(self.project), [])
        self.assertNotIn("How long should", S.regen_state_md(self.project).split("## Tasks")[0])
        self.assertEqual(self.resolve(message), result, "the same semantic record is retry-idempotent")
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 2)

    def test_owner_withdraws_before_analysis_without_changing_work_or_authority(self):
        question = self.current()
        guidance = T.message(self.project, self.slug, "l3", "Check the changed storage requirement first.")
        T.resume(self.project, self.slug)
        before = S.load_task(self.project, self.slug)
        args = (self.project, self.slug, question["id"], 1, None)
        kwargs = dict(disposition="withdrawn", reason="Checking whether storage changes this choice.", expected_attempt=1)
        result = T.resolve_question(*args, **kwargs)
        self.assertEqual(result["resolution"]["by"], "l2")
        self.assertIsNone(result["resolution"]["message_id"])
        after = S.load_task(self.project, self.slug)
        for key in ("state", "hold_merge", "agent_id", "session_id", "attempt", "resume_request", "resume_after"):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(T.pending(self.project, self.slug), [guidance])
        self.assertEqual(T.decisions(self.project), [])
        self.assertEqual(T.resolve_question(*args, **kwargs), result)
        with self.assertRaises(T.TransitionError):
            T.accept_question(self.project, self.slug, question["id"], 1)
        fresh = self.ask(question["detail"])
        self.assertNotEqual(fresh["id"], question["id"])
        self.assertEqual(fresh["revision"], 1)
        self.assertIsNone(fresh["resolution"])
        with self.assertRaises(T.TransitionError):
            T.resolve_question(*args, **{**kwargs, "reason": "A conflicting retry"})

    def test_withdrawal_refuses_decision_fields_and_cannot_replace_an_answer(self):
        question = self.current()
        args = (self.project, self.slug, question["id"], 1, None)
        kwargs = dict(disposition="withdrawn", reason="Review needs checking.", expected_attempt=1)
        before = S.load_task(self.project, self.slug)
        for extra in ({"remaining": "Approve later?"}, {"recommendation": "Merge"}, {"source": "project"},
                      {"l3_authority": "L3 said so"}, {"expected_attempt": 0}):
            with self.subTest(extra=extra), self.assertRaises(T.TransitionError):
                T.resolve_question(*args, **{**kwargs, **extra})
            self.assertEqual(S.load_task(self.project, self.slug), before)
        l3.chat_log(self.project, "user", "Use seven days", trigger="chat")
        for disposition in ("answered", "superseded"):
            with self.assertRaises(T.TransitionError):
                T.resolve_question(*args, **{**kwargs, "disposition": disposition, "source": "project"})
        accepted = T.accept_question(self.project, self.slug, question["id"], 1)
        accepted = self.resolve({"id": accepted["response"]["message_id"]})
        correction = T.message(self.project, self.slug, "burak", "Finish the extra checks before merging.")
        with self.assertRaises(T.TransitionError):
            T.resolve_question(*args, **kwargs)
        self.assertEqual(self.current()["resolution"], accepted["resolution"])
        self.assertIn(correction, T.pending(self.project, self.slug))
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], "Operator review")

    def test_partial_answer_retains_only_relevant_remainder_then_new_direction_closes_it(self):
        question = self.ask("How long should we keep the index, and when should cleanup run?")
        message = T.message(self.project, self.slug, "burak", "Keep it fourteen days; I still need to decide cleanup.")
        result = self.resolve(message, remaining="When should cleanup run after fourteen days?")
        self.assertEqual(result["resolution"]["remaining"], "When should cleanup run after fourteen days?")
        remaining = self.current()
        self.assertEqual((remaining["id"], remaining["revision"]), (question["id"], question["revision"] + 1))
        self.assertIsNone(remaining["recommendation"], "partial approval must not inherit the old recommendation")
        self.assertEqual([q["question"] for q in T.decisions(self.project)], [remaining["question"]])
        with self.assertRaisesRegex(T.TransitionError, "no longer open"):
            T.accept_question(self.project, self.slug, question["id"], question["revision"])
        change = T.message(self.project, self.slug, "burak", "Drop this rollout; use the managed index instead.")
        result = self.resolve(change, disposition="superseded", reason="Managed index removes retention and cleanup choices.")
        self.assertEqual(result["resolution"]["disposition"], "superseded")
        self.assertEqual(T.decisions(self.project), [])
        self.assertNotIn("acceptance_message", S.load_task(self.project, self.slug)["questions"][-1])

    def test_original_authority_and_current_attempt_are_required(self):
        for role, by in (("l2", "l2"), ("l3", "l3"), ("burak", "l3")):
            with self.subTest(role=role, by=by):
                message = T.message(self.project, self.slug, role, "The operator approved seven days.", by=by)
                with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
                    self.resolve(message)
        question = self.current()
        message = T.message(self.project, self.slug, "burak", "14 days")
        with self.assertRaisesRegex(T.TransitionError, "attempt 0 is no longer current"):
            T.resolve_question(self.project, self.slug, question["id"], 1, message["id"], expected_attempt=0,
                               disposition="answered", reason="14 days")
        self.assertEqual(self.current()["status"], "open")

    def test_l3_answer_after_escalation_requires_explicit_owner_authority_assessment(self):
        change = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Drop this retention dilemma.")
        self.resolve(change, disposition="superseded", reason="Original dilemma is no longer relevant.")
        self.ask(waiting="l3")
        answer = T.message(self.project, self.slug, "l3", "The brief already specifies fourteen days.")
        self.resolve(answer)
        self.assertEqual(self.current()["resolution"]["by"], "l3")
        self.ask(waiting="l3")
        T.escalate(self.project, self.slug, "Choose retention within the agreed budget.",
                   recommendation="Use seven days.")
        answer = T.message(self.project, self.slug, "l3", "I believe seven days is fine.")
        with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
            self.resolve(answer)

    def test_owner_records_l3_authority_and_retry_cannot_replace_its_rationale(self):
        question = self.ask("Does the assigned lease permit test changes?")
        answer = T.message(self.project, self.slug, "l3", "The assigned lease includes tests/; proceed within that scope.")
        basis = "Task status paths includes tests/; L3 already assigned that scope, so no operator choice remains."
        with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
            self.resolve(answer)
        result = self.resolve(answer, reason="Test changes are within the assigned lease.", l3_authority=basis)
        receipt = result["resolution"]
        self.assertEqual((result["audience"], receipt["by"], receipt["source"], receipt["message_id"]),
                         ("operator", "l3", "task", answer["id"]))
        self.assertEqual((receipt["l3_authority"], receipt["recorded_by"], receipt["recorded_attempt"]), (basis, "l2", 1))
        self.assertIn(basis, T.question_context(result))
        self.assertEqual(T.decisions(self.project), [])
        self.assertEqual(self.resolve(answer, reason=receipt["text"], l3_authority="  " + basis + "  "), result)
        with self.assertRaisesRegex(T.TransitionError, "already resolved"):
            self.resolve(answer, l3_authority="A different claim.", reason=receipt["text"])
        replacement = self.ask("Approve an expanded lease with a security policy change?")
        with self.assertRaisesRegex(T.TransitionError, "exact question revision"):
            self.resolve(answer, l3_authority=basis)
        self.assertEqual(self.current()["id"], replacement["id"])
        self.assertNotEqual(question["id"], replacement["id"])
        self.assertEqual(self.current()["status"], "open")

    def test_l3_attestation_refuses_blank_spoofed_operator_and_unbound_sources(self):
        for role, by in (("l2", "l2"), ("l3", "l2"), (T.OPERATOR_MESSAGE_ROLE, "l3"),
                         (T.OPERATOR_MESSAGE_ROLE, T.OPERATOR_MESSAGE_ROLE)):
            source = T.message(self.project, self.slug, role, "Use the original brief.", by=by)
            with self.subTest(role=role, by=by), self.assertRaisesRegex(T.TransitionError, "original L3 task message"):
                self.resolve(source, l3_authority="The brief already settles this.")
        source = T.message(self.project, self.slug, "l3", "The brief settles this.")
        for empty in ("", "  ", False):
            with self.subTest(empty=empty), self.assertRaisesRegex(T.TransitionError, "specific evidence"):
                self.resolve(source, l3_authority=empty)
        l3.chat_log(self.project, "user", "Keep fourteen days.", trigger="chat", turn_id="operator-decision")
        with self.assertRaisesRegex(T.TransitionError, "original L3 task message"):
            self.resolve({"id": "operator-decision"}, source="project", l3_authority="An L3 relay.")
        unbound = {k: v for k, v in source.items() if k != "question_refs"}
        with mock.patch.object(T, "task_messages", return_value=[unbound]), \
             self.assertRaisesRegex(T.TransitionError, "exact question revision"):
            self.resolve(source, l3_authority="The brief already settles this.")
        old = {**source, "at": "2000-01-01T00:00:00+00:00"}
        with mock.patch.object(T, "task_messages", return_value=[old]), \
             self.assertRaisesRegex(T.TransitionError, "predates"):
            self.resolve(source, l3_authority="The brief already settles this.")
        self.assertEqual(self.current()["status"], "open")

    def test_partial_l3_answer_preserves_operator_remainder_and_independent_fault_or_wait(self):
        for waiting, fault in (("l3", "fixture-fault"), (None, None)):
            with self.subTest(waiting=waiting, fault=fault):
                task = S.load_task(self.project, self.slug)
                task["fault"] = None
                S.save_task(self.project, task)
                question = self.ask("Are tests in scope, and may we change the security policy?")
                self.assertEqual(question["detail"], "Are tests in scope, and may we change the security policy?")
                task = S.load_task(self.project, self.slug)
                task.update(waiting_on=waiting, fault=fault, resume_after="2099-01-01T00:00:00+00:00",
                            blocked_reason="Independent recovery or capacity wait.")
                S.save_task(self.project, task)
                source = T.message(self.project, self.slug, "l3", "The lease covers tests; security policy still needs the operator.")
                before = S.load_task(self.project, self.slug)
                self.resolve(source, reason="Tests are already assigned.", l3_authority="The recorded task lease includes tests/.",
                             remaining="May we change the security policy?")
                after = S.load_task(self.project, self.slug)
                for key in ("state", "waiting_on", "fault", "resume_after", "resume_request", "blocked_reason",
                            "agent_id", "session_id", "attempt", "hold_merge"):
                    self.assertEqual(after.get(key), before.get(key), key)
                remainder = self.current()
                self.assertEqual((remainder["id"], remainder["revision"], remainder["audience"]),
                                 (question["id"], question["revision"] + 1, "operator"))
                self.assertIsNone(remainder["recommendation"])
                with self.assertRaisesRegex(T.TransitionError, "exact question revision"):
                    self.resolve(source, l3_authority="The recorded task lease includes tests/.")
                self.assertEqual([q["id"] for q in T.decisions(self.project)], [remainder["id"]])

    def test_project_relay_cites_actual_operator_turn_and_refuses_system_or_assistant(self):
        question = self.current()
        for role, trigger, turn in (("assistant", "chat", "assistant"), ("user", "report", "system")):
            l3.chat_log(self.project, role, "Use seven days", trigger=trigger, turn_id=turn)
            with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
                self.resolve({"id": turn}, source="project")
        l3.chat_log(self.project, "user", "Use fourteen days", trigger="chat", turn_id="operator-turn")
        result = self.resolve({"id": "operator-turn"}, source="project")
        self.assertEqual((result["id"], result["resolution"]["source"], result["resolution"]["message_id"]),
                         (question["id"], "project", "operator-turn"))

    def test_stale_navigation_retains_anchors_and_cannot_target_a_new_recommendation(self):
        first = self.current()
        current = self.ask("Should we keep the index for fourteen days instead?")
        with self.assertRaisesRegex(T.TransitionError, "no longer open"):
            T.accept_question(self.project, self.slug, first["id"], first["revision"])
        with mock.patch.object(server.monitor, "sessions", return_value=[]):
            view = server.task_view(self.project, self.slug)
        self.assertEqual(view["question"]["revision"], current["revision"])
        self.assertEqual([m["id"] for m in view["messages"]], [q["anchor_id"] for q in view["questions"]])
        history = T.message(self.project, self.slug, "burak", "Why was the first proposal changed?",
                            question_id=first["id"], revision=first["revision"])
        self.assertIn(f"{first['id']} revision {first['revision']} is resolved", history["question_context"])
        with self.assertRaisesRegex(T.TransitionError, "different question revision"):
            self.resolve(history)
        with self.assertRaisesRegex(T.TransitionError, "unavailable"):
            T.message(self.project, self.slug, "burak", "hello", question_id="missing", revision=1)

    def test_single_revision_updates_the_group_reason_used_for_reparking(self):
        original = self.current()
        question = self.ask("Choose the revised retention period.")
        task = S.load_task(self.project, self.slug)
        context = T.group_context(task)
        self.assertIn("Saved group reason: Choose the revised retention period.", context)
        self.assertNotIn(f"Saved group reason: {original['detail']}", context)
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Can we discuss that revision?")
        T.resume(self.project, self.slug)
        T.block(self.project, self.slug, "Choose the revised retention period.", actor="l2",
                updates={"waiting_on": "l3"})
        current = self.current()
        self.assertEqual((current["id"], current["revision"], current["recommendation"], current["audience"]),
                         (question["id"], question["revision"], question["recommendation"], "operator"))

    def test_acceptance_and_message_share_one_durable_write_and_claim_delivery_is_recoverable(self):
        question = self.current()
        with mock.patch.object(S, "append_event", side_effect=OSError("event write failed")):
            with self.assertRaises(OSError):
                T.accept_question(self.project, self.slug, question["id"], 1)
        receipt = T.accept_question(self.project, self.slug, question["id"], 1)
        [message] = T.pending(self.project, self.slug)
        self.assertEqual(T.task_messages(self.project, self.slug)[-1], message)
        self.assertEqual(receipt["response"]["message_id"], message["id"])
        claim = T.claim_resume(self.project, self.slug)
        self.assertEqual(claim["messages"], [message])
        self.assertEqual(T.pending(self.project, self.slug), [])
        T.release_resume_claim(self.project, self.slug, claim["id"], consume_request=False)
        self.assertEqual(T.pending(self.project, self.slug), [message])
        T.accept_question(self.project, self.slug, question["id"], 1)
        self.assertEqual(T.pending(self.project, self.slug), [message], "retry preserves the restored batch without doubling it")
        T.take_inbox(self.project, self.slug)
        T.accept_question(self.project, self.slug, question["id"], 1)
        self.assertEqual(T.pending(self.project, self.slug), [])

    def test_acceptance_claim_release_failure_keeps_its_only_copy_durable_until_recovery(self):
        question = self.current()
        receipt = T.accept_question(self.project, self.slug, question["id"], 1)
        [message] = T.pending(self.project, self.slug)
        claim = T.claim_resume(self.project, self.slug)
        self.assertEqual(claim["phase"], "claimed", "the provider has not received the batch yet")
        with mock.patch.object(S, "atomic_write", side_effect=OSError("release write failed")):
            with self.assertRaises(OSError):
                T.release_resume_claim(self.project, self.slug, claim["id"], consume_request=False)
        self.assertEqual(S.load_task(self.project, self.slug)["resume_claim"]["messages"], [message])
        self.assertEqual(T.accept_question(self.project, self.slug, question["id"], 1)["response"], receipt["response"])
        self.assertEqual(T.pending(self.project, self.slug), [], "the durable claim owns delivery until release succeeds")
        T.release_resume_claim(self.project, self.slug, claim["id"], consume_request=False)
        T.accept_question(self.project, self.slug, question["id"], 1)
        self.assertEqual(T.pending(self.project, self.slug), [message])
        self.assertEqual(T.take_inbox(self.project, self.slug), [message])
        self.assertEqual(T.pending(self.project, self.slug), [])

    def test_retry_after_report_or_archival_returns_receipt_without_new_inbox_work(self):
        question = self.current()
        with mock.patch.object(T, "_append_jsonl", side_effect=OSError("delivery write failed")):
            with self.assertRaises(OSError):
                T.accept_question(self.project, self.slug, question["id"], 1)
        receipt = self.current()["response"]
        # The owner consumes the recovered acceptance before handing its report to review.
        self.assertEqual(len(T.take_inbox(self.project, self.slug)), 1)
        T.report(self.project, self.slug, {"verdict": "ok"})
        for lifecycle in ("reported", "done"):
            with self.subTest(lifecycle=lifecycle):
                if lifecycle == "done":
                    T.done(self.project, self.slug)
                result = T.accept_question_result(self.project, self.slug, question["id"], 1)
                self.assertEqual(result["question"]["response"], receipt)
                self.assertEqual(result["question_group"]["questions"][0]["state"], lifecycle)
                self.assertFalse((S.task_dir(self.project, self.slug) / "inbox.jsonl").exists())
                self.assertEqual(T.pending(self.project, self.slug), [])
                self.assertNotIn("resume_after", S.load_task(self.project, self.slug))

    def test_legacy_parsed_question_keeps_its_display_text_when_authority_changes(self):
        text = "Which retention policy? Option A: keep seven days. Option B: keep fourteen days. I recommend A."
        change = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "The previous dilemma is obsolete.")
        self.resolve(change, disposition="superseded", reason="Ask the budget question instead.")
        T.resume(self.project, self.slug)
        T.block(self.project, self.slug, text, actor="l2", updates={"waiting_on": "l3"})
        before = self.current()
        self.assertEqual(before["question"], "Which retention policy?")
        self.assertIsNotNone(before["recommendation"])
        T.escalate(self.project, self.slug, text)
        current = self.current()
        self.assertEqual(current["question"], before["question"])
        self.assertEqual(current["detail"], before["detail"])
        self.assertEqual(current["recommendation"], before["recommendation"])
        self.assertEqual((current["id"], current["revision"], current["audience"]), (before["id"], 2, "operator"))

    def test_rejected_task_retry_does_not_recreate_failed_acceptance_delivery(self):
        question = self.current()
        with mock.patch.object(T, "_append_jsonl", side_effect=OSError("delivery write failed")):
            with self.assertRaises(OSError):
                T.accept_question(self.project, self.slug, question["id"], 1)
        receipt = self.current()["response"]
        with mock.patch.object(engines, "remove_l2_worker", return_value="stopped fixture"):
            T.reject(self.project, self.slug, "The rollout was canceled.")
        result = T.accept_question_result(self.project, self.slug, question["id"], 1)
        self.assertEqual(result["question"]["response"], receipt)
        self.assertEqual(result["question_group"]["questions"][0]["state"], "rejected")
        self.assertFalse((S.task_dir(self.project, self.slug) / "inbox.jsonl").exists())
        self.assertEqual(T.pending(self.project, self.slug), [])

    def test_rejection_disposes_of_open_question_without_accepting_it(self):
        with mock.patch.object(engines, "remove_l2_worker", return_value="stopped fixture"):
            T.reject(self.project, self.slug, "Direction changed")
        self.assertEqual(self.current()["resolution"]["disposition"], "rejected")
        self.assertEqual(T.decisions(self.project), [])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 1)

    def test_escalation_alone_never_requests_a_worker_wake(self):
        self.ask(waiting="l3")
        T.escalate(self.project, self.slug, "Choose a retention period.", recommendation="Keep seven days.")
        self.assertTrue(T.pending(self.project, self.slug))
        self.assertEqual(dispatch.resume_due(self.project), [])
        T.message(self.project, self.slug, "burak", "Could it be longer?")
        with mock.patch.object(dispatch, "wip_hold", return_value=None):
            self.assertEqual(dispatch.resume_due(self.project), [self.slug])

    def test_acceptance_is_saved_even_while_worker_capacity_is_unavailable(self):
        question = self.current()
        with mock.patch.object(dispatch, "wip_hold", return_value="capacity unavailable"):
            T.accept_question(self.project, self.slug, question["id"], question["revision"])
            self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertEqual(self.current()["status"], "open")
        self.assertEqual(T.decisions(self.project), [])
        self.assertTrue(S.load_task(self.project, self.slug)["resume_after"])

    def test_completion_disposes_of_the_question_with_a_terminal_receipt(self):
        make_repo(self.repo)
        worktree = add_worktree(self.repo, self.slug)
        task = T.resume(self.project, self.slug)
        task["worktree"] = str(worktree)
        S.save_task(self.project, task)
        T.done(self.project, self.slug, actor="l2", expected_attempt=1, digest="Research complete")
        T.finalize_completion(self.project, self.slug)
        self.assertEqual(self.current()["resolution"]["disposition"], "completed")
        self.assertEqual(T.decisions(self.project), [])

    def test_report_handoff_closes_previous_question_without_accepting_its_recommendation(self):
        question = self.current()
        T.report(self.project, self.slug, {"verdict": "ok"})
        receipt = self.current()["resolution"]
        self.assertEqual(receipt["disposition"], "superseded")
        self.assertIn("recommendation was not accepted", receipt["text"])
        self.assertEqual(T.decisions(self.project), [])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 1)
        with self.assertRaisesRegex(T.TransitionError, "no longer open"):
            T.accept_question(self.project, self.slug, question["id"], question["revision"])
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], "Operator review")
        T.done(self.project, self.slug)
        self.assertEqual(self.current()["resolution"], receipt, "archival preserves the actual report disposition")

    def test_question_poll_uses_one_durable_adoption_then_only_reads_without_the_project_lock(self):
        task = S.load_task(self.project, self.slug)
        task.pop("questions")
        task.pop("block_actor")
        task["updated"] = "2000-01-01T00:00:00+00:00"
        S.write_json(S.status_path(self.project, self.slug), task)
        with mock.patch.object(S, "project_lock", wraps=S.project_lock) as lock:
            adopted = T.question_views(self.project, self.slug)
        lock.assert_called_once_with(self.project)
        self.assertEqual(S.load_task(self.project, self.slug)["updated"], task["updated"])
        with mock.patch.object(S, "project_lock", side_effect=AssertionError("poll must remain a read")), \
             mock.patch.object(S, "write_json", side_effect=AssertionError("poll must not write")), \
             mock.patch.object(S, "read_events", side_effect=AssertionError("adopted poll needs no event scan")):
            self.assertEqual(T.question_views(self.project, self.slug), adopted)

    def test_state_summary_distinguishes_operator_and_l3_questions_during_discussion(self):
        operator = self.current()
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Can we discuss it?")
        T.resume(self.project, self.slug)
        other = T.new(self.project, "Lease decision", "Clarify the lease.")
        other["state"] = "running"
        S.save_task(self.project, other)
        coordinator = T.block(self.project, other["slug"], "Does the lease include tests?", actor="l2",
                              updates={"waiting_on": "l3"})["questions"][-1]
        summary = S.regen_state_md(self.project)
        needs_user, rest = summary.split("## Needs L3 input")
        needs_l3 = rest.split("## Tasks")[0]
        self.assertIn(operator["id"], needs_user)
        self.assertNotIn(coordinator["id"], needs_user)
        self.assertIn(coordinator["id"], needs_l3)
        self.assertNotIn(operator["id"], needs_l3)

    def test_new_queued_task_without_question_history_still_refuses_task_messages(self):
        queued = T.new(self.project, "No owner yet", "Wait for dispatch.")
        for role in (T.OPERATOR_MESSAGE_ROLE, "l3", "l2"):
            with self.subTest(role=role), self.assertRaisesRegex(T.TransitionError, "in queued state"):
                T.message(self.project, queued["slug"], role, "A new message")
        self.assertEqual(T.pending(self.project, queued["slug"]), [])

    def test_owner_cli_records_cited_message_and_refuses_cross_task_or_stale_attempt(self):
        question = self.current()
        message = T.message(self.project, self.slug, "burak", "14 days")
        args = ["--project", self.project, "task", "resolve", self.slug, "--question", question["id"],
                "--revision", "1", "--message", message["id"], "--disposition", "answered", "--reason", "14 days"]
        env = {"ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": self.project,
               "ALTITUDE_TASK": "another-task", "ALTITUDE_ATTEMPT": "1"}
        self.assertNotEqual(self.alt(*args, env=env).returncode, 0)
        env.update(ALTITUDE_TASK=self.slug, ALTITUDE_ATTEMPT="0")
        self.assertNotEqual(self.alt(*args, env=env).returncode, 0)
        env["ALTITUDE_ATTEMPT"] = "1"
        result = self.alt(*args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current()["resolution"]["message_id"], message["id"])

    def test_withdraw_cli_requires_current_owner_and_no_operator_source(self):
        question = self.current()
        args = ["task", "resolve", self.slug, "--question", question["id"], "--revision", "1",
                "--disposition", "withdrawn", "--reason", "Reassess before continuing."]
        env = {"ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2",
               "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"}
        for override in ({"ALTITUDE_ACTOR": "l3"}, {"ALTITUDE_TASK": "another-task"}, {"ALTITUDE_ATTEMPT": "0"}):
            self.assertNotEqual(self.alt(*args, env={**env, **override}).returncode, 0)
        self.assertNotEqual(self.alt(*args, "--message", "operator-message", env=env).returncode, 0)
        result = self.alt(*args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current()["resolution"]["disposition"], "withdrawn")

    def test_cli_l3_authority_remains_current_owner_only_and_retains_source(self):
        question = self.ask("May I run the required local tests?")
        message = T.message(self.project, self.slug, "l3", "The project rules require local tests.")
        basis = "AGENTS.md requires local tests; no new operator choice is needed."
        args = ["--project", self.project, "task", "resolve", self.slug, "--question", question["id"],
                "--revision", str(question["revision"]), "--message", message["id"],
                "--disposition", "answered", "--reason", "Run the required local tests.", "--l3-authority", basis]
        env = {"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": self.project,
               "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"}
        self.assertNotEqual(self.alt(*args, env=env).returncode, 0)
        env.update(ALTITUDE_ACTOR="l2", ALTITUDE_TASK="another-task")
        self.assertNotEqual(self.alt(*args, env=env).returncode, 0)
        env.update(ALTITUDE_TASK=self.slug, ALTITUDE_ATTEMPT="0")
        self.assertNotEqual(self.alt(*args, env=env).returncode, 0)
        env["ALTITUDE_ATTEMPT"] = "1"
        result = self.alt(*args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = self.current()["resolution"]
        self.assertEqual((receipt["by"], receipt["message_id"], receipt["l3_authority"]), ("l3", message["id"], basis))
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], "Operator review")

    def test_legacy_timed_operational_holds_and_stops_never_become_human_questions(self):
        for actor, timed in (("altd", True), (T.OPERATOR_MESSAGE_ROLE, False), (None, True)):
            with self.subTest(actor=actor, timed=timed):
                task = T.new(self.project, f"Legacy wait {actor}", "Resume the existing work.")
                task.update(state="blocked", blocked_reason="Waiting for capacity", attempt=1)
                if timed:
                    task["resume_after"] = "2099-01-01T00:00:00+00:00"
                S.save_task(self.project, task)
                if actor:
                    S.append_event(self.project, task["slug"], "state", frm="running", to="blocked", by=actor,
                                   reason=task["blocked_reason"])
                self.assertEqual(T.question_views(self.project, task["slug"]), [])
                T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "The machine is ready.")
                self.assertFalse(S.load_task(self.project, task["slug"]).get("questions"))
                T.resume(self.project, task["slug"])
                self.assertFalse(S.load_task(self.project, task["slug"]).get("questions"))

    def test_legacy_human_dilemma_uses_its_block_event_before_a_timed_discussion_wake(self):
        task = S.load_task(self.project, self.slug)
        original = task["questions"][-1]["detail"]
        task.pop("questions")
        task.pop("block_actor")
        task.update(blocked_reason="waiting: no worker capacity", resume_after="2099-01-01T00:00:00+00:00")
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "resume-held", hold="no worker capacity")
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Could we retain it longer?")
        question = self.current()
        self.assertEqual(question["detail"], original)
        self.assertEqual(question["asked_by"], "l2")
        self.assertEqual(T.task_messages(self.project, self.slug)[0]["id"], question["anchor_id"])
        self.assertIn(question["id"], message["question_context"])
        T.resume(self.project, self.slug)
        self.assertEqual(self.current()["status"], "open")
        self.assertEqual(T.decisions(self.project)[0]["id"], question["id"])

    def test_owner_reparks_the_same_question_after_discussion_without_revising_or_downgrading_it(self):
        for escalated in (False, True):
            with self.subTest(escalated=escalated):
                if escalated:
                    T.escalate(self.project, self.slug, "Choose the retention period.",
                               recommendation="Keep it for seven days.", recommendation_label="Use 7 days & resume",
                               recommendation_why="Allows rollback within the storage budget.")
                before = self.current()
                T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Could rollback take longer?")
                T.resume(self.project, self.slug)
                T.message(self.project, self.slug, "l2", "Yes; fourteen days would allow a longer rollback window.",
                          expected_attempt=1)
                result = self.alt("--project", self.project, "task", "block", self.slug, "--reason", before["detail"],
                                  env={"ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": self.project,
                                       "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"})
                self.assertEqual(result.returncode, 0, result.stderr)
                after = self.current()
                for field in ("id", "revision", "anchor_id", "recommendation", "audience", "asked_by", "status"):
                    self.assertEqual(after[field], before[field], field)
                self.assertEqual(S.load_task(self.project, self.slug)["waiting_on"], T.OPERATOR_MESSAGE_ROLE)
                self.assertEqual(T.decisions(self.project), [after])
                self.assertFalse(l3.queue_path(self.project).exists(), "parking an operator dilemma does not queue L3 again")
                self.assertIn("Keep it visible if still valid", T.question_context(after))
        T.resume(self.project, self.slug)
        changed = T.block(self.project, self.slug, before["detail"], actor="l2", updates={"waiting_on": "l3"},
                          recommendation="Keep it for fourteen days.")["questions"][-1]
        self.assertEqual(changed["id"], before["id"])
        self.assertEqual(changed["revision"], before["revision"] + 1)
        self.assertNotEqual(changed["anchor_id"], before["anchor_id"])
        self.assertEqual(changed["audience"], "operator")
        self.assertEqual(changed["recommendation"]["text"], "Keep it for fourteen days.")
