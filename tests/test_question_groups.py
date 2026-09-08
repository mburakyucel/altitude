"""Independent questions share a conversation and one atomic answer submission, never implicit defaults."""
import copy
import json
from unittest import mock

from tests.support import AltitudeCase
from altitude import dispatch, server, state as S, tasks as T


class QuestionGroups(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        task = T.new(self.project, "Search rollout", "Choose a small rollout.", hold_merge="Operator review")
        task.update(state="running", attempt=1, agent_id="owner", session_id="conversation")
        S.save_task(self.project, task)
        self.slug = task["slug"]
        self.payload = {"questions": [
            {"question": "How long should rollback stay available?", "options": [
                {"key": "seven", "label": "7 days", "text": "Keep rollback for seven days."},
                {"key": "fourteen", "label": "14 days", "text": "Keep rollback for fourteen days."}],
             "recommended_key": "seven", "why": "Fits the storage budget."},
            {"question": "When should cleanup run?", "options": [
                {"key": "night", "label": "At night", "text": "Run cleanup at night."}],
             "recommended_key": "night"},
            {"question": "Which team owns the rollout?"}]}

    def ask(self, payload=None, waiting="burak"):
        T.block(self.project, self.slug, "Set rollout details.", actor="l2", updates={"waiting_on": waiting},
                questions=payload or self.payload)
        return self.group()

    def group(self):
        return T.question_group_view(self.project, S.load_task(self.project, self.slug))

    def answers(self, group, indices=(0, 1)):
        return [{"question_id": group["questions"][i]["id"], "revision": group["questions"][i]["revision"],
                 "option_key": group["questions"][i]["recommended_key"]} for i in indices]

    def resolve(self, question, message, **kwargs):
        return T.resolve_question(self.project, self.slug, question["id"], question["revision"], message["id"],
                                  expected_attempt=1, disposition=kwargs.pop("disposition", "answered"),
                                  reason=kwargs.pop("reason", "The search team owns this rollout."), **kwargs)

    def test_publication_has_three_independent_questions_and_stable_conversation_anchor(self):
        group = self.ask()
        self.assertEqual(group["revision"], 1)
        self.assertEqual(len(group["questions"]), 3)
        self.assertEqual(group["anchor_id"], group["questions"][0]["anchor_id"])
        self.assertEqual([q["detail"] for q in group["questions"]], [q["question"] for q in self.payload["questions"]])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 3)
        self.assertEqual(len(T.decisions(self.project)), 3)
        self.assertIsNone(group["questions"][2]["recommendation"])
        self.assertEqual(group["questions"][0]["recommended_key"], "seven")
        with mock.patch.object(server.monitor, "sessions", return_value=[]):
            self.assertEqual(server.task_view(self.project, self.slug)["question_group"], group)
        with mock.patch.object(S, "project_lock", side_effect=AssertionError("poll remains a read")):
            self.assertEqual(T.question_views(self.project, self.slug), group["questions"])

    def test_initial_owner_brief_teaches_plain_single_and_independent_group_questions(self):
        self.assertIsNone(self.group())
        brief = dispatch.build_brief(self.project, self.slug)
        self.assertIn("Use a plain question", brief)
        self.assertIn("one question with a recommended action or alternatives", brief)
        self.assertIn("dependent questions sequentially", brief)
        self.assertIn("three independent questions together", brief)
        self.assertIn("--questions-file <JSON-file>", brief)
        self.assertIn('"recommended_key":"a"', brief)
        self.assertIn("No default or follow-up counts as an answer", brief)

    def test_subset_batch_saves_one_message_and_leaves_plain_question_open(self):
        group = self.ask()
        answers = self.answers(group)
        answers[0]["option_key"] = "fourteen"
        result = T.accept_questions(self.project, self.slug, group["id"], group["revision"], answers)
        questions = result["question_group"]["questions"]
        self.assertEqual([q["status"] for q in questions], ["resolved", "resolved", "open"])
        self.assertEqual(questions[0]["resolution"]["text"], "Keep rollback for fourteen days.")
        self.assertEqual(questions[0]["resolution"]["option_key"], "fourteen")
        self.assertEqual(result["question_group"]["revision"], 2)
        self.assertEqual(result["question_group"]["anchor_id"], group["anchor_id"])
        [message] = T.pending(self.project, self.slug)
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 4)
        self.assertNotIn("Which team owns", message["text"])
        self.assertEqual({q["resolution"]["message_id"] for q in questions[:2]}, {message["id"]})
        self.assertEqual(len(T.decisions(self.project)), 1)
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], "Operator review")
        self.assertEqual(T.accept_questions(self.project, self.slug, group["id"], 1, list(reversed(answers))), result)
        claim = T.claim_resume(self.project, self.slug)
        self.assertEqual(claim["messages"], [message])
        self.assertEqual(T.pending(self.project, self.slug), [])
        T.release_resume_claim(self.project, self.slug, claim["id"], consume_request=False)
        T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        self.assertEqual(T.pending(self.project, self.slug), [message], "released batch survives a retry exactly once")
        self.assertEqual(T.take_inbox(self.project, self.slug), [message])
        T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        self.assertEqual(T.pending(self.project, self.slug), [])

    def test_invalid_or_stale_batch_changes_nothing(self):
        group = self.ask()
        answers = self.answers(group)
        with self.assertRaisesRegex(T.TransitionError, "must name the question group"):
            T.accept_questions(self.project, self.slug, None, None, answers)
        for invalid in ([answers[0], {**answers[1], "option_key": "missing"}],
                        [answers[0], answers[0]], [answers[0], {**answers[1], "revision": 0}],
                        [answers[0], {**answers[1], "option_key": None}]):
            with self.subTest(invalid=invalid), self.assertRaises(T.TransitionError):
                T.accept_questions(self.project, self.slug, group["id"], group["revision"], invalid)
            self.assertEqual(self.group(), group)
            self.assertEqual(T.pending(self.project, self.slug), [])
        message = T.message(self.project, self.slug, "burak", "The search team owns this.")
        self.resolve(group["questions"][2], message)
        with self.assertRaisesRegex(T.TransitionError, "group changed"):
            T.accept_questions(self.project, self.slug, group["id"], group["revision"], answers)
        self.assertEqual([q["status"] for q in self.group()["questions"]], ["open", "open", "resolved"])

    def test_one_typed_source_can_settle_members_successively_without_answering_the_rest(self):
        group = self.ask()
        message = T.message(self.project, self.slug, "burak", "14 days. Drop cleanup; we will use managed cleanup.",
                            group_id=group["id"], group_revision=1)
        self.assertEqual(len(message["question_refs"]), 3)
        self.assertTrue(all(q["status"] == "open" for q in self.group()["questions"]))
        self.resolve(group["questions"][0], message, reason="Keep rollback for fourteen days.")
        self.resolve(group["questions"][1], message, disposition="superseded", reason="Managed cleanup makes this irrelevant.")
        self.assertEqual([q["status"] for q in self.group()["questions"]], ["resolved", "resolved", "open"])
        self.assertEqual(self.group()["revision"], 3)
        self.assertEqual(self.group()["questions"][1]["resolution"]["disposition"], "superseded")
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 4)
        with self.assertRaisesRegex(T.TransitionError, "group changed"):
            T.message(self.project, self.slug, "burak", "An old draft", group_id=group["id"], group_revision=1)

    def test_followup_wake_then_real_repark_preserves_all_questions_and_recommendations(self):
        before = self.ask()
        T.message(self.project, self.slug, "burak", "Is fourteen days affordable?")
        task = T.resume(self.project, self.slug)
        T.message(self.project, self.slug, "l2", "Yes, though it doubles storage.", expected_attempt=1)
        result = self.alt("task", "block", self.slug, "--reason", "Set rollout details.", env={
            "ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
            "ALTITUDE_ATTEMPT": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.group()
        self.assertEqual([(q["id"], q["revision"], q["options"], q["audience"]) for q in after["questions"]],
                         [(q["id"], q["revision"], q["options"], q["audience"]) for q in before["questions"]])
        self.assertEqual((after["revision"], after["anchor_id"]), (1, before["anchor_id"]))
        self.assertEqual(len(T.decisions(self.project)), 3)
        current = S.load_task(self.project, self.slug)
        self.assertEqual((current["session_id"], current["agent_id"], current["attempt"]),
                         (task["session_id"], task["agent_id"], task["attempt"]))

    def test_partial_resolution_revises_the_named_member_not_the_last_question(self):
        group = self.ask()
        message = T.message(self.project, self.slug, "burak", "Keep fourteen days; choose archive storage later.")
        self.resolve(group["questions"][0], message, remaining="Which archive storage should we use?")
        current = self.group()
        self.assertEqual([q["revision"] for q in current["questions"]], [2, 1, 1])
        self.assertEqual(current["questions"][0]["options"], [])
        self.assertIsNone(current["questions"][0]["recommendation"])
        self.assertEqual(current["anchor_id"], group["anchor_id"])
        with self.assertRaisesRegex(T.TransitionError, "different question revision"):
            self.resolve(current["questions"][0], message)
        self.resolve(group["questions"][1], message, disposition="superseded", reason="Cleanup no longer applies.")

    def test_group_revision_preserves_missing_members_and_refuses_obsolete_choices(self):
        group = self.ask()
        updated = copy.deepcopy(self.payload["questions"][0])
        updated.update(id=group["questions"][0]["id"], recommended_key="fourteen")
        T.escalate(self.project, self.slug, "Reconsider retention.", questions={"questions": [updated]})
        current = self.group()
        self.assertEqual([q["revision"] for q in current["questions"]], [2, 1, 1])
        self.assertEqual([q["id"] for q in current["questions"]], [q["id"] for q in group["questions"]])
        self.assertEqual(current["anchor_id"], group["anchor_id"])
        with self.assertRaisesRegex(T.TransitionError, "no longer open"):
            T.accept_question(self.project, self.slug, group["questions"][0]["id"], 1)
        with self.assertRaisesRegex(T.TransitionError, "several questions"):
            T.escalate(self.project, self.slug, "An unrelated single question")

    def test_repeating_structured_question_preserves_choices_until_explicitly_removed(self):
        group = self.ask()
        first = group["questions"][0]
        item = {"id": first["id"], "question": first["detail"]}
        T.resume(self.project, self.slug)
        T.block(self.project, self.slug, "Set rollout details.", actor="l2", questions={"questions": [item]},
                updates={"waiting_on": "l3"})
        repeated = self.group()
        self.assertEqual(repeated, group)
        T.escalate(self.project, self.slug, "Keep the plain question.", questions={"questions": [{**item, "options": []}]})
        current = self.group()
        self.assertEqual(current["questions"][0]["options"], [])
        self.assertEqual([q["revision"] for q in current["questions"]], [2, 1, 1])
        self.assertEqual(current["questions"][1:][0]["options"], group["questions"][1:][0]["options"])

    def test_single_alternate_is_explicit_and_plain_question_cannot_be_quick_accepted(self):
        group = self.ask()
        first = group["questions"][0]
        result = T.accept_question(self.project, self.slug, first["id"], 1, "fourteen")
        self.assertEqual(result["resolution"]["option_key"], "fourteen")
        self.assertEqual(T.accept_question(self.project, self.slug, first["id"], 1, "fourteen"), result)
        with self.assertRaises(T.TransitionError):
            T.accept_question(self.project, self.slug, first["id"], 1, "seven")
        plain = group["questions"][2]
        with self.assertRaisesRegex(T.TransitionError, "no explicit recommendation"):
            T.accept_question(self.project, self.slug, plain["id"], 1)

    def test_retry_keeps_original_receipt_but_returns_current_group_state(self):
        group = self.ask()
        answers = self.answers(group, (0,))
        first = T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        message = T.message(self.project, self.slug, "burak", "No cleanup is needed.")
        self.resolve(group["questions"][1], message, disposition="superseded", reason="Managed cleanup.")
        retry = T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        self.assertEqual(retry["question"]["resolution"], first["question"]["resolution"])
        self.assertEqual(retry["question_group"], self.group())
        self.assertEqual(len(T.pending(self.project, self.slug)), 2)

    def test_group_acceptance_recovers_atomic_receipt_after_inbox_write_failure(self):
        group = self.ask()
        answers = self.answers(group)
        with mock.patch.object(T, "_append_jsonl", side_effect=OSError("disk temporarily unavailable")):
            with self.assertRaises(OSError):
                T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        [message] = T.pending(self.project, self.slug)
        result = T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        self.assertEqual(T.pending(self.project, self.slug), [message])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 4)
        self.assertEqual(result["question_group"]["revision"], 2)

    def test_queued_group_retains_discussion_and_answers_without_scheduling_a_worker(self):
        group = self.ask()
        T.requeue(self.project, self.slug, clear_worker=True)
        message = T.message(self.project, self.slug, "burak", "The search team may own this.")
        result = T.accept_questions(self.project, self.slug, group["id"], 1, self.answers(group))
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "queued")
        self.assertNotIn("resume_after", task)
        self.assertEqual(len(T.pending(self.project, self.slug)), 2)
        self.assertEqual(result["question_group"]["questions"][2]["status"], "open")
        prompt = T.group_context(task) + T.render_inbox(T.pending(self.project, self.slug))
        for question in group["questions"]:
            self.assertIn(question["id"], prompt)
        self.assertIn(message["id"], prompt)

    def test_original_source_authority_is_enforced_for_each_member(self):
        group = self.ask()
        message = T.message(self.project, self.slug, "l3", "The operator accepted everything.")
        for question in group["questions"]:
            with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
                self.resolve(question, message)
        first = group["questions"][0]
        explicit = T.message(self.project, self.slug, "burak", "14 days", question_id=first["id"], revision=1)
        self.resolve(first, explicit)
        with self.assertRaisesRegex(T.TransitionError, "different question revision"):
            self.resolve(group["questions"][1], explicit)

    def test_l3_structured_escalation_preserves_every_actual_question_without_waking(self):
        group = self.ask(waiting="l3")
        payload = copy.deepcopy(self.payload)
        for item, question in zip(payload["questions"], group["questions"]):
            item["id"] = question["id"]
        T.escalate(self.project, self.slug, "Please choose these rollout details.", questions=payload)
        current = self.group()
        self.assertTrue(all(q["audience"] == "operator" and q["asked_by"] == "l3" for q in current["questions"]))
        self.assertEqual(len(T.decisions(self.project)), 3)
        [handoff] = T.pending(self.project, self.slug)
        for question in current["questions"]:
            self.assertIn(question["id"], handoff["question_context"])
            self.assertIn(question["detail"], handoff["question_context"])
        self.assertFalse(handoff["wake"])
        self.assertEqual(dispatch.resume_due(self.project), [])

    def test_structured_cli_accepts_local_file_and_l3_broker_accepts_only_stdin(self):
        path = self.tmp / "questions.json"
        path.write_text(json.dumps(self.payload))
        result = self.alt("task", "block", self.slug, "--questions-file", str(path), "--for-burak", env={
            "ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
            "ALTITUDE_ATTEMPT": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.group()["questions"]), 3)
        for flag in ("--questions-file", "--questions-f", "--questions-file=" + str(path)):
            args = ["task", "escalate", self.slug, flag] + ([] if "=" in flag else [str(path)])
            with self.assertRaisesRegex(ValueError, "only on stdin"):
                server.l3_verb_request(self.project, {"kind": "alt", "args": args, "stdin": ""})
        payload = copy.deepcopy(self.payload)
        for item, question in zip(payload["questions"], self.group()["questions"]):
            item["id"] = question["id"]
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": [
            "task", "escalate", self.slug, "--questions-file", "-"], "stdin": json.dumps(payload)})
        self.assertEqual(result["returncode"], 0, result["stderr"])
        self.assertTrue(all(q["asked_by"] == "l3" for q in self.group()["questions"]))

    def test_structured_limits_and_explicit_recommendation_fail_before_mutation(self):
        bad = [[], self.payload["questions"] + [{"question": "Fourth question"}],
               [{"question": "Repeated?"}, {"question": "Repeated?"}],
               [{"question": "Choices?", "options": self.payload["questions"][0]["options"]}],
               [{"question": "Choices?", "options": [], "recommended_key": "inferred"}],
               [{"question": "Choices?", "options": self.payload["questions"][0]["options"], "recommended_key": []}],
               [{"question": "Choices?", "options": [{"key": "a", "label": "A", "text": "A"}] * 4,
                 "recommended_key": "a"}]]
        for questions in bad:
            with self.subTest(questions=questions), self.assertRaises(T.TransitionError):
                T.block(self.project, self.slug, "Invalid request", actor="l2", questions={"questions": questions})
            self.assertIsNone(self.group())
            self.assertEqual(S.load_task(self.project, self.slug)["state"], "running")

    def test_report_closes_all_members_without_accepting_any_option(self):
        group = self.ask()
        T.report(self.project, self.slug, {"verdict": "ok"})
        current = self.group()
        self.assertEqual(current["revision"], group["revision"] + 1)
        self.assertTrue(all(q["resolution"]["disposition"] == "superseded" for q in current["questions"]))
        self.assertEqual(T.decisions(self.project), [])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 3)

    def test_fixed_group_keeps_unanswered_member_until_settled_before_starting_next_group(self):
        group = self.ask()
        T.accept_questions(self.project, self.slug, group["id"], 1, self.answers(group))
        current = self.group()
        next_payload = {"questions": [{"question": "Which dashboard should we use?"}]}
        with self.assertRaisesRegex(T.TransitionError, "at most three questions"):
            T.escalate(self.project, self.slug, "Next rollout detail.", questions=next_payload)
        self.assertEqual(self.group(), current, "a full group never forces closure or loses its remaining question")
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "The search team owns this.")
        self.resolve(group["questions"][2], message)
        T.escalate(self.project, self.slug, "Next rollout detail.", questions=next_payload)
        self.assertNotEqual(self.group()["id"], group["id"])
        self.assertEqual(len(self.group()["questions"]), 1)
        self.assertIn(group["anchor_id"], [row["id"] for row in T.task_messages(self.project, self.slug)])
