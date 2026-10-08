"""Live transcript: every turn of the thread in time order, conversation fields, hidden reasoning, parsing,
fencing, and access policy."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, state as S, tasks as T, transcript


def _stamp(minutes: int, seconds: int = 0) -> str:
    """A timestamp relative to now, so fixtures order deterministically against the task's own events."""
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=minutes, seconds=seconds)).isoformat()


class _CodexTranscriptCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Observed work", "Do it")
        self.slug = task["slug"]
        task.update({"state": "running", "attempt": 1, "l2_engine": "codex", "session_id": "thread-1", "agent_id": "w2",
                     "worktree": "/repo/wt"})
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "state", frm="queued", to="running", by="altd")
        self.root = dispatch.l2_job_root(self.project, self.slug)
        self.root.mkdir(parents=True, exist_ok=True)
        # Two turns of thread-1 around the task's own events (the second learns its thread id from its own
        # events and was a resume) and one turn of another thread.
        self.old = self._turn("w1", _stamp(-10), "thread-1")
        self.new = self._turn("w2", _stamp(10), None, resume=True)
        self._turn("w0", _stamp(-20), "thread-0").write_text(
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "other thread"}}) + "\n")

    def _turn(self, worker_id, started_at, session_id, *, resume=False):
        S.write_json(self.root / f"{worker_id}.json",
                     {"id": worker_id, "started_at": started_at, "session_id": session_id, "resume": resume})
        path = self.root / f"{worker_id}.stdout.jsonl"
        path.write_text("")
        return path

    def _view(self, **kwargs):
        return transcript.view(self.project, self.slug, **{"engine": "codex", "session_id": "thread-1", "attempt": 1, **kwargs})


class TestCodexTranscript(_CodexTranscriptCase):
    def test_every_turn_in_time_order_with_prompts_commands_and_file_changes(self):
        T.brief(self.project, self.slug, "# Brief\nDo it")
        message = T.message(self.project, self.slug, "burak", "Prefer the smaller diff")
        task = S.load_task(self.project, self.slug)
        task["message_deliveries"] = {message["id"]: {"agent_id": "w2", "session_id": "thread-1", "at": _stamp(10)}}
        S.save_task(self.project, task)
        self.old.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "item_1", "type": "command_execution", "command": "/bin/bash -lc 'make test'",
            "aggregated_output": "ok\n", "exit_code": 0, "status": "completed"}}) + "\n")
        self.new.write_text("".join(json.dumps(e) + "\n" for e in (
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "item.completed", "item": {"id": "r", "type": "reasoning", "text": "private plan"}},
            {"type": "item.started", "item": {"id": "item_2", "type": "command_execution", "command": "bash -lc \"ls\"",
                                              "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
            {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "done"}},
            {"type": "item.completed", "item": {"id": "item_4", "type": "file_change", "status": "completed",
                                                "changes": [{"path": "/repo/wt/a.py", "kind": "update"}]}})))
        first = self._view()
        self.assertEqual([(e["source"], e["kind"], e["role"]) for e in first["events"]], [
            ("platform", "message", "user"), ("codex", "command", "assistant"),  # turn 1: the brief, then its work
            ("platform", "boundary", "system"),
            ("platform", "message", "user"),  # turn 2: the message the resume delivered
            ("codex", "command", "assistant"),
            ("codex", "message", "assistant"), ("codex", "file", "assistant")])
        events = first["events"]
        self.assertEqual(events[0]["text"], "# Brief\nDo it\n")
        self.assertEqual(events[0]["at"], json.loads((self.root / "w1.json").read_text())["started_at"])
        self.assertEqual(events[2]["text"], "queued → running · altd")
        self.assertRegex(events[3]["text"], r"^Message from Operator \(.*\):\nPrefer the smaller diff$")
        self.assertEqual({k: events[1][k] for k in ("tool", "summary", "text", "output", "tool_use_id", "status", "error")},
                         {"tool": "command", "summary": "make test", "text": "make test", "output": "ok\n",
                          "tool_use_id": "item_1", "status": "completed", "error": False})
        self.assertEqual((events[4]["summary"], events[4]["status"], events[4]["output"]), ("ls", "in_progress", ""))
        self.assertEqual((events[5]["text"], events[6]["summary"], events[6]["tool_use_id"]), ("done", "update a.py", "item_4"))
        for raw in (False, True):
            page = json.dumps(self._view(raw=raw))
            self.assertNotIn("other thread", page)
            self.assertNotIn("private plan", page)
        self.assertEqual(self._view(mode="delta", cursor=first["cursor"])["events"], [])

    def test_a_resume_that_delivered_nothing_shows_the_continue_prompt(self):
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n")
        record = S.read_json(self.root / "w2.json")
        record["input_delivered"] = True
        S.write_json(self.root / "w2.json", record)
        first = self._view()
        prompts = [e["text"] for e in first["events"] if e["role"] == "user"]
        self.assertEqual(prompts, [transcript.RESUME_PROMPT])  # no brief on disk, so turn 1 shows no prompt

    def test_partial_and_corrupt_records_are_visible_without_crashing(self):
        self.old.write_bytes(b'{bad}\n{"type":"turn.started"')
        errors = [e["text"] for e in self._view()["events"] if e["kind"] == "error"]
        self.assertEqual(errors, ["corrupt record 1", "partial record; waiting for completion"])

    def test_session_fence_rejects_stale_or_incomplete_identity(self):
        for changed in ({"engine": "claude"}, {"session_id": "old"}, {"session_id": ""}, {"attempt": 2}):
            with self.assertRaisesRegex(transcript.TranscriptAccessError, "generation changed"):
                self._view(**changed)

    def test_no_browser_path_and_redaction_policy(self):
        self.assertIsNone(engines._claude_path("../../etc/passwd"))
        for project, slug in (("../etc", self.slug), (self.project, "../status"), ("unmanaged", "task")):
            with self.assertRaises(transcript.TranscriptAccessError):
                transcript.view(project, slug, engine="codex", session_id="x", attempt=1)
        self.old.write_text(json.dumps({"type": "item.completed", "authorization": "Bearer abcdefghijklmnop",
                                        "output": "token ghp_abcdefghijklmnop"}) + "\n")
        event = next(e for e in self._view(raw=True)["events"] if e["source"] == "codex")
        raw = json.loads(self._view(raw=True, mode="record", record=event["id"])["text"])
        self.assertEqual(raw["authorization"], "[REDACTED]")
        self.assertNotIn("ghp_", raw["output"])


