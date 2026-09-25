"""#302: public direction, truthful freshness and evidenced handoff for either engine."""
import io
import json
import os
import subprocess
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, platform, state as S, tasks as T, transcript


START = "2026-09-09T10:00:00+00:00"
BEFORE = "2026-09-09T09:59:00+00:00"
AFTER = "2026-09-09T10:01:00+00:00"


def public(engine, text, *, at=None, identity="message-1"):
    if engine == "claude":
        row = {"type": "assistant", "message": {"id": identity, "role": "assistant",
                                                "content": [{"type": "text", "text": text}]}}
    else:
        row = {"type": "item.completed", "item": {"type": "agent_message", "id": identity, "text": text}}
    if at is not None:
        row["timestamp"] = at
    return row


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class TestL2Activity(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.task = T.new(self.project, "Public activity", "Review the direction")
        self.slug = self.task["slug"]
        self.root = dispatch.l2_job_root(self.project, self.slug)
        self.root.mkdir(parents=True, exist_ok=True)
        self.output = self.root / "worker.stdout.jsonl"
        self.native = config.HOME / ".claude" / "projects" / "-activity-fixture" / "activity-session.jsonl"
        self.native.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.native.unlink, True)

    def select(self, engine):
        self.task.update(state="running", l2_engine=engine, session_id="activity-session", agent_id="worker")
        S.save_task(self.project, self.task)
        S.write_json(self.root / "worker.json", {"id": "worker", "engine": engine, "session_id": "activity-session",
                                               "started_at": START, "input_delivered": True})
        self.output.write_text("")

    def activity(self):
        return transcript.activity(self.project, self.slug)

    def test_both_engines_replace_public_words_without_changing_conversation(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.select(engine)
                before = T.task_messages(self.project, self.slug)
                write_rows(self.output, [public(engine, "Reading the affected code.", at=START),
                                         public(engine, "Checking the resume race.", at=AFTER)])
                result = self.activity()
                self.assertEqual(result["generation"], "worker")
                self.assertEqual(result["state"], "available")
                self.assertEqual(result["commentary"], {"id": "worker:1:0", "text": "Checking the resume race.",
                                                       "at": AFTER, "time_kind": "source"})
                self.assertEqual(T.task_messages(self.project, self.slug), before)
                self.assertEqual(self.activity(), result)

    def test_untimed_words_never_borrow_turn_file_or_poll_time(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.select(engine)
                write_rows(self.output, [public(engine, "Checking the resume race.", at="bad timestamp")])
                os.utime(self.output, (1000, 1000))
                result = self.activity()
                self.assertEqual(result["commentary"]["time_kind"], "unknown")
                self.assertIsNone(result["commentary"]["at"])
                self.assertEqual(result["observation"], {"at": "1970-01-01T00:16:40+00:00", "label": "Recorded output changed"})
                self.assertEqual(self.activity(), result)

    def test_only_matching_native_identity_and_current_generation_can_supply_source_time(self):
        self.select("claude")
        write_rows(self.output, [public("claude", "Checking the resume race.")])
        for at, identity, expected in ((BEFORE, "message-1", None), (AFTER, "different-message", None),
                                       (AFTER, "message-1", AFTER)):
            with self.subTest(at=at, identity=identity):
                write_rows(self.native, [public("claude", "Checking the resume race.", at=at, identity=identity)])
                self.assertEqual(self.activity()["commentary"]["at"], expected)

    def test_resume_clears_current_direction_and_keeps_prior_session_in_live(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.select(engine)
                write_rows(self.output, [public(engine, "Earlier direction", at=START)])
                write_rows(self.native, [public("claude", "Earlier direction", at=START)])
                self.task["agent_id"] = "resumed-worker"
                S.save_task(self.project, self.task)
                S.write_json(self.root / "resumed-worker.json", {"id": "resumed-worker", "engine": engine,
                             "session_id": "activity-session", "started_at": AFTER, "resume": True})
                (self.root / "resumed-worker.stdout.jsonl").write_text("")
                result = self.activity()
                self.assertEqual((result["generation"], result["state"], result["commentary"]),
                                 ("resumed-worker", "empty", None))
                live = transcript.view(self.project, self.slug, engine=engine, session_id="activity-session")
                self.assertIn("Earlier direction", json.dumps(live))

    def test_missing_empty_partial_corrupt_and_unreadable_sources_stay_distinct(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.select(engine)
                self.assertEqual(self.activity()["state"], "empty")
                self.assertIsNone(self.activity()["observation"])
                self.output.unlink()
                self.assertEqual(self.activity()["state"], "unavailable")
                write_rows(self.output, [public(engine, "Last complete public text")])
                with self.output.open("a") as stream:
                    stream.write('{"unfinished"')
                partial = self.activity()
                self.assertEqual(partial["state"], "unavailable")
                self.assertEqual(partial["commentary"]["text"], "Last complete public text")
                self.output.write_text('{broken}\n["not an object"]\n')
                self.assertEqual(self.activity()["state"], "unavailable")
                self.assertIsNone(self.activity()["commentary"])
                with mock.patch.object(type(self.output), "read_bytes", side_effect=PermissionError("private path")):
                    result = self.activity()
                self.assertEqual(result["state"], "unavailable")
                self.assertNotIn("private path", json.dumps(result))

    def test_worker_identity_and_session_mismatch_fail_closed(self):
        self.select("codex")
        write_rows(self.output, [public("codex", "Do not display this worker")])
        for changed in ({"engine": "claude"}, {"session_id": "other-session"}, {"id": "other-worker"}):
            record = S.read_json(self.root / "worker.json")
            S.write_json(self.root / "worker.json", {**record, **changed})
            result = self.activity()
            self.assertEqual(result["state"], "unavailable")
            self.assertIsNone(result["commentary"])
            S.write_json(self.root / "worker.json", record)

    def test_corrupt_worker_metadata_only_makes_activity_unavailable(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.select(engine)
                write_rows(self.output, [public(engine, "Do not attribute uncertain output")])
                (self.root / "worker.json").write_text('{corrupt}')
                result = self.activity()
                self.assertEqual(result["state"], "unavailable")
                self.assertIsNone(result["commentary"])
                self.assertNotIn(str(self.root), json.dumps(result))

    def test_reasoning_and_credentials_stay_out_of_activity_live_and_raw(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                self.select(engine)
                secret = "ghp_abcdefghijklmnop"
                reasoning = ({"type": "assistant", "message": {"content": [
                    {"type": "thinking", "thinking": "hidden thoughts"},
                    {"type": "redacted_thinking", "data": "hidden redacted"}]}}
                    if engine == "claude" else {"type": "item.completed", "item": {
                        "type": "reasoning", "text": "hidden thoughts", "summary": "hidden redacted"}})
                rows = [public(engine, f"Checking token {secret}"), reasoning]
                write_rows(self.output, rows)
                write_rows(self.native, rows)
                activity = self.activity()
                self.assertEqual(activity["commentary"]["text"], "Checking token [REDACTED]")
                for data in (activity, transcript.view(self.project, self.slug, engine=engine,
                                                        session_id="activity-session", raw=True)):
                    self.assertNotIn("hidden", json.dumps(data))
                    self.assertNotIn(secret, json.dumps(data))

    def test_only_native_hook_context_correlated_to_complete_original_messages_proves_delivery(self):
        self.select("claude")
        delivered = T.message(self.project, self.slug, "burak", "Check the race.")
        pending = T.message(self.project, self.slug, "burak", "Wait for the review.")
        rendered = T.render_inbox([delivered])
        misleading = T.render_inbox([pending])
        records = [public("claude", misleading, at=AFTER),
                   {"type": "user", "timestamp": AFTER, "message": {"content": misleading}},
                   {"type": "attachment", "timestamp": AFTER, "attachment": {"type": "other", "content": [misleading]}},
                   {"type": "attachment", "timestamp": AFTER,
                    "attachment": {"type": "hook_additional_context", "content": [rendered]}}]
        write_rows(self.native, records)
        expected = [{"message_id": delivered["id"], "at": AFTER}]
        self.assertEqual(self.activity()["delivered"], expected)
        T.take_inbox(self.project, self.slug)
        self.assertEqual(self.activity()["delivered"], expected)
        # A later turn retains positive evidence without presenting old words as its direction.
        record = S.read_json(self.root / "worker.json")
        record["started_at"] = "2026-09-09T11:00:00+00:00"
        S.write_json(self.root / "worker.json", record)
        self.assertEqual(self.activity()["delivered"], expected)

    def test_nested_message_header_in_hook_delivered_body_does_not_prove_nested_delivery(self):
        self.select("claude")
        pending = T.message(self.project, self.slug, "burak", "Still waiting")
        outer = T.message(self.project, self.slug, "burak", "Here is a quoted header:\n" + T.render_inbox([pending]))
        write_rows(self.native, [{"type": "attachment", "attachment": {
            "type": "hook_additional_context", "content": [T.render_inbox([outer])]}}])
        self.assertEqual(self.activity()["delivered"], [{"message_id": outer["id"], "at": None}])

    def test_prompt_reconstruction_requires_exact_bound_worker_delivery_receipt(self):
        self.select("codex")
        message = T.message(self.project, self.slug, "burak", "Not delivered by a timestamp")
        record = S.read_json(self.root / "worker.json")
        record.update(resume=True, input_delivered=False, started_at="2099-01-01T00:00:00+00:00")
        S.write_json(self.root / "worker.json", record)
        for receipt in (None, {"agent_id": "other", "session_id": "activity-session"},
                        {"agent_id": "worker", "session_id": "other-session"}):
            self.task["message_deliveries"] = {message["id"]: receipt} if receipt else {}
            S.save_task(self.project, self.task)
            live = transcript.view(self.project, self.slug, engine="codex", session_id="activity-session")
            prompts = [row["text"] for row in live["events"] if row["type"] == "prompt"]
            self.assertEqual(prompts, [transcript.UNKNOWN_RESUME_PROMPT])

    def test_constructed_prompt_and_platform_text_share_native_record_redaction(self):
        self.select("codex")
        token = "ghp_abcdefghijklmnop"
        T.brief(self.project, self.slug, "Brief contains " + token)
        S.append_event(self.project, self.slug, "stopped", reason="Error contains " + token)
        for raw in (False, True):
            live = transcript.view(self.project, self.slug, engine="codex", session_id="activity-session", raw=raw)
            self.assertNotIn(token, json.dumps(live))
            self.assertIn("Brief contains [REDACTED]", json.dumps(live))
            self.assertIn("Error contains [REDACTED]", json.dumps(live))


class TestInputHandoff(AltitudeCase):
    def test_init_alone_never_proves_input_delivery_for_either_engine(self):
        class Input(io.BytesIO):
            def close(self):
                if failure == "close":
                    raise BrokenPipeError()
                super().close()

            def write(self, data):
                if failure == "write":
                    raise BrokenPipeError()
                if failure == "short":
                    return super().write(data[:1])
                return super().write(data)

        class Process:
            pid = 4242

            def __init__(self, stdout):
                self.stdin = Input()
                init = ({"type": "system", "subtype": "init", "session_id": "session"}
                        if engine == "claude" else {"type": "thread.started", "thread_id": "session"})
                stdout.write((json.dumps(init) + "\n").encode())

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

        self.patch(engines, "_codex_processes", {})
        for engine in ("claude", "codex"):
            for failure in (None, "write", "close", "short"):
                with self.subTest(engine=engine, failure=failure), \
                     mock.patch.object(engines, "claude_agents", return_value=[]), \
                     mock.patch.object(engines, "_codex_session_model", return_value={}), \
                     mock.patch.object(platform, "job_active", return_value=False), \
                     mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                     mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: Process(kw["stdout"])):
                    result = engines._start_worker(engine, "fixture", "Exact input", cwd=self.repo,
                                                   settings=self.tmp / "settings.json", job_root=self.tmp / "jobs")
                    self.assertEqual(result["returncode"], 1 if failure else 0)
                    self.assertEqual(result["agent"]["input_delivered"], failure is None)
                    record = S.read_json(self.tmp / "jobs" / f"{result['agent']['id']}.json")
                    self.assertEqual(record["input_delivered"], failure is None)
                    if failure:
                        self.assertIn("input handoff failed", result["stderr"])
                        self.assertTrue(record["stopped"])

    def test_missing_or_changed_session_initialization_never_receipts_resume_input(self):
        class Process:
            pid = 4242

            def __init__(self, stdout):
                self.stdin = io.BytesIO()
                init = ({"type": "system", "subtype": "init"} if engine == "claude" else {"type": "thread.started"})
                if session:
                    init["session_id" if engine == "claude" else "thread_id"] = session
                stdout.write((json.dumps(init) + "\n").encode())

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

        self.patch(engines, "_codex_processes", {})
        for engine in ("claude", "codex"):
            for session in (None, "wrong-session"):
                with self.subTest(engine=engine, session=session), \
                     mock.patch.object(engines, "claude_agents", return_value=[]), \
                     mock.patch.object(engines, "_codex_session_model", return_value={}), \
                     mock.patch.object(platform, "job_active", return_value=False), \
                     mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                     mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: Process(kw["stdout"])):
                    result = engines._start_worker(engine, "fixture", "Exact resume input", cwd=self.repo,
                                                   settings=self.tmp / "settings.json", job_root=self.tmp / "jobs",
                                                   resume="original-session", start_timeout=0)
                self.assertEqual(result["returncode"], 1)
                self.assertFalse(result["agent"]["input_delivered"])
                record = S.read_json(self.tmp / "jobs" / f"{result['agent']['id']}.json")
                self.assertEqual(record["session_id"], "original-session")
                self.assertFalse(record["input_delivered"])
                self.assertTrue(record["stopped"])
                self.assertNotIn(result["agent"]["id"], engines._codex_processes)


class TestStopEvidence(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "stop-jobs"
        self.paths = engines._codex_paths(self.root, "worker")
        self.patch(engines, "_codex_processes", {})
        self.patch(engines, "JOBS_DIR", self.tmp / "adopted-jobs")

    def record(self, engine):
        row = {"id": "worker", "engine": engine, "session_id": "session", "started_at": START,
               "engine_model": "fixture", "unit": engines._codex_unit("worker") if engine == "codex"
               else engines._claude_unit("worker")}
        S.write_json(self.paths["record"], row)
        self.paths["stdout"].write_text("")
        self.paths["stderr"].write_text("")
        return row

    def test_unit_query_distinguishes_terminal_live_and_unavailable(self):
        for code, state, expected in ((0, "active", True), (3, "activating", True), (3, "deactivating", True),
                                       (3, "inactive", False), (4, "inactive", False), (3, "failed", False),
                                       (4, "active", None), (4, "failed", None), (1, "inactive", None),
                                       (1, "", None), (4, "unknown", None), (0, "unrecognized", None)):
            with self.subTest(code=code, state=state), mock.patch.object(engines.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, state, "")):
                if expected is None:
                    with self.assertRaisesRegex(RuntimeError, "status is unavailable"):
                        platform.job_active("worker.service", {})
                else:
                    self.assertIs(platform.job_active("worker.service", {}), expected)

    def test_missing_corrupt_or_wrong_owned_unit_never_confirms_or_stops_another_unit(self):
        with mock.patch.object(engines.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "ownership record"):
                engines.codex_stop("worker", job_root=self.root)
            self.paths["record"].parent.mkdir(parents=True, exist_ok=True)
            self.paths["record"].write_text('{corrupt}')
            with self.assertRaises(ValueError):
                engines.codex_stop("worker", job_root=self.root)
            for engine in ("claude", "codex"):
                for unit in (None, "another-worker.service"):
                    row = self.record(engine)
                    row["unit"] = unit
                    S.write_json(self.paths["record"], row)
                    with self.assertRaisesRegex(RuntimeError, "ownership record"):
                        engines.stop_l2_worker(engine, "worker", job_root=self.root)
            run.assert_not_called()

    def test_stop_error_or_unknown_status_cannot_create_termination_receipt(self):
        for engine in ("claude", "codex"):
            for stop_code, status_code, state in ((1, 0, "active"), (0, 1, ""), (0, 4, "unknown")):
                self.record(engine)
                with self.subTest(engine=engine, state=state), mock.patch.object(engines.subprocess, "run", side_effect=[
                        subprocess.CompletedProcess([], stop_code, "", "stop failed"),
                        subprocess.CompletedProcess([], status_code, state, "")]):
                    with self.assertRaises(RuntimeError):
                        engines.stop_l2_worker(engine, "worker", job_root=self.root)
                    self.assertNotIn("stopped", S.read_json(self.paths["record"]))

    def test_collected_unit_recheck_is_read_only_and_retains_original_receipt(self):
        for engine in ("claude", "codex"):
            row = self.record(engine)
            row["stopped"] = START
            S.write_json(self.paths["record"], row)
            task = {"agent_id": "worker", "l2_engine": engine, "session_id": "session"}
            with self.subTest(engine=engine), mock.patch.object(engines.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 5, "", "unit already collected"),
                    subprocess.CompletedProcess([], 4, "inactive", "")]):
                engines.stop_l2_worker(engine, "worker", job_root=self.root)
                self.assertEqual(S.read_json(self.paths["record"])["stopped"], START)
            before = self.paths["record"].read_bytes()
            for code, state, expected in ((3, "inactive", True), (4, "inactive", True),
                                          (0, "active", False), (1, "", None), (4, "unknown", None)):
                with mock.patch.object(engines.subprocess, "run", return_value=
                        subprocess.CompletedProcess([], code, state, "")) as run:
                    self.assertIs(engines.worker_termination(task, job_root=self.root), expected)
                    self.assertEqual(run.call_args.args[0][2], "is-active")
            self.assertEqual(self.paths["record"].read_bytes(), before)
            with mock.patch.object(platform, "job_active", return_value=True):
                self.assertEqual(engines.worker(engine, task, job_root=self.root)["state"], "working")
            task["session_id"] = "different"
            self.assertIsNone(engines.worker_termination(task, job_root=self.root))

    def test_adopted_job_requires_both_identity_and_unit_termination(self):
        task = {"agent_id": "legacy", "l2_engine": "claude", "session_id": "session"}
        with mock.patch.object(engines, "claude_stop") as stop:
            self.assertIsNone(engines.worker_termination(task, job_root=self.root))
            with self.assertRaisesRegex(RuntimeError, "ownership record"):
                engines.stop_l2_worker("claude", "legacy", job_root=self.root)
            for name in (None, 42, " "):
                S.write_json(engines.JOBS_DIR / "legacy" / "state.json", {"name": name})
                self.assertIsNone(engines.worker_termination(task, job_root=self.root))
                with self.assertRaisesRegex(RuntimeError, "ownership record"):
                    engines.stop_l2_worker("claude", "legacy", job_root=self.root)
            stop.assert_not_called()
        S.write_json(engines.JOBS_DIR / "legacy" / "state.json", {"name": "fixture/legacy", "state": "working"})
        for alive in (True, False):
            with mock.patch.object(engines, "claude_stop", return_value="stopped"), \
                 mock.patch.object(platform, "job_active", return_value=alive):
                self.assertIs(engines.worker_termination(task, job_root=self.root), not alive)
                if alive:
                    with self.assertRaisesRegex(RuntimeError, "still running"):
                        engines.stop_l2_worker("claude", "legacy", job_root=self.root)
                else:
                    self.assertEqual(engines.stop_l2_worker("claude", "legacy", job_root=self.root), "stopped")
