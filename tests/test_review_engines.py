"""Captured review adapters: real fixture processes; never live provider or service calls."""
from tests.support import AltitudeCase

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote
from unittest.mock import patch

from altitude import config, engines, platform, route


class ReviewEngineTests(AltitudeCase):
    host = "linux"  # systemd fixtures

    def setUp(self):
        super().setUp()
        self.snapshot = self.tmp / "snapshot"
        self.runtime = self.tmp / "runtime"
        self.snapshot.mkdir()
        self.runtime.mkdir()
        (self.snapshot / "code.py").write_text("one\ntwo\nthree\n")

    def test_captured_tool_reads_searches_and_refuses_private_symlink_and_special_files(self):
        private = self.tmp / "private.txt"
        private.write_text("credential")
        (self.snapshot / "link").symlink_to(private)
        (self.snapshot / "outside").symlink_to(self.tmp, target_is_directory=True)
        os.mkfifo(self.snapshot / "pipe")
        self.assertEqual(engines._review_read(self.snapshot, {"operation": "list"})["files"], ["code.py"])
        self.assertEqual(engines._review_read(self.snapshot, {"operation": "search", "path": "code.py", "query": "two"})["lines"],
                         [{"line": 2, "text": "two"}])
        self.assertEqual(engines._review_read(self.snapshot, {"operation": "read", "path": "code.py", "line": 3})["lines"],
                         [{"line": 3, "text": "three"}])
        for name in ("../private.txt", str(private), "link", "outside/private.txt", "pipe"):
            with self.subTest(path=name), self.assertRaises((ValueError, OSError)):
                engines._review_read(self.snapshot, {"operation": "read", "path": name})
        for operation in ("exec", "write", "network", "delegate"):
            with self.assertRaises(ValueError):
                engines._review_read(self.snapshot, {"operation": operation})

    def test_long_captured_line_is_readable_in_full_through_continuation(self):
        # I-20260924-132135: a 6887-character JSON proposal line was clipped at 2000 characters with no way on.
        proposal = "".join(f"section {n}: " + "text " * 40 for n in range(100))
        self.assertGreater(len(proposal), 16384, "longer than one response, far past the former 2000-character clip")
        (self.snapshot / "context.json").write_text("{\n" + json.dumps({"proposal": proposal}) + "\n}\n")
        read, chunks = {"operation": "read", "path": "context.json"}, []
        while True:
            reply = engines._review_read(self.snapshot, read)
            chunks.extend(reply["lines"])
            if not reply["truncated"]:
                self.assertNotIn("next", reply)
                break
            self.assertGreaterEqual(reply["next"]["column"], 1)
            read = {**read, **reply["next"]}
        self.assertEqual("".join(chunk["text"] for chunk in chunks if chunk["line"] == 2), json.dumps({"proposal": proposal}))
        self.assertEqual([(chunk["line"], chunk.get("column", 1)) for chunk in chunks], [(1, 1), (2, 1), (2, 16384), (3, 1)])
        self.assertTrue(all(chunk["text"] for chunk in chunks if chunk["line"] == 2))
        search = engines._review_read(self.snapshot, {"operation": "search", "path": "context.json", "query": "section 99"})
        self.assertEqual([(chunk["line"], chunk.get("column", 1)) for chunk in search["lines"]], [(2, 1)])
        self.assertTrue(search["truncated"], "a match longer than one response says so instead of silently clipping")
        rest = engines._review_read(self.snapshot, {"operation": "search", "path": "context.json", "query": "section 99",
                                                    **search["next"]})
        self.assertEqual(search["lines"][0]["text"] + rest["lines"][0]["text"], json.dumps({"proposal": proposal}))
        self.assertEqual(rest["lines"][0]["column"], search["next"]["column"])
        (self.snapshot / "many.txt").write_text("\n".join(f"row {n}" for n in range(1, 251)) + "\n")
        page = engines._review_read(self.snapshot, {"operation": "read", "path": "many.txt", "line": 51})
        self.assertEqual((page["lines"][0]["line"], page["lines"][-1]["line"], len(page["lines"])), (51, 150, 100))
        self.assertEqual((page["truncated"], page["next"]), (True, {"line": 151, "column": 1}))
        self.assertFalse(engines._review_read(self.snapshot, {"operation": "read", "path": "many.txt", "line": 151})["truncated"])

    def test_mcp_exposes_only_captured_read_tool_and_redacts_path_failures(self):
        served = engines._review_served(self.runtime)
        requests = [{"id": 1, "method": "tools/list"},
                    {"id": 2, "method": "tools/call", "params": {"name": "captured_input", "arguments":
                     {"operation": "read", "path": "/private/credential"}}},
                    {"id": 3, "method": "tools/call", "params": {"name": "shell", "arguments": {}}}]
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests)))), patch.object(sys, "stdout", output):
            engines.review_mcp(str(self.snapshot), str(served))
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([tool["name"] for tool in replies[0]["result"]["tools"]], ["captured_input"])
        self.assertIn("error", replies[1])
        self.assertIn("error", replies[2])
        self.assertNotIn("/private", output.getvalue())
        self.assertFalse(served.exists(), "tool listing and refused reads are not coverage")
        for arguments, coverage in (({"operation": "list"}, False),
                                    ({"operation": "search", "path": "code.py", "query": "absent"}, False),
                                    ({"operation": "read", "path": "code.py", "line": 2}, True)):
            request = {"id": 4, "method": "tools/call", "params": {"name": "captured_input", "arguments": arguments}}
            with patch.object(sys, "stdin", io.StringIO(json.dumps(request))), patch.object(sys, "stdout", io.StringIO()):
                engines.review_mcp(str(self.snapshot), str(served))
            self.assertEqual(served.exists(), coverage, arguments)

    def test_commands_disable_native_tools_and_user_configuration_for_both_engines(self):
        features = ("shell_tool", "apps", "plugins", "multi_agent", "view_image", "code_mode_host", "skip_host_skill_discovery")
        self.patch(engines, "_review_native", return_value=(True, features))
        first = engines._review_command("claude", self.snapshot, self.runtime, "selected")
        for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
            self.assertIn(flag, first)
        self.assertNotIn("--safe-mode", first, "safe mode disconnects the captured adapter (I-20260924-080541)")
        self.assertEqual(first[first.index("--tools") + 1], "")
        self.assertEqual(first[first.index("--allowedTools") + 1], "mcp__captured__captured_input")
        second = engines._review_command("codex", self.snapshot, self.runtime, "selected")
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config"):
            self.assertIn(flag, second)
        for setting in ('approval_policy="never"', 'web_search="disabled"', 'permissions.captured-review.network.enabled=false',
                        'features.shell_tool=false', 'features.apps=false', 'features.plugins=false', 'features.multi_agent=false',
                        'features.view_image=false', 'features.skip_host_skill_discovery=true', 'features.code_mode_host=true'):
            self.assertIn(setting, second)
        self.assertNotIn('features.code_mode_host=false', second)
        for command in (first, second):
            adapter = next(item for item in command if "review_mcp(" in item)
            self.assertIn(f"review_mcp({str(self.snapshot)!r}, {str(self.runtime / 'captured-input.read')!r})", adapter)
        filesystem = next(item for item in second if item.startswith("permissions.captured-review.filesystem="))
        self.assertIn('\":root\"=\"deny\"', filesystem)
        self.assertNotIn('"write"', filesystem)
        self.assertIn('"' + str(self.snapshot) + '"="read"', filesystem)
        for command in (first, second):
            self.assertNotIn("resume", command)
            self.assertNotIn("altitude-l3", " ".join(command))

    def test_environment_strips_owner_machine_and_credential_authority(self):
        with patch.dict(os.environ, {"ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": "owner", "ALTITUDE_SESSION_KEY": "key",
                                     "GH_TOKEN": "secret", "ANTHROPIC_API_KEY": "secret", "OPENAI_API_KEY": "secret",
                                     "DBUS_SESSION_BUS_ADDRESS": "private", "SSH_AUTH_SOCK": "private"}):
            env = engines._review_env()
        self.assertEqual(set(env) - {"HOME", "PATH", "LANG", "LC_ALL", "CODEX_HOME", "CLAUDE_CONFIG_DIR"}, set())
        self.assertNotIn("secret", env.values())

    def fixture(self, *, result=None, answer=None, exitcode=0, served=True):
        if served:
            engines._review_served(self.runtime).touch()
        self.patch(engines, "review_capability", return_value={"available": True})
        self.patch(engines, "_review_command", return_value=["fixture-only"])
        self.patch(platform, "job_active", return_value=False)
        payload = result if result is not None else {"text": "Needs a fix", "findings": [
            {"severity": "high", "title": "Missing check", "body": "Evidence", "path": "code.py", "line": 2}],
            "limitations": ["Tests were not executed."]}
        record = {"result": json.dumps(payload) if answer is None else answer, "usage": {"input_tokens": 20}}
        program = f"import sys; sys.stdin.read(); print({json.dumps(record)!r}); sys.exit({exitcode})"
        service = self.patch(platform, "job_command", return_value=[sys.executable, "-I", "-c", program])
        return service

    def test_result_process_lifecycle_without_duration_deadline(self):
        service = self.fixture()
        workers = []
        result = engines.review("Review checkpoint", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                on_start=lambda worker: workers.append(worker))
        self.assertIsNone(result["error"])
        self.assertEqual(result["findings"][0]["id"], "F1")
        self.assertEqual(result["usage"], {"input_tokens": 20})
        self.assertEqual(workers, [{"unit": result["worker"]["unit"], "pid": None, "started_ticks": None}, result["worker"]])
        self.assertTrue(result["termination_confirmed"])
        self.assertNotIn("runtime_max", service.call_args.kwargs)
        self.assertNotIn("diagnostics", result, "successful reviews keep their existing result contract")

    def test_startup_failure_keeps_sanitized_exception_and_errno(self):
        self.fixture()
        self.patch(engines.subprocess, "Popen", side_effect=FileNotFoundError(
            2, "missing executable api_key=fictional-secret-value", "/Users/fictional/private/tool"))
        result = engines.review("Private assignment", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        evidence = result["diagnostics"]
        self.assertEqual((evidence["exit_status"], evidence["exception_type"], evidence["errno"]),
                         (None, "FileNotFoundError", 2))
        self.assertIn("missing executable", evidence["stderr"])
        self.assertIn("[REDACTED]", evidence["stderr"])
        self.assertNotIn("fictional-secret-value", json.dumps(result))
        self.assertNotIn("/Users/fictional", json.dumps(result))
        self.assertTrue(result["termination_confirmed"])

    def test_command_preparation_failure_keeps_diagnostics_before_spawn(self):
        self.fixture()
        self.patch(engines, "_review_command", side_effect=FileNotFoundError(2, "review executable disappeared"))
        spawn = self.patch(engines.subprocess, "Popen")
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertEqual(result["diagnostics"]["errno"], 2)
        self.assertIn("executable disappeared", result["diagnostics"]["stderr"])
        self.assertTrue(result["termination_confirmed"])
        spawn.assert_not_called()

    def test_encoded_quoted_credentials_are_redacted_in_stderr_and_startup_exceptions(self):
        credential = quote('"password": "fictional secret with spaces"', safe="")
        service = self.fixture()
        service.return_value = [sys.executable, "-I", "-c",
            f"import sys; sys.stdin.read(); sys.stderr.write('startup failed\\n' + {credential!r}); sys.exit(12)"]
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIn("startup failed", result["diagnostics"]["stderr"])
        self.assertIn("[REDACTED]", result["diagnostics"]["stderr"])
        self.assertNotIn("fictional", json.dumps(result))
        self.assertNotIn("with spaces", json.dumps(result))
        self.patch(engines.subprocess, "Popen", side_effect=OSError(2, "startup failed " + credential))
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertEqual(result["diagnostics"]["errno"], 2)
        self.assertIn("startup failed", result["diagnostics"]["stderr"])
        self.assertNotIn("fictional", json.dumps(result))
        self.assertNotIn("with spaces", json.dumps(result))
        capture = engines._BoundedRawCapture()
        capture.add(quote(credential, safe=""))
        self.assertIn("withheld", engines._review_diagnostics(capture)["stderr"])

    def test_nonzero_exit_keeps_stderr_and_status_without_stdout_transcript(self):
        service = self.fixture()
        service.return_value = [sys.executable, "-I", "-c",
            "import sys; sys.stdin.read(); print('PRIVATE TRANSCRIPT'); "
            "sys.stderr.buffer.write(b'cannot connect captured adapter\\ninvalid byte: \\xff\\n'"
            "b'api_key=fictional-secret-value\\n/Users/fictional/private/tool\\n'); sys.exit(23)"]
        result = engines.review("Private assignment", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        evidence = result["diagnostics"]
        self.assertEqual(evidence["exit_status"], 23)
        self.assertIn("status 23", result["error"])
        self.assertIn("cannot connect captured adapter", evidence["stderr"])
        self.assertIn("invalid byte: \ufffd", evidence["stderr"])
        self.assertIn("[REDACTED]", evidence["stderr"])
        for private in ("PRIVATE TRANSCRIPT", "Private assignment", "fictional-secret-value", "/Users/fictional"):
            self.assertNotIn(private, json.dumps(result))
        self.assertTrue(evidence["capture_complete"])
        self.assertFalse(evidence["stderr_truncated"])
        self.assertFalse(evidence["stdout_truncated"])

    def test_capture_overflow_names_stream_and_withholds_ambiguous_stderr_tail(self):
        service = self.fixture()
        service.return_value = [sys.executable, "-I", "-c",
            "import sys; sys.stdin.read(); print('PRIVATE TRANSCRIPT' * 1000); "
            "sys.stderr.write('startup diagnostic\\napi_key=fictional-secret-value\\n' + 'x' * 20000 + "
            "'\\nlast diagnostic\\n'); sys.exit(0)"]
        with patch.object(engines, "RAW_CAPTURE_CAP", 1024):
            result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIn("capture limit", result["error"])
        evidence = result["diagnostics"]
        self.assertEqual(evidence["exit_status"], 0)
        self.assertTrue(evidence["stdout_truncated"])
        self.assertTrue(evidence["stderr_truncated"])
        self.assertIn("startup diagnostic", evidence["stderr"])
        self.assertNotIn("last diagnostic", evidence["stderr"])
        self.assertIn("stderr tail withheld", evidence["stderr"])
        self.assertNotIn("PRIVATE TRANSCRIPT", json.dumps(result))
        self.assertNotIn("fictional-secret-value", json.dumps(result))
        self.assertLessEqual(len(evidence["stderr"].encode()), 8192)

    def test_diagnostic_bound_and_raw_cuts_do_not_expose_partial_credentials(self):
        capture = engines._BoundedRawCapture(128)
        capture.add("useful start\napi_key=" + "s" * 400 + "\nuseful end\n")
        evidence = engines._review_diagnostics(capture)
        self.assertEqual(evidence["stderr"], "useful start\n[capture limit: stderr tail withheld]\n")
        self.assertTrue(evidence["stderr_truncated"])
        capture = engines._BoundedRawCapture(128)
        capture.add("useful start\n-----BEGIN PRIVATE KEY-----\n" + "PRIVATE MATERIAL\n" * 100
                    + "-----END PRIVATE KEY-----\nuseful end\n")
        evidence = engines._review_diagnostics(capture)
        self.assertNotIn("PRIVATE MATERIAL", evidence["stderr"])
        self.assertNotIn("PRIVATE KEY", evidence["stderr"])
        self.assertIn("useful start", evidence["stderr"])
        self.assertNotIn("useful end", evidence["stderr"], "raw truncation withholds the ambiguous tail")
        for private in ("prefix\n-----BEGIN PRIVATE KEY-----\nPRIVATE MATERIAL\n",
                        "prefix\n" + "x" * 200 + "\n-----BEGIN PRIVATE KEY-----\n" + "PRIVATE MATERIAL\n" * 100
                        + "-----END PRIVATE KEY-----\nuseful end\n"):
            capture = engines._BoundedRawCapture(128)
            capture.add(private)
            evidence = engines._review_diagnostics(capture)
            self.assertNotIn("PRIVATE MATERIAL", evidence["stderr"])
            self.assertNotIn("PRIVATE KEY", evidence["stderr"])
        capture = engines._BoundedRawCapture(128)
        capture.add("useful start\n" + "benign padding\n" * 100 + "-----BEGIN PRIVATE KEY-----\n"
                    + "PRIVATE MATERIAL\n" * 100)
        evidence = engines._review_diagnostics(capture)
        self.assertIn("useful start", evidence["stderr"])
        self.assertIn("stderr tail withheld", evidence["stderr"])
        self.assertNotIn("PRIVATE MATERIAL", evidence["stderr"])
        self.assertNotIn("PRIVATE KEY", evidence["stderr"])
        capture = engines._BoundedRawCapture()
        capture.add("useful start\n" + "é" * 10000 + "\nuseful end\n")
        evidence = engines._review_diagnostics(capture)
        self.assertTrue(evidence["stderr_truncated"])
        self.assertLessEqual(len(evidence["stderr"].encode()), 8192)
        self.assertIn("useful start", evidence["stderr"])
        self.assertIn("useful end", evidence["stderr"])
        capture = engines._BoundedRawCapture()
        capture.add('connection refused\n"password": "fictional secret with spaces"\n'
                    'cookie=session=fictional-cookie\nAuthorization: Bearer fictional-access-token\n')
        evidence = engines._review_diagnostics(capture)
        self.assertIn("connection refused", evidence["stderr"])
        self.assertNotIn("fictional", evidence["stderr"])
        self.assertEqual(evidence["stderr"].count("[REDACTED]"), 1)

    def test_multiline_credentials_and_remaining_capture_are_withheld(self):
        assignments = ['"password":\n"fictional secret with spaces"',
                       'api_key="fictional\nsecret\nwith spaces"',
                       'cookie=\nfictional-secret', 'Authorization: Bearer\nfictional-secret',
                       '"secret": "unterminated\nfictional-secret']
        for assignment in assignments:
            for encoded in (False, True):
                with self.subTest(assignment=assignment, encoded=encoded):
                    stderr = "adapter startup failed\n" + assignment + "\nfollowing diagnostic\n"
                    if encoded:
                        stderr = quote(stderr, safe="")
                    service = self.fixture()
                    service.return_value = [sys.executable, "-I", "-c",
                        f"import sys; sys.stdin.read(); sys.stderr.write({stderr!r}); sys.exit(12)"]
                    result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
                    evidence = result["diagnostics"]
                    self.assertEqual(evidence["exit_status"], 12)
                    self.assertIn("adapter startup failed", evidence["stderr"])
                    self.assertIn("[REDACTED]", evidence["stderr"])
                    for private in ("fictional", "with spaces", "unterminated", "following diagnostic"):
                        self.assertNotIn(private, evidence["stderr"])
                    with patch.object(engines.subprocess, "Popen", side_effect=OSError(2, stderr)):
                        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
                    self.assertEqual(result["diagnostics"]["errno"], 2)
                    self.assertIn("adapter startup failed", result["diagnostics"]["stderr"])
                    self.assertNotIn("fictional", json.dumps(result))
                    self.assertNotIn("with spaces", json.dumps(result))

    def test_incomplete_stderr_capture_is_named_in_failure_evidence(self):
        self.fixture(exitcode=4)
        self.patch(engines, "review_stop", return_value=True)
        spawn = engines.subprocess.Popen
        class FailedReader:
            def read(self, size):
                raise OSError("private reader failure")
            def close(self):
                self.stream.close()
        def with_failed_stderr(*args, **kwargs):
            process = spawn(*args, **kwargs)
            reader = FailedReader()
            reader.stream = process.stderr
            process.stderr = reader
            return process
        self.patch(engines.subprocess, "Popen", side_effect=with_failed_stderr)
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertEqual(result["diagnostics"]["exit_status"], 4)
        self.assertFalse(result["diagnostics"]["capture_complete"])
        self.assertNotIn("private reader failure", json.dumps(result))

    def test_stderr_overflow_does_not_fail_a_successful_review(self):
        service = self.fixture()
        service.return_value[-1] = service.return_value[-1].replace("sys.exit(0)", "sys.stderr.write('x' * 10000); sys.exit(0)")
        with patch.object(engines, "RAW_CAPTURE_CAP", 4096):
            result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIsNone(result["error"])
        self.assertEqual(result["findings"][0]["id"], "F1")
        self.assertNotIn("diagnostics", result)

    def test_failure_and_malformed_findings_never_become_clean_review(self):
        self.fixture(result={"text": "Malformed", "findings": [{"title": "No body"}]})
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIn("invalid findings", result["error"])
        self.assertEqual(result["findings"], [])
        self.fixture(answer="I reviewed the captured input but found nothing to report; no JSON follows {sorry.")
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIn("invalid findings", result["error"], "an answer with no JSON object still fails with the reason")
        self.assertEqual(result["findings"], [])

    def test_prose_around_the_json_result_keeps_findings_intact(self):
        # I-20260924-132135: a Claude reviewer opened with a sentence before its fenced JSON and the review failed.
        payload = {"text": "Two material risks.", "findings": [
            {"severity": "high", "title": "Reader clips", "body": "Line 2161 clips at 2000.", "path": "altitude/engines.py", "line": 2161},
            {"severity": "medium", "title": "Parser strict", "body": "Only a leading fence is stripped."}],
            "limitations": ["Tests were not executed."]}
        answer = ("Here is my review of the captured input {as requested}.\n\n```json\n" + json.dumps(payload, indent=2)
                  + "\n```\n\nThe summary above stands.")
        self.fixture(answer=answer)
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIsNone(result["error"])
        self.assertEqual(result["text"], payload["text"])
        self.assertEqual([(f["id"], f["title"], f.get("line")) for f in result["findings"]],
                         [("F1", "Reader clips", 2161), ("F2", "Parser strict", None)])
        self.assertEqual(result["limitations"], payload["limitations"])
        self.fixture(answer=json.dumps(payload))
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertEqual(len(result["findings"]), 2, "a bare object still parses")

    def test_reviewer_that_never_reads_captured_input_fails_with_reason(self):
        # I-20260924-080541: Codex completed a review with zero coverage after its tool discovery failed.
        service = self.fixture(served=False)
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIn("never read its captured input", result["error"])
        self.assertEqual(result["findings"], [])
        payload = {"text": "captured_input is not available among the exposed tools.", "findings": [],
                   "limitations": ["No source or diff was examined."]}
        records = [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}},
                   {"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 10}}]
        stdout = "\n".join(map(json.dumps, records))
        service.return_value = [sys.executable, "-I", "-c", f"import sys; sys.stdin.read(); print({stdout!r})"]
        result = engines.review("Review", engine="codex", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIn("never read its captured input", result["error"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["text"], json.dumps(payload), "the reviewer's own account is retained as evidence")
        self.assertEqual(result["usage"]["input_tokens"], 30)
        self.assertTrue(result["termination_confirmed"])
        failed = "\n".join(map(json.dumps, [{"type": "turn.failed", "error": {"message": "quota"}}, records[1]]))
        service.return_value = [sys.executable, "-I", "-c", f"import sys; sys.stdin.read(); print({failed!r})"]
        result = engines.review("Review", engine="codex", snapshot=self.snapshot, runtime=self.runtime)
        self.assertEqual(result["error"], "The review engine returned an error.", "engine failures keep precedence")

    def test_codex_result_uses_same_findings_and_usage_contract(self):
        service = self.fixture()
        payload = {"text": "No findings in captured checkpoint", "findings": [], "limitations": ["No test execution"]}
        records = [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}},
                   {"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 10}}]
        stdout = "\n".join(map(json.dumps, records))
        service.return_value = [sys.executable, "-I", "-c", f"import sys; sys.stdin.read(); print({stdout!r})"]
        result = engines.review("Review", engine="codex", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIsNone(result["error"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["text"], payload["text"])
        self.assertEqual(result["usage"]["input_tokens"], 30)

    def test_callback_failure_retains_unknown_termination_evidence(self):
        self.fixture()
        self.patch(engines, "review_stop", return_value=False)
        self.patch(engines, "review_active", return_value=None)
        def failed(worker):
            if worker["pid"] is not None:
                raise ValueError("private callback detail")
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime, on_start=failed)
        self.assertFalse(result["termination_confirmed"])
        self.assertIn("failed", result["error"])
        self.assertNotIn("private callback", result["error"])

    def test_unavailable_inspection_refuses_before_launch_or_prompt(self):
        self.fixture()
        self.patch(platform, "job_active", side_effect=RuntimeError("user bus unavailable"))
        spawn = self.patch(engines.subprocess, "Popen")
        started = self.patch(engines, "review_stop")
        result = engines.review("No model call", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertTrue(result["unavailable"])
        self.assertEqual(result["diagnostics"]["exception_type"], "RuntimeError")
        self.assertIn("user bus unavailable", result["diagnostics"]["stderr"])
        self.assertTrue(result["termination_confirmed"])
        self.assertIsNone(result["worker"])
        spawn.assert_not_called()
        started.assert_not_called()

    def test_interrupt_stops_unit_even_if_inspection_becomes_unavailable(self):
        service = self.fixture()
        service.return_value = [sys.executable, "-I", "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"]
        stop = self.patch(engines, "review_stop", return_value=False)
        self.patch(engines, "review_active", return_value=None)
        workers = []
        def interrupted():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                           on_start=lambda worker: workers.append(worker), on_wait=interrupted)
        stop.assert_called_once_with(workers[-1])
        self.assertFalse(engines._review_launcher(workers[-1]))

    def test_capture_setup_interruption_cannot_orphan_a_launched_process(self):
        self.fixture()
        spawn = self.patch(engines.subprocess, "Popen")
        self.patch(engines, "_BoundedRawCapture", side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        spawn.assert_not_called()

    def test_failed_launcher_stops_independent_unit_and_retains_unknown_cleanup(self):
        self.fixture(exitcode=1)
        stop = self.patch(engines, "review_stop", return_value=False)
        active = self.patch(engines, "review_active", return_value=None)
        for state in (None, False):
            with self.subTest(remaining=state):
                active.return_value = state
                result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
                self.assertIn("status 1", result["error"])
                self.assertEqual(result["termination_confirmed"], state is False)
                stop.assert_called_with(result["worker"])
        self.assertEqual(stop.call_count, 2)

    def test_system_exit_in_started_callback_stops_unit_and_launcher(self):
        self.fixture()
        workers = []
        stop = self.patch(engines, "review_stop", return_value=False)
        def interrupted(worker):
            workers.append(worker)
            if worker["pid"] is not None:
                raise SystemExit(7)
        with self.assertRaises(SystemExit):
            engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime, on_start=interrupted)
        stop.assert_called_once_with(workers[-1])
        self.assertFalse(engines._review_launcher(workers[-1]))

    def test_review_survives_wait_intervals_past_former_deadline(self):
        service = self.fixture()
        real_wait = subprocess.Popen.wait
        waits = []
        def wait(proc, timeout=None):
            waits.append(timeout)
            if len(waits) <= 2:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            return real_wait(proc, timeout=timeout)
        # Waiting wakes for connection/lifecycle checks, never to enforce total elapsed time.
        with patch.object(subprocess.Popen, "wait", wait), patch.object(engines.time, "monotonic", return_value=86400):
            result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                    on_wait=lambda: True)
        self.assertIsNone(result["error"])
        self.assertTrue(result["termination_confirmed"])
        self.assertGreaterEqual(len(waits), 3)
        self.assertNotIn("runtime_max", service.call_args.kwargs)

    def test_disconnected_caller_stops_unit_and_cannot_return_success(self):
        service = self.fixture()
        service.return_value = [sys.executable, "-I", "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"]
        stop = self.patch(engines, "review_stop", return_value=False)
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                on_wait=lambda: False)
        self.assertIn("cancelled", result["error"])
        stop.assert_called_once_with(result["worker"])
        self.assertTrue(result["termination_confirmed"])

    def test_cancel_callback_prevents_prompt_delivery(self):
        service = self.fixture()
        received = self.runtime / "fixture-input.txt"
        ready = self.runtime / "fixture-ready"
        service.return_value = [sys.executable, "-I", "-c",
                                f"import sys,pathlib; pathlib.Path({str(ready)!r}).touch(); "
                                f"pathlib.Path({str(received)!r}).write_text(sys.stdin.read())"]
        def cancel_when_ready(worker):
            if worker["pid"] is None:
                return True
            until = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < until:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            return False
        def fixture_stop(worker):
            until = time.monotonic() + 5
            while not received.exists() and time.monotonic() < until:
                time.sleep(0.01)
            return False
        stop = self.patch(engines, "review_stop", side_effect=fixture_stop)
        result = engines.review("Must not be delivered", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                on_start=cancel_when_ready)
        self.assertIn("cancelled", result["error"])
        stop.assert_called_once_with(result["worker"])
        self.assertTrue(result["termination_confirmed"])
        self.assertEqual(received.read_text(), "")

    def test_unit_identity_is_durable_before_spawn_and_failed_spawn_is_confirmed(self):
        self.fixture()
        receipts = []
        def spawn(*args, **kwargs):
            self.assertEqual(len(receipts), 1)
            self.assertIsNone(receipts[0]["pid"])
            self.assertRegex(receipts[0]["unit"], r"^altitude-review-[a-f0-9]{32}\.service$")
            raise OSError("fixture refused spawn")
        self.patch(engines.subprocess, "Popen", side_effect=spawn)
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                on_start=lambda worker: receipts.append(worker))
        self.assertTrue(result["termination_confirmed"])
        self.assertEqual(result["worker"], receipts[0])
        self.assertIn("launch failed", result["error"])

    def test_cancel_or_callback_error_before_spawn_never_launches(self):
        self.fixture()
        spawn = self.patch(engines.subprocess, "Popen")
        for callback in (lambda worker: False, lambda worker: (_ for _ in ()).throw(ValueError("cannot save"))):
            result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                    on_start=callback)
            self.assertTrue(result["termination_confirmed"])
            self.assertIsNone(result["worker"]["pid"])
        spawn.assert_not_called()

    def test_unit_only_restart_receipt_keeps_unknown_until_terminal_evidence(self):
        worker = {"unit": "altitude-review-" + "b" * 32 + ".service", "pid": None, "started_ticks": None}
        active = self.patch(platform, "job_active", return_value=False)
        status = self.patch(engines, "service_status", return_value={"load_state": "not-found", "state": "inactive"})
        self.assertIsNone(engines.review_active(worker))
        stop = self.patch(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.assertFalse(engines.review_stop(worker))
        self.assertEqual(stop.call_args.args[0][-1], worker["unit"])
        status.return_value = {"load_state": "loaded", "state": "failed"}
        self.assertIsNone(engines.review_active(worker))
        status.return_value.update(exited_monotonic="12345", exec_main_code="1")
        self.assertFalse(engines.review_active(worker))
        self.assertTrue(engines.review_stop(worker))
        active.return_value = True
        self.assertTrue(engines.review_active(worker))

    def test_unknown_termination_and_foreign_units_are_never_confirmed(self):
        self.assertIsNone(engines.review_active({"unit": "altitude.service"}))
        self.assertFalse(engines.review_stop({"unit": "altitude.service"}))
        worker = {"unit": "altitude-review-" + "a" * 32 + ".service", "pid": os.getpid(), "started_ticks": "wrong"}
        self.patch(platform, "job_active", side_effect=RuntimeError("unknown"))
        self.assertIsNone(engines.review_active(worker))

    def test_a_launcher_that_exits_at_once_reads_as_ended_not_unknown(self):
        proc = self.tmp / "proc"
        stat = proc / "4242" / "stat"
        stat.parent.mkdir(parents=True)
        self.patch(platform, "PROC", proc)
        for state, running in (("R", True), ("Z", False)):
            stat.write_text("4242 (py) " + " ".join([state, *["0"] * 18, "777", *["0"] * 10]))
            with self.subTest(state=state):
                self.assertEqual(platform.process_start(4242), "777")
                worker = {"unit": "altitude-review-" + "c" * 32 + ".service", "pid": 4242, "started_ticks": "777"}
                self.assertIs(engines._review_launcher(worker), running)
                self.assertIsNone(engines._review_launcher({**worker, "started_ticks": "Z"}))
        stat.unlink()
        self.assertIs(engines._review_launcher(worker), False)

    def test_missing_adapter_refuses_without_process(self):
        self.patch(engines, "review_capability", return_value={"available": False, "why": "Unsupported confinement"})
        spawn = self.patch(engines.subprocess, "Popen")
        result = engines.review("Review", engine="absent", snapshot=self.snapshot, runtime=self.runtime)
        self.assertTrue(result["unavailable"])
        self.assertTrue(result["termination_confirmed"])
        spawn.assert_not_called()


class ReviewRoutingTests(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.usage = self.patch(route, "_usage", return_value={"claude": (None, None), "codex": (None, None)})
        self.installation = self.patch(engines, "installation", return_value={"available": True})
        self.capability = self.patch(engines, "review_capability", return_value={"available": True})

    def test_excludes_actual_owner_seat_all_models_and_keeps_unknown_explicit(self):
        project = {"routing": config.parse_routing("claude:opus>claude:fable>codex")}
        selected = route.pick_review({"l2_engine": "claude", "engine": "codex"}, project)
        self.assertEqual(selected["engine"], "codex")
        self.assertFalse(selected["allowance_known"])
        self.assertFalse(selected["same_engine"])
        self.assertEqual(selected["fallback_reason"], "")

    def test_owner_effort_does_not_exclude_native_default_reviewer(self):
        selected = route.pick_review({"l2_engine": "codex"}, {"l2_effort": "max"})
        self.assertEqual(selected["engine"], "claude")
        self.assertFalse(selected["same_engine"])
        self.assertIsNone(selected["effort"])

    def test_single_configured_engine_supports_separate_review_with_unknown_allowance(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine), patch.object(config, "ENGINES", (engine,)):
                selected = route.pick_review({"l2_engine": engine}, {"routing": [[{"engine": engine}]]})
                self.assertEqual(selected["engine"], engine)
                self.assertEqual(selected["label"], config.ENGINE_LABELS[engine])
                self.assertTrue(selected["same_engine"])
                self.assertFalse(selected["allowance_known"])
                self.assertIn("No alternate engine", selected["fallback_reason"])
                self.capability.assert_called_with(engine)

    def test_project_engine_and_model_pins_remain_strict_for_same_engine_review(self):
        task = {"l2_engine": "claude"}
        for project in ({"l2_engine": "claude"}, {"l2_engine": "claude", "l2_model": "sonnet"},
                        {"routing": config.parse_routing("claude:opus>claude:fable")}):
            with self.subTest(project=project):
                selected = route.pick_review(task, project)
                self.assertEqual(selected["engine"], "claude")
                self.assertTrue(selected["same_engine"])
                self.assertEqual(selected["model"], project.get("l2_model") or "opus")
        self.usage.return_value = {"claude": (100, 0), "codex": (0, 0)}
        self.assertIsNone(route.pick_review(task, {"l2_engine": "claude"})["engine"])

    def test_unavailable_alternate_pin_cannot_fall_back_to_owner(self):
        self.installation.side_effect = lambda engine: {"available": engine != "codex", "why": "executable missing"}
        selected = route.pick_review({"l2_engine": "claude"}, {"l2_engine": "codex"})
        self.assertIsNone(selected["engine"])
        self.assertIn("executable missing", selected["fallback_reason"])
        self.capability.assert_not_called()

    def test_missing_or_unconfigured_actual_owner_refuses(self):
        self.assertIsNone(route.pick_review({}, {})["engine"])
        self.assertIsNone(route.pick_review({"l2_engine": "absent"}, {})["engine"])
        self.capability.assert_not_called()

    def test_missing_alternate_executable_falls_back_without_probing_its_capability(self):
        self.installation.side_effect = lambda engine: {"available": engine != "claude", "why": "executable missing"}
        selected = route.pick_review({"l2_engine": "codex"}, {})
        self.assertEqual(selected["engine"], "codex")
        self.assertTrue(selected["same_engine"])
        self.assertIn("executable missing", selected["fallback_reason"])
        self.capability.assert_called_once_with("codex")

    def test_alternate_exhaustion_falls_back_but_owner_exhaustion_refuses(self):
        for window, expected in (((100, 0), "weekly window exhausted"), ((0, 100), "short window exhausted")):
            with self.subTest(window=window):
                self.usage.return_value = {"claude": window, "codex": (0, 0)}
                selected = route.pick_review({"l2_engine": "codex"}, {})
                self.assertEqual(selected["engine"], "codex")
                self.assertTrue(selected["same_engine"])
                self.assertTrue(selected["allowance_known"])
                self.assertIn(expected, selected["fallback_reason"])
                self.usage.return_value["codex"] = window
                selected = route.pick_review({"l2_engine": "codex"}, {})
                self.assertIsNone(selected["engine"])
                self.assertIn(expected, selected["why"])

    def test_account_rejection_covers_all_alternate_models_and_owner_fallback(self):
        route.note_rejection({"engine": "claude"}, {"scope": "engine", "why": "Account sign in required"})
        selected = route.pick_review({"l2_engine": "codex"}, {})
        self.assertEqual(selected["engine"], "codex")
        self.assertIn("Account sign in required", selected["fallback_reason"])
        self.capability.assert_called_once_with("codex")
        route.note_rejection({"engine": "codex"}, {"scope": "engine", "why": "Owner account blocked"})
        selected = route.pick_review({"l2_engine": "codex"}, {})
        self.assertIsNone(selected["engine"])
        self.assertIn("Owner account blocked", selected["why"])

    def test_model_rejection_uses_configured_alternate_model_before_same_engine(self):
        route.note_rejection({"engine": "claude", "model": "fable"}, {"scope": "model", "why": "Model unavailable"})
        selected = route.pick_review({"l2_engine": "codex"}, {})
        self.assertEqual((selected["engine"], selected["model"]), ("claude", "opus"))
        self.assertFalse(selected["same_engine"])

    def test_unsupported_alternate_capability_falls_back_under_same_contract(self):
        self.capability.side_effect = lambda engine: {"available": engine == "codex", "why": "Confinement unavailable"}
        selected = route.pick_review({"l2_engine": "codex"}, {})
        self.assertEqual(selected["engine"], "codex")
        self.assertTrue(selected["same_engine"])
        self.assertIn("Confinement unavailable", selected["fallback_reason"])
        self.assertEqual([call.args[0] for call in self.capability.call_args_list], ["claude", "codex"])
        self.capability.side_effect = None
        self.capability.return_value = {"available": False, "why": "Confinement unavailable"}
        selected = route.pick_review({"l2_engine": "codex"}, {})
        self.assertIsNone(selected["engine"])
        self.assertIn("Confinement unavailable", selected["why"])

    def test_same_engine_fallback_never_adds_an_unconfigured_option(self):
        self.capability.return_value = {"available": False, "why": "Confinement unavailable"}
        selected = route.pick_review({"l2_engine": "codex"}, {"routing": config.parse_routing("claude:opus")})
        self.assertIsNone(selected["engine"])
        self.capability.assert_called_once_with("claude")

    def test_explicit_selection_is_the_only_candidate_and_is_never_substituted(self):
        project = {"l2_engine": "codex", "l2_model": "sonnet", "routing": config.parse_routing("codex>claude:opus")}
        selected = route.pick_review({"l2_engine": "claude"}, project, model="fable")
        self.assertEqual((selected["engine"], selected["model"]), ("claude", "fable"))
        self.assertTrue(selected["same_engine"])
        self.assertEqual(selected["fallback_reason"], "Selected for this review.")
        selected = route.pick_review({"l2_engine": "claude"}, project, engine="codex")
        self.assertEqual(selected["engine"], "codex")
        self.assertFalse(selected["same_engine"])
        route.note_rejection({"engine": "claude", "model": "fable"}, {"scope": "model", "why": "Model unavailable"})
        selected = route.pick_review({"l2_engine": "codex"}, project, engine="claude", model="fable")
        self.assertIsNone(selected["engine"])
        self.assertIn("Model unavailable", selected["why"])
        self.assertIn("selected reviewer is unavailable", selected["why"])

    def test_selection_keeps_quota_capability_and_unknown_allowance_explicit(self):
        selected = route.pick_review({"l2_engine": "codex"}, {}, engine="claude", model="fable")
        self.assertFalse(selected["allowance_known"])
        self.usage.return_value = {"claude": (100, 0), "codex": (0, 0)}
        selected = route.pick_review({"l2_engine": "codex"}, {}, engine="claude", model="fable")
        self.assertIsNone(selected["engine"])
        self.assertIn("weekly window exhausted", selected["why"])
        self.usage.return_value = {"claude": (0, 0), "codex": (0, 0)}
        self.capability.return_value = {"available": False, "why": "Confinement unavailable"}
        selected = route.pick_review({"l2_engine": "codex"}, {}, engine="claude")
        self.assertIsNone(selected["engine"])
        self.assertIn("Confinement unavailable", selected["why"])
        with patch.object(config, "ENGINES", ("codex",)):
            self.assertIsNone(route.pick_review({"l2_engine": "codex"}, {}, engine="claude")["engine"])
        self.assertIn("requires --engine", route.pick_review({"l2_engine": "codex"}, {"l2_engine": "claude"}, model="custom")["why"])
