"""A Stop is task state, never a question: L3's Stop waits on L3, the operator's keeps its Needs you row."""
from tests.support import AltitudeCase
from altitude import config, dispatch, engines, incidents, state as S, tasks as T


class TestStopAudience(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(engines, "stop_l2_worker", return_value="stopped")

    def task(self, state="running", title="Stop audience"):
        task = T.new(self.project, title, "Do it.", actor="burak")
        task.update({"state": state, "attempt": 1, "agent_id": "agent-old", "session_id": "session-old",
                     "l2_engine": "claude"})
        S.save_task(self.project, task)
        return task

    def stop(self, slug, actor, reason):
        dispatch.request_task_operation(self.project, slug, "stop", reason, actor=actor)
        return dispatch.run_task_operation(self.project, slug)

    def card(self, slug):
        return [row for row in T.decisions(self.project) if row["slug"] == slug]

    def notes(self, slug):
        return [row for row in T.task_messages(self.project, slug) if row.get("summary") == "Stopped the task"]

    def test_l3_stop_waits_on_l3_with_its_note_and_no_operator_question(self):
        slug = self.task()["slug"]
        reason = "The orphaned landing holds the repository turn; stopping to release it."
        queued = S.load_task(self.project, slug)
        dispatch.request_task_operation(self.project, slug, "stop", reason, actor="l3")
        self.assertEqual(T.block_status(self.project, S.load_task(self.project, slug)), ("stopped", "stopped by L3"),
                         "a queued L3 Stop already names its requester")
        self.assertEqual(dispatch.run_task_operation(self.project, slug)["state"], "blocked")
        dispatch.run_task_operation(self.project, slug)  # a repeated daemon pass changes nothing

        task = S.load_task(self.project, slug)
        self.assertEqual(task.get("questions", []), [], "a Stop publishes no question for anyone")
        self.assertEqual((task["block_actor"], task["waiting_on"], T.stopped_by(task)), ("l3", "l3", "l3"))
        self.assertEqual(self.card(slug), [], "Needs you stays quiet")
        self.assertEqual(T.block_status(self.project, task), ("stopped", "stopped by L3"))
        self.assertEqual([(row["role"], row["text"]) for row in self.notes(slug)], [("l3", reason)],
                         "the reason appears once in the conversation as L3's note")
        self.assertEqual(T.pending(self.project, slug), [], "the note is not inbox input for the stopped owner")
        self.assertIn(slug, (config.project_dir(self.project) / "STATE.md").read_text().split("## Needs L3 input")[1])
        self.assertNotEqual(queued.get("block_id"), task["block_id"])

        T.message(self.project, slug, "l3", "Resume follows with the candidate state.", by="l3")
        self.assertEqual(S.load_task(self.project, slug).get("questions", []), [],
                         "reading the block later never adopts the Stop reason as a question")

    def test_operator_stop_is_unchanged(self):
        slug = self.task()["slug"]
        self.stop(slug, config.OPERATOR_ACTOR, "Operator requested Stop.")
        task = S.load_task(self.project, slug)
        self.assertEqual((task.get("questions", []), task.get("waiting_on"), task["block_actor"]),
                         ([], None, config.OPERATOR_ACTOR))
        self.assertEqual([(row["kind"], row["detail"]) for row in self.card(slug)],
                         [("stopped", "Operator requested Stop.")])
        self.assertEqual(T.block_status(self.project, task), ("stopped", f"stopped by {config.operator_label()}"))
        self.assertEqual(self.notes(slug), [])

    def test_daemon_and_recovery_blocks_publish_no_question(self):
        parked = self.task(title="Parked")["slug"]
        T.block(self.project, parked, "L2 session ended without a report (report.json missing)")
        faulted = self.task(title="Faulted")["slug"]
        incidents._block_faulting_task(self.project, faulted, "system fault [worker]: the sandbox refused the build",
                                       "worker", T._UNSET, None)
        for slug, waiting in ((parked, None), (faulted, "l3")):
            with self.subTest(slug=slug):
                task = S.load_task(self.project, slug)
                self.assertEqual((task["state"], task["block_actor"], task.get("questions", []),
                                  task.get("waiting_on")), ("blocked", "altd", [], waiting))
                self.assertEqual(self.card(slug), [])

    def test_only_the_owner_block_asks(self):
        slug = self.task()["slug"]
        with self.assertRaisesRegex(T.TransitionError, "L2 human dilemma"):
            T.block(self.project, slug, "Pick one.", actor="l3",
                    questions={"questions": [{"question": "Pick one?"}]})
        T.block(self.project, slug, "Stopping for coordination.", actor="l3")
        self.assertEqual(S.load_task(self.project, slug).get("questions", []), [])
