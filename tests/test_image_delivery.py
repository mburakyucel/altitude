"""Operator images remain attached through native turns, inbox checkpoints and failed resumes."""
import base64
import contextlib
import hashlib
import io
import json
import runpy
import subprocess
import sys
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, images, platform, state as S, tasks as T


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGfoAAAAASUVORK5CYII=")


class _Input(io.StringIO):
    def close(self):
        pass


class _BytesInput(io.BytesIO):
    def close(self):
        pass


class _Process:
    pid, returncode = 4242, 0

    def __init__(self, engine, output=None):
        events = ([{"type": "system", "subtype": "init", "session_id": "session"},
                   {"type": "result", "session_id": "session", "result": "Seen", "is_error": False}]
                  if engine == "claude" else
                  [{"type": "thread.started", "thread_id": "session"},
                   {"type": "turn.completed", "usage": {"input_tokens": 1}}])
        self.events = "".join(json.dumps(event) + "\n" for event in events)
        self.stdin = _BytesInput() if output else _Input()
        self.stdout, self.stderr = io.StringIO(self.events), io.StringIO()
        if output:
            output.write(self.events.encode())

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass

    def communicate(self, text, timeout=None):
        self.stdin.write(text)
        return self.events, ""


class ImageDeliveryCase(AltitudeCase):
    host = "linux"  # systemd fixtures: the engine command is read from the systemd-run argv

    def setUp(self):
        super().setUp()
        path = self.tmp / "image.png"
        path.write_bytes(PNG)
        self.image = {"id": "1" * 32, "name": "screenshot.png", "mime_type": "image/png",
                      "size": len(PNG), "width": 1, "height": 1,
                      "sha256": hashlib.sha256(PNG).hexdigest(), "source_message_id": "message-one",
                      "source_task": None, "path": str(path)}
        self.patch(engines, "image_capability", return_value={"available": True, "why": "fixture transport"})

    def assert_input(self, engine, command, payload, *, resume):
        if isinstance(payload, bytes):
            payload = payload.decode()
        if engine == "claude":
            self.assertEqual(command[command.index("--input-format") + 1], "stream-json")
            self.assertEqual("--resume" in command, resume)
            body = json.loads(payload)
            self.assertEqual((body["type"], body["message"]["role"]), ("user", "user"))
            self.assertIsNone(body["parent_tool_use_id"])
            content = body["message"]["content"]
            self.assertEqual(base64.b64decode(content[1]["source"]["data"]), PNG)
            self.assertEqual(content[1]["source"]["media_type"], "image/png")
            text = content[0]["text"]
        else:
            self.assertEqual(command[command.index("--image") + 1], self.image["path"])
            self.assertEqual(command[command.index("--image") + 2], "--json",
                             "a following flag terminates the variadic image argument before the prompt")
            self.assertEqual("resume" in command, resume)
            self.assertEqual(command[-2:] if resume else command[-1:], ["session", "-"] if resume else ["-"])
            text = payload
        self.assertIn("inspect the screenshot", text)
        self.assertIn("message-one", text)
        self.assertIn(self.image["id"], text)
        self.assertIn("attached visually in the listed order", text)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)


