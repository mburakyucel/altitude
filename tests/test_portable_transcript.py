"""Durable provider-neutral transcript bundles and offline validation."""
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-portable-transcript-"))
os.environ["ALTITUDE_HOME"] = str(_TMP / "home")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, transcript  # noqa: E402


class TestPortableTranscript(unittest.TestCase):
    def setUp(self):
        self.project = f"portable-{self._testMethodName}"
        repo = _TMP / self.project / "repo"
        repo.mkdir(parents=True)
        projects = config.load_projects()
        projects[self.project] = {"path": str(repo)}
        config.save_projects(projects)
        self.task = T.new(self.project, "Portable evidence", "retain it")
        self.task.update({"state": "running", "dispatch_id": "dispatch-1", "attempt": 1,
                          "l2_engine": "codex", "engine_model": "gpt-test",
                          "session_id": "thread-1", "agent_id": "worker-1"})
        S.save_task(self.project, self.task)
        self.native = _TMP / self.project / "worker.stdout.jsonl"

    def test_sync_redacts_validates_and_exports_without_provider_store(self):
        self.native.write_text(json.dumps({"type": "assistant", "text": "done",
                                           "authorization": "Bearer abcdefghijklmnop"}) + "\n")
        with mock.patch.object(transcript, "_engine_paths",
                               return_value=[("codex", "thread-1", self.native)]):
            S.append_event(self.project, self.task["slug"], "interrupted", reason="limit")
            root = transcript.sync(self.project, self.task["slug"])
        assert root is not None
        self.native.unlink()  # validation and export no longer need provider-specific files
        result = transcript.validate_bundle(root)
        self.assertTrue(result["valid"])
        native_path = next(root.glob("native-codex-*.jsonl"))
        native = native_path.read_text()
        self.assertNotIn("abcdefghijklmnop", native)
        destination = _TMP / self.project / "export.tar.gz"
        transcript.export(self.project, self.task["slug"], destination)
        extracted = _TMP / self.project / "clean-machine"
        with tarfile.open(destination) as archive:
            archive.extractall(extracted, filter="data")
        exported_root = next(extracted.iterdir())
        self.assertTrue(transcript.validate_bundle(exported_root)["valid"])
        self.assertTrue(next(exported_root.glob("native-codex-*.jsonl")).is_file())

    def test_redispatch_links_attempts_and_preserves_old_bundle(self):
        self.native.write_text(json.dumps({"type": "turn.failed", "message": "lost worker"}) + "\n")
        self.task["state"] = "blocked"
        S.save_task(self.project, self.task)
        with mock.patch.object(transcript, "_engine_paths",
                               return_value=[("codex", "thread-1", self.native)]):
            T.dispatch(self.project, self.task["slug"], dispatch_id="dispatch-2",
                       session_id="thread-2", agent_id="worker-2", worktree="/tmp/w",
                       branch="worktree-x", l2_token="cap", l2_engine="claude")
        current = S.load_task(self.project, self.task["slug"])
        self.assertEqual(current["previous_dispatch_id"], "dispatch-1")
        old = S.task_dir(self.project, self.task["slug"]) / "transcripts" / "dispatch-1"
        self.assertTrue(transcript.validate_bundle(old)["valid"])


if __name__ == "__main__":
    unittest.main()
