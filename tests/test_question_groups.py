"""Independent questions share a conversation and one atomic answer submission, never implicit defaults."""
import copy
import json
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import dispatch, l3, server, state as S, tasks as T


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

    def test_question_procedure_lives_in_the_persona_not_the_brief(self):
        self.assertIsNone(self.group())
        brief = dispatch.build_brief(self.project, self.slug)
        self.assertNotIn("--questions-file", brief)
        self.assertIn("--questions-file", (Path(__file__).parents[1] / "personas" / "l2.md").read_text())

    def test_delivery_carries_the_words_and_ids_while_the_owner_keeps_its_own_questions(self):
        group = self.ask()
        first, _, third = group["questions"]
        T.accept_questions(self.project, self.slug, group["id"], group["revision"], self.answers(group, (0,)))
        typed = T.message(self.project, self.slug, "burak", "The search team.")
        delivered = T.render_inbox(T.pending(self.project, self.slug))
        self.assertIn(f"answers question {first['id']}):\n", delivered)
        self.assertIn(f"(message id {typed['id']}):\nThe search team.", delivered)
        for detail in (group["questions"][1]["detail"], third["detail"], "Fits the storage budget."):
            self.assertNotIn(detail, delivered)
        self.assertLess(len(delivered), 400)
        # A re-published question keeps the operator's earlier answer valid; the current revision is the default.
        T.resume(self.project, self.slug)
        T.block(self.project, self.slug, "Set rollout details.", actor="l2",
                questions={"questions": [{"id": third["id"], "question": "Which team owns the rollout, again?"}]})
        resolved = T.resolve_question(self.project, self.slug, third["id"], None, typed["id"], expected_attempt=1,
                                      disposition="answered", reason="The search team owns this rollout.")
        self.assertEqual((resolved["revision"], resolved["status"]), (2, "resolved"))
        # A replacement session never saw the questions, so its brief lists what is still open, once.
        brief = dispatch.build_brief(self.project, self.slug)
        self.assertIn(group["questions"][1]["detail"], brief)
        self.assertNotIn(third["detail"], brief)

    def test_subset_batch_saves_one_message_and_leaves_plain_question_open(self):
        group = self.ask()
        answers = self.answers(group)
        answers[0]["option_key"] = "fourteen"
        result = T.accept_questions(self.project, self.slug, group["id"], group["revision"], answers)
        questions = result["question_group"]["questions"]
        self.assertEqual([q["status"] for q in questions], ["open", "open", "open"])
        self.assertEqual(questions[0]["response"]["text"], "Keep rollback for fourteen days.")
        self.assertTrue(all(q["resolution"] is None for q in questions))
        self.assertEqual(result["question_group"]["revision"], 2)
        self.assertEqual(result["question_group"]["anchor_id"], group["anchor_id"])
        [message] = T.pending(self.project, self.slug)
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 4)
        self.assertNotIn("Which team owns", message["text"])
        self.assertEqual({q["response"]["message_id"] for q in questions[:2]}, {message["id"]})
        self.assertEqual(T.decisions(self.project), [], "a partial answer hands the whole task back")
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

    def test_custom_and_preset_responses_share_attributed_message_without_resolving(self):
        group = self.ask()
        first, second, plain = group["questions"]
        answers = [{"question_id": first["id"], "revision": 1, "text": "21 days"},
                   self.answers(group, (1,))[0],
                   {"question_id": plain["id"], "revision": 1, "text": "Which teams are available?"}]
        result = T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        [message] = T.pending(self.project, self.slug)
        self.assertEqual(message["role"], T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(message["by"], T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(message["question_refs"], [{"id": q["id"], "revision": 1} for q in group["questions"]])
        self.assertEqual(message["text"], f"{first['question']}\n21 days\n\n{second['question']}\nRun cleanup at night."
                         f"\n\n{plain['question']}\nWhich teams are available?")
        self.assertEqual([q["response"]["text"] for q in result["question_group"]["questions"]],
                         ["21 days", "Run cleanup at night.", "Which teams are available?"])
        self.assertTrue(all(q["status"] == "open" and q["resolution"] is None for q in self.group()["questions"]))
        self.assertEqual(T.decisions(self.project), [])
        self.resolve(first, message, reason="Keep rollback for twenty-one days.")
        self.resolve(second, message, reason="Run cleanup at night.")
        self.assertEqual([q["status"] for q in self.group()["questions"]], ["resolved", "resolved", "open"])
        still_open = T.open_questions(S.load_task(self.project, self.slug))
        self.assertIn("Which teams are available?", still_open)
        self.assertNotIn("Run cleanup at night.", still_open)
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], "Operator review")

    def test_custom_followup_reask_restores_only_named_input_and_refuses_stale_retargeting(self):
        group = self.ask()
        first, second, _ = group["questions"]
        sent = T.accept_question(self.project, self.slug, first["id"], 1, text="Why only these periods?")
        independent = self.group()["questions"][1:]
        T.resume(self.project, self.slug)
        T.block(self.project, self.slug, "Set rollout details.", actor="l2")
        self.assertEqual(self.group()["questions"][0]["response"], sent["response"])
        T.resume(self.project, self.slug)
        item = {"id": first["id"], "question": first["detail"]}
        T.block(self.project, self.slug, "Set rollout details.", actor="l2", questions={"questions": [item]})
        current = self.group()
        self.assertEqual((current["questions"][0]["revision"], current["questions"][0]["response"]), (2, None))
        self.assertEqual(current["questions"][0]["audience"], "operator")
        self.assertEqual(current["questions"][0]["options"], first["options"])
        for old, new in zip(independent, current["questions"][1:]):
            self.assertEqual(new, {**old, "asked_again": True, "group_revision": current["revision"], "resume_after": None})
        retry = T.accept_question_result(self.project, self.slug, first["id"], 1, text="Why only these periods?")
        self.assertEqual(retry["question"]["response"], sent["response"])
        self.assertEqual(retry["question_group"], current)
        with self.assertRaisesRegex(T.TransitionError, "no longer open"):
            T.accept_question(self.project, self.slug, first["id"], 1, text="21 days")
        fresh = T.accept_question(self.project, self.slug, first["id"], 2, text="21 days")
        self.assertNotEqual(fresh["response"]["message_id"], sent["response"]["message_id"])
        self.assertEqual(T.decisions(self.project), [], "each answer hands the task back until its owner re-parks")

    def test_custom_batch_validation_and_conflict_are_atomic_and_keep_audience(self):
        group = self.ask()
        answer = {"question_id": group["questions"][0]["id"], "revision": 1, "text": "21 days"}
        other = {"question_id": group["questions"][1]["id"], "revision": 1}
        for value in ("", "  ", None, 21, {}):
            with self.subTest(value=value), self.assertRaises(T.TransitionError):
                T.accept_questions(self.project, self.slug, group["id"], 1, [answer, {**other, "text": value}])
            self.assertEqual(self.group(), group)
            self.assertEqual(T.pending(self.project, self.slug), [])
        with self.assertRaises(T.TransitionError):
            T.accept_questions(self.project, self.slug, group["id"], 1, [{**answer, "option_key": "seven"}])
        sent = T.accept_questions(self.project, self.slug, group["id"], 1, [answer])
        with self.assertRaises(T.TransitionError):
            T.accept_questions(self.project, self.slug, group["id"], 2,
                               [{**answer, "text": "28 days"}, {**other, "option_key": "night"}])
        self.assertEqual(self.group(), sent["question_group"])

    def test_custom_response_respects_l3_question_audience(self):
        group = self.ask(waiting="l3")
        question = group["questions"][0]
        with self.assertRaisesRegex(T.TransitionError, "no longer open"):
            T.accept_question(self.project, self.slug, question["id"], 1, text="21 days")
        self.assertEqual(self.group(), group)
        self.assertEqual(T.pending(self.project, self.slug), [])


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

    def test_sourced_project_pivot_closes_only_obsolete_member_after_l3_update(self):
        group = self.ask({"questions": self.payload["questions"][1:]})
        obsolete, unanswered = group["questions"]
        T.message(self.project, self.slug, "l3",
                  "Another owner is exploring managed cleanup. I recommend it; no delivery is verified.")
        self.assertEqual(len(T.decisions(self.project)), 2)

        turn = "operator-managed-cleanup"
        l3.chat_log(self.project, "user", "Drop scheduled cleanup; use managed cleanup. Rollout ownership is still open.",
                    trigger="chat", turn_id=turn)
        relay = T.message(self.project, self.slug, "l3",
                          f"Project decision {turn}: managed cleanup supersedes the scheduled-cleanup choice. "
                          "Update the plan; the rollout owner still needs the operator's decision.")
        with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
            self.resolve(obsolete, relay, disposition="superseded", reason="Managed cleanup replaces our schedule.")
        self.assertEqual(len(T.decisions(self.project)), 2, "a relay alone does not resolve either member")
        before = S.load_task(self.project, self.slug)
        unanswered = self.group()["questions"][1]
        pending = T.pending(self.project, self.slug)
        result = self.resolve(obsolete, {"id": turn}, source="project", disposition="superseded",
                              reason="The operator's managed-cleanup direction removes the scheduling choice.")

        receipt = result["resolution"]
        self.assertEqual((receipt["disposition"], receipt["source"], receipt["message_id"], receipt["by"]),
                         ("superseded", "project", turn, T.OPERATOR_MESSAGE_ROLE))
        self.assertEqual(self.group()["questions"][1], {**unanswered, "group_revision": 2})
        self.assertEqual([q["id"] for q in T.decisions(self.project)], [unanswered["id"]])
        self.assertNotIn("acceptance_message", S.load_task(self.project, self.slug)["questions"][0])
        with self.assertRaisesRegex(T.TransitionError, "no longer open"):
            T.accept_question(self.project, self.slug, obsolete["id"], obsolete["revision"])
        with mock.patch.object(server.monitor, "sessions", return_value=[]):
            task_view = server.task_view(self.project, self.slug)
            project_view = server.project_view(self.project)
            overview = server.overview()
        self.assertEqual([q["status"] for q in task_view["question_group"]["questions"]], ["resolved", "open"])
        self.assertEqual([q["id"] for q in project_view["decisions"]], [unanswered["id"]])
        self.assertEqual([q["id"] for q in overview["queue"] if q["project"] == self.project], [unanswered["id"]])
        project = next(p for p in overview["projects"] if p["name"] == self.project)
        self.assertEqual((project["counts"]["blocked"], project["counts"]["running"]), (1, 0))
        after = S.load_task(self.project, self.slug)
        for field in ("state", "waiting_on", "blocked_reason", "agent_id", "session_id", "attempt", "fault",
                      "resume_after", "resume_request", "hold_merge"):
            self.assertEqual(after.get(field), before.get(field), field)
        self.assertEqual(T.pending(self.project, self.slug), pending)
        self.assertEqual(next(m for m in task_view["messages"] if m["id"] == relay["id"])["role"], "l3")

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
        self.assertEqual(result["response"]["text"], "Keep rollback for fourteen days.")
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
        self.assertEqual(retry["question"]["response"], first["question"]["response"])
        self.assertEqual(retry["question_group"], self.group())
        self.assertEqual(len(T.pending(self.project, self.slug)), 2)

    def test_group_acceptance_recovers_atomic_receipt_after_inbox_write_failure(self):
        group = self.ask()
        answers = self.answers(group)
        answers[0] = {"question_id": group["questions"][0]["id"], "revision": 1, "text": "21 days"}
        with mock.patch.object(T, "_append_jsonl", side_effect=OSError("disk temporarily unavailable")):
            with self.assertRaises(OSError):
                T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        [message] = T.pending(self.project, self.slug)
        self.assertIn("21 days", message["text"])
        result = T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        self.assertEqual(T.pending(self.project, self.slug), [message])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 4)
        self.assertEqual(result["question_group"]["revision"], 2)

    def test_queued_group_retains_discussion_and_answers_without_scheduling_a_worker(self):
        group = self.ask()
        T.requeue(self.project, self.slug, clear_worker=True)
        message = T.message(self.project, self.slug, "burak", "The search team may own this.")
        answers = self.answers(group)
        answers[0] = {"question_id": group["questions"][0]["id"], "revision": 1, "text": "Why only seven days?"}
        result = T.accept_questions(self.project, self.slug, group["id"], 1, answers)
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "queued")
        self.assertNotIn("resume_after", task)
        self.assertEqual(len(T.pending(self.project, self.slug)), 2)
        self.assertEqual(result["question_group"]["questions"][2]["status"], "open")
        prompt = T.render_inbox(T.pending(self.project, self.slug))
        self.assertIn(f"answers question {group['questions'][0]['id']}", prompt)
        self.assertIn(message["id"], prompt)
        self.assertIn("Why only seven days?", prompt)
        self.assertTrue(all(q["resolution"] is None for q in result["question_group"]["questions"]))

    def test_original_source_authority_is_enforced_for_each_member(self):
        group = self.ask()
        message = T.message(self.project, self.slug, "l3", "The operator accepted everything.")
        for question in group["questions"]:
            with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
                self.resolve(question, message)
        first = group["questions"][0]
        explicit = T.message(self.project, self.slug, "burak", "14 days", question_id=first["id"], revision=1)
        self.resolve(first, explicit)
        with self.assertRaisesRegex(T.TransitionError, "answers a different question"):
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
        self.assertNotIn("question_context", handoff)
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

    def test_reasking_keeps_independent_questions_and_bounds_only_open_members(self):
        group = self.ask()
        independent = S.load_task(self.project, self.slug)["questions"][:2]
        review = group["questions"][2]
        for turn in range(4):
            T.resume(self.project, self.slug)
            before = self.group()
            payload = {"questions": [{"question": review["question"] if turn % 2 else "Review the updated rollout?"}]}
            with self.assertRaisesRegex(T.TransitionError, "at most three open questions"):
                self.ask({"questions": [{"question": "A fourth independent decision?"}]})
            self.assertEqual(self.group(), before)
            T.resolve_question(self.project, self.slug, review["id"], review["revision"], None,
                               disposition="withdrawn", reason="Assess new guidance first.", expected_attempt=1)
            with self.assertRaisesRegex(T.TransitionError, "group changed"):
                T.accept_questions(self.project, self.slug, group["id"], before["revision"], self.answers(group))
            current = self.ask(payload)
            self.assertEqual(S.load_task(self.project, self.slug)["questions"][:2], independent)
            self.assertEqual(current["id"], group["id"])
            fresh = current["questions"][-1]
            self.assertNotEqual(fresh["id"], review["id"])
            self.assertIsNone(fresh["resolution"])
            review = fresh
        self.assertEqual(len(self.group()["questions"]), 7)
        self.assertEqual(len(T.decisions(self.project)), 3)
        self.assertIn(group["anchor_id"], [row["id"] for row in T.task_messages(self.project, self.slug)])
