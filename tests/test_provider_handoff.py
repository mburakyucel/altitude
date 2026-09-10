"""#310: explicit recovery retains one task while replacing its exited provider attempt."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, digest, dispatch, route, server, state as S, tasks as T


class TestProviderHandoff(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.policy()
        self.engine = FakeL2()
        self.engine.install(self)

    def policy(self, routing="claude:fable > codex:target-a > codex:target-b", **pins):
        self.register(self.project, routing=config.parse_routing(routing), **pins)

    def owner(self, title="Recover existing owner", *, question=False):
        task = T.new(self.project, title, "Finish the existing authorized change.",
                     paths=["README.md"], hold_merge="Operator must review the delivered PR")
        slug = task["slug"]
        dispatch.run(self.project, slug)
        if question:
            T.block(self.project, slug, "Which retention period is approved?", actor="l2",
                    updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, recommendation="Retain fourteen days.")
            T.resume(self.project, slug)
        task = S.load_task(self.project, slug)
        self.engine.workers[task["agent_id"]].update(state="failed", status="exited")
        return T.block(self.project, slug, "The existing worker exited without a report.",
                       actor="altd", updates={"fault": "l2-died"})

    def request(self, task, **options):
        return dispatch.request_task_operation(
            self.project, task["slug"], "handoff", "Continue the authorized work on the requested engine.",
            actor="l3", engine=options.get("engine", "codex"),
            expected_attempt=options.get("attempt", task["attempt"]))

    def execute(self, task):
        result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertEqual(result["request"]["status"], "done", result)
        return S.load_task(self.project, task["slug"])

    def test_coordinator_handoff_preserves_dirty_work_delivery_questions_and_history_then_resumes_normally(self):
        task = self.owner(question=True)
        slug = task["slug"]
        worktree = Path(task["worktree"])
        progress = S.task_dir(self.project, slug) / "progress.md"
        progress.write_text("Done: saved implementation. Next: finish required checks and preserve the review hold.\n")
        (worktree / "README.md").write_text("Existing uncommitted implementation.\n")
        task.update(prs=[42], adopted_pr={"number": 42, "head": git("rev-parse", "HEAD", cwd=worktree).strip()})
        task["delivery"] = {"number": 42, "at": S.now(), "branch": task["branch"]}
        task["verified"] = {"verdict": "ok", "attempt": task["attempt"], "delivery": task["delivery"]}
        report = progress.with_name("report.json")
        report.write_text('{"landed": {"prs": [{"number": 42, "merged": true}]}}\n')
        prior_report = report.read_text()
        S.save_task(self.project, task)
        message = T.message(self.project, slug, "l3", "Continue the existing work; the unanswered choice stays open.")
        before = S.load_task(self.project, slug)
        conversation = T.task_messages(self.project, slug)
        events = S.read_events(self.project, slug)
        arguments = ["task", "handoff", slug, "--engine", "codex", "--attempt", str(task["attempt"]),
                     "--reason", "The operator authorized continuation on the other engine."]
        response = server.l3_verb_request(self.project, {"kind": "alt", "args": arguments})
        self.assertEqual(response["returncode"], 0, response["stderr"])
        receipt = json.loads(response["stdout"])
        self.assertTrue(receipt["queued"])
        self.assertEqual(len(self.engine.calls), 1, "the coordinator persists a request without launching")
        self.assertEqual(S.load_task(self.project, slug)["state"], "blocked")
        queued = self.execute(task)
        self.assertEqual((queued["state"], queued["attempt"], queued["next_engine"]), ("queued", 1, "codex"))
        self.assertNotIn("verified", queued, "earlier success is not current-attempt verification")
        self.assertEqual(report.read_text(), prior_report, "the earlier report remains history")
        self.assertEqual(len(self.engine.calls), 1, "handoff uses the ordinary fresh-dispatch queue")
        self.assertFalse(queued.get("resume_after"))
        dispatch.run(self.project, slug)
        current = S.load_task(self.project, slug)
        self.assertEqual((current["state"], current["attempt"], current["l2_engine"]), ("running", 2, "codex"))
        self.assertNotEqual(current["session_id"], before["session_id"])
        self.assertNotEqual(current["agent_id"], before["agent_id"])
        self.assertNotIn("next_engine", current)
        self.assertFalse(current.get("engine") or current.get("model") or current.get("routing_pinned"))
        for key in ("slug", "title", "request", "worktree", "branch", "paths", "prs", "adopted_pr", "delivery", "questions", "hold_merge"):
            self.assertEqual(current.get(key), before.get(key), key)
        self.assertEqual(T.task_messages(self.project, slug), conversation)
        self.assertEqual(S.read_events(self.project, slug)[:len(events)], events)
        self.assertIn(before["agent_id"], self.engine.workers, "earlier worker evidence stays available")
        self.assertIn({"engine": "claude", "session_id": before["session_id"], "attempt": 1, "attempts": [1]},
                      current["token_usage_sessions"])
        self.assertEqual((worktree / "README.md").read_text(), "Existing uncommitted implementation.\n")
        self.assertIn(progress.read_text().strip(), self.engine.calls[-1]["prompt"])
        self.assertIn(before["questions"][-1]["id"], self.engine.calls[-1]["prompt"])
        self.assertEqual(current["questions"][-1]["status"], "open")
        self.assertEqual([row["id"] for row in T.take_inbox(self.project, slug)], [message["id"]])
        self.policy("claude:opus")
        self.engine.workers[current["agent_id"]].update(state="failed", status="exited")
        T.block(self.project, slug, "Continue the same conversation after the checkpoint.", actor="altd")
        dispatch.request_task_operation(self.project, slug, "resume", "Continue the same session.", actor="l3")
        self.execute(current)
        resumed = S.load_task(self.project, slug)
        self.assertEqual((resumed["attempt"], resumed["l2_engine"], resumed["session_id"], resumed["launch_model"]),
                         (2, "codex", current["session_id"], "target-a"))
        self.assertEqual(self.engine.calls[-1]["session_id"], current["session_id"])
        self.assertEqual(resumed["questions"], before["questions"])
        self.assertEqual(resumed["hold_merge"], before["hold_merge"])

    def test_handoff_request_is_idempotent_and_target_and_attempt_are_part_of_its_identity(self):
        task = self.owner()
        first = self.request(task)
        again = self.request(task)
        self.assertTrue(again["idempotent"])
        self.assertEqual(first["request"]["id"], again["request"]["id"])
        self.assertEqual((first["request"]["engine"], first["request"]["attempt"]), ("codex", 1))
        for changed in ({"engine": "claude"}, {"attempt": 2}):
            with self.subTest(changed=changed), self.assertRaisesRegex(T.TransitionError, "already queued"):
                self.request(task, **changed)
        self.execute(task)
        self.assertTrue(self.request(task)["idempotent"])
        self.assertTrue(dispatch.run_task_operation(self.project, task["slug"])["idempotent"])
        self.assertEqual(len([event for event in S.read_events(self.project, task["slug"])
                              if event["kind"] == "daemon-request"]), 1)
        self.assertEqual(len(self.engine.calls), 1)

    def test_competing_daemon_runners_preserve_one_successful_handoff(self):
        task = self.owner()
        self.request(task)
        barrier = threading.Barrier(2)
        handoff = dispatch.handoff
        def concurrent_handoff(*args):
            barrier.wait(timeout=5)
            return handoff(*args)
        with mock.patch.object(dispatch, "handoff", side_effect=concurrent_handoff), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: dispatch.run_task_operation(self.project, task["slug"]), range(2)))
        self.assertEqual([row["request"]["status"] for row in results], ["done", "done"])
        self.assertEqual(sum(event.get("to") == "queued" for event in S.read_events(self.project, task["slug"])), 1)
        dispatch.run(self.project, task["slug"])
        self.assertEqual(len(self.engine.calls), 2)

    def test_interrupted_receipt_holds_dispatch_until_daemon_reconciles_the_requeue(self):
        task = self.owner()
        self.request(task)
        def interrupted(*args):
            with self.assertRaisesRegex(T.TransitionError, "daemon request"):
                dispatch.run(self.project, task["slug"])
            raise RuntimeError("fixture daemon exit before receipt")
        with mock.patch.object(dispatch, "_finish_task_operation", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "fixture daemon exit"):
                dispatch.run_task_operation(self.project, task["slug"])
        self.assertEqual(len(self.engine.calls), 1)
        recovered = self.execute(task)
        self.assertEqual(recovered["next_engine"], "codex")
        dispatch.run(self.project, task["slug"])
        self.assertEqual(len(self.engine.calls), 2)

    def test_execution_refuses_changed_attempt_worker_session_or_block(self):
        for key, value in (("attempt", 2), ("agent_id", "replacement-worker"),
                           ("session_id", "replacement-session"), ("block_id", "replacement-block")):
            with self.subTest(key=key):
                task = self.owner("Fence " + key)
                self.request(task)
                changed = S.load_task(self.project, task["slug"])
                changed[key] = value
                S.save_task(self.project, changed)
                result = dispatch.run_task_operation(self.project, task["slug"])
                self.assertEqual(result["request"]["status"], "refused", result)
                saved = S.load_task(self.project, task["slug"])
                self.assertEqual(saved[key], value)
                self.assertEqual(saved["state"], "blocked")
                self.assertFalse(saved.get("next_engine"))

    def test_live_worker_and_inflight_resume_are_never_replaced(self):
        task = self.owner()
        self.request(task)
        self.engine.workers[task["agent_id"]].update(state="working", status="busy")
        result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertEqual(result["request"]["status"], "refused")
        self.assertIn("live worker", result["request"]["note"])
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "working")
        self.assertEqual(len(self.engine.calls), 1)
        for key in ("dispatching", "resume_claim", "completion_requested"):
            with self.subTest(key=key):
                claimed = self.owner("Active " + key)
                claimed[key] = {"id": "active"} if key == "resume_claim" else S.now()
                S.save_task(self.project, claimed)
                with self.assertRaisesRegex(T.TransitionError, "no active claim"):
                    self.request(claimed)

    def test_decision_only_other_fault_unlaunched_and_finished_tasks_are_ineligible(self):
        for state, fault, attempt in (("blocked", None, 1), ("blocked", "external-ci", 1),
                                      ("blocked", "l2-died", 0), ("reported", "l2-died", 1),
                                      ("done", "l2-died", 1)):
            with self.subTest(state=state, fault=fault, attempt=attempt):
                task = self.owner(f"Eligibility {state} {fault} {attempt}", question=True)
                task.update(state=state, fault=fault, attempt=attempt)
                S.save_task(self.project, task)
                with self.assertRaises(T.TransitionError):
                    self.request(task)
                self.assertEqual(S.load_task(self.project, task["slug"]), task)

    def test_usage_block_with_unknown_reset_is_eligible_without_claiming_a_system_fault(self):
        task = self.owner()
        task.pop("fault")
        task["usage_limit"] = {"scope": "model", "model": "fable", "until": None,
                               "why": "model allowance exhausted; reset unknown"}
        S.save_task(self.project, task)
        self.request(task)
        queued = self.execute(task)
        self.assertFalse(queued.get("fault") or queued.get("resume_after"))
        self.assertEqual(queued["next_engine"], "codex")

    def test_task_attempt_and_project_pins_are_never_overridden(self):
        for pin in ({"engine": "claude"}, {"model": "fable"}, {"routing_pinned": True}):
            with self.subTest(pin=pin):
                task = self.owner("Pinned " + next(iter(pin)))
                task.update(pin)
                S.save_task(self.project, task)
                with self.assertRaisesRegex(T.TransitionError, "pin"):
                    self.request(task)
        task = self.owner("Project pinned")
        self.policy(l2_engine="claude")
        with self.assertRaisesRegex(T.TransitionError, "pin"):
            self.request(task)

    def test_configuration_changes_are_checked_again_at_execution_and_fresh_dispatch(self):
        for phase in ("execution", "dispatch"):
            for change in ("pin", "routing"):
                with self.subTest(phase=phase, change=change):
                    self.policy()
                    task = self.owner(f"Configuration {phase} {change}")
                    self.request(task)
                    if phase == "dispatch":
                        self.execute(task)
                    if change == "pin":
                        self.policy(l2_engine="claude")
                    else:
                        self.policy("claude:fable")
                    launched = len(self.engine.calls)
                    if phase == "execution":
                        result = dispatch.run_task_operation(self.project, task["slug"])
                        self.assertEqual(result["request"]["status"], "refused", result)
                    else:
                        with self.assertRaisesRegex(T.TransitionError, "pin|configured routing"):
                            dispatch.run(self.project, task["slug"])
                        self.assertEqual(S.load_task(self.project, task["slug"])["next_engine"], "codex")
                    self.assertEqual(len(self.engine.calls), launched)

    def test_rejected_target_models_never_fall_back_to_an_unrequested_engine(self):
        task = self.owner()
        self.request(task)
        self.execute(task)
        rejected = {"returncode": 0, "rejection": {"scope": "model", "why": "model access rejected"},
                    "safe_to_retry": True, "agent": {"id": "rejected", "sessionId": "rejected-session", "state": "failed"}}
        self.engine.outcomes = [rejected, rejected]
        with self.assertRaisesRegex(T.TransitionError, "no configured option available"):
            dispatch.run(self.project, task["slug"])
        self.assertEqual([(call["engine"], call["model"]) for call in self.engine.calls[1:]],
                         [("codex", "target-a"), ("codex", "target-b")])
        self.assertEqual(S.load_task(self.project, task["slug"])["next_engine"], "codex")

    def test_queue_and_dispatch_share_target_unavailability_and_changed_pin_explanations(self):
        task = self.owner()
        self.request(task)
        queued = self.execute(task)
        self.assertEqual(digest._waiting(self.project, queued, None)["reason"], "ready for dispatch")
        route.note_rejection({"engine": "codex"}, {"scope": "engine", "why": "target temporarily unavailable"})
        for change in ("unavailable", "pin", "removed"):
            with self.subTest(change=change):
                if change == "pin":
                    self.policy(l2_engine="claude")
                elif change == "removed":
                    self.policy("claude:fable")
                expected = route.pick_task(config.project(self.project), queued)
                self.assertIsNone(expected["engine"])
                waiting = digest._waiting(self.project, queued, None)
                self.assertEqual((waiting["kind"], waiting["reason"]), ("engine", "engine hold: " + expected["why"]))
                with self.assertRaises(T.TransitionError) as failure:
                    dispatch.run(self.project, task["slug"])
                self.assertEqual(str(failure.exception), waiting["reason"])
                self.assertEqual(len(self.engine.calls), 1)
                self.assertEqual(S.load_task(self.project, task["slug"]), queued)

    def test_cli_denies_l2_and_coordinator_cannot_redirect_project_or_target_path(self):
        task = self.owner()
        arguments = ["task", "handoff", task["slug"], "--engine", "codex", "--attempt", "1", "--reason", "Continue"]
        denied = self.alt(*arguments, env=dispatch.l2_env(self.project, task["slug"], task["attempt"]))
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("not available to an L2 worker", denied.stderr)
        for args in ([*arguments, "--project", "another-project"],
                     ["task", "handoff", "../another-task", *arguments[3:]]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                server.l3_verb_request(self.project, {"kind": "alt", "args": args})
        self.assertNotIn("daemon_request", S.load_task(self.project, task["slug"]))
