"""Live transcript parsing, continuity, fencing, and access policy."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-transcript-"))
os.environ["ALTITUDE_HOME"] = str(_TMP / "home")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, transcript  # noqa: E402


class TestLiveTranscript(unittest.TestCase):
    def setUp(self):
        self.project = f"transcript-{self._testMethodName}"
        repo = _TMP / self.project / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        projects = config.load_projects()
        projects[self.project] = {"path": str(repo)}
        config.save_projects(projects)
        task = T.new(self.project, "Observed work", "Do it")
        self.slug = task["slug"]
        task.update({"state": "running", "dispatch_id": "observed-1", "l2_engine": "codex",
                     "session_id": "thread-1", "agent_id": "worker-2"})
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "resumed", engine="codex", agent_id="worker-2",
                       session_id="thread-1", previous_worker="worker-1")
        self.old = _TMP / self.project / "old.jsonl"
        self.new = _TMP / self.project / "new.jsonl"

    def _view(self, **kwargs):
        args = {"dispatch_id": "observed-1", "engine": "codex", "session_id": "thread-1"}
        args.update(kwargs)
        paths = [("codex", "thread-1", self.old), ("codex", "thread-1", self.new)]
        with mock.patch.object(transcript, "_engine_paths", return_value=paths):
            return transcript.view(self.project, self.slug, **args)

    def test_incremental_cursor_and_continuation_are_deterministic(self):
        self.old.write_text(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "make test"}}) + "\n")
        self.new.write_text(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3}}) + "\n")
        first = self._view()
        self.assertEqual([e["source"] for e in first["events"]], ["platform", "platform", "codex", "codex"])
        self.assertEqual(first["events"][1]["kind"], "boundary")
        self.assertEqual(first["events"][2]["kind"], "command")
        later = self._view(cursor=first["cursor"])
        self.assertEqual(later["events"], [])

    def test_partial_and_corrupt_records_are_visible_without_crashing(self):
        self.old.write_bytes(b'{bad}\n{"type":"turn.started"')
        self.new.write_text("")
        errors = [e["text"] for e in self._view()["events"] if e["kind"] == "error"]
        self.assertEqual(errors, ["corrupt record 1", "partial record; waiting for completion"])

    def test_generation_fence_rejects_stale_or_incomplete_identity(self):
        self.old.write_text(""); self.new.write_text("")
        for changed in ({"dispatch_id": "old"}, {"engine": "claude"}, {"session_id": "old"}, {"session_id": ""}):
            with self.assertRaisesRegex(transcript.TranscriptAccessError, "generation changed"):
                self._view(**changed)

    def test_no_browser_path_and_redaction_policy(self):
        self.assertIsNone(transcript._claude_path("../../etc/passwd"))
        for project, slug in (("../etc", self.slug), (self.project, "../status"), ("unmanaged", "task")):
            with self.assertRaises(transcript.TranscriptAccessError):
                transcript.view(project, slug, dispatch_id="x", engine="codex", session_id="x")
        self.old.write_text(json.dumps({"type": "item.completed", "authorization": "Bearer abcdefghijklmnop",
                                        "output": "token ghp_abcdefghijklmnop"}) + "\n")
        self.new.write_text("")
        raw = self._view(raw=True)["events"][-1]["raw"]
        self.assertEqual(raw["authorization"], "[REDACTED]")
        self.assertNotIn("ghp_", raw["output"])


if __name__ == "__main__":
    unittest.main()