class TestClaudeTranscript(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Observed work", "Do it")
        self.slug = task["slug"]
        task.update({"state": "running", "attempt": 1, "l2_engine": "claude", "session_id": "sess-1", "agent_id": "a1",
                     "worktree": "/repo/wt"})
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "state", frm="queued", to="running", by="altd")
        self.path = config.HOME / ".claude" / "projects" / "-repo-wt" / "sess-1.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.path.unlink, True)

    def _view(self, **kwargs):
        return transcript.view(self.project, self.slug, **{"engine": "claude", "session_id": "sess-1", "attempt": 1, **kwargs})

    def test_blocks_become_prompts_replies_calls_and_results_without_reasoning(self):
        started_at = datetime.fromisoformat(_stamp(10))
        def at(seconds):
            return (started_at + timedelta(seconds=seconds)).isoformat()
        message = "Message from Operator (2026-09-03T10:00:07+00:00):\nPrefer the smaller diff"
        self.path.write_text("".join(json.dumps(r) + "\n" for r in (
            {"type": "user", "timestamp": at(1), "message": {"role": "user", "content": "# Brief\nDo it"}},
            {"type": "assistant", "timestamp": at(2), "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "private plan", "signature": "sig"}]}},
            {"type": "assistant", "timestamp": at(3), "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Checking the tree."}]}},
            {"type": "assistant", "timestamp": at(4), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "git status\n", "description": "Show status"}}]}},
            {"type": "user", "timestamp": at(5), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "clean", "is_error": False}]}},
            {"type": "assistant", "timestamp": at(6), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_2", "name": "Edit",
                 "input": {"file_path": "/repo/wt/altitude/tasks.py", "old_string": "a", "new_string": "b"}}]}},
            {"type": "user", "timestamp": at(7), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_2", "content": [{"type": "text", "text": "edited"}], "is_error": True}]}},
            {"type": "attachment", "timestamp": at(8), "attachment": {"type": "hook_additional_context", "content": [message]}},
            {"type": "user", "timestamp": at(9), "isMeta": True, "message": {"role": "user", "content": [
                {"type": "text", "text": "<local-command-caveat>injected</local-command-caveat>"}]}},
            {"type": "system", "subtype": "stop_hook_summary", "timestamp": at(10)},
            {"type": "assistant", "timestamp": at(11), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_3", "name": "Agent", "input": {"description": "Survey routes", "prompt": "long"}}]}},
        )))
        events = self._view()["events"]
        self.assertEqual([e["kind"] for e in events if e["source"] == "platform"], ["boundary"])
        self.assertLess(max(e["order"] for e in events if e["source"] == "platform"),
                        min(e["order"] for e in events if e["source"] == "claude"))
        rows = [e for e in events if e["source"] == "claude"]
        self.assertEqual([(e["kind"], e["role"], e.get("tool"), e.get("summary"), e.get("tool_use_id"), e["text"]) for e in rows], [
            ("message", "user", None, None, None, "# Brief\nDo it"),
            ("message", "assistant", None, None, None, "Checking the tree."),
            ("command", "assistant", "Bash", "git status", "toolu_1", "git status\n"),
            ("file", "assistant", "Edit", "altitude/tasks.py", "toolu_2", "- a\n+ b"),
            ("message", "user", None, None, None, message),
            ("tool", "assistant", "Agent", "Survey routes", "toolu_3", '{\n  "description": "Survey routes",\n  "prompt": "long"\n}'),
        ])
        self.assertEqual([(e["output"], e["error"]) for e in rows if e["kind"] in {"command", "file"}],
                         [("clean", False), ("edited", True)])
        self.assertEqual(rows[0]["at"], at(1))
        for raw in (False, True):
            self.assertNotIn("private plan", json.dumps(self._view(raw=raw)))
        raw_rows = [json.loads(self._view(raw=True, mode="record", record=e["id"])["text"])
                    for e in self._view(raw=True)["events"] if e["source"] == "claude"]
        self.assertEqual(len(raw_rows), 11)
        self.assertEqual(raw_rows[1]["message"]["content"], [])

    def test_result_is_folded_with_its_call_before_history_is_selected(self):
        call = {"type": "assistant", "timestamp": _stamp(1), "message": {"content": [
            {"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "true"}}]}}
        filler = [{"type": "assistant", "timestamp": _stamp(2, index),
                   "message": {"content": [{"type": "text", "text": f"message {index}"}]}} for index in range(80)]
        result = {"type": "user", "timestamp": _stamp(5), "message": {"content": [
            {"type": "tool_result", "tool_use_id": "call", "content": "complete"}]}}
        self.path.write_text("".join(json.dumps(row) + "\n" for row in [call, *filler, result]))
        first = self._view()
        self.assertFalse(any(row["kind"] in {"command", "result"} for row in first["events"]))
        older = self._view(mode="history", before=first["lower"], cursor=first["cursor"])
        shown = next(row for row in older["events"] if row["kind"] == "command")
        self.assertEqual(shown["output"], "complete")
        result["message"]["content"][0]["content"] = "late output"
        with self.path.open("a") as stream:
            stream.write(json.dumps(result) + "\n")
        delta = self._view(mode="delta", cursor=first["cursor"], lower=older["lower"])
        self.assertEqual(len(delta["events"]), 1)
        self.assertEqual(delta["events"][0]["id"], shown["id"])
        self.assertEqual(delta["events"][0]["output"], "complete\nlate output")

    def test_long_output_and_raw_preview_are_bounded_with_full_raw_access(self):
        self.path.write_text(json.dumps({"type": "user", "timestamp": _stamp(10), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "x" * (transcript.MAX_DEFAULT_TEXT + 5)}]}}) + "\n")
        row = next(e for e in self._view()["events"] if e["kind"] == "result")
        self.assertTrue(row["truncated"])
        self.assertTrue(row["text"].endswith("… output collapsed"))
        raw = next(e for e in self._view(raw=True)["events"] if e["kind"] == "result")
        self.assertTrue(raw["truncated"])
        first = self._view(raw=True, mode="record", record=raw["id"])
        second = self._view(raw=True, mode="record", record=raw["id"], offset=first["next_offset"])
        self.assertEqual(json.loads(first["text"] + second["text"])["message"]["content"][0]["content"],
                         "x" * (transcript.MAX_DEFAULT_TEXT + 5))


if __name__ == "__main__":
    unittest.main()
