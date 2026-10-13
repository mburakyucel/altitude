"""Coordinator context comes from own requests; native compaction retains the conversation."""
import io
import json
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, l3


class TestCoordinatorContext(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.home = self.tmp / "provider"
        self.rollout = self.home / "sessions/2030/01/01/rollout-fixture-context-thread.jsonl"
        self.rollout.parent.mkdir(parents=True)

    def append(self, kind, payload, at=None):
        with self.rollout.open("a") as stream:
            stream.write(json.dumps({"type": kind, "payload": payload,
                                     "timestamp": at or datetime.now(timezone.utc).isoformat()}) + "\n")

    def test_real_adapter_and_coordinator_resume_after_large_aggregate_native_compaction_and_failure(self):
        resumes = []
        real_popen = subprocess.Popen
        # A request at the native threshold still resumes; its next request reflects compaction.
        scenarios = [(50000, False), (475000, False), (40000, True), (45000, False)]

        def launch(command, **kwargs):
            if config.CODEX_BIN not in command:
                return real_popen(command, **kwargs)
            resumes.append("resume" in command)
            tokens, failed = scenarios[len(resumes) - 1]

            def communicate(**kwargs):
                self.append("turn_context", {"model": "fixture-model", "effort": "high"})
                self.append("token_usage_record", {"thread_id": "context-thread", "response_id": str(len(resumes)),
                            "usage": {"input_tokens": tokens, "output_tokens": 100}})
                self.append("event_msg", {"type": "token_count", "info": {
                            "last_token_usage": {"input_tokens": tokens, "output_tokens": 100},
                            "total_token_usage": {"input_tokens": 3000000, "output_tokens": 1000},
                            "model_context_window": 475000}})
                events = [{"type": "thread.started", "thread_id": "context-thread"},
                          {"type": "item.completed", "item": {"type": "agent_message", "text": "Partial reply"}},
                          {"type": "turn.failed", "error": {"message": "fixture failure"}} if failed else
                          {"type": "turn.completed", "usage": {"input_tokens": 3000000,
                                                                "cached_input_tokens": 2900000, "output_tokens": 1000}}]
                return "\n".join(map(json.dumps, events)), ""

            return SimpleNamespace(pid=1, returncode=1 if failed else 0,
                                   stdin=io.StringIO(), communicate=communicate)

        with mock.patch.object(l3, "_select", return_value={"engine": "codex", "why": "fixture"}), \
             mock.patch.object(engines, "_codex_home", return_value=self.home), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=launch):
            for tokens, failed in scenarios:
                result = l3.turn(self.project, "Continue the authorized work.")
                self.assertEqual(result["completed"], not failed)
                self.assertEqual(result["context_tokens"], tokens)
                self.assertEqual(result["context_percent"], round(100 * tokens / 475000, 1))
                session = l3.info(self.project)["sessions"]["codex"]
                self.assertEqual(session["session_id"], "context-thread")
                self.assertFalse(session["rotate_next"])
                if not failed:
                    self.assertEqual(session["usage"]["input_tokens"], 3000000)
        self.assertEqual(resumes, [False, True, True, True])
        self.assertEqual(l3.info(self.project)["sessions"]["codex"]["turns"], 4)

    def test_missing_context_does_not_use_aggregate_or_old_request(self):
        self.append("token_usage_record", {"thread_id": "context-thread", "response_id": "old",
                    "usage": {"input_tokens": 80000, "output_tokens": 100}}, "2029-01-01T00:00:00+00:00")
        self.append("event_msg", {"type": "token_count", "info": {
                    "total_token_usage": {"input_tokens": 3000000, "output_tokens": 1000}}},
                    "2030-01-01T00:00:01+00:00")
        self.append("token_usage_record", {"thread_id": "other-thread", "response_id": "copied",
                    "usage": {"input_tokens": 90000, "output_tokens": 100}}, "2030-01-01T00:00:02+00:00")
        self.append("token_usage_record", {"thread_id": "context-thread", "response_id": "incomplete",
                    "usage": {"input_tokens": 100000}}, "2030-01-01T00:00:03+00:00")
        self.assertIsNone(engines._codex_session_context("context-thread", "2030-01-01T00:00:00+00:00", self.home))
        result = {"text": "answer", "reported_session_id": "context-thread",
                  "usage": {"input_tokens": 3000000}, "context": None}
        with mock.patch.object(l3, "_select", return_value={"engine": "codex", "why": "fixture"}), \
             mock.patch.object(engines, "codex_exec", return_value=result) as execute:
            first = l3.turn(self.project, "First request.")
            second = l3.turn(self.project, "Follow-up.")
        self.assertIsNone(first["context_percent"])
        self.assertIsNone(second["context_tokens"])
        self.assertEqual(l3.info(self.project)["sessions"]["codex"]["context_state"], "unknown")
        self.assertEqual([c.kwargs["resume"] for c in execute.call_args_list], [None, "context-thread"])

    def test_request_without_window_keeps_tokens_without_estimated_percent(self):
        self.append("token_usage_record", {"thread_id": "context-thread", "response_id": "new",
                    "usage": {"input_tokens": 80000, "output_tokens": 100}}, "2030-01-01T00:00:01+00:00")
        with self.rollout.open("a") as stream:
            stream.write('{"type": "event_msg"')  # unfinished provider write
        result = engines._codex_session_context("context-thread", "2030-01-01T00:00:00+00:00", self.home)
        self.assertEqual(result["tokens"], 80000)
        self.assertIsNone(result["window"])
        self.assertIsNone(result["percent"])

    def test_managed_rotation_remains_for_the_other_engine(self):
        for pct in (None, 29.9, 30, 100):
            self.assertEqual(engines.context_rotation_due(pct, "claude"), pct is not None and pct >= 30)
            self.assertFalse(engines.context_rotation_due(pct, "codex"))