class TestNativeImages(ImageDeliveryCase):
    def test_project_fresh_and_resumed_turns_receive_actual_image_content(self):
        for engine in ("claude", "codex"):
            for resume in (False, True):
                process = _Process(engine)
                with self.subTest(engine=engine, resume=resume), \
                     mock.patch.object(engines, "usage_hold", return_value=None), \
                     mock.patch.object(engines.subprocess, "Popen", return_value=process) as popen:
                    common = dict(cwd=self.repo, model="original-model", resume="session" if resume else None,
                                  images=[self.image])
                    result = (engines.claude_print("inspect the screenshot", settings=self.tmp / "settings.json", **common)
                              if engine == "claude" else
                              engines.codex_exec("inspect the screenshot", sandbox_settings=[], **common))
                self.assertEqual(result["session_id"], "session")
                self.assertIn("original-model", popen.call_args.args[0])
                self.assert_input(engine, popen.call_args.args[0], process.stdin.getvalue(), resume=resume)

    def test_task_fresh_and_resumed_workers_keep_engine_model_session_and_images(self):
        for engine in ("claude", "codex"):
            for resume in (False, True):
                processes = []
                def popen(command, **kw):
                    process = _Process(engine, kw["stdout"])
                    processes.append(process)
                    return process
                persona = self.tmp / "persona.md"
                persona.write_text("Task authority")
                with self.subTest(engine=engine, resume=resume), \
                     mock.patch.object(engines, "claude_agents", return_value=[]), \
                     mock.patch.object(platform, "job_active", return_value=True), \
                     mock.patch.object(engines, "_git_dirs", return_value=[]), \
                     mock.patch.object(engines.subprocess, "Popen", side_effect=popen) as launch:
                    common = dict(cwd=self.repo, persona=persona, model="original-model", settings=self.tmp / "settings.json",
                                  extra_env={"ALTITUDE_TASK": "owner"}, job_root=self.tmp / "jobs", images=[self.image],
                                  effort="xhigh" if engine == "codex" else None)
                    result = (engines.resume_l2(engine, "owner", "session", "inspect the screenshot", **common) if resume else
                              engines.start_l2(engine, "owner", "inspect the screenshot", **common))
                self.assertEqual(result["returncode"], 0)
                self.assertEqual(result["agent"]["sessionId"], "session")
                command = launch.call_args.args[0]
                self.assertIn("original-model", command)
                if engine == "codex":
                    self.assertIn('model_reasoning_effort="xhigh"', command)
                self.assertIn("ALTITUDE_TASK=owner", command)
                self.assert_input(engine, command, processes[-1].stdin.getvalue(), resume=resume)

    def test_larger_inbox_batch_keeps_every_image_visually_inspectable(self):
        batch = [{**self.image, "id": str(index) * 32, "source_message_id": f"message-{index}"} for index in range(5)]
        for engine, reader in (("claude", "Read"), ("codex", "view_image")):
            with self.subTest(engine=engine):
                args, prompt = engines._image_input(engine, "Each caption belongs to its message.", batch)
                self.assertEqual(args, [])
                self.assertIn(f"native {reader} tool", prompt)
                for item in batch:
                    self.assertIn(item["id"], prompt)
                    self.assertIn(item["source_message_id"], prompt)
                self.assertNotIn("attached visually", prompt)

    def test_missing_or_unsupported_images_never_launch_or_stop_an_existing_worker(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable), \
                 mock.patch.object(engines, "image_capability", return_value={"available": not unavailable, "why": "Image input unavailable"}), \
                 mock.patch.object(engines.subprocess, "Popen") as launch, \
                 mock.patch.object(engines, "claude_agents") as old_workers:
                image = {**self.image, "path": str(self.tmp / "missing.png")}
                for engine in ("claude", "codex"):
                    with self.assertRaises(engines.ImageInputError):
                        engines._start_worker(engine, "owner", "caption", cwd=self.repo,
                                              job_root=self.tmp / "jobs", images=[image])
                launch.assert_not_called()
                old_workers.assert_not_called()


