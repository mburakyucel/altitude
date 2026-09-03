"""Live transcript: every turn of the thread, parsing, continuity, fencing, and access policy."""
import json
import unittest

from tests.support import AltitudeCase
from altitude import dispatch, state as S, tasks as T, transcript


class TestLiveTranscript(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Observed work", "Do it")
        self.slug = task["slug"]
        task.update({"state": "running", "attempt": 1, "l2_engine": "codex", "session_id": "thread-1", "agent_id": "w2"})
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "state", frm="queued", to="running", by="altd")
        self.root = dispatch.l2_job_root(self.project, self.slug)
        self.root.mkdir(parents=True, exist_ok=True)
        # Two turns of thread-1 (the second learns its thread id from its own events) and one of another thread.
        self.old = self._turn("w1", "2026-09-03T10:00:00+00:00", "thread-1")
        self.new = self._turn("w2", "2026-09-03T10:05:00+00:00", None)
        self._turn("w0", "2026-09-03T09:00:00+00:00", "thread-0").write_text(
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "other thread"}}) + "\n")

    def _turn(self, worker_id, started_at, session_id):
        S.write_json(self.root / f"{worker_id}.json", {"id": worker_id, "started_at": started_at, "session_id": session_id})
        path = self.root / f"{worker_id}.stdout.jsonl"
        path.write_text("")
        return path

    def _view(self, **kwargs):
        return transcript.view(self.project, self.slug, **{"engine": "codex", "session_id": "thread-1", **kwargs})

    def test_every_turn_of_the_thread_in_order_with_a_deterministic_cursor(self):
        self.old.write_text(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "make test"}}) + "\n")
        self.new.write_text("".join(json.dumps(e) + "\n" for e in (
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
            {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a.py", "kind": "update"}]}})))
        first = self._view()
        self.assertEqual([e["source"] for e in first["events"]], ["platform", "platform", "codex", "codex", "codex", "codex"])
        self.assertEqual(first["events"][1]["kind"], "boundary")
        self.assertEqual([(e["kind"], e["text"]) for e in first["events"][2:]],
                         [("command", "make test"), ("engine", ""), ("message", "done"), ("file", "update a.py")])
        self.assertNotIn("other thread", json.dumps(first))
        later = self._view(cursor=first["cursor"])
        self.assertEqual(later["events"], [])

    def test_partial_and_corrupt_records_are_visible_without_crashing(self):
        self.old.write_bytes(b'{bad}\n{"type":"turn.started"')
        errors = [e["text"] for e in self._view()["events"] if e["kind"] == "error"]
        self.assertEqual(errors, ["corrupt record 1", "partial record; waiting for completion"])

    def test_session_fence_rejects_stale_or_incomplete_identity(self):
        for changed in ({"engine": "claude"}, {"session_id": "old"}, {"session_id": ""}):
            with self.assertRaisesRegex(transcript.TranscriptAccessError, "generation changed"):
                self._view(**changed)

    def test_no_browser_path_and_redaction_policy(self):
        self.assertIsNone(transcript._claude_path("../../etc/passwd"))
        for project, slug in (("../etc", self.slug), (self.project, "../status"), ("unmanaged", "task")):
            with self.assertRaises(transcript.TranscriptAccessError):
                transcript.view(project, slug, engine="codex", session_id="x")
        self.old.write_text(json.dumps({"type": "item.completed", "authorization": "Bearer abcdefghijklmnop",
                                        "output": "token ghp_abcdefghijklmnop"}) + "\n")
        raw = self._view(raw=True)["events"][-1]["raw"]
        self.assertEqual(raw["authorization"], "[REDACTED]")
        self.assertNotIn("ghp_", raw["output"])


if __name__ == "__main__":
    unittest.main()
