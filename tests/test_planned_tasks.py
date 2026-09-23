"""Planned intake through real CLI/storage/Git/dispatch; external workers are fixtures."""
import json
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, digest, dispatch, server, state as S, status, tasks as T


class TestPlannedTasks(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.engine = FakeL2()
        self.engine.install(self)
        self.patch(server, "log")

    def create(self, title="Planned work", *flags):
        result = self.alt("--project", self.project, "task", "new", "--title", title,
                          "Original operator-authorized brief", *flags, env={"ALTITUDE_ACTOR": "l3"})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def complete(self, task):
        if task["state"] == "queued":
            dispatch.run(self.project, task["slug"])
        T.report(self.project, task["slug"], {"verdict": "ok"})
        T.done(self.project, task["slug"])

    def test_manual_wait_is_visible_without_wip_or_worker_and_does_not_starve_ready_work(self):
        task = self.create("A planned task", "--wait", "the rollout window")
        slug = task["slug"]
        ordinary = self.create("Z ready task")
        self.assertEqual(task["state"], "queued")
        self.assertEqual(task["planned_wait"], {"reason": "the rollout window", "after": None})
        self.assertIn("Planned: waits for the rollout window", S.regen_state_md(self.project))
        self.assertIn("waits for the rollout window", digest.text())
        self.assertEqual(digest.wip()["machine"], 0)
        self.assertEqual(digest.wip()["waiting"][0]["why"], "planned")
        self.assertEqual(status.status(self.project, slug)["planned_wait"], task["planned_wait"])
        with self.assertRaisesRegex(T.TransitionError, "waits for"):
            dispatch.run(self.project, slug)
        self.assertFalse((self.repo / ".claude/worktrees" / slug).exists())
        server.dispatch_waiting(self.project)
        saved = S.load_task(self.project, slug)
        self.assertEqual((saved["state"], saved["attempt"], saved["worktree"]), ("queued", 0, None))
        self.assertEqual(S.load_task(self.project, ordinary["slug"])["state"], "running")
        self.assertEqual(len(self.engine.calls), 1)

    def test_explicit_release_preserves_authority_messages_and_other_holds(self):
        task = self.create("Waiting brief", "--wait", "review to finish", "--hold-merge", "Operator review")
        slug = task["slug"]
        message = T.message(self.project, slug, "l3", "Use the revised API in the same authorized scope")
        self.assertTrue(S.load_task(self.project, slug)["planned_wait"])
        self.assertFalse(S.load_task(self.project, slug).get("resume_after"))
        self.assertEqual(len(self.engine.calls), 0)
        refused = self.alt("--project", self.project, "task", "release", slug, "--reason", "Approved",
                           env={"ALTITUDE_ACTOR": "l2"})
        self.assertNotEqual(refused.returncode, 0)
        with self.assertRaisesRegex(T.TransitionError, "only L3 or the operator"):
            T.release(self.project, slug, "Approved", actor="l2")
        for actor in ("burak", "l3"):
            with self.assertRaises(T.TransitionError):
                T.release(self.project, slug, " ", actor=actor)
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": [
            "task", "release", slug, "--reason", "Review prerequisite is complete"]})
        self.assertEqual(result["returncode"], 0, result)
        queued = S.load_task(self.project, slug)
        self.assertFalse(queued.get("planned_wait"))
        self.assertEqual(queued["hold_merge"], "Operator review")
        self.assertEqual(queued["source"], "chat")
        second = T.message(self.project, slug, "burak", "Keep the rollback path")
        dispatch.run(self.project, slug)
        self.assertIn(message["text"], self.engine.calls[0]["prompt"])
        self.assertIn(second["text"], self.engine.calls[0]["prompt"])
        self.assertIn("Original operator-authorized brief", self.engine.calls[0]["prompt"])
        self.assertEqual(T.pending(self.project, slug), [])
        self.assertEqual((S.task_dir(self.project, slug) / "request.md").read_text(), "Original operator-authorized brief\n")
        released = next(event for event in S.read_events(self.project, slug) if event["kind"] == "released")
        self.assertEqual((released["by"], released["reason"]), ("l3", "Review prerequisite is complete"))
        self.assertEqual([row["delivery"]["state"] for row in T.message_views(self.project, slug, [])], ["delivered", "delivered"])

    def test_dependency_requires_archive_done_and_satisfied_creation_is_ready(self):
        prerequisite = self.create("Prerequisite")
        task = self.create("Dependent", "--after", prerequisite["slug"])
        dispatch.run(self.project, prerequisite["slug"])
        T.report(self.project, prerequisite["slug"], {"verdict": "ok"})
        self.assertTrue(T.release_dependency(self.project, task["slug"])["planned_wait"])
        # A merged PR or a report does not satisfy archived-done semantics.
        self.assertTrue(S.load_task(self.project, task["slug"])["planned_wait"])
        T.done(self.project, prerequisite["slug"])
        server.dispatch_waiting(self.project)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        self.assertFalse(S.load_task(self.project, task["slug"]).get("planned_wait"))
        next_task = self.create("Already satisfied", "--after", prerequisite["slug"])
        self.assertFalse(next_task.get("planned_wait"))

    def test_rejected_or_missing_dependency_never_auto_releases_but_operator_can_override(self):
        prerequisite = self.create("Rejected prerequisite")
        dependent = self.create("Dependent", "--after", prerequisite["slug"])
        T.reject(self.project, prerequisite["slug"], "Superseded")
        self.assertTrue(T.release_dependency(self.project, dependent["slug"])["planned_wait"])
        archive = S.archive_dir(self.project) / prerequisite["slug"] / "status.json"
        archive.unlink()
        self.assertTrue(T.release_dependency(self.project, dependent["slug"])["planned_wait"])
        T.release(self.project, dependent["slug"], "The replacement prerequisite is verified", actor="burak")
        dispatch.run(self.project, dependent["slug"])
        self.assertEqual(S.load_task(self.project, dependent["slug"])["state"], "running")

    def test_release_keeps_capacity_gate_and_dependency_can_lift_while_full(self):
        self.patch(config, "WIP_PER_MACHINE", 1)
        prerequisite = self.create("Prerequisite")
        named = self.create("Z named wait", "--after", prerequisite["slug"])
        self.complete(prerequisite)
        running = self.create("Busy worker")
        dispatch.run(self.project, running["slug"])
        planned = self.create("A planned", "--wait", "prerequisite to land")
        T.release(self.project, planned["slug"], "Verified", actor="burak")
        server.dispatch_waiting(self.project)
        self.assertEqual(S.load_task(self.project, planned["slug"])["state"], "queued")
        self.assertFalse(S.load_task(self.project, named["slug"]).get("planned_wait"))
        self.assertIn("WIP limit", digest.wip()["waiting"][0]["hold"])

    def test_creation_validates_one_short_reason_and_project_local_existing_dependency(self):
        for flags in (("--wait", "x", "--after", "missing"), ("--wait", ""),
                      ("--wait", "two\nlines"), ("--wait", "x" * 161),
                      ("--after", "missing"), ("--after", "../foreign")):
            result = self.alt("--project", self.project, "task", "new", "--title", "Invalid", "Brief", *flags)
            self.assertNotEqual(result.returncode, 0, flags)
        with self.assertRaises(T.TransitionError):
            T.new(self.project, "No brief", " ", wait="approval")
        self.assertEqual(S.list_tasks(self.project), [])

    def test_failed_or_uncertain_launch_retains_messages_and_late_updates_are_not_consumed(self):
        task = self.create("Messages before dispatch", "--wait", "approval")
        slug = task["slug"]
        initial = T.message(self.project, slug, "burak", "Preserve this update")
        T.release(self.project, slug, "Approved")
        self.engine.outcomes.append(RuntimeError("fixture launch failed"))
        with mock.patch("altitude.incidents.system_fault"), self.assertRaises(dispatch.DispatchFailure):
            dispatch.run(self.project, slug)
        self.assertEqual([row["id"] for row in T.pending(self.project, slug)], [initial["id"]])
        self.assertEqual(T.removable_messages(self.project, slug, S.load_task(self.project, slug)), set())
        launch = self.engine.start_l2

        def delayed(*args, **kwargs):
            T.message(self.project, slug, "burak", "Arrived during launch")
            return launch(*args, **kwargs)

        with mock.patch.object(dispatch.engines, "start_l2", side_effect=delayed):
            dispatch.run(self.project, slug)
        self.assertIn(initial["text"], self.engine.calls[-1]["prompt"])
        self.assertNotIn("Arrived during launch", self.engine.calls[-1]["prompt"])
        self.assertEqual([row["text"] for row in T.pending(self.project, slug)], ["Arrived during launch"])

    def test_direct_task_binding_cannot_bypass_a_wait(self):
        task = self.create("Held", "--wait", "approval")
        with self.assertRaisesRegex(T.TransitionError, "planned wait"):
            T.dispatch(self.project, task["slug"], attempt=1, session_id="session", agent_id="worker",
                       worktree=None, branch=None)

    def test_confirmed_receipt_owns_delivery_even_while_raw_inbox_rows_remain(self):
        task = self.create("Receipt interruption", "--wait", "approval")
        slug = task["slug"]
        message = T.message(self.project, slug, "burak", "Do this once")
        T.release(self.project, slug, "Approved")
        T.dispatch(self.project, slug, attempt=1, session_id="session", agent_id="worker",
                   worktree=None, branch=None, messages=[message], input_delivered=True)
        self.assertEqual(S.load_task(self.project, slug)["state"], "running")
        self.assertIn(message["id"], (S.task_dir(self.project, slug) / "inbox.jsonl").read_text())
        self.assertEqual(T.pending(self.project, slug), [])
        self.assertEqual(T.take_inbox(self.project, slug), [])

    def test_unknown_input_delivery_keeps_update_pending(self):
        task = self.create("Unknown handoff", "--wait", "approval")
        slug = task["slug"]
        message = T.message(self.project, slug, "burak", "Keep this until acknowledged")
        T.release(self.project, slug, "Approved")
        self.engine.outcomes.append({"returncode": 0, "agent": {"id": "worker", "sessionId": "session"}})
        dispatch.run(self.project, slug)
        self.assertEqual([row["id"] for row in T.pending(self.project, slug)], [message["id"]])
        self.assertEqual(T.message_views(self.project, slug, [])[0]["delivery"]["state"], "unconfirmed")
