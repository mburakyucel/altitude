"""Engine adapters return both raw streams, capped by the runtime constant."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase
from altitude import engines


class _Input(io.StringIO):
    def close(self):  # keep the launch input readable after the adapter closes the pipe
        pass


class FakeProcess:
    pid = 123
    returncode = 0

    def __init__(self, stdout="", stderr=""):
        self.stdin = _Input()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


class TestEngineRawCapture(AltitudeCase):
    def claude_print(self, process):
        self.patch(engines, "usage_hold", return_value=None)
        self.patch(engines.subprocess, "Popen", side_effect=lambda *_args, **_kwargs: process)
        return engines.claude_print("prompt", cwd=self.tmp, settings=self.tmp / "settings.json")

    def test_codex_exec_returns_both_complete_raw_streams(self):
        stdout = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3}}) + "\n"
        process = SimpleNamespace(pid=1, returncode=0, stdin=io.StringIO(), communicate=lambda *_a, **_k: (stdout, "codex diagnostic\n"))
        with mock.patch.object(engines.subprocess, "Popen", return_value=process):
            result = engines.codex_exec("prompt", cwd=self.tmp)

        self.assertEqual(result["raw_stdout"], stdout)
        self.assertEqual(result["raw_stderr"], "codex diagnostic\n")
        self.assertEqual(result["usage"], {"input_tokens": 3})

    def test_codex_coordinator_turn_returns_both_raw_streams(self):
        stdout = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3}}) + "\n"
        with mock.patch.object(engines.subprocess, "Popen", return_value=FakeProcess(stdout, "codex diagnostic\n")):
            result = engines.codex_turn("prompt", cwd=self.tmp)

        self.assertEqual(result["raw_stdout"], stdout)
        self.assertEqual(result["raw_stderr"], "codex diagnostic\n")
        self.assertEqual(result["usage"], {"input_tokens": 3})

    def test_claude_print_returns_raw_stream_json_and_stderr(self):
        event = {"type": "result", "result": "done", "session_id": "sid", "is_error": False}
        result = self.claude_print(FakeProcess(json.dumps(event) + "\n", "claude diagnostic\n"))

        self.assertEqual(result["raw_stdout"], json.dumps(event) + "\n")
        self.assertEqual(result["raw_stderr"], "claude diagnostic\n")

    def test_claude_print_caps_raw_stdout_with_runtime_constant(self):
        raw = "HEAD" + ("x" * 300) + "TAIL"
        cap = 120
        self.patch(engines, "RAW_CAPTURE_CAP", new=cap)
        result = self.claude_print(FakeProcess(raw))

        rendered = result["raw_stdout"]
        self.assertLessEqual(len(rendered.encode()), cap)
        self.assertTrue(rendered.startswith("HEAD"))
        self.assertTrue(rendered.endswith("TAIL"))


if __name__ == "__main__":
    unittest.main()
