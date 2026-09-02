"""Raw engine stdout/stderr artifacts for every L1 run."""
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from altitude import config, engines, incidents, l1, state as S


def _response(**overrides):
    response = {
        "text": "RESULT: no PR — engine stopped",
        "error": None,
        "usage": {},
        "structured": None,
        "returncode": 0,
        "raw_stdout": "",
        "raw_stderr": "",
        "raw_stdout_truncated": False,
        "raw_stderr_truncated": False,
    }
    response.update(overrides)
    return response


class TestL1RawOutput(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="altitude-l1-raw-")
        self.root = Path(self.tempdir.name)
        self.patches = ExitStack()
        self.patches.enter_context(mock.patch.dict(os.environ, {"ALTITUDE_HOME": str(self.root)}))
        self.patches.enter_context(mock.patch.object(config, "ROOT", self.root))
        self.patches.enter_context(mock.patch.object(config, "MONITOR_DIR", self.root / "monitor"))

    def tearDown(self):
        self.patches.close()
        self.tempdir.cleanup()

    def _run(self, response, *, engine="codex"):
        run_dir = self.root / "l1"
        run_dir.mkdir(exist_ok=True)
        name = "implementer-1"
        (run_dir / f"{name}.prompt.md").write_text("test prompt")
        rec = {
            "name": name,
            "role": "implementer",
            "engine": engine,
            "model": "test-model",
            "worktree": str(self.root),
        }
        faults = []
        engine_call = response if callable(response) else lambda *_args, **_kwargs: response
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(l1, "load", return_value=rec))
            stack.enter_context(mock.patch.object(S, "load_task", return_value={"l2_engine": engine}))
            stack.enter_context(mock.patch.object(l1, "runs_dir", return_value=run_dir))
            stack.enter_context(mock.patch.object(l1, "save", return_value=None))
            stack.enter_context(mock.patch.object(l1, "_git", return_value=SimpleNamespace(stdout="")))
            stack.enter_context(mock.patch.object(S, "append_event", return_value=None))
            stack.enter_context(mock.patch.object(engines, "codex_exec", engine_call))
            stack.enter_context(mock.patch.object(engines, "claude_print", engine_call))
            stack.enter_context(mock.patch.object(incidents, "system_fault", side_effect=lambda **kw: faults.append(kw)))
            result = l1.exec_run("project", "task", name)
        return result, run_dir, faults

    def test_engine_stderr_is_persisted_and_cited(self):
        rec, run_dir, faults = self._run(_response(raw_stderr="engine warning\n"))

        self.assertEqual((run_dir / "implementer-1.stderr").read_text(), "engine warning\n")
        self.assertTrue((run_dir / "implementer-1.stdout").exists())
        self.assertEqual(rec["result"]["raw"], {
            "stdout": str(run_dir / "implementer-1.stdout"),
            "stderr": str(run_dir / "implementer-1.stderr"),
            "truncated": False,
        })
        self.assertEqual(l1._compact(rec)["raw"], rec["result"]["raw"])
        self.assertEqual(faults, [])

    def test_over_cap_stream_keeps_head_tail_and_notes_bytes_dropped(self):
        raw = "HEAD" + ("x" * 300) + "TAIL"
        with mock.patch.object(l1, "RAW_OUTPUT_CAP", 120):
            rec, run_dir, _faults = self._run(_response(raw_stdout=raw))

        persisted = (run_dir / "implementer-1.stdout").read_text()
        self.assertLessEqual(len(persisted.encode()), 120)
        self.assertTrue(persisted.startswith("HEAD"))
        self.assertTrue(persisted.endswith("TAIL"))
        self.assertIn("[altitude: raw output truncated;", persisted)
        self.assertIn("bytes dropped]", persisted)
        match = re.search(r"\[altitude: raw output truncated; (\d+) bytes dropped\]", persisted)
        self.assertIsNotNone(match)
        notice = match.group(0)
        kept = len(persisted.encode()) - len(notice.encode()) - 4  # the notice's two blank lines on each side
        self.assertEqual(int(match.group(1)), len(raw.encode()) - kept)
        self.assertTrue(rec["result"]["raw"]["truncated"])

    def test_raw_only_bwrap_denial_reports_codex_sandbox_fault(self):
        response = _response(raw_stderr="bwrap: setting up uid map: Permission denied\n")

        rec, _run_dir, faults = self._run(response)

        self.assertEqual(rec["result"]["summary"], "engine fault: codex-sandbox")
        self.assertEqual(faults, [{
            "kind": "codex-sandbox",
            "detail": "bwrap: setting up uid map: Permission denied",
            "project": "project",
            "task": "task",
        }])

    def test_landed_pr_ignores_bwrap_line_in_raw_stream(self):
        event = {"type": "item.completed", "item": {
            "type": "command_execution",
            "aggregated_output": "bwrap: setting up uid map: Operation not permitted",
        }}
        response = _response(text="RESULT: PR #42 — landed", raw_stdout=json.dumps(event) + "\n")

        rec, _run_dir, faults = self._run(response)

        self.assertEqual(rec["result"]["pr"], 42)
        self.assertEqual(rec["result"]["summary"], "PR #42 — landed")
        self.assertEqual(faults, [])

    def test_clean_raw_output_does_not_report_sandbox_fault(self):
        response = _response(raw_stdout="normal engine event\n", raw_stderr="ordinary warning\n")

        rec, _run_dir, faults = self._run(response)

        self.assertEqual(rec["result"]["summary"], "no PR — engine stopped")
        self.assertEqual(faults, [])

    def test_notice_literal_does_not_report_truncation(self):
        response = _response(raw_stdout="echo '[altitude: raw output truncated; 7 bytes dropped]'\n")

        rec, _run_dir, _faults = self._run(response)

        self.assertFalse(rec["result"]["raw"]["truncated"])

    def test_engine_exception_still_leaves_raw_artifacts(self):
        failure = RuntimeError("engine crashed")
        failure.raw_stdout = "last stdout evidence\n"
        failure.raw_stderr = "last stderr evidence\n"

        def crash(*_args, **_kwargs):
            raise failure

        rec, run_dir, faults = self._run(crash)

        self.assertEqual((run_dir / "implementer-1.stdout").read_text(), "last stdout evidence\n")
        self.assertEqual((run_dir / "implementer-1.stderr").read_text(), "last stderr evidence\n")
        self.assertIn("RuntimeError: engine crashed", rec["result"]["error"])
        self.assertEqual(faults, [])

    def test_codex_exec_returns_both_complete_raw_streams(self):
        completed = SimpleNamespace(
            stdout=json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3}}) + "\n",
            stderr="codex diagnostic\n",
            returncode=0,
        )
        with mock.patch.object(engines.subprocess, "run", return_value=completed):
            result = engines.codex_exec("prompt", cwd=self.root)

        self.assertEqual(result["raw_stdout"], completed.stdout)
        self.assertEqual(result["raw_stderr"], completed.stderr)

    def test_claude_print_returns_raw_stream_json_and_stderr(self):
        event = {"type": "result", "result": "done", "session_id": "sid", "is_error": False}

        class FakeProcess:
            pid = 123
            returncode = 0

            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(json.dumps(event) + "\n")
                self.stderr = io.StringIO("claude diagnostic\n")

            def wait(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        with mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=lambda *_args, **_kwargs: FakeProcess()):
            with mock.patch.object(config, "AUTONOMOUS_ENGINES", ("claude", "codex")):
                result = engines.claude_print("prompt", cwd=self.root, settings=self.root / "settings.json")

        self.assertEqual(result["raw_stdout"], json.dumps(event) + "\n")
        self.assertEqual(result["raw_stderr"], "claude diagnostic\n")

    def test_claude_print_caps_raw_stdout_with_runtime_constant(self):
        raw = "HEAD" + ("x" * 300) + "TAIL"
        cap = 120

        class FakeProcess:
            pid = 123
            returncode = 0

            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(raw)
                self.stderr = io.StringIO()

            def wait(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        with mock.patch.object(engines, "RAW_CAPTURE_CAP", cap), \
             mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=lambda *_args, **_kwargs: FakeProcess()):
            with mock.patch.object(config, "AUTONOMOUS_ENGINES", ("claude", "codex")):
                result = engines.claude_print("prompt", cwd=self.root, settings=self.root / "settings.json")

        rendered = result["raw_stdout"]
        self.assertLessEqual(len(rendered.encode()), cap)
        self.assertTrue(rendered.startswith("HEAD"))
        self.assertTrue(rendered.endswith("TAIL"))
        match = re.search(r"\[altitude: raw output truncated; (\d+) bytes dropped\]", rendered)
        self.assertIsNotNone(match)
        notice = match.group(0)
        kept = len(rendered.encode()) - len(notice.encode()) - 4
        self.assertEqual(int(match.group(1)), len(raw.encode()) - kept)
        self.assertTrue(result["raw_stdout_truncated"])


if __name__ == "__main__":
    unittest.main()
