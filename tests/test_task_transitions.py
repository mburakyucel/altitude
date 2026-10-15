"""Task record transitions keep their guards: a requeue never crosses a launch in flight, a block remembers where it
came from, and completion waits for a current report (docs/ARCHITECTURE.md#task-lifecycle)."""
from unittest import mock

from tests.support import AltitudeCase
from altitude import state as S, tasks as T


class TaskTransitions(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.slug = T.new(self.project, "Guarded task", "Change the copy.")["slug"]

    def task(self):
        return S.load_task(self.project, self.slug)

    def test_a_requeue_waits_for_a_launch_or_resume_in_flight(self):
        T.block(self.project, self.slug, "Which copy?")
        claim = T.claim_resume(self.project, self.slug)
        for held in ({**self.task(), "dispatching": None}, {**self.task(), "resume_claim": None}):
            with self.subTest(claim=bool(held["resume_claim"])):
                S.save_task(self.project, held)
                with self.assertRaisesRegex(T.TransitionError, "active launch/resume claim prevents requeue"):
                    T.requeue(self.project, self.slug)
                self.assertEqual(self.task(), held)
        S.save_task(self.project, {**self.task(), "resume_claim": claim})
        T.release_resume_claim(self.project, self.slug, claim["id"], consume_request=False)
        self.assertEqual(T.requeue(self.project, self.slug)["state"], "queued")

    def test_a_report_promotes_only_a_block_its_worker_made(self):
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree="/wt", branch="b")
        T.block(self.project, self.slug, "Landing window pending")
        self.assertEqual(T.report(self.project, self.slug, {"verdict": "ok"}, expected_state="blocked",
                                  expected_attempt=1, expected_block_from="running")["state"], "reported")
        other = T.new(self.project, "Blocked before launch", "Change the copy.")["slug"]
        T.block(self.project, other, "Which copy?")
        with self.assertRaisesRegex(T.TransitionError, "latest block did not come from running"):
            T.report(self.project, other, {"verdict": "ok"}, expected_state="blocked", expected_block_from="running")
        self.assertEqual(S.load_task(self.project, other)["state"], "blocked")

    def reported(self):
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree="/wt", branch="b")
        T.report(self.project, self.slug, {"verdict": "ok"})
        return self.task()

    def refused(self, **kwargs):
        before = self.task()
        with self.assertRaisesRegex(T.TransitionError, "follow-up work requires a current report"):
            T.done(self.project, self.slug, digest="Done.", **kwargs)
        self.assertEqual(self.task(), before)

    def test_completion_refuses_a_superseded_report(self):
        task = self.reported()
        self.refused(expected_owner={**T.report_owner(task), "session_id": "earlier"})
        # Follow-up work after the report: its verification names the earlier owner.
        S.save_task(self.project, {**task, "report_after": S.now()})
        self.refused()
        S.save_task(self.project, task)
        self.assertEqual(T.done(self.project, self.slug, expected_owner=T.report_owner(task))["state"], "done")

    def test_completion_refuses_a_message_that_wakes_the_owner(self):
        self.reported()
        inbox = S.task_dir(self.project, self.slug) / "inbox.jsonl"
        S.atomic_write(inbox, '{"id": "aaaaaaaaaaaa", "role": "l3", "text": "One more fix."}\n')
        self.refused()
        # A row that does not wake the owner, such as a resume's own reason, leaves completion to the report.
        S.atomic_write(inbox, '{"id": "aaaaaaaaaaaa", "role": "l3", "text": "Resumed.", "wake": false}\n')
        self.assertEqual(T.done(self.project, self.slug, digest="Done.")["state"], "done")

    def test_completion_drops_a_reverification_read_for_an_earlier_owner(self):
        task = self.reported()
        delivery = {"number": 41, "head": "abc", "base": "def", "branch": "b", "at": S.now()}
        task = {**task, "delivery": delivery, "verified": {
            "verdict": "contradicted", "problems": ["PR #41: reported merge SHA differs from GitHub"],
            "delivery": delivery, "owner": T.report_owner(task), "attempt": 1}}
        S.save_task(self.project, task)
        fresh = {"verdict": "ok", "problems": [], "delivery": delivery}
        # GitHub was read before the task moved on: that verdict no longer describes the current owner's report.
        with mock.patch.object(T, "_reverify", return_value={**fresh, "owner": {**T.report_owner(task),
                                                                                   "session_id": "earlier"}}):
            with self.assertRaisesRegex(T.TransitionError, "reported merge SHA differs from GitHub"):
                T.done(self.project, self.slug, digest="Done.")
        self.assertEqual(self.task(), task)
        with mock.patch.object(T, "_reverify", return_value={**fresh, "owner": T.report_owner(task)}):
            self.assertEqual(T.done(self.project, self.slug, digest="Done.")["verified"]["verdict"], "ok")