class TestImageCapability(AltitudeCase):
    def test_local_help_checks_both_start_and_resume_and_refreshes_after_binary_change(self):
        binary = self.tmp / "engine"
        binary.write_text("fixture executable")
        self.addCleanup(engines._image_cli_support.cache_clear)
        with mock.patch.object(engines.shutil, "which", return_value=str(binary)), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "--image", "")) as run:
            self.assertTrue(engines.image_capability("codex")["available"])
            self.assertEqual([call.args[0][1:] for call in run.call_args_list],
                             [["exec", "--help"], ["exec", "resume", "--help"]])
            self.assertTrue(engines.image_capability("codex")["available"])
            self.assertEqual(run.call_count, 2)
            binary.write_text("replacement without image support")
            run.return_value = subprocess.CompletedProcess([], 0, "--json", "")
            self.assertFalse(engines.image_capability("codex")["available"])
            self.assertEqual(run.call_count, 3)

    def test_absent_and_unsupported_transport_are_explicit(self):
        with mock.patch.object(engines.shutil, "which", return_value=None), \
             mock.patch.object(engines.subprocess, "run") as run:
            self.assertFalse(engines.image_capability("claude")["available"])
            run.assert_not_called()
        binary = self.tmp / "engine"
        binary.write_text("fixture")
        self.addCleanup(engines._image_cli_support.cache_clear)
        with mock.patch.object(engines.shutil, "which", return_value=str(binary)), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "--input-format stream-json", "")):
            self.assertTrue(engines.image_capability("claude")["available"])


