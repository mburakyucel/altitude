"""Codex exec exposes resumable lifecycle state without losing bounded raw evidence."""
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from altitude import config, engines


class TestCodexExecLifecycle(unittest.TestCase):
    def test_fresh_and_resume_surface_pid_session_text_usage_and_resume_flags(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-exec-") as tmp:
            root = Path(tmp)
            args_path = root / "args.jsonl"
            fake = root / "codex"
            fake.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["ARGS_PATH"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
out = args[args.index("-o") + 1]
open(out, "w").write("final answer")
print(json.dumps({"type": "thread.started", "thread_id": "thread-1"}), flush=True)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "streamed answer"}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}}), flush=True)
print("codex diagnostic", file=sys.stderr, flush=True)
""")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            starts, sessions, texts = [], [], []
            env = {"ARGS_PATH": str(args_path)}
            with patch.object(config, "CODEX_BIN", str(fake)), \
                 patch.object(engines, "codex_sandbox_preflight", return_value=None) as preflight:
                fresh = engines.codex_exec("build", cwd=root, sandbox="workspace-write", extra_env=env,
                                           on_start=starts.append, on_session=sessions.append, on_text=texts.append)
                resumed = engines.codex_exec("continue", cwd=root, sandbox="workspace-write", extra_env=env,
                                             resume="thread-1", bypass_hook_trust=True)
            fresh_args, resume_args = [json.loads(line) for line in args_path.read_text().splitlines()]

        self.assertEqual((fresh["session_id"], fresh["text"]), ("thread-1", "final answer"))
        self.assertEqual(fresh["usage"], {"input_tokens": 14, "output_tokens": 5})
        self.assertGreater(starts[0], 0)
        self.assertEqual((sessions, texts), (["thread-1"], ["streamed answer"]))
        self.assertEqual(preflight.call_count, 2)
        self.assertIn("thread.started", fresh["raw_stdout"])
        self.assertEqual(fresh["raw_stderr"], "codex diagnostic\n")
        self.assertFalse(fresh["raw_stdout_truncated"])
        self.assertEqual(fresh_args[:2], ["exec", "--json"])
        self.assertEqual(resume_args[:2], ["exec", "resume"])
        self.assertIn("thread-1", resume_args)
        self.assertIn('sandbox_mode="workspace-write"', resume_args)
        self.assertIn("--dangerously-bypass-hook-trust", resume_args)
        self.assertIsNone(resumed["error"])

    def test_missing_binary_is_a_structured_failure_with_empty_raw_contract(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-exec-") as tmp, \
             patch.object(config, "CODEX_BIN", str(Path(tmp) / "missing-codex")):
            result = engines.codex_exec("build", cwd=Path(tmp), resume="thread-old")

        self.assertEqual((result["returncode"], result["session_id"]), (127, "thread-old"))
        self.assertIn("could not start", result["error"])
        self.assertEqual((result["raw_stdout"], result["raw_stderr"]), ("", ""))
        self.assertFalse(result["raw_stdout_truncated"])
        self.assertFalse(result["raw_stderr_truncated"])

    def test_raw_streams_are_bounded_without_losing_their_head_and_tail(self):
        raw_stdout = "HEAD" + ("x" * 300) + "TAIL"
        raw_stderr = "ERR-HEAD" + ("y" * 300) + "ERR-TAIL"

        class FakeProcess:
            pid = 456
            returncode = 0

            def __init__(self):
                self.stdout = io.StringIO(raw_stdout)
                self.stderr = io.StringIO(raw_stderr)

            def wait(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        with patch.object(engines, "RAW_CAPTURE_CAP", 120), \
             patch.object(engines.subprocess, "Popen", side_effect=lambda *args, **kwargs: FakeProcess()):
            result = engines.codex_exec("build", cwd=Path("."))

        self.assertTrue(result["raw_stdout"].startswith("HEAD"))
        self.assertTrue(result["raw_stdout"].endswith("TAIL"))
        self.assertTrue(result["raw_stderr"].startswith("ERR-HEAD"))
        self.assertTrue(result["raw_stderr"].endswith("ERR-TAIL"))
        self.assertLessEqual(len(result["raw_stdout"].encode()), 120)
        self.assertLessEqual(len(result["raw_stderr"].encode()), 120)
        self.assertTrue(result["raw_stdout_truncated"])
        self.assertTrue(result["raw_stderr_truncated"])


    def test_nested_engine_callback_failure_kills_child_without_new_process_group(self):
        class FakeProcess:
            pid = 456
            returncode = None

            def __init__(self):
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.killed = False

            def kill(self):
                self.killed = True
                self.returncode = -9

            def wait(self):
                return self.returncode

        child = FakeProcess()
        seen = {}

        def popen(*args, **kwargs):
            seen.update(kwargs)
            return child

        with patch.object(engines.subprocess, "Popen", side_effect=popen):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                engines.codex_exec("build", cwd=Path("."), start_new_session=False,
                                   on_start=lambda _pid: (_ for _ in ()).throw(RuntimeError("cancelled")))
        self.assertFalse(seen["start_new_session"])
        self.assertTrue(child.killed)


if __name__ == "__main__":
    unittest.main()
