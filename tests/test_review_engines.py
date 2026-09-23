"""Captured review adapters: real fixture processes; never live provider or service calls."""
from tests.support import AltitudeCase

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from altitude import config, engines, route


class ReviewEngineTests(AltitudeCase):
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

    def test_mcp_exposes_only_captured_read_tool_and_redacts_path_failures(self):
        requests = [{"id": 1, "method": "tools/list"},
                    {"id": 2, "method": "tools/call", "params": {"name": "captured_input", "arguments":
                     {"operation": "read", "path": "/private/credential"}}},
                    {"id": 3, "method": "tools/call", "params": {"name": "shell", "arguments": {}}}]
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests)))), patch.object(sys, "stdout", output):
            engines.review_mcp(str(self.snapshot))
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([tool["name"] for tool in replies[0]["result"]["tools"]], ["captured_input"])
        self.assertIn("error", replies[1])
        self.assertIn("error", replies[2])
        self.assertNotIn("/private", output.getvalue())

    def test_commands_disable_native_tools_and_user_configuration_for_both_engines(self):
        features = ("shell_tool", "apps", "plugins", "multi_agent", "view_image", "code_mode_host", "skip_host_skill_discovery")
        self.patch(engines, "_review_native", return_value=(True, features))
        first = engines._review_command("claude", self.snapshot, self.runtime, "selected")
        for flag in ("--safe-mode", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
            self.assertIn(flag, first)
        self.assertEqual(first[first.index("--tools") + 1], "")
        self.assertEqual(first[first.index("--allowedTools") + 1], "mcp__captured__captured_input")
        second = engines._review_command("codex", self.snapshot, self.runtime, "selected")
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config"):
            self.assertIn(flag, second)
        for setting in ('approval_policy="never"', 'web_search="disabled"', 'permissions.captured-review.network.enabled=false',
                        'features.shell_tool=false', 'features.apps=false', 'features.plugins=false', 'features.multi_agent=false',
                        'features.view_image=false', 'features.skip_host_skill_discovery=true'):
            self.assertIn(setting, second)
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

    def fixture(self, *, result=None, exitcode=0):
        self.patch(engines, "review_capability", return_value={"available": True})
        self.patch(engines, "_review_command", return_value=["fixture-only"])
        self.patch(engines, "_unit_active", return_value=False)
        payload = result if result is not None else {"text": "Needs a fix", "findings": [
            {"severity": "high", "title": "Missing check", "body": "Evidence", "path": "code.py", "line": 2}],
            "limitations": ["Tests were not executed."]}
        record = {"result": json.dumps(payload), "usage": {"input_tokens": 20}}
        program = f"import sys; sys.stdin.read(); print({json.dumps(record)!r}); sys.exit({exitcode})"
        service = self.patch(engines, "_codex_service_command", return_value=[sys.executable, "-I", "-c", program])
        return service

    def test_result_process_lifecycle_and_resource_bound(self):
        service = self.fixture()
        workers = []
        result = engines.review("Review checkpoint", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                timeout=900, on_start=lambda worker: workers.append(worker))
        self.assertIsNone(result["error"])
        self.assertEqual(result["findings"][0]["id"], "F1")
        self.assertEqual(result["usage"], {"input_tokens": 20})
        self.assertEqual(workers, [{"unit": result["worker"]["unit"], "pid": None, "started_ticks": None}, result["worker"]])
        self.assertTrue(result["termination_confirmed"])
        self.assertEqual(service.call_args.kwargs["runtime_max"], 600)

    def test_failure_and_malformed_findings_never_become_clean_review(self):
        self.fixture(result={"text": "Malformed", "findings": [{"title": "No body"}]})
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime)
        self.assertIn("invalid findings", result["error"])
        self.assertEqual(result["findings"], [])

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

    def test_timeout_stops_unit_and_cannot_return_success(self):
        service = self.fixture()
        service.return_value = [sys.executable, "-I", "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"]
        stop = self.patch(engines, "review_stop", return_value=False)
        result = engines.review("Review", engine="claude", snapshot=self.snapshot, runtime=self.runtime, timeout=1)
        self.assertIn("time limit", result["error"])
        stop.assert_called_once_with(result["worker"])
        self.assertTrue(result["termination_confirmed"])

    def test_cancel_callback_prevents_prompt_delivery(self):
        service = self.fixture()
        received = self.runtime / "fixture-input.txt"
        service.return_value = [sys.executable, "-I", "-c",
                                f"import sys,pathlib; pathlib.Path({str(received)!r}).write_text(sys.stdin.read())"]
        stop = self.patch(engines, "review_stop", return_value=False)
        result = engines.review("Must not be delivered", engine="claude", snapshot=self.snapshot, runtime=self.runtime,
                                on_start=lambda worker: worker["pid"] is None)
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
        active = self.patch(engines, "_unit_active", return_value=False)
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
        self.patch(engines, "_unit_active", side_effect=RuntimeError("unknown"))
        self.assertIsNone(engines.review_active(worker))

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
        self.patch(route, "_usage", return_value={"claude": (None, None), "codex": (None, None)})
        self.patch(engines, "installation", return_value={"available": True})
        self.patch(engines, "review_capability", return_value={"available": True})

    def test_excludes_actual_owner_seat_all_models_and_keeps_unknown_explicit(self):
        project = {"routing": config.parse_routing("claude:opus>claude:fable>codex")}
        selected = route.pick_review({"l2_engine": "claude", "engine": "codex"}, project)
        self.assertEqual(selected["engine"], "codex")
        self.assertFalse(selected["allowance_known"])

    def test_project_pins_and_single_engine_never_fall_back_to_owner(self):
        task = {"l2_engine": "claude"}
        for project in ({"l2_engine": "claude"}, {"l2_model": "opus"},
                        {"routing": config.parse_routing("claude:opus>claude:fable")}):
            with self.subTest(project=project):
                self.assertIsNone(route.pick_review(task, project)["engine"])
        self.assertIsNone(route.pick_review({}, {})["engine"])

    def test_exhaustion_and_unsupported_confinement_refuse_without_upgrade(self):
        self.patch(route, "_usage", return_value={"claude": (100, 0), "codex": (0, 0)})
        self.assertIsNone(route.pick_review({"l2_engine": "codex"}, {})["engine"])
        self.patch(engines, "review_capability", return_value={"available": False, "why": "Confinement unavailable"})
        selected = route.pick_review({"l2_engine": "claude"}, {})
        self.assertIsNone(selected["engine"])
        self.assertEqual(selected["why"], "Confinement unavailable")