class TestImageInbox(ImageDeliveryCase):
    def setUp(self):
        super().setUp()
        self.quiet_engines()
        self.private_ledgers()
        self.patch(dispatch.git_policy, "fetch_origin", return_value="a" * 40)
        self.patch(dispatch, "_task_worktree", return_value=self.repo)
        self.patch(dispatch, "_validate_task_worktree")
        self.patch(dispatch, "wip_hold", return_value=None)
        self.patch(engines, "worker_live", return_value=False)
        self.resolve = self.patch(images, "resolve", side_effect=lambda project, refs, **kw:
                                  [{**ref, "path": self.image["path"]} for ref in refs])

    def task(self, *, running=False):
        task = T.new(self.project, "Image task", "Inspect the screenshot before implementing.")
        task.update(images=[{key: value for key, value in self.image.items() if key != "path"}])
        if running:
            task.update(state="running", attempt=1, session_id="session", agent_id="old-worker",
                        worktree=str(self.repo), l2_engine="codex", launch_model="original-model")
        S.save_task(self.project, task)
        return task

    def message(self, task):
        with mock.patch.object(images, "lookup", return_value=task["images"]):
            return T.message(self.project, task["slug"], "l3", "Inspect this screenshot.",
                             image_ids=[image["id"] for image in task["images"]])

    def test_initial_assignment_reaches_the_task_launch(self):
        task = self.task()
        with mock.patch.object(engines, "start_l2", return_value={"returncode": 0, "agent": {
                "id": "worker", "sessionId": "session"}}) as launch:
            dispatch.run(self.project, task["slug"])
        self.assertEqual(launch.call_args.kwargs["images"], [self.image])
        self.resolve.assert_called_with(self.project, task["images"], task=task["slug"])
        self.assertEqual(S.load_task(self.project, task["slug"])["images"], task["images"])

    def test_image_failure_blocks_the_owning_task_without_trying_another_provider(self):
        self.register(self.project, routing=config.parse_routing("codex > claude:opus"))
        task = self.task()
        with mock.patch.object(engines, "start_l2", side_effect=engines.ImageInputError("Image input unavailable")) as launch:
            with self.assertRaises(dispatch.DispatchFailure):
                dispatch.run(self.project, task["slug"])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.args[0], "codex")
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["state"], "blocked")
        self.assertEqual(saved["images"], task["images"])
        self.assertIn("Image input unavailable", saved["blocked_reason"])

    def test_missing_resolved_image_restores_the_claim_before_any_resume_launch(self):
        task = self.task(running=True)
        original = self.message(task)
        task = S.load_task(self.project, task["slug"])
        task["state"] = "blocked"
        S.save_task(self.project, task)
        self.resolve.side_effect = images.ImageError("Image content is unavailable.", 404)
        with mock.patch.object(engines, "resume_l2") as launch:
            with self.assertRaises(dispatch.ResumeFailure):
                dispatch.resume(self.project, task["slug"])
        launch.assert_not_called()
        self.assertEqual(T.pending(self.project, task["slug"]), [original])
        self.assertEqual(S.load_task(self.project, task["slug"])["session_id"], "session")

    def test_image_model_rejection_does_not_launch_another_provider(self):
        self.register(self.project, routing=config.parse_routing("codex > claude:opus"))
        task = self.task()
        with mock.patch.object(engines, "start_l2", return_value={"returncode": 1,
                "rejection": {"scope": "model", "reason": "Model unavailable"},
                "safe_to_retry": True, "stderr": "Image model unavailable"}) as launch:
            with self.assertRaises(dispatch.DispatchFailure):
                dispatch.run(self.project, task["slug"])
        self.assertEqual([call.args[0] for call in launch.call_args_list], ["codex"])
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["state"], "blocked")
        self.assertEqual(saved["images"], task["images"])

    def test_resume_failure_restores_exact_image_message_alongside_a_late_message(self):
        task = self.task(running=True)
        original = self.message(task)
        task = S.load_task(self.project, task["slug"])
        task["state"] = "blocked"
        S.save_task(self.project, task)
        def fail(*args, **kw):
            self.assertEqual(kw["images"], [self.image])
            self.assertEqual((args[0], args[2], kw["model"]), ("codex", "session", "original-model"))
            T.message(self.project, task["slug"], "burak", "Keep the text association.")
            raise engines.ImageInputError("Image input unavailable")
        with mock.patch.object(engines, "resume_l2", side_effect=fail):
            with self.assertRaises(dispatch.ResumeFailure):
                dispatch.resume(self.project, task["slug"])
        pending = T.pending(self.project, task["slug"])
        self.assertEqual(pending[0], original)
        self.assertEqual(pending[1]["text"], "Keep the text association.")
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["session_id"], "session")
        self.assertFalse(saved.get("resume_claim"))

    def test_busy_checkpoint_does_not_consume_images_until_visual_access_is_available(self):
        task = self.task(running=True)
        row = self.message(task)
        self.assertFalse((S.task_dir(self.project, task["slug"]) / "inbox.jsonl").exists(),
                         "image-only admission is owned by the durable task status record")
        self.setenv("ALTITUDE_PROJECT", self.project)
        self.setenv("ALTITUDE_TASK", task["slug"])
        hook = config.HOOKS / "inbox.py"
        for failed in (True, False):
            output = io.StringIO()
            with mock.patch.object(engines, "image_capability", return_value={"available": not failed, "why": "Image input unavailable"}), \
                 mock.patch.object(sys, "stdin", io.StringIO('{"hook_event_name":"PostToolUse"}')), \
                 contextlib.redirect_stdout(output):
                try:
                    runpy.run_path(str(hook))
                except SystemExit as exc:
                    self.assertEqual(exc.code, 0)
            if failed:
                self.assertEqual(T.pending(self.project, task["slug"]), [row])
                self.assertEqual(output.getvalue(), "")
            else:
                self.assertTrue(output.getvalue(), (config.MONITOR_DIR / "hook-faults.log").read_text())
                delivered = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
                self.assertIn("native view_image tool", delivered)
                self.assertIn(self.image["path"], delivered)
                self.assertIn(row["id"], delivered)
                self.assertEqual(T.pending(self.project, task["slug"]), [])

    def test_a_block_during_visual_preparation_keeps_the_message_for_resume(self):
        task = self.task(running=True)
        row = self.message(task)
        self.setenv("ALTITUDE_PROJECT", self.project)
        self.setenv("ALTITUDE_TASK", task["slug"])
        prepare = engines.image_read_instructions
        def blocked(*args, **kwargs):
            text = prepare(*args, **kwargs)
            T.block(self.project, task["slug"], "A decision is needed before continuing.")
            return text
        output = io.StringIO()
        with mock.patch.object(engines, "image_read_instructions", side_effect=blocked), \
             mock.patch.object(sys, "stdin", io.StringIO('{"hook_event_name":"Stop"}')), \
             contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit):
                runpy.run_path(str(config.HOOKS / "inbox.py"))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(T.pending(self.project, task["slug"]), [row])
