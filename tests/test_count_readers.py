"""Verify and monitor prefer dispatch-keyed counts while retaining legacy session counts."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from altitude import config, engines, monitor, state as S, verify


class CountReaders(unittest.TestCase):
    def setUp(self):
        self.monitor_dir = Path(tempfile.mkdtemp(prefix="altitude-count-readers-"))
        self.addCleanup(shutil.rmtree, self.monitor_dir, ignore_errors=True)
        self.original_monitor_dir = config.MONITOR_DIR
        config.MONITOR_DIR = self.monitor_dir

    def tearDown(self):
        config.MONITOR_DIR = self.original_monitor_dir

    def write_counts(self, key, **counts):
        (self.monitor_dir / f"counts-{key}.json").write_text(json.dumps(counts))

    def test_engine_context_percent_is_clamped_for_public_consumers(self):
        self.assertEqual(engines.context_percent(config.CONTEXT_WINDOW * 2), 100.0)
        self.assertEqual(engines.context_percent(-1), 0.0)

    def test_verify_reads_dispatch_key_and_session_fallbacks(self):
        self.write_counts("demo--task-1", subagent_launches=2, edits=3)
        self.write_counts("new-session", subagent_launches=9, edits=9)
        keyed = verify._spend({}, "demo", {"dispatch_id": "task-1", "session_id": "new-session"}, Path("."))
        self.assertEqual(keyed["spend"]["subagent_launches_hook"], 2)
        self.assertEqual(keyed["spend"]["edits_hook"], 3)

        self.write_counts("old-session", subagent_launches=4, edits=5)
        unkeyed = verify._spend({}, "demo", {"session_id": "old-session"}, Path("."))
        self.assertEqual(unkeyed["spend"]["subagent_launches_hook"], 4)
        self.assertEqual(unkeyed["spend"]["edits_hook"], 5)

        self.write_counts("pre-change-session", subagent_launches=6)
        legacy = verify._spend({}, "demo", {"dispatch_id": "task-2", "session_id": "pre-change-session"}, Path("."))
        self.assertEqual(legacy["spend"]["subagent_launches_hook"], 6)

    def test_codex_context_reads_last_rollout_turn_not_cumulative_usage(self):
        session = "01a-test-session"
        rollout = self.monitor_dir / f"rollout-2026-08-30-{session}.jsonl"
        rollout.write_text("\n".join([
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": 900000},
                "last_token_usage": {"input_tokens": 30000, "output_tokens": 2000, "total_tokens": 32000},
                "model_context_window": 256000}}}),
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"total_tokens": "truncated"}, "model_context_window": 256000}}}),
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {}, "model_context_window": 256000}}}),
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"total_tokens": -5}, "model_context_window": 256000}}}),
            "{not json",
        ]) + "\n")
        original = monitor.CODEX_SESSIONS
        monitor.CODEX_SESSIONS = self.monitor_dir
        try:
            self.assertEqual(monitor.codex_context_percent(session), 12.5)
        finally:
            monitor.CODEX_SESSIONS = original

    def test_codex_context_finds_isolated_worker_home_rollout(self):
        session = "01a-isolated-session"
        worker_root = self.monitor_dir / "codex-workers"
        rollout_dir = worker_root / "l2-deadbeef" / "sessions" / "2026" / "08" / "30"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / f"rollout-2026-08-30-{session}.jsonl").write_text(json.dumps({
            "type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"total_tokens": 64000}, "model_context_window": 256000,
            }},
        }) + "\n")
        with patch.object(engines, "CODEX_WORKER_HOMES", worker_root), \
             patch.object(monitor, "CODEX_SESSIONS", self.monitor_dir / "no-interactive-sessions"):
            self.assertEqual(monitor.codex_context_percent(session), 25.0)

    def test_claude_context_skips_malformed_newest_usage_and_uses_older_valid_row(self):
        home = self.monitor_dir / "home"
        transcript = home / ".claude" / "projects" / "demo" / "sid-fallback.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("\n".join([
            json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": 1000, "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 1000}}}),
            json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": "malformed", "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0}}}),
        ]) + "\n")

        with patch.object(Path, "home", return_value=home):
            percent = monitor.transcript_context_percent("sid-fallback", Path("."))

        expected = round(100.0 * 4000 / config.CONTEXT_WINDOW, 1)
        self.assertEqual(percent, expected)

    def test_claude_context_uses_newest_duplicate_checkout_transcript(self):
        home = self.monitor_dir / "home"
        old = home / ".claude" / "projects" / "old-checkout" / "sid-duplicate.jsonl"
        new = home / ".claude" / "projects" / "new-checkout" / "sid-duplicate.jsonl"
        old.parent.mkdir(parents=True); new.parent.mkdir(parents=True)
        old.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 1000, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}}}) + "\n")
        new.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 5000, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}}}) + "\n")
        os.utime(old, ns=(1_000_000_000, 1_000_000_000))
        os.utime(new, ns=(2_000_000_000, 2_000_000_000))

        with patch.object(Path, "home", return_value=home):
            percent = monitor.transcript_context_percent("sid-duplicate", Path("."))

        self.assertEqual(percent, round(100.0 * 5000 / config.CONTEXT_WINDOW, 1))



    def test_l2_lifecycle_names_the_owner_of_the_next_action(self):
        self.assertEqual(monitor._l2_lifecycle({"state": "reported"}, None), "awaiting_closeout")
        self.assertEqual(monitor._l2_lifecycle({"state": "blocked", "needs_user": {"reason": "pick"}}, None), "needs_user")
        self.assertEqual(monitor._l2_lifecycle({"state": "blocked", "resume_after": "later"}, None), "resume_scheduled")
        self.assertEqual(monitor._l2_lifecycle({"state": "blocked", "pending_resume": {"generation": "g"}}, None), "resume_in_progress")
        self.assertEqual(monitor._l2_lifecycle({"state": "blocked"}, None), "awaiting_l3_recovery")
        self.assertEqual(monitor._l2_lifecycle({"state": "running"}, None), "recovery_pending")
        self.assertEqual(monitor._l2_lifecycle({"state": "running"}, {"status": "exited", "state": "done"}), "recovery_pending")
        self.assertEqual(monitor._l2_lifecycle({"state": "running"}, {"status": "active", "state": "tool"}), "active")


    def test_monitor_reads_dispatch_key_and_session_fallbacks(self):
        tasks = [
            {"slug": "keyed", "state": "running", "dispatch_id": "keyed-1", "session_id": "new-session"},
            {"slug": "unkeyed", "state": "blocked", "session_id": "old-session"},
            {"slug": "legacy", "state": "reported", "dispatch_id": "legacy-1", "session_id": "pre-change-session"},
        ]
        self.write_counts("demo--keyed-1", subagent_launches=2, edits=3)
        self.write_counts("new-session", subagent_launches=9, edits=9)
        self.write_counts("old-session", subagent_launches=4, edits=5)
        self.write_counts("pre-change-session", subagent_launches=6, edits=7)
        originals = config.load_projects, S.list_tasks, monitor.transcript_context_percent
        config.load_projects = lambda: {"demo": {"path": "."}}
        S.list_tasks = lambda project: tasks
        monitor.transcript_context_percent = lambda session_id, cwd: None
        try:
            rows = {row["slug"]: row for row in monitor.sessions() if row.get("kind") == "l2"}
        finally:
            config.load_projects, S.list_tasks, monitor.transcript_context_percent = originals
        self.assertEqual((rows["keyed"]["subagent_launches"], rows["keyed"]["edits"]), (2, 3))
        self.assertEqual((rows["unkeyed"]["subagent_launches"], rows["unkeyed"]["edits"]), (4, 5))
        self.assertEqual((rows["legacy"]["subagent_launches"], rows["legacy"]["edits"]), (6, 7))
        self.assertEqual(rows["keyed"]["lifecycle"], "recovery_pending")
        self.assertEqual(rows["unkeyed"]["lifecycle"], "awaiting_l3_recovery")
        self.assertEqual(rows["legacy"]["lifecycle"], "awaiting_closeout")


if __name__ == "__main__":
    unittest.main()
