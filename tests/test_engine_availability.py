"""Auto fallback needs actual provider rejection and proof that replay cannot duplicate work."""
import io
import json
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, platform, state as S


MODEL_ERROR = "There's an issue with the selected model (fable). It may not exist or you may not have access to it."
REVOKED = "Failed to authenticate: OAuth token revoked. Please log in again or contact your administrator."


def revoked_stream(text=REVOKED):
    """The CLI's own shape for a lapsed sign-in: its message is marked with an authentication code and a 401."""
    return stream({"type": "system", "subtype": "init", "session_id": "session"},
                  {"type": "assistant", "error": "authentication_failed", "is_api_error_message": True,
                   "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}},
                  {"type": "result", "is_error": True, "api_error_status": 401, "terminal_reason": "api_error",
                   "result": text})


def stream(*events):
    return "".join(json.dumps(event) + "\n" for event in events)


class TestAvailability(AltitudeCase):
    def test_installation_does_not_claim_authentication_or_entitlement(self):
        for engine, binary in (("claude", config.CLAUDE_BIN), ("codex", config.CODEX_BIN)):
            with self.subTest(engine=engine), \
                    mock.patch.object(engines.common, "clean_env", return_value={"PATH": "/fixture/tools"}), \
                    mock.patch.object(engines.common.shutil, "which", return_value=None) as which:
                self.assertIs(engines.installation(engine)["available"], False)
                which.assert_called_once_with(binary, path="/fixture/tools")
            with mock.patch.object(engines.common.shutil, "which", return_value="/installed/cli"):
                self.assertIsNone(engines.installation(engine)["available"])

    def test_verified_model_rejections_are_model_scoped_and_sanitized(self):
        for engine, result in (
            ("claude", {"error": MODEL_ERROR}),
            ("claude", {"raw_stdout": stream({"type": "assistant", "error": "model_not_found",
                 "message": {"model": "<synthetic>", "content": [{"type": "text", "text": MODEL_ERROR}]}})}),
            ("codex", {"raw_stdout": stream({"type": "turn.failed", "error": {
                "code": "model_not_found", "message": "private account detail secret-value"}})}),
        ):
            with self.subTest(engine=engine, result=result):
                rejected = engines.rejection(engine, result, "fable")
                self.assertEqual(rejected["scope"], "model")
                self.assertNotIn("secret-value", rejected["why"])

    def test_auth_rejections_are_engine_scoped(self):
        for engine, error in (("claude", "Not logged in · Please run /login"),
                              ("claude", {"type": "authentication_error", "message": "invalid x-api-key"}),
                              ("codex", "Your access token could not be refreshed. Please log out and sign in again."),
                              ("codex", "This account is not currently authorized to use Codex in this workspace.")):
            with self.subTest(engine=engine, error=error):
                self.assertEqual(engines.rejection(engine, {"error": error})["scope"], "engine")

    def test_a_lapsed_sign_in_is_recognized_by_its_code_or_401_whatever_the_wording(self):
        future = "Your session token secret-value is no longer accepted."
        for engine, result in (
            ("claude", {"raw_stdout": revoked_stream()}),
            ("claude", {"raw_stdout": revoked_stream(future)}),
            ("claude", {"raw_stdout": stream({"type": "result", "is_error": True, "api_error_status": 401,
                                              "result": future})}),
            ("claude", {"error": REVOKED}),
            ("claude", {"error": "Failed to authenticate: OAuth session expired and could not be refreshed"}),
            ("codex", {"raw_stdout": stream({"type": "turn.failed", "error": {
                "message": "unexpected status 401 Unauthorized: token secret-value revoked"}})}),
            ("codex", {"error": "Your access token could not be refreshed because your refresh token was revoked. "
                                "Please log out and sign in again."}),
            ("codex", {"error": {"code": "authentication_failed", "message": future}}),
        ):
            with self.subTest(engine=engine, result=result):
                rejected = engines.rejection(engine, result)
                self.assertEqual((rejected["scope"], rejected["sign_in"]), ("engine", True))
                self.assertNotIn("secret-value", json.dumps(rejected))
        for engine in ("claude", "codex"):
            self.assertIsNone(engines.rejection(engine, {"raw_stdout": stream(
                {"type": "result", "is_error": True, "api_error_status": 403, "result": "Forbidden"})}))
        # A tool or MCP server's own authentication diagnostic is not the engine's sign-in.
        for engine, result in (
            ("claude", {"raw_stderr": "Failed to authenticate with MCP server fixture-docs", "raw_stdout": stream(
                {"type": "result", "is_error": False, "result": "Answered"})}),
            ("claude", {"error": "claude exit 1: MCP server fixture-docs: Failed to authenticate"}),
            ("codex", {"raw_stderr": "mcp client fixture-docs: unexpected status 401 Unauthorized"}),
        ):
            with self.subTest(engine=engine, result=result):
                self.assertIsNone(engines.rejection(engine, result))

    def test_a_coordinator_turn_failing_on_a_revoked_sign_in_is_a_safe_sign_in_rejection(self):
        process = mock.Mock(pid=123, returncode=1, stdout=io.StringIO(revoked_stream()), stderr=io.StringIO(""),
                            stdin=io.StringIO())
        with mock.patch.object(engines.common.subprocess, "Popen", return_value=process), \
             mock.patch.object(engines.common, "usage_hold", return_value=None):
            result = engines.claude_print("hello", unit="altitude-coordinator-fixture.service", home=self.tmp / "claude-home", cwd=self.repo)
        self.assertTrue(result["rejection"]["sign_in"])
        self.assertTrue(result["safe_to_retry"])
        self.assertEqual(result["text"], "")

    def test_a_task_owner_ending_on_a_revoked_sign_in_has_the_same_rejection(self):
        paths = engines.common._worker_paths(self.tmp, "claude")
        S.write_json(paths["record"], {"engine": "claude", "unit": "owned.service", "started_at": S.now(),
                                       "launch_model": "opus"})
        paths["stderr"].write_text("")
        paths["stdout"].write_text(revoked_stream())
        with mock.patch.object(platform, "job_active", return_value=False), \
             mock.patch.object(engines.codex, "_codex_session_model", return_value={}):
            row = engines.worker("claude", {"agent_id": "claude"}, job_root=self.tmp)
        self.assertTrue(row["rejection"]["sign_in"])
        self.assertIn("sign in, then Retry or Resume", row["rejection"]["why"])

    def test_unknown_errors_capacity_quota_and_plan_names_do_not_prove_unavailability(self):
        for error in (engines.TEMPORARY_CAPACITY_TEXT, "API Error: 529 overloaded_error", "connection refused",
                      "You've hit your session limit", "API Error: 403 permission denied", "API Error: 404 not found",
                      "Plan: Free", "model item type is unsupported", "permission denied while opening a file",
                      "MCP OAuth provider refresh timed out"):
            for engine in ("claude", "codex"):
                with self.subTest(engine=engine, error=error):
                    self.assertIsNone(engines.rejection(engine, {"error": error}, "fable"))
        self.assertIsNone(engines.rejection("claude", {"text": MODEL_ERROR}))
        self.assertIsNone(engines.rejection("codex", {"raw_stdout": stream({"type": "item.completed",
            "item": {"type": "agent_message", "text": '{"code":"model_not_found"}'}})}))

    def test_any_assistant_or_tool_activity_prevents_replay_even_with_a_later_rejection(self):
        events = [
            ("claude", {"type": "assistant", "message": {"model": "fable", "content": []}}),
            ("claude", {"type": "stream_event", "event": {"type": "content_block_delta"}}),
            ("claude", {"type": "assistant", "message": {"model": "<synthetic>",
                "content": [{"type": "tool_use", "name": "Bash"}]}}),
            *[("codex", {"type": "item.started", "item": {"type": typ}}) for typ in
              ("agent_message", "command_execution", "mcp_tool_call", "file_change", "web_search", "reasoning")],
        ]
        for engine, event in events:
            with self.subTest(event=event):
                self.assertFalse(engines.common._safe_output(engine, stream(event, {"type": "turn.failed"})))
        self.assertFalse(engines.common._safe_output("codex", '{"type":"item.started"'))
        self.assertFalse(engines.common._safe_output("codex", '{"type":"unknown"}\n'))


class TestFailureEvidence(AltitudeCase):
    def test_fable_limit_result_without_stderr_is_scoped_and_has_no_reset(self):
        stdout = stream({"type": "result", "is_error": True,
                         "result": "You've reached your Fable limit. Switch to another model."})
        process = mock.Mock(pid=123, returncode=1, stdout=io.StringIO(stdout), stderr=io.StringIO(""),
                            stdin=io.StringIO())
        self.private_ledgers()
        with mock.patch.object(engines.common.subprocess, "Popen", return_value=process), \
             mock.patch.object(engines.common, "usage_hold", return_value=None):
            result = engines.claude_print("hello", unit="altitude-coordinator-fixture.service", home=self.tmp / "claude-home", cwd=self.repo, model="fable")
        self.assertEqual((result["limited"]["scope"], result["limited"]["model"], result["limited"]["until"]),
                         ("model", "fable", None))
        self.assertFalse(engines.usage_limit_path().exists())

    def test_codex_sync_uses_structured_failure_with_empty_stderr(self):
        stdout = stream({"type": "thread.started", "thread_id": "session"},
                        {"type": "turn.failed", "error": {"code": "model_not_found", "message": "missing"}})
        process = mock.Mock(pid=123, returncode=1, stdin=io.StringIO())
        process.communicate.return_value = (stdout, "")
        with mock.patch.object(engines.common.subprocess, "Popen", return_value=process), \
             mock.patch.object(engines.codex, "_codex_session_model", return_value={}):
            result = engines.codex_exec("hello", unit="altitude-coordinator-fixture.service", sandbox_settings=[], cwd=self.repo, model="missing")
        self.assertIn("model_not_found", result["error"])
        self.assertEqual(result["rejection"]["scope"], "model")
        self.assertTrue(result["safe_to_retry"])

    def test_codex_limit_evidence_never_allows_replay_after_tool_activity(self):
        for activity in ([], [{"type": "item.started", "item": {"type": "mcp_tool_call"}}]):
            stdout = stream({"type": "thread.started", "thread_id": "session"}, *activity,
                            {"type": "turn.failed", "error": {"message": "You've hit your usage limit"}})
            process = mock.Mock(pid=123, returncode=1, stdin=io.StringIO())
            process.communicate.return_value = (stdout, "")
            with self.subTest(activity=activity), mock.patch.object(engines.common.subprocess, "Popen", return_value=process), \
                 mock.patch.object(engines.codex, "_codex_session_model", return_value={}):
                result = engines.codex_exec("hello", unit="altitude-coordinator-fixture.service", sandbox_settings=[], cwd=self.repo)
            self.assertTrue(result["limited"])
            self.assertIsNone(result["rejection"])
            self.assertEqual(result["safe_to_retry"], not activity)

    def test_claude_sync_keeps_all_activity_evidence_beyond_raw_capture(self):
        failure = {"type": "result", "is_error": True, "result": MODEL_ERROR}
        for activity in ([], [{"type": "assistant", "message": {"model": "fable",
                "content": [{"type": "tool_use", "name": "mcp__tool"}]}}]):
            stdout = stream({"type": "system", "subtype": "init", "session_id": "session"}, *activity, failure)
            process = mock.Mock(pid=123, returncode=1, stdout=io.StringIO(stdout), stderr=io.StringIO(""),
                                stdin=io.StringIO())
            with self.subTest(activity=activity), \
                 mock.patch.object(engines.common.subprocess, "Popen", return_value=process), \
                 mock.patch.object(engines.common, "usage_hold", return_value=None), \
                 mock.patch.object(engines.common, "RAW_CAPTURE_CAP", 80):
                result = engines.claude_print("hello", unit="altitude-coordinator-fixture.service", home=self.tmp / "claude-home", cwd=self.repo, model="fable")
            self.assertEqual(result["rejection"]["scope"], "model")
            self.assertEqual(result["safe_to_retry"], not activity)
            self.assertTrue(result["raw_stdout_truncated"])

    def test_worker_failure_after_init_retains_full_turn_evidence_and_waits_for_exit(self):
        for engine, init, failed in (
            ("claude", {"type": "system", "subtype": "init", "session_id": "session", "model": "fable"},
             {"type": "result", "is_error": True, "result": MODEL_ERROR}),
            ("codex", {"type": "thread.started", "thread_id": "session"},
             {"type": "turn.failed", "error": {"code": "model_not_found", "message": "missing"}}),
        ):
            paths = engines.common._worker_paths(self.tmp, engine)
            S.write_json(paths["record"], {"engine": engine, "unit": "owned.service", "started_at": S.now(),
                                           "launch_model": "fable", "resume": True})
            paths["stderr"].write_text("")
            for live, activity in ((True, []), (False, []), (False, [{"type": "item.completed",
                "item": {"type": "file_change"}}])):
                paths["stdout"].write_text(stream(init, *activity, failed))
                with self.subTest(engine=engine, live=live, activity=activity), \
                     mock.patch.object(platform, "job_active", return_value=live), \
                     mock.patch.object(engines.codex, "_codex_session_model", return_value={}):
                    row = engines.worker(engine, {"agent_id": engine}, job_root=self.tmp)
                self.assertEqual(row["sessionId"], "session")
                self.assertTrue(row["resumed"])
                self.assertEqual(row["rejection"]["scope"], "model")
                self.assertEqual(row["safe_to_retry"], not live and not activity)
