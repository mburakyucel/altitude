"""Task-page transfers use the same durable daemon requests as the CLI."""
import http.client
import json
import threading
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, server, state as S, tasks as T


class TestTaskTransferAPI(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.register(self.project, routing=config.parse_routing("claude:fable > codex:target-a"))
        self.engine = FakeL2()
        self.engine.install(self)
        self.patch(engines, "review_capability", return_value={"available": False, "why": "Fixture has no reviewer"})
        self.patch(server, "spawn", return_value=True)
        self.patch(server, "log")
        self.patch(server.monitor, "sessions", return_value=[])
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        task = T.new(self.project, "Switch this task", "Keep its decisions.", paths=["README.md"], hold_merge="Review first")
        dispatch.run(self.project, task["slug"])
        self.task = S.load_task(self.project, task["slug"])

    def request(self, path, body=None, status=200):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            connection.request("GET" if body is None else "POST", path,
                               body=json.dumps(body) if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = json.loads(response.read())
            self.assertEqual(response.status, status, data)
            return data
        finally:
            connection.close()

    def view(self):
        return self.request(f"/api/task/{self.project}/{self.task['slug']}")["transfer"]

    def action(self, action="handoff", status=200, **fields):
        return self.request("/api/task/action", {"project": self.project, "slug": self.task["slug"],
                                               "action": action, **fields}, status)

    def test_patient_request_reconciles_then_cancel_preserves_owner_and_hold(self):
        view = self.view()
        self.assertTrue(view["eligible"])
        self.assertTrue(view["preparation"])
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])
        self.action(engine=target["engine"], observed=view["observed"])
        request = self.view()["request"]
        self.assertEqual(request["status"], "pending")
        self.assertEqual(request["target_label"], target["label"])
        self.action(engine=target["engine"], observed=view["observed"])
        self.assertEqual(self.view()["request"]["id"], request["id"])
        self.action("handoff-cancel", request_id=request["id"])
        after = S.load_task(self.project, self.task["slug"])
        self.assertEqual(after["agent_id"], self.task["agent_id"])
        self.assertEqual(after["hold_merge"], self.task["hold_merge"])
        self.assertEqual(self.view()["request"]["status"], "cancelled")

    def test_reading_the_view_requests_nothing_and_a_switch_names_who_started_it(self):
        view = self.view()
        self.assertIsNone(view["request"])
        self.assertIsNone(S.load_task(self.project, self.task["slug"]).get("daemon_request"))
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])
        dispatch.request_task_operation(self.project, self.task["slug"], "handoff", "Run existing tasks on the other engine.",
                                        actor="l3", engine=target["engine"], expected_attempt=self.task["attempt"])
        request = self.view()["request"]
        self.assertEqual((request["by"], request["reason"]), ("l3", "Run existing tasks on the other engine."))
        self.action("handoff-cancel", request_id=request["id"])
        self.action(engine=target["engine"], observed=self.view()["observed"])
        request = self.view()["request"]
        self.assertEqual((request["by"], request["reason"]), ("operator", None))

    def test_switch_now_is_fenced_and_daemon_requeues_original_task(self):
        view = self.view()
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"]
        self.action(engine=target, observed=view["observed"])
        request = self.view()["request"]
        self.action("handoff-now", request_id="stale-request", status=409)
        self.action("handoff-now", request_id=request["id"])
        self.assertTrue(self.view()["request"]["now"])
        dispatch.run_task_operation(self.project, self.task["slug"])
        after = S.load_task(self.project, self.task["slug"])
        self.assertEqual(after["state"], "queued")
        self.assertEqual(after["worktree"], self.task["worktree"])
        self.assertEqual(after["branch"], self.task["branch"])
        self.assertEqual(self.view()["request"]["status"], "done")
        dispatch.run_task_operation(self.project, self.task["slug"])
        receipts = [row for row in T.task_messages(self.project, self.task["slug"])
                    if row["id"] == request["id"] + "-outcome"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["text"], f"Switch to {config.ENGINE_LABELS[target]} queued.")
        dispatch.run(self.project, self.task["slug"])
        after = S.load_task(self.project, self.task["slug"])
        self.assertEqual(after["l2_engine"], target)
        self.assertEqual(after["attempt"], self.task["attempt"] + 1)

    def test_a_read_between_the_requeue_and_its_receipt_shows_the_switch_done(self):
        view = self.view()
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"]
        self.action(engine=target, observed=view["observed"])
        self.action("handoff-now", request_id=self.view()["request"]["id"])
        finish, seen = dispatch._finish_task_operation, []

        def read_then_finish(*args):
            seen.append((S.load_task(self.project, self.task["slug"]), self.request(f"/api/task/{self.project}/{self.task['slug']}")))
            return finish(*args)

        with mock.patch.object(dispatch, "_finish_task_operation", side_effect=read_then_finish):
            dispatch.run_task_operation(self.project, self.task["slug"])
        record, page = seen[0]
        self.assertEqual((record["state"], record["daemon_request"]["status"]), ("queued", "executing"))
        self.assertEqual((page["state"], page["transfer"]["request"]["status"]), ("queued", "done"))
        self.assertEqual(S.load_task(self.project, self.task["slug"])["daemon_request"]["status"], "done")

    def test_checkpoint_correction_has_one_short_display_receipt_and_unchanged_native_text(self):
        view = self.view()
        self.action(engine=next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"], observed=view["observed"])
        dispatch.run_task_operation(self.project, self.task["slug"])
        request = self.view()["request"]
        self.action("handoff-cancel", request_id=request["id"])
        self.action("handoff-cancel", request_id=request["id"])
        raw = T.task_messages(self.project, self.task["slug"])
        corrections = [row for row in raw if row["id"].startswith(request["id"])]
        self.assertEqual(len(corrections), 1)
        self.assertIn("Stop waiting for that transfer", corrections[0]["text"])
        page = self.request(f"/api/task/{self.project}/{self.task['slug']}")
        shown = next(row for row in page["messages"] if row["id"] == corrections[0]["id"])
        self.assertEqual(shown["text"], "Switch cancelled.")

    def test_stale_attempt_worker_session_or_block_never_queues(self):
        view = self.view()
        for key in ("attempt", "agent_id", "session_id", "block_id"):
            with self.subTest(key=key):
                observed = {**view["observed"], key: "changed"}
                self.action(engine=next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"], observed=observed, status=409)
                self.assertNotIn("daemon_request", S.load_task(self.project, self.task["slug"]))
        self.action(engine=next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"], status=400)

    def test_pins_unavailable_and_single_engine_are_explained(self):
        self.task["routing_pinned"] = True
        S.save_task(self.project, self.task)
        view = self.view()
        self.assertFalse(view["eligible"])
        self.assertIn("pin", view["reason"])
        self.action(engine=next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"], observed=view["observed"], status=409)
        self.task.pop("routing_pinned")
        S.save_task(self.project, self.task)
        self.register(self.project, routing=[[{"engine": self.task["l2_engine"], "model": "fable"}]])
        view = self.view()
        self.assertEqual([item["engine"] for item in view["targets"]], [view["current_engine"]])
        self.assertTrue(view["eligible"])
        self.action(engine=view["current_engine"], selection={"model": view["current_model"], "effort": "medium"}, observed=view["observed"])
        self.assertEqual(self.view()["request"]["selection"]["effort"], "medium")

    def test_quota_stop_or_confirmed_end_skips_preparation(self):
        self.task["usage_limit"] = {"kind": "weekly"}
        S.save_task(self.project, self.task)
        self.assertTrue(self.view()["quota_limited"])
        self.assertFalse(self.view()["preparation"])
        self.task.pop("usage_limit")
        S.save_task(self.project, self.task)
        self.engine.workers[self.task["agent_id"]].update(state="failed", status="exited")
        self.assertFalse(self.view()["preparation"])

    def test_task_selection_posts_exact_tuple_and_checks_default_preview(self):
        view = self.view()
        before = config.project(self.project)
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])
        selected = target["models"][0]["model"]
        self.action(engine=target["engine"], selection={"model": selected, "effort": "medium"},
                    default_model="stale-default", observed=view["observed"], status=409)
        self.action(engine=target["engine"], selection={"model": selected, "effort": "medium"}, observed=view["observed"])
        self.assertEqual(self.view()["request"]["selection"], {"model": selected, "effort": "medium"})
        self.assertEqual(config.project(self.project), before)

    def test_a_switch_names_a_model_of_its_engine_and_spells_no_override_once(self):
        view = self.view()
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])
        other = next(engine for engine, rows in config.NAMED_MODELS.items() if engine != target["engine"] and rows)
        foreign = config.NAMED_MODELS[other][0][0]
        refused = self.action(engine=target["engine"], selection={"model": foreign, "effort": None},
                              observed=view["observed"], status=409)
        self.assertIn(f"is a {config.ENGINE_LABELS[other]} model", refused["error"])
        self.assertNotIn("daemon_request", S.load_task(self.project, self.task["slug"]))
        selected = target["models"][0]["model"]
        self.action(engine=target["engine"], selection={"model": selected, "effort": "native"}, observed=view["observed"])
        first = self.view()["request"]
        self.assertEqual(first["selection"], {"model": selected, "effort": None})
        # Either spelling is the same request: the retry is accepted and queues nothing new.
        self.action(engine=target["engine"], selection={"model": selected, "effort": None}, observed=view["observed"])
        self.assertEqual(self.view()["request"]["id"], first["id"])

    def test_default_configured_target_catalog_launches_exact_named_choice(self):
        self.register(self.project, routing=config.parse_routing("claude > codex"))
        before = config.project(self.project)
        defaults = config.machine_settings()
        view = self.view()
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])
        self.assertTrue(target["available"], target)
        named = next(model for model in target["models"] if model["value"] != "default")
        self.assertTrue(named["available"], named)
        selection = {"model": named["model"], "effort": "medium"}
        self.action(engine=target["engine"], selection=selection, observed=view["observed"])
        self.assertEqual(self.view()["request"]["selection"], selection)
        self.action("handoff-now", request_id=self.view()["request"]["id"])
        dispatch.run_task_operation(self.project, self.task["slug"])
        dispatch.run(self.project, self.task["slug"])
        after = S.load_task(self.project, self.task["slug"])
        self.assertEqual(after["state"], "running")
        self.assertEqual(after["attempt"], self.task["attempt"] + 1)
        self.assertEqual(after["l2_engine"], target["engine"])
        self.assertEqual(after["launch_model"], selection["model"])
        self.assertEqual(after["launch_effort"], selection["effort"])
        for key in ("worktree", "branch", "hold_merge"):
            self.assertEqual(after[key], self.task[key])
        self.assertEqual(config.project(self.project), before)
        self.assertEqual(config.machine_settings(), defaults)

    def test_stopped_task_transfers_and_completed_task_refuses(self):
        self.action("stop", generation=self.task["agent_id"])
        dispatch.run_task_operation(self.project, self.task["slug"])
        view = self.view()
        self.assertTrue(view["eligible"])
        self.assertFalse(view["preparation"])
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"]
        self.action(engine=target, observed=view["observed"])
        dispatch.run_task_operation(self.project, self.task["slug"])
        self.assertEqual(self.view()["request"]["status"], "done")
        task = S.load_task(self.project, self.task["slug"])
        task["state"] = "reported"
        S.save_task(self.project, task)
        view = self.view()
        self.assertFalse(view["eligible"])
        self.assertEqual(view["targets"], [])
        self.action(engine=target, observed=view["observed"], status=409)

    def test_unavailable_target_refuses_without_silent_fallback(self):
        with mock.patch.object(engines, "installation", return_value={"available": False, "why": "Fixture engine unavailable"}):
            view = self.view()
            self.assertFalse(view["eligible"])
            self.assertIn("unavailable", view["reason"])
            self.action(engine=next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"], observed=view["observed"], status=409)
        self.assertNotIn("daemon_request", S.load_task(self.project, self.task["slug"]))

    def test_unconfirmed_handoff_stop_uses_termination_evidence_without_mutating_lifecycle(self):
        view = self.view()
        self.action(engine=next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"], observed=view["observed"])
        request = self.view()["request"]
        self.action("handoff-now", request_id=request["id"])

        def unknown_termination(task, *, job_root):
            self.assertEqual(T.steering_view(task, [], job_root=job_root)["state"], "stopping")
            return None

        with mock.patch.object(engines, "worker_termination", side_effect=unknown_termination):
            dispatch.run_task_operation(self.project, self.task["slug"])
        after = S.load_task(self.project, self.task["slug"])
        self.assertEqual(after["daemon_request"]["status"], "refused")
        self.assertEqual(after["state"], "running")
        self.assertNotIn("stop_id", after)
        self.assertNotIn("next_engine", after)
        url = f"/api/task/{self.project}/{self.task['slug']}"
        with mock.patch.object(engines, "worker_termination", return_value=None):
            self.assertEqual(self.request(url)["steering"]["state"], "stop_unconfirmed")
        with mock.patch.object(engines, "worker_termination", return_value=True):
            steering = self.request(url)["steering"]
            self.assertEqual(steering["state"], "stopped")
            self.assertIsNone(steering["stop_id"])
        self.assertEqual(S.load_task(self.project, self.task["slug"]), after)
        for key in ("attempt", "agent_id", "session_id"):
            changed = {**after, key: "another-attempt"}
            self.assertEqual(T.steering_view(changed, [])["state"], "running")
        for status in ("done", "cancelled"):
            ended = {**after, "daemon_request": {**after["daemon_request"], "status": status}}
            self.assertEqual(T.steering_view(ended, [])["state"], "running")
        item = next(row for row in dispatch.poll(self.project) if row["task"]["slug"] == self.task["slug"])
        self.assertTrue(item["died"])
        with mock.patch.object(server.incidents, "system_fault") as fault:
            server._on_l2_finished(self.project, item)
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "l2-died")
        self.assertEqual(S.load_task(self.project, self.task["slug"])["state"], "blocked")
        with mock.patch.object(engines, "worker_termination", return_value=None):
            self.assertEqual(self.request(url)["steering"]["state"], "stop_unconfirmed")
        with mock.patch.object(engines, "worker_termination", return_value=True):
            self.assertEqual(self.request(url)["steering"]["state"], "idle")
        self.action("resume", stop_id=None)
        self.assertEqual(S.load_task(self.project, self.task["slug"])["daemon_request"]["operation"], "resume")

    def test_question_blocked_ended_owner_transfers_without_answering_question(self):
        slug = self.task["slug"]
        T.block(self.project, slug, "Which retention period?", actor="l2",
                updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, recommendation="Fourteen days.")
        view = self.view()
        target = next(item for item in view["targets"] if item["engine"] != view["current_engine"])["engine"]
        self.assertFalse(view["eligible"], "A question alone does not prove the old worker ended")
        self.action(engine=target, observed=view["observed"], status=409)
        self.engine.workers[self.task["agent_id"]].update(state="done", status="exited")
        before = S.load_task(self.project, slug)
        questions = before["questions"]
        view = self.view()
        self.assertTrue(view["eligible"])
        self.assertFalse(view["preparation"])
        self.action(engine=target, observed=view["observed"])
        dispatch.run_task_operation(self.project, slug)
        dispatch.run(self.project, slug)
        after = S.load_task(self.project, slug)
        self.assertEqual(after["attempt"], before["attempt"] + 1)
        self.assertEqual(after["l2_engine"], target)
        self.assertEqual(after["questions"], questions)
        for key in ("worktree", "branch", "hold_merge"):
            self.assertEqual(after[key], before[key])

    def test_historical_quota_record_does_not_outlive_current_resume_hold(self):
        T.block(self.project, self.task["slug"], "Usage limit reached.", actor="altd",
                updates={"usage_limit": {"kind": "weekly"}})
        with mock.patch.object(engines, "window_hold", return_value="later"):
            view = self.view()
            self.assertTrue(view["quota_limited"])
            self.assertIn("usage limit", view["resume_hold"])
        with mock.patch.object(engines, "window_hold", return_value=None):
            view = self.view()
            self.assertTrue(view["quota_limited"])
            self.assertIsNone(view["resume_hold"])

    def test_archived_task_remains_readable_after_project_removal(self):
        self.action("reject")
        dispatch.run_task_operation(self.project, self.task["slug"])
        config.remove_project(self.project)
        page = self.request(f"/api/task/{self.project}/{self.task['slug']}")
        self.assertEqual(page["state"], "rejected")
        self.assertEqual(page["transfer"]["targets"], [])
        self.assertFalse(page["transfer"]["eligible"])
