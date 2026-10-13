"""#310: explicit recovery retains one task while replacing its exited provider attempt."""
import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from tests.test_images import upload
from altitude import config, digest, dispatch, engines, route, server, state as S, tasks as T


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
        messages = T.task_messages(self.project, slug)
        self.assertEqual(messages[:-1], conversation)
        self.assertEqual(messages[-1]["text"], "Switch to Codex queued.")
        self.assertEqual(S.read_events(self.project, slug)[:len(events)], events)
        self.assertIn(before["agent_id"], self.engine.workers, "earlier worker evidence stays available")
        self.assertIn({"engine": "claude", "session_id": before["session_id"], "attempt": 1, "attempts": [1]},
                      current["token_usage_sessions"])
        self.assertEqual((worktree / "README.md").read_text(), "Existing uncommitted implementation.\n")
        self.assertIn(progress.read_text().strip(), self.engine.calls[-1]["prompt"])
        self.assertIn(before["questions"][-1]["id"], self.engine.calls[-1]["prompt"])
        self.assertEqual(current["questions"][-1]["status"], "open")
        self.assertIn(message["id"], self.engine.calls[-1]["prompt"])
        self.assertIn(message["text"], self.engine.calls[-1]["prompt"])
        self.assertEqual(current["message_deliveries"][message["id"]]["agent_id"], current["agent_id"])
        self.assertEqual(T.take_inbox(self.project, slug), [])
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

    def test_fresh_handoff_keeps_already_delivered_chat_images_visually_inspectable(self):
        capability = self.patch(engines, "image_capability", return_value={"available": True, "why": "fixture transport"})
        self.patch(engines.common, "image_capability", new=capability)
        task = self.owner()
        slug = task["slug"]
        rows = [T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, f"Inspect screenshot {index}.",
                          uploads=[upload(name=f"screen-{index}.png")]) for index in range(5)]
        self.assertEqual(T.take_inbox(self.project, slug), rows)
        before = S.load_task(self.project, slug)
        self.assertFalse(before.get("images"), "Only already-delivered chat images can supply this context")
        self.request(before)
        self.execute(before)
        dispatch.run(self.project, slug)
        current = S.load_task(self.project, slug)
        launch = self.engine.calls[-1]
        self.assertEqual((current["attempt"], launch["engine"]), (2, "codex"))
        self.assertIsNone(launch["session_id"], "An explicit handoff uses a fresh native session")
        self.assertEqual(current["hold_merge"], before["hold_merge"])
        messages = T.task_messages(self.project, slug)
        self.assertEqual(messages[:-1], rows)
        self.assertEqual(messages[-1]["text"], "Switch to Codex queued.")
        self.assertEqual(T.pending(self.project, slug), [], "Historical context must not requeue delivered messages")
        self.assertEqual(len(launch["images"]), len(rows))
        content = engines.common._engine_input(launch["engine"], launch["prompt"], launch["images"])
        self.assertEqual([item["type"] for item in content], ["text"], "A larger history uses native visual reads")
        prompt = content[0]["text"]
        self.assertIn("native view_image tool", prompt)
        for row, image in zip(rows, launch["images"]):
            self.assertIn(row["text"], prompt)
            self.assertIn(row["id"], prompt)
            self.assertIn(image["path"], prompt)
            self.assertEqual(image["source_message_id"], row["id"])
            self.assertEqual(hashlib.sha256(Path(image["path"]).read_bytes()).hexdigest(), image["sha256"])

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

    def test_live_worker_waits_for_checkpoint_and_inflight_claims_refuse(self):
        task = self.owner()
        self.request(task)
        self.engine.workers[task["agent_id"]].update(state="working", status="busy")
        result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertTrue(result["pending"])
        self.assertIn("handoff note", result["request"]["note"])
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "working")
        self.assertEqual(len(self.engine.calls), 1)
        for key in ("dispatching", "resume_claim", "completion_requested"):
            with self.subTest(key=key):
                claimed = self.owner("Active " + key)
                claimed[key] = {"id": "active"} if key == "resume_claim" else S.now()
                S.save_task(self.project, claimed)
                with self.assertRaisesRegex(T.TransitionError, "no active claim"):
                    self.request(claimed)

    def running_owner(self, title="Transfer running owner"):
        task = T.new(self.project, title, "Finish the existing authorized change.", paths=["README.md"])
        dispatch.run(self.project, task["slug"])
        return S.load_task(self.project, task["slug"])

    def select(self, task, engine, selection, **options):
        return dispatch.request_task_operation(
            self.project, task["slug"], "handoff", "Apply the chosen task model and effort.",
            actor="l3", engine=engine, selection=selection, expected_attempt=task["attempt"], **options)

    def test_same_engine_selection_waits_then_starts_exact_fresh_attempt_without_pin(self):
        task = self.running_owner()
        selection = {"model": task["launch_model"], "effort": "high"}
        request = self.select(task, task["l2_engine"], selection)["request"]
        waiting = dispatch.run_task_operation(self.project, task["slug"])
        self.assertTrue(waiting["pending"])
        self.assertEqual(len(self.engine.calls), 1)
        self.ready(task, request)
        queued = self.execute(task)
        self.assertEqual(queued["next_selection"], selection)
        self.assertEqual(queued["l2_engine"], task["l2_engine"])
        dispatch.run(self.project, task["slug"])
        fresh = S.load_task(self.project, task["slug"])
        self.assertEqual(fresh["attempt"], 2)
        self.assertNotEqual(fresh["session_id"], task["session_id"])
        self.assertEqual((fresh["launch_model"], fresh["launch_effort"]), (selection["model"], "high"))
        self.assertFalse(fresh["routing_pinned"])
        self.assertNotIn("next_selection", fresh)
        self.assertNotIn("next_engine", fresh)
        self.assertTrue(self.select(fresh, fresh["l2_engine"], {**selection, "effort": "medium"})["queued"])

    def test_selected_tuple_is_frozen_across_default_changes_and_request_retries(self):
        task = self.owner(question=True)
        selection = {"model": "target-a", "effort": "medium"}
        first = self.select(task, "codex", selection)
        self.assertEqual(self.select(task, "codex", selection)["request"]["id"], first["request"]["id"])
        with self.assertRaisesRegex(T.TransitionError, "already queued"):
            self.select(task, "codex", {**selection, "effort": "high"})
        with mock.patch.object(config, "default_model", return_value="different-default"), \
                mock.patch.object(config, "default_effort", return_value="ultra"):
            self.execute(task)
            dispatch.run(self.project, task["slug"])
        fresh = S.load_task(self.project, task["slug"])
        self.assertEqual((fresh["launch_model"], fresh["launch_effort"]), ("target-a", "medium"))
        self.assertEqual(fresh["questions"], task["questions"])
        self.assertEqual(fresh["hold_merge"], task["hold_merge"])

    def test_current_launch_facts_change_only_after_successful_successor_start(self):
        task = self.owner()
        selection = {"model": "target-a", "effort": "medium"}
        self.select(task, "codex", selection)
        self.execute(task)
        observed = []

        def start(engine, name, prompt, **kwargs):
            current = S.load_task(self.project, task["slug"])
            view = server.task_transfer_view(self.project, current)
            observed.append(current)
            self.assertEqual(current["state"], "queued")
            self.assertEqual((view["current_engine"], view["current_model"], view["current_effort"]),
                             (task["l2_engine"], task["launch_model"], task.get("launch_effort")))
            self.assertIn("codex (target-a)", prompt)
            return self.engine.start_l2(engine, name, prompt, **kwargs)

        with mock.patch.object(engines, "start_l2", side_effect=start):
            dispatch.run(self.project, task["slug"])
        self.assertEqual(len(observed), 1)
        fresh = S.load_task(self.project, task["slug"])
        self.assertEqual((fresh["l2_engine"], fresh["launch_model"], fresh["launch_effort"]),
                         ("codex", "target-a", "medium"))

    def test_failed_successor_launch_does_not_apply_requested_settings(self):
        task = self.owner()
        self.select(task, "codex", {"model": "target-a", "effort": "medium"})
        self.execute(task)
        with mock.patch.object(engines, "start_l2", return_value={"returncode": 1, "stderr": "fixture failure"}), \
                mock.patch("altitude.incidents.system_fault"):
            with self.assertRaises(dispatch.DispatchFailure):
                dispatch.run(self.project, task["slug"])
        failed = S.load_task(self.project, task["slug"])
        for key in ("l2_engine", "launch_model", "launch_effort", "attempt"):
            self.assertEqual(failed.get(key), task.get(key), key)

    def test_default_preview_changes_and_unchanged_tuple_create_no_request(self):
        task = self.running_owner()
        current = {"model": task["launch_model"], "effort": task.get("launch_effort")}
        with self.assertRaisesRegex(T.TransitionError, "already uses"):
            self.select(task, task["l2_engine"], current)
        with self.assertRaisesRegex(T.TransitionError, "Default changed"):
            self.select(task, "codex", {"model": "old-default", "effort": "high"}, handoff_default="old-default")
        self.assertFalse(S.load_task(self.project, task["slug"]).get("daemon_request"))

    def test_selected_transfer_keeps_pin_and_attempt_refusals(self):
        task = self.owner()
        selection = {"model": "target-a", "effort": "medium"}
        task["routing_pinned"] = True
        S.save_task(self.project, task)
        with self.assertRaisesRegex(T.TransitionError, "pin"):
            self.select(task, "codex", selection)
        task["routing_pinned"] = False
        S.save_task(self.project, task)
        self.select(task, "codex", selection)
        changed = S.load_task(self.project, task["slug"])
        changed["attempt"] += 1
        S.save_task(self.project, changed)
        result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertEqual(result["request"]["status"], "refused")
        self.assertNotIn("next_selection", S.load_task(self.project, task["slug"]))

    def ready(self, task, request):
        folder = S.task_dir(self.project, task["slug"])
        (folder / "progress.md").write_text("Saved work. Next: validate the unfinished change.\n")
        S.write_json(folder / f"handoff-{request['id']}.ready.json",
                     {key: request[key] for key in ("id", "attempt", "agent_id", "session_id")})

    def test_running_owner_checkpoint_then_fresh_attempt_preserves_dirty_work(self):
        task = self.running_owner()
        path = Path(task["worktree"]) / "README.md"
        path.write_text("Uncommitted work\n")
        request = self.request(task)["request"]
        for _ in range(3):
            self.assertTrue(dispatch.run_task_operation(self.project, task["slug"])["pending"])
        sends = self.engine.worker_sends(task["agent_id"], job_root=dispatch.l2_job_root(self.project, task["slug"]))
        self.assertEqual(len(list(sends.glob("*.json"))), 1)
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "working")
        self.ready(task, request)
        with self.assertRaisesRegex(T.TransitionError, "readiness is recorded"):
            T.block(self.project, task["slug"], "Waiting for transfer", actor="l2",
                    expected_attempt=task["attempt"])
        self.execute(task)
        dispatch.run(self.project, task["slug"])
        current = S.load_task(self.project, task["slug"])
        self.assertEqual((current["attempt"], current["l2_engine"]), (2, "codex"))
        self.assertEqual(path.read_text(), "Uncommitted work\n")
        self.assertEqual(current["daemon_request"]["checkpoint"], "ready")
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "stopped")

    def test_running_natural_exit_transfers_without_fresh_note(self):
        task = self.running_owner()
        self.request(task)
        self.assertTrue(dispatch.run_task_operation(self.project, task["slug"])["pending"])
        self.engine.workers[task["agent_id"]].update(state="done", status="exited")
        queued = self.execute(task)
        self.assertEqual(queued["daemon_request"]["checkpoint"], "not refreshed")

    def test_explicit_stop_by_each_actor_qualifies(self):
        for actor in ("l3", config.OPERATOR_ACTOR):
            task = self.running_owner("Stopped by " + actor)
            dispatch.request_task_operation(self.project, task["slug"], "stop", "Pause", actor=actor)
            dispatch.run_task_operation(self.project, task["slug"])
            task = S.load_task(self.project, task["slug"])
            self.request(task)
            self.assertEqual(self.execute(task)["state"], "queued")

    def test_switch_now_and_cancel_are_request_fenced(self):
        for action in ("now", "cancel"):
            task = self.running_owner("Control " + action)
            request = self.request(task)["request"]
            self.assertTrue(dispatch.run_task_operation(self.project, task["slug"])["pending"])
            with self.assertRaisesRegex(T.TransitionError, "changed"):
                dispatch.handoff_control(self.project, task["slug"], "stale", action, actor="l3", reason="Explicit")
            result = dispatch.handoff_control(self.project, task["slug"], request["id"], action,
                                              actor="l3", reason="Explicit")
            if action == "now":
                self.assertEqual(self.execute(task)["state"], "queued")
            else:
                self.assertEqual(result["request"]["status"], "cancelled")
                self.ready(task, request)
                self.assertTrue(dispatch.run_task_operation(self.project, task["slug"])["idempotent"])
                self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "working")

    def test_unknown_termination_never_queues_replacement(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        dispatch.handoff_control(self.project, task["slug"], request["id"], "now", actor="l3", reason="Explicit")
        with mock.patch.object(engines, "worker_termination", return_value=None):
            result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertEqual(result["request"]["status"], "refused")
        self.assertIn("unconfirmed", result["request"]["note"])
        self.assertFalse(S.load_task(self.project, task["slug"]).get("next_engine"))

    def test_stale_readiness_does_not_stop_running_owner(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        self.ready(task, {**request, "attempt": task["attempt"] + 1})
        self.assertTrue(dispatch.run_task_operation(self.project, task["slug"])["pending"])
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "working")

    def test_checkpoint_send_survives_driver_claim_and_daemon_restart(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        dispatch.run_task_operation(self.project, task["slug"])
        sends = self.engine.worker_sends(task["agent_id"], job_root=dispatch.l2_job_root(self.project, task["slug"]))
        drop = sends / (request["id"] + ".json")
        drop.rename(drop.with_suffix(".writing"))
        dispatch.run_task_operation(self.project, task["slug"])
        self.assertFalse(drop.exists(), "restart must not recreate an already claimed instruction")
        drop.with_suffix(".writing").rename(drop.with_suffix(".delivered"))
        dispatch.run_task_operation(self.project, task["slug"])
        self.assertFalse(drop.exists())

    def test_interruption_after_confirmed_stop_can_requeue_same_request(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        self.ready(task, request)
        with mock.patch.object(T, "requeue", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                dispatch.run_task_operation(self.project, task["slug"])
        stopped = S.load_task(self.project, task["slug"])
        self.assertEqual(stopped["daemon_request"]["phase"], "stopping")
        with self.assertRaisesRegex(T.TransitionError, "committed"):
            dispatch.handoff_control(self.project, task["slug"], request["id"], "cancel", actor="l3", reason="Cancel")
        self.assertEqual(self.execute(task)["state"], "queued")

    def test_cancel_correction_recovers_after_interruption(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        dispatch.run_task_operation(self.project, task["slug"])
        with mock.patch.object(dispatch, "_notify_handoff_cancelled", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                dispatch.handoff_control(self.project, task["slug"], request["id"], "cancel", actor="l3", reason="Cancel")
        self.assertIn(task["slug"], dispatch.pending_task_operations(self.project))
        dispatch.run_task_operation(self.project, task["slug"])
        self.assertNotIn(task["slug"], dispatch.pending_task_operations(self.project))
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "working")
        sends = self.engine.worker_sends(task["agent_id"], job_root=dispatch.l2_job_root(self.project, task["slug"]))
        self.assertTrue((sends / (request["id"] + "-cancel.json")).exists())

    def test_slow_and_disconnected_workers_are_not_forced(self):
        task = self.running_owner()
        self.request(task)
        current = S.load_task(self.project, task["slug"])
        current["daemon_request"]["at"] = "2000-01-01T00:00:00+00:00"
        S.save_task(self.project, current)
        self.engine.workers[task["agent_id"]].update(detail="Network disconnected", status="busy")
        self.assertTrue(dispatch.run_task_operation(self.project, task["slug"])["pending"])
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "working")

    def test_cli_now_cancel_and_invalid_combinations(self):
        task = self.running_owner()
        prefix = ["task", "handoff", task["slug"]]
        response = server.l3_verb_request(self.project, {"kind": "alt", "args": [*prefix, "--engine", "codex",
                                         "--attempt", "1", "--reason", "Transfer"]})
        self.assertEqual(response["returncode"], 0, response)
        request = json.loads(response["stdout"])["request"]
        response = server.l3_verb_request(self.project, {"kind": "alt", "args": [*prefix, "--request", request["id"],
                                         "--cancel", "--reason", "Keep owner"]})
        self.assertEqual(response["returncode"], 0, response)
        self.assertEqual(json.loads(response["stdout"])["request"]["status"], "cancelled")
        for flags in (["--cancel"], ["--request", request["id"]], ["--engine", "codex"]):
            response = self.alt(*prefix, *flags, "--reason", "Invalid", env={"ALTITUDE_ACTOR": "l3"})
            self.assertNotEqual(response.returncode, 0)

    def test_target_refusal_returns_owner_to_saved_work_with_durable_correction(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        dispatch.run_task_operation(self.project, task["slug"])
        self.policy("claude:fable")
        result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertEqual(result["request"]["status"], "refused")
        correction = next(row for row in T.pending(self.project, task["slug"]) if row["id"] == request["id"] + "-cancel")
        self.assertIn("Stop waiting", correction["text"])
        self.assertTrue(correction["wake"])

    def test_cancel_after_ready_and_exit_reaches_saved_session_inbox(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        dispatch.run_task_operation(self.project, task["slug"])
        self.ready(task, request)
        self.engine.workers[task["agent_id"]].update(state="done", status="exited")
        dispatch.handoff_control(self.project, task["slug"], request["id"], "cancel", actor="l3", reason="Cancel")
        T.settle_send_now(self.project, task["slug"], request["id"] + "-cancel", "returned", agent_id=task["agent_id"])
        self.assertTrue(any("Stop waiting" in row["text"] for row in T.pending(self.project, task["slug"])))
        with mock.patch.object(server, "request_task_resume") as resume:
            server._on_l2_finished(self.project, {"task": S.load_task(self.project, task["slug"]),
                                   "agent": self.engine.workers[task["agent_id"]], "died": True})
        resume.assert_called_once()
        self.assertTrue(any("Stop waiting" in row["text"] for row in T.pending(self.project, task["slug"])))

    def test_fresh_report_during_preparation_is_not_requeued(self):
        task = self.running_owner()
        self.request(task)
        dispatch.run_task_operation(self.project, task["slug"])
        report = S.task_dir(self.project, task["slug"]) / "report.json"
        report.write_text('{"landed": {"prs": []}, "review": [], "blocked": ""}')
        self.engine.workers[task["agent_id"]].update(state="done", status="exited")
        result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertEqual(result["request"]["status"], "refused")
        self.assertIn("report", result["request"]["note"])
        self.assertFalse(S.load_task(self.project, task["slug"]).get("next_engine"))
        self.assertFalse(any(row.get("wake", True) for row in T.pending(self.project, task["slug"])))

    def test_owner_question_and_operator_stop_supersede_preparation(self):
        for action in ("question", "stop", "reject"):
            task = self.running_owner("Interrupt preparation " + action)
            self.request(task)
            dispatch.run_task_operation(self.project, task["slug"])
            if action == "question":
                T.block(self.project, task["slug"], "Which scope?", actor="l2", expected_attempt=task["attempt"])
                current = S.load_task(self.project, task["slug"])
                self.assertEqual(current["daemon_request"]["status"], "cancelled")
                self.assertEqual(current["questions"][-1]["status"], "open")
            else:
                result = dispatch.request_task_operation(self.project, task["slug"], action, "Operator interruption",
                                                         actor=config.OPERATOR_ACTOR)
                self.assertEqual(result["request"]["operation"], action)
                current = dispatch.run_task_operation(self.project, task["slug"])
                self.assertEqual(current["request"]["status"], "done")

    def test_checkpoint_note_is_not_reused_for_a_later_unrelated_attempt(self):
        task = self.owner()
        self.request(task)
        self.execute(task)
        self.assertIn("handoff note was not refreshed", dispatch.build_brief(self.project, task["slug"]))
        current = S.load_task(self.project, task["slug"])
        current["attempt"] += 1
        S.save_task(self.project, current)
        self.assertNotIn("handoff note was", dispatch.build_brief(self.project, task["slug"]))

    def test_invalid_owner_block_does_not_cancel_preparation(self):
        task = self.running_owner()
        request = self.request(task)["request"]
        dispatch.run_task_operation(self.project, task["slug"])
        for options in ({"questions": {"invalid": True}}, {"expected_owner": {"stale": True}},
                        {"show": [], "updates": {"fault": "bad"}}):
            with self.subTest(options=options), self.assertRaises(T.TransitionError):
                T.block(self.project, task["slug"], "Invalid block", actor="l2",
                        expected_attempt=task["attempt"], **options)
            current = S.load_task(self.project, task["slug"])
            self.assertEqual(current["daemon_request"]["status"], "executing")
            self.assertFalse(any(row["id"] == request["id"] + "-cancel"
                                 for row in T.pending(self.project, task["slug"])))

    def test_unconfirmed_blocked_unlaunched_and_finished_tasks_are_ineligible(self):
        for state, fault, attempt in (("blocked", None, 1), ("blocked", "external-ci", 1),
                                      ("blocked", "l2-died", 0), ("reported", "l2-died", 1),
                                      ("done", "l2-died", 1)):
            with self.subTest(state=state, fault=fault, attempt=attempt):
                task = self.owner(f"Eligibility {state} {fault} {attempt}", question=True)
                self.engine.workers[task["agent_id"]].update(state="working", status="busy")
                task.update(state=state, fault=fault, attempt=attempt)
                S.save_task(self.project, task)
                with self.assertRaises(T.TransitionError):
                    self.request(task)
                self.assertEqual(S.load_task(self.project, task["slug"]), task)

    def test_ended_worker_does_not_release_a_fault_or_dependency_wait(self):
        for fault in ("external-ci", "git", None):
            with self.subTest(fault=fault):
                task = self.owner(f"Ended wait {fault}", question=bool(fault))
                task.update(fault=fault, questions=task.get("questions", []) if fault else [])
                S.save_task(self.project, task)
                with self.assertRaisesRegex(T.TransitionError, "recovery or dependency"):
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
