"""Operator image admission through real HTTP/storage, queue delivery, task handoff and recovery."""
import hashlib
import http.client
import json
import threading
import uuid
from pathlib import Path
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from tests.support import AltitudeCase
from tests.test_images import upload
from altitude import config, dispatch, engines, images, l3, server, state as S, tasks as T, verify


class TestImageConversations(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.engine = config.ENGINES[0]
        self.patch(l3, "_select", return_value={"engine": self.engine, "why": "image fixture"})
        self.patch(engines, "image_capability", return_value={"available": True, "why": "fixture"})
        self.drain = server.request_l3_drain
        self.patch(server, "request_l3_drain", return_value=True)
        self.patch(server, "request_task_resume", return_value=True)
        self.task = T.new(self.project, "Image owner", "Inspect the attached screen.", hold_merge="Operator review")
        self.slug = self.task["slug"]
        self.task.update(state="running", attempt=1, session_id="native-session", agent_id="fixture",
                         l2_engine=self.engine)
        S.save_task(self.project, self.task)
        self.calls = []

        def answer(prompt, **kw):
            self.calls.append((prompt, kw))
            for image in kw.get("images", []):
                self.assertEqual(hashlib.sha256(Path(image["path"]).read_bytes()).hexdigest(), image["sha256"])
            return {"text": "Image inspected.", "session_id": "l3-fixture", "reported_session_id": "l3-fixture"}

        self.patch(engines, "claude_print", side_effect=answer)
        self.patch(engines, "codex_exec", side_effect=answer)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, body=None, *, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("POST" if body is not None else "GET", path,
                           json.dumps(body) if body is not None else None,
                           {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        raw = response.read()
        data = json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw
        return response.status, data, dict(response.getheaders())

    def body(self, **fields):
        return {"project": self.project, "text": "Inspect this image.", "images": [upload()],
                "request_id": str(uuid.uuid4()), **fields}

    def test_task_atomic_admission_retry_delivery_and_archive(self):
        body = self.body(slug=self.slug, text="")
        status, first, _ = self.request("/api/l2/message", body)
        self.assertEqual(status, 200, first)
        message = {key: value for key, value in first["message"].items() if key != "delivery"}
        self.assertEqual(T.pending(self.project, self.slug), [message])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 1)
        with mock.patch.object(images, "store", side_effect=AssertionError("retry must reuse files")):
            self.assertEqual(self.request("/api/l2/message", body)[1], first)
        self.assertEqual(T.take_inbox(self.project, self.slug, running_only=True), [message])
        self.assertEqual(self.request("/api/l2/message", body)[1], first)
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 1)
        task = S.load_task(self.project, self.slug)
        task.update(state="done", agent_id=None)
        S.save_task(self.project, task)
        T._archive(self.project, self.slug)
        self.assertEqual(self.request("/api/l2/message", body)[1], first)
        image = message["images"][0]
        status, raw, headers = self.request(f"/api/images/{self.project}/{image['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), image["sha256"])
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_removed_task_image_stays_out_of_checkpoint_and_resume_batches(self):
        body = self.body(slug=self.slug)
        _, saved, _ = self.request("/api/l2/message", body)
        removed = saved["message"]["id"]
        kept = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Keep this correction.")
        status, _, _ = self.request("/api/l2/remove", {"project": self.project, "slug": self.slug, "id": removed})
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)], [kept["id"]])
        self.assertEqual(self.request("/api/l2/message", body)[0], 200)
        self.assertEqual([row["id"] for row in T.take_inbox(self.project, self.slug)], [kept["id"]])
        self.assertEqual(T.pending(self.project, self.slug), [])
        task = S.load_task(self.project, self.slug)
        task.update(state="blocked", blocked_reason="Wait", block_id="blocked")
        S.save_task(self.project, task)
        next_row = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Resume here.")
        claim = T.claim_resume(self.project, self.slug)
        self.assertEqual([row["id"] for row in claim["messages"]], [next_row["id"]])
        self.assertEqual(T.message_views(self.project, self.slug, [])[0]["delivery"]["state"], "removed")

    def test_image_queue_admission_is_visible_before_provider_execution(self):
        body = self.body()
        self.request("/api/chat", body)
        original = l3.turn
        def observe(*args, **kwargs):
            snapshot = l3.chat_state(self.project)
            self.assertEqual(snapshot["queued"], [])
            self.assertEqual(snapshot["history"][-1]["text"], body["text"])
            self.assertEqual(snapshot["history"][-1]["turn_id"], snapshot["active"]["id"])
            self.assertEqual(len(snapshot["history"][-1]["images"]), 1)
            return original(*args, **kwargs)
        with mock.patch.object(l3, "turn", side_effect=observe):
            self.assertTrue(l3.deliver_queued(self.project)["completed"])
        self.assertEqual(len([row for row in l3.chat_history(self.project, None) if row["role"] == "user"]), 1)

    def test_finished_image_claim_never_returns_to_waiting_queue(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                body = self.body()
                _, receipt, _ = self.request("/api/chat", body)
                message = receipt["queued"]
                finish = l3._finish_image_queue
                observed = []

                def observe(project):
                    if any(row.get("image_turn_id") for row in l3._queue_rows(l3.queue_path(project))):
                        observed.append(project)
                        self.assertIsNone(l3.active(project))
                        status, snapshot, _ = self.request(f"/api/chat/{project}")
                        self.assertEqual(status, 200)
                        saved = [row for row in snapshot["history"] if row.get("request_id") == message["id"]]
                        self.assertEqual(len(saved), 1)
                        self.assertEqual(saved[0]["images"], message["images"])
                        self.assertEqual(snapshot["queued"], [])
                        self.assertEqual(l3.queued(project), [])
                        self.assertFalse(l3.drop_queued(project, message["id"]))
                        # Retained claims still own the immutable receipt until finalization.
                        self.assertEqual(self.request("/api/chat", body)[1]["queued"]["id"], message["id"])
                        self.assertEqual(self.request("/api/chat", {**body, "text": "Changed"})[0], 409)
                    return finish(project)

                try:
                    with ExitStack() as stack:
                        stack.enter_context(mock.patch.object(l3, "_finish_image_queue", side_effect=observe))
                        if failed:
                            stack.enter_context(mock.patch.object(l3, "turn", side_effect=RuntimeError("Fixture turn failed")))
                        result = l3.deliver_queued(self.project)
                    self.assertEqual(observed, [self.project])
                    self.assertEqual(result["completed"], not failed)
                    self.assertEqual(l3._queue_rows(l3.queue_path(self.project)), [])
                    self.assertEqual(l3.chat_history(self.project, None)[-1]["role"], "error" if failed else "assistant")
                finally:
                    finish(self.project)

    def test_stopped_task_image_correction_keeps_stop_identity_and_saved_receipt(self):
        task = S.load_task(self.project, self.slug)
        task.update(state="blocked", stop_id="stopped-owner")
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "stopped", stop_id="stopped-owner")
        stale = self.body(slug=self.slug)
        self.assertEqual(self.request("/api/l2/message", stale)[0], 200)
        self.assertFalse(S.load_task(self.project, self.slug).get("resume_after"))
        self.assertEqual(T.take_inbox(self.project, self.slug, running_only=True), [])
        correction = self.body(slug=self.slug, stop_id="stopped-owner")
        with mock.patch.object(server, "request_task_resume", side_effect=OSError("wake unavailable")), \
             mock.patch.object(server, "log"):
            status, receipt, _ = self.request("/api/l2/message", correction)
        self.assertEqual(status, 200)
        self.assertTrue(S.load_task(self.project, self.slug).get("resume_after"))
        task = S.load_task(self.project, self.slug)
        task.pop("stop_id")
        S.save_task(self.project, task)
        self.assertEqual(self.request("/api/l2/message", correction)[1], receipt)

    def test_reported_owner_continues_only_after_image_validation(self):
        task = S.load_task(self.project, self.slug)
        task.update(state="reported", worktree=str(self.repo), prs=[47], verified={"verdict": "ok"})
        S.save_task(self.project, task)
        report = {"landed": {"prs": [{"number": 47, "merged": False}]}}
        S.write_json(S.task_dir(self.project, self.slug) / "report.json", report)
        with mock.patch.object(verify, "gh", return_value={"state": "OPEN"}):
            status, _, _ = self.request("/api/l2/message", self.body(slug=self.slug, images=[upload(b"invalid")]))
            self.assertEqual(status, 415)
            self.assertEqual(S.load_task(self.project, self.slug), task)
            self.assertEqual(T.task_messages(self.project, self.slug), [])
            status, receipt, _ = self.request("/api/l2/message", self.body(slug=self.slug))
        self.assertEqual(status, 200)
        saved = S.load_task(self.project, self.slug)
        self.assertEqual(saved["state"], "blocked")
        self.assertEqual(saved["resume_request"], receipt["message"]["id"])
        self.assertNotIn("verified", saved)
        self.assertEqual(saved["hold_merge"], "Operator review")
        self.assertEqual(saved["session_id"], task["session_id"])
        self.assertEqual(len(T.pending(self.project, self.slug)[0]["images"]), 1)
        self.assertEqual(S.read_json(S.task_dir(self.project, self.slug) / "report.json"), report)

    def test_project_queue_caption_pairing_native_delivery_and_lost_receipt_retry(self):
        first, second = self.body(text="First image"), self.body(text="Second image")
        for body in (first, second):
            self.assertEqual(self.request("/api/chat", body)[0], 200)
        l3.queue_message(self.project, "Text after images", trigger="chat", role=T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(len(l3.queued(self.project)), 3)
        for text in ("First image", "Second image", "Text after images"):
            result = l3.deliver_queued(self.project)
            self.assertTrue(result.get("completed"), result)
            self.assertIn(text, self.calls[-1][0])
        self.assertEqual([len(options.get("images", [])) for _, options in self.calls], [1, 1, 0])
        self.assertEqual(l3.queued(self.project), [])
        status, receipt, _ = self.request("/api/chat", first)
        self.assertEqual(status, 200)
        self.assertEqual(receipt["queued"]["request_id"], uuid.UUID(first["request_id"]).hex)
        self.assertEqual(l3.queued(self.project), [])
        humans = [row for row in l3.chat_history(self.project, None) if row["role"] == "user"]
        self.assertEqual([row["text"] for row in humans], ["First image", "Second image", "Text after images"])
        self.assertNotEqual(humans[0]["images"][0]["id"], humans[1]["images"][0]["id"])

    def test_saved_claim_after_interruption_fails_visibly_without_provider_replay(self):
        body = self.body()
        self.request("/api/chat", body)
        with S.project_lock(self.project):
            rows = l3._queue_rows(l3.queue_path(self.project))
            rows[0]["image_turn_id"] = "interrupted-turn"
            l3._write_queue(l3.queue_path(self.project), rows)
        self.assertIsNone(l3.deliver_queued(self.project))
        self.assertEqual(self.calls, [])
        history = l3.chat_history(self.project, None)
        self.assertEqual([row["role"] for row in history], ["user", "error"])
        self.assertIn("interrupted", history[-1]["text"])
        self.assertEqual(self.request("/api/chat", body)[0], 200)
        self.assertEqual(l3.queued(self.project), [])

    def test_cancelled_queued_message_is_not_resurrected_by_retry(self):
        body = self.body()
        _, saved, _ = self.request("/api/chat", body)
        message = saved["queued"]
        self.assertTrue(l3.drop_queued(self.project, message["id"]))
        status, error, _ = self.request("/api/chat", body)
        self.assertEqual(status, 409)
        self.assertIn("removed", error["error"])
        with self.assertRaises(images.ImageError):
            images.lookup(self.project, [message["images"][0]["id"]])

    def test_native_error_with_partial_reply_keeps_saved_image_retry(self):
        self.request("/api/chat", self.body())
        with mock.patch.object(engines, "claude_print", return_value={
                "session_id": "partial-session", "text": "Started inspecting.", "error": "Delivery interrupted."}):
            result = l3.deliver_queued(self.project)
        self.assertFalse(result["completed"])
        history = l3.chat_history(self.project, None)
        self.assertEqual(history[-1]["role"], "error")
        self.assertEqual(history[-1]["text"], "Delivery interrupted.")
        self.assertTrue(history[0]["images"])
        self.assertEqual(l3.queued(self.project), [])

    def test_l3_handoff_preserves_source_authority_and_claim_restoration(self):
        _, response, _ = self.request("/api/chat", self.body())
        ref = response["queued"]["images"][0]
        new = T.new(self.project, "Image handoff", "Fix what the screenshot shows.", image_ids=[ref["id"]],
                    paths=["web/src/"], hold_merge="Operator review")
        self.assertEqual(new["images"], [ref])
        row = T.message(self.project, self.slug, "l3", "Use this screenshot.", image_ids=[ref["id"]])
        self.assertEqual(row["role"], "l3")
        self.assertEqual(row["images"][0]["source_message_id"], ref["source_message_id"])
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], "Operator review")
        task = S.load_task(self.project, self.slug)
        task.update(state="blocked", blocked_reason="Existing question", block_id="same-block")
        S.save_task(self.project, task)
        claim = T.claim_resume(self.project, self.slug)
        self.assertEqual(claim["messages"], [row])
        self.assertEqual(T.pending(self.project, self.slug), [])
        late = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Keep the original caption.")
        self.assertTrue(T.release_resume_claim(self.project, self.slug, claim["id"], consume_request=False))
        self.assertEqual([item["id"] for item in T.pending(self.project, self.slug)], [row["id"], late["id"]])
        self.assertEqual(images.resolve(self.project, [ref], task=new["slug"])[0]["id"], ref["id"])

    def test_foreign_refs_unknown_images_caps_and_invalid_envelopes_refuse(self):
        _, saved, _ = self.request("/api/chat", self.body())
        ref = saved["queued"]["images"][0]
        self.register("other-image-project")
        self.assertEqual(self.request(f"/api/images/other-image-project/{ref['id']}")[0], 404)
        with self.assertRaises(images.ImageError):
            T.new("other-image-project", "Foreign image", "Cannot attach.", image_ids=[ref["id"]])
        self.assertEqual(self.request(f"/api/images/{self.project}/{ref['id']}", headers={"Origin": "https://elsewhere.invalid"})[0], 403)
        self.assertEqual(self.request(f"/api/images/{self.project}/..%2Fstatus.json")[0], 404)
        self.assertEqual(self.request("/api/chat", self.body(request_id="bad"))[0], 400)
        self.assertEqual(self.request("/api/chat", self.body(images=[upload(b"<svg/>")]))[0], 415)
        with mock.patch.object(images, "capability", return_value={"available": False, "reason": "Image input unavailable."}):
            self.assertEqual(self.request("/api/chat", self.body())[0], 422)
            status, cap, _ = self.request(f"/api/images/{self.project}?task={self.slug}")
            self.assertEqual(status, 200)
            self.assertFalse(cap["available"])
        with mock.patch.object(images, "MAX_BODY", 100):
            self.assertEqual(self.request("/api/chat", self.body())[0], 413)

    def test_request_identity_cannot_replace_saved_images_or_text(self):
        for path, extra in (("/api/chat", {}), ("/api/l2/message", {"slug": self.slug})):
            body = self.body(**extra)
            self.assertEqual(self.request(path, body)[0], 200)
            self.assertEqual(self.request(path, {**body, "text": "Changed after admission"})[0], 409)

    def test_concurrent_admission_retries_create_one_message_and_file(self):
        for path, extra in (("/api/chat", {}), ("/api/l2/message", {"slug": self.slug})):
            body = self.body(**extra)
            with ThreadPoolExecutor(max_workers=3) as pool:
                responses = list(pool.map(lambda _: self.request(path, body), range(3)))
            self.assertEqual([row[0] for row in responses], [200, 200, 200])
            rows = [row[1].get("message") or row[1]["queued"] for row in responses]
            self.assertEqual(len({row["id"] for row in rows}), 1)
            self.assertEqual(len({row["images"][0]["id"] for row in rows}), 1)
        self.assertEqual(len(l3.queued(self.project)), 1)
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 1)

    def test_committed_reference_selection_keeps_message_limits(self):
        _, saved, _ = self.request("/api/chat", self.body())
        image = saved["queued"]["images"][0]
        for path, extra in (("/api/chat", {}), ("/api/l2/message", {"slug": self.slug})):
            self.assertEqual(self.request(path, self.body(images=None, image_ids=[image["id"]] * 5, **extra))[0], 422)
            with mock.patch.object(images, "MAX_TOTAL_BYTES", image["size"] - 1):
                self.assertEqual(self.request(path, self.body(images=None, image_ids=[image["id"]], **extra))[0], 413)
        with self.assertRaises(images.ImageError):
            T.new(self.project, "Too many", "Keep image bounds.", image_ids=[image["id"]] * 5)

    def test_coordinator_cli_relay_uses_real_role_fence_and_same_project_ids(self):
        _, saved, _ = self.request("/api/chat", self.body())
        ref = saved["queued"]["images"][0]
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": [
            "task", "new", "--title", "Visual handoff", "--hold-merge", "Operator review",
            "--paths", "web/src/", "--image", ref["id"], "-"], "stdin": "Use the discussed screenshot."})
        self.assertEqual(result["returncode"], 0, result["stderr"])
        task = S.load_task(self.project, "visual-handoff")
        self.assertEqual(task["images"], [ref])
        self.assertEqual(task["hold_merge"], "Operator review")
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": [
            "task", "message", self.slug, "Use the discussed image.", "--image", ref["id"]]})
        self.assertEqual(result["returncode"], 0, result["stderr"])
        message = T.task_messages(self.project, self.slug)[-1]
        self.assertEqual((message["role"], message["images"]), ("l3", [ref]))
        denied = self.alt("--project", self.project, "task", "new", "--title", "Unassigned relay",
                          "--image", ref["id"], "No task creation authority.",
                          env={"ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"})
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("l2", denied.stderr.lower())
        self.assertFalse(S.task_dir(self.project, "unassigned-relay").exists())

    def test_unexpected_storage_errors_are_private_and_accepted_retry_is_safe(self):
        body = self.body()
        with mock.patch.object(images, "store", side_effect=OSError("/private/runtime/image-staging")), \
             mock.patch.object(server, "log"):
            status, error, _ = self.request("/api/chat", body)
        self.assertEqual(status, 500)
        self.assertNotIn("/private", error["error"])
        self.assertEqual(l3.queued(self.project), [])
        with mock.patch.object(server, "request_l3_drain", wraps=self.drain), \
             mock.patch.object(server, "spawn", side_effect=OSError("/private/runtime/lock")), \
             mock.patch.object(server, "log"):
            status, receipt, _ = self.request("/api/chat", body)
            self.assertEqual(status, 200)
            self.assertTrue(receipt["accepted"])
        self.assertEqual(self.request("/api/chat", body)[0], 200)
        self.assertEqual(len(l3.queued(self.project)), 1)
        image = l3.queued(self.project)[0]["images"][0]
        with mock.patch.object(images, "read", side_effect=OSError("/private/runtime/canonical")), \
             mock.patch.object(server, "log"):
            status, error, _ = self.request(f"/api/images/{self.project}/{image['id']}")
        self.assertEqual(status, 500)
        self.assertNotIn("/private", error["error"])

    def test_orphan_cleanup_failure_does_not_stop_project_maintenance(self):
        with ExitStack() as patches:
            patches.enter_context(mock.patch.object(images, "collect", side_effect=images.ImageError("Reference ledger unavailable.", 503)))
            patches.enter_context(mock.patch.object(dispatch, "self_deploy_fast_forward"))
            settings = patches.enter_context(mock.patch.object(dispatch, "run_settings"))
            patches.enter_context(mock.patch.object(dispatch, "pending_task_operations", return_value=[]))
            poll = patches.enter_context(mock.patch.object(dispatch, "poll", return_value=[]))
            patches.enter_context(mock.patch.object(dispatch, "resume_due", return_value=[]))
            patches.enter_context(mock.patch.object(server, "resume_stranded_reports"))
            waiting = patches.enter_context(mock.patch.object(server, "dispatch_waiting"))
            patches.enter_context(mock.patch.object(server, "log"))
            server.tick_project(self.project)
        settings.assert_called_once_with(self.project)
        poll.assert_called_once_with(self.project)
        waiting.assert_called_once_with(self.project)

    def test_missing_image_fails_delivery_without_text_only_success(self):
        _, saved, _ = self.request("/api/chat", self.body())
        ref = saved["queued"]["images"][0]
        Path(images.resolve(self.project, [ref])[0]["path"]).unlink()
        result = l3.deliver_queued(self.project)
        self.assertFalse(result["completed"])
        self.assertEqual(self.calls, [])
        history = l3.chat_history(self.project, None)
        self.assertEqual(history[0]["images"], [ref])
        self.assertEqual(history[-1]["role"], "error")
        self.assertEqual(self.request(f"/api/images/{self.project}/{ref['id']}")[0], 404)
