"""Redacted local-record shapes: passive counts never launch a provider or read message text."""
import json
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import engines


class TestEngineTokenUsage(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "provider"
        self.patch(engines, "_TOKEN_ROLLOUT_INDEX", {})

    def write(self, path, events, *, append=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if append else "w") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\n")
        return path

    def rollout(self, sid, events, *, parent=None, fork=False):
        meta = {"id": sid, "timestamp": "2026-09-07T10:00:00Z"}
        if parent:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
        if fork:
            meta.update(forked_from_id=parent or "unrelated", subagent_history_start_ordinal=10)
        return self.write(self.home / f"sessions/2026/09/07/rollout-test-{sid}.jsonl",
                          [{"type": "session_meta", "payload": meta}, *events])

    def response(self, sid, response, *, turn="turn-1", inp=100, out=20, cache=40, timestamp="2026-09-07T10:01:00Z"):
        return {"type": "token_usage_record", "timestamp": timestamp, "payload": {
            "thread_id": sid, "turn_id": turn, "response_id": response,
            "usage": {"input_tokens": inp, "output_tokens": out, "cached_input_tokens": cache,
                      "cache_write_input_tokens": 0, "reasoning_output_tokens": 7, "total_tokens": inp + out}}}

    def snapshot(self, inp, out=20, *, turn="turn-1", ordinal=20):
        return [{"type": "event_msg", "ordinal": ordinal, "payload": {"type": "task_started", "turn_id": turn}},
                {"type": "event_msg", "ordinal": ordinal + 1, "timestamp": "2026-09-07T10:01:00Z", "payload": {
                    "type": "token_count", "info": {"total_token_usage": {
                        "input_tokens": inp, "output_tokens": out, "cached_input_tokens": 40,
                        "reasoning_output_tokens": 7}, "last_token_usage": {"input_tokens": 999999}}}}]

    def message(self, sid, mid, *, inp=3, out=20, agent=None, **extra):
        return {"type": "assistant", "sessionId": sid, "agentId": agent, "isSidechain": bool(agent),
                "timestamp": "2026-09-07T10:01:00Z", "message": {"id": mid, "usage": {
                    "input_tokens": inp, "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10,
                    "output_tokens": out, "output_tokens_details": {"thinking_tokens": 7}, **extra}}}

    def observe(self, engine, sid="owner", cursor=None, **kwargs):
        return engines.observe_token_usage(engine, sid, cursor, home=self.home, **kwargs)

    def test_codex_response_records_deduplicate_snapshot_and_replay_cache_and_reasoning_subsets(self):
        response = self.response("owner", "response-1")
        self.rollout("owner", [response, response, *self.snapshot(100)])
        result = self.observe("codex")
        row = result["sessions"][0]
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["total_tokens"]), (100, 20, 120))
        self.assertEqual((row["cache_read_tokens"], row["cache_write_tokens"], row["reasoning_tokens"]), (40, 0, 7))
        self.assertEqual((row["role"], row["status"]), ("owner", "observed"))
        self.assertEqual(result["status"], "partial")  # provider stores do not prove exhaustive helper coverage
        self.assertEqual(self.observe("codex", cursor=result["cursor"])["sessions"], result["sessions"])

    def test_codex_live_append_and_persisted_cursor_resume_do_not_rescan_old_log(self):
        path = self.rollout("owner", [self.response("owner", "response-1")])
        first = self.observe("codex")
        prior_offset = first["cursor"]["files"][str(path)]["offset"]
        self.write(path, [self.response("owner", "response-2", turn="turn-2", inp=70, out=5)], append=True)
        cursor = json.loads(json.dumps(first["cursor"]))
        with mock.patch.object(engines, "_token_read", wraps=engines._token_read) as read:
            result = self.observe("codex", cursor=cursor)
        self.assertGreater(result["cursor"]["files"][str(path)]["offset"], prior_offset)
        self.assertEqual(read.call_args.args[1]["identity"], cursor["files"][str(path)]["identity"])
        self.assertEqual(result["sessions"][0]["total_tokens"], 195)
        self.assertEqual(first["sessions"][0]["total_tokens"], 120)

    def test_codex_parentage_excludes_unrelated_threads_and_fork_inherited_responses(self):
        inherited = self.response("owner", "parent-response")
        self.rollout("owner", [inherited])
        self.rollout("helper", [inherited, self.response("helper", "helper-response", inp=50)], parent="owner", fork=True)
        self.rollout("nested", [self.response("nested", "nested-response", inp=30)], parent="helper")
        self.rollout("unrelated", [self.response("unrelated", "unrelated-response", inp=999999)])
        result = self.observe("codex")
        rows = {row["session_id"]: row for row in result["sessions"]}
        self.assertEqual(set(rows), {"owner", "helper", "nested"})
        self.assertEqual(rows["helper"]["parent_session_id"], "owner")
        self.assertEqual(rows["helper"]["role"], "delegated")
        self.assertEqual(sum(row["total_tokens"] for row in rows.values()), 240)

    def test_codex_legacy_snapshot_is_per_turn_not_sum_of_repeated_counters(self):
        self.rollout("owner", [*self.snapshot(100), *self.snapshot(100), *self.snapshot(200),
                                *self.snapshot(60, out=10, turn="turn-2")])
        self.rollout("helper", [self.response("helper", "response", inp=50)], parent="owner")
        result = self.observe("codex")
        self.assertEqual(len(result["sessions"]), 1)
        row = result["sessions"][0]
        self.assertEqual(row["total_tokens"], 290)
        self.assertEqual((row["role"], row["status"]), ("provider", "partial"))
        self.assertIsNone(row["cache_write_tokens"])

    def test_codex_fork_legacy_skips_inherited_ordinals_and_unreconciled_reset_is_partial(self):
        self.rollout("owner", [*self.snapshot(900, ordinal=1), *self.snapshot(100, ordinal=20),
                                *self.snapshot(80, ordinal=30)], fork=True)
        row = self.observe("codex")["sessions"][0]
        self.assertIsNone(row["total_tokens"])
        self.assertTrue(any("reset" in note for note in row["notes"]))

    def test_modern_parent_does_not_add_child_legacy_fork_inherited_aggregate(self):
        self.rollout("owner", [self.response("owner", "parent")])
        self.rollout("helper", self.snapshot(200), parent="owner", fork=True)
        rows = {row["session_id"]: row for row in self.observe("codex")["sessions"]}
        self.assertEqual(rows["owner"]["total_tokens"], 120)
        self.assertIsNone(rows["helper"]["total_tokens"])
        self.assertEqual(rows["helper"]["status"], "unknown")

    def test_codex_stdout_only_retains_largest_aggregate_without_adding_rollout_usage(self):
        jobs = self.tmp / "workers"
        for name, inp in (("first", 100), ("second", 60)):
            self.write(jobs / f"{name}.json", [{"session_id": "owner", "engine": "codex"}])
            event = {"type": "turn.completed", "usage": {"input_tokens": inp, "output_tokens": 10}}
            self.write(jobs / f"{name}.stdout.jsonl", [event, event])
        result = self.observe("codex", job_root=jobs)
        self.assertEqual(result["sessions"][0]["total_tokens"], 110)
        self.rollout("owner", [self.response("owner", "response", inp=300)])
        engines._TOKEN_ROLLOUT_INDEX.clear()
        result = self.observe("codex", cursor=result["cursor"], job_root=jobs)
        self.assertEqual(result["sessions"][0]["total_tokens"], 320)

    def test_incomplete_richer_source_retains_larger_previously_observed_provider_total(self):
        jobs = self.tmp / "workers"
        self.write(jobs / "worker.json", [{"session_id": "owner", "engine": "codex"}])
        self.write(jobs / "worker.stdout.jsonl", [{"type": "turn.completed", "usage": {"input_tokens": 500, "output_tokens": 100}}])
        first = self.observe("codex", job_root=jobs)
        self.rollout("owner", [self.response("owner", "resumed-response", inp=50, out=10)])
        engines._TOKEN_ROLLOUT_INDEX.clear()
        row = self.observe("codex", cursor=first["cursor"], job_root=jobs)["sessions"][0]
        self.assertEqual(row["total_tokens"], 600)
        self.assertEqual((row["role"], row["status"]), ("provider", "partial"))

    def test_claude_message_stream_updates_deduplicate_and_caches_are_additive(self):
        path = self.home / "projects/project/owner.jsonl"
        self.write(path, [self.message("owner", "msg", out=5)] * 3)
        result = self.observe("claude")
        self.assertEqual(result["sessions"][0]["total_tokens"], 58)
        self.write(path, [self.message("owner", "msg", out=20), self.message("owner", "msg-2")], append=True)
        row = self.observe("claude", cursor=result["cursor"])["sessions"][0]
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["total_tokens"]), (106, 40, 146))
        self.assertEqual((row["cache_read_tokens"], row["cache_write_tokens"], row["reasoning_tokens"]), (80, 20, 14))

    def test_claude_native_children_require_directory_parent_and_agent_identity(self):
        path = self.home / "projects/project/owner.jsonl"
        self.write(path, [self.message("owner", "owner-message"), {"type": "result", "usage": {"input_tokens": 999999}}])
        child = path.with_suffix("") / "subagents/agent-helper.jsonl"
        self.write(child, [self.message("owner", "helper-message", agent="helper"),
                           self.message("other", "unrelated-message", agent="helper"),
                           self.message("owner", "wrong-agent", agent="wrong")])
        rows = self.observe("claude")["sessions"]
        self.assertEqual([(row["role"], row["total_tokens"]) for row in rows], [("owner", 73), ("delegated", 73)])

    def test_claude_replacement_session_inherited_message_ids_are_counted_once_across_roots(self):
        self.write(self.home / "projects/project/owner.jsonl", [self.message("owner", "shared")])
        self.write(self.home / "projects/project/replacement.jsonl", [self.message("replacement", "shared"),
                                                                    self.message("replacement", "fresh")])
        first = self.observe("claude")
        result = self.observe("claude", "replacement", first["cursor"])
        self.assertEqual(sum(row["total_tokens"] for row in result["sessions"]), 146)
        self.assertEqual(len(result["sessions"]), 2)

    def test_incomplete_append_and_budget_resume_without_counting_partial_json(self):
        path = self.rollout("owner", [self.response("owner", "response-1")])
        event = json.dumps(self.response("owner", "response-2"))
        with path.open("a") as stream:
            stream.write(event[:40])
        result = self.observe("codex")
        self.assertTrue(result["pending"])
        self.assertEqual(result["sessions"][0]["total_tokens"], 120)
        with path.open("a") as stream:
            stream.write(event[40:] + "\n")
        result = self.observe("codex", cursor=result["cursor"])
        self.assertFalse(result["pending"])
        self.assertEqual(result["sessions"][0]["total_tokens"], 240)

    def test_lost_or_truncated_logs_retain_observations_but_mark_partial(self):
        path = self.rollout("owner", [self.response("owner", "response-1")])
        result = self.observe("codex")
        path.unlink()
        lost = self.observe("codex", cursor=result["cursor"])
        self.assertEqual(lost["sessions"][0]["total_tokens"], 120)
        self.assertEqual(lost["sessions"][0]["status"], "partial")
        self.write(path, [self.response("owner", "response-1"), self.response("owner", "response-2")])
        replaced = self.observe("codex", cursor=lost["cursor"])
        self.assertEqual(replaced["sessions"][0]["total_tokens"], 240)
        self.assertEqual(replaced["sessions"][0]["status"], "partial")

    def test_missing_counters_are_unknown_never_zero(self):
        missing = self.observe("codex")
        self.assertIsNone(missing["sessions"][0]["total_tokens"])
        self.assertEqual(missing["sessions"][0]["status"], "unknown")
        event = self.message("owner", "partial", cache_read_input_tokens=None)
        self.write(self.home / "projects/project/owner.jsonl", [event])
        row = self.observe("claude")["sessions"][0]
        self.assertEqual(row["input_tokens"], 13)
        self.assertEqual(row["total_tokens"], 33)
        self.assertEqual((row["output_tokens"], row["status"]), (20, "partial"))

    def test_codex_legacy_thread_cumulative_snapshots_are_not_summed_across_turns(self):
        self.rollout("owner", [*self.snapshot(100), *self.snapshot(200, out=40, turn="turn-2"),
                                *self.snapshot(300, out=60, turn="turn-2")])
        self.assertEqual(self.observe("codex")["sessions"][0]["total_tokens"], 360)

    def test_persisted_sorted_cursor_does_not_reorder_legacy_turn_chronology(self):
        self.rollout("owner", [*self.snapshot(100, turn="z-first"), *self.snapshot(200, out=40, turn="a-second")])
        result = self.observe("codex")
        cursor = json.loads(json.dumps(result["cursor"], sort_keys=True))
        self.assertEqual(self.observe("codex", cursor=cursor)["sessions"][0]["total_tokens"], 240)

    def test_read_budget_yields_and_eventually_catches_up(self):
        path = self.rollout("owner", [self.response("owner", f"response-{n}") for n in range(10)])
        first = self.observe("codex", max_bytes=700)
        self.assertTrue(first["pending"])
        self.assertLess(first["cursor"]["files"][str(path)]["offset"], path.stat().st_size)
        result = first
        for _ in range(20):
            result = self.observe("codex", cursor=result["cursor"], max_bytes=700)
            if not result["pending"]:
                break
        self.assertFalse(result["pending"])
        self.assertEqual(result["sessions"][0]["total_tokens"], 1200)

    def test_complete_modern_thread_evidence_can_replace_ambiguous_provider_aggregate(self):
        jobs = self.tmp / "workers"
        self.write(jobs / "worker.json", [{"session_id": "owner", "engine": "codex"}])
        self.write(jobs / "worker.stdout.jsonl", [{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}])
        first = self.observe("codex", job_root=jobs)
        response = self.response("owner", "request", inp=200)
        response["payload"]["thread_token_usage"] = response["payload"]["usage"].copy()
        self.rollout("owner", [response])
        engines._TOKEN_ROLLOUT_INDEX.clear()
        row = self.observe("codex", cursor=first["cursor"], job_root=jobs)["sessions"][0]
        self.assertEqual((row["role"], row["total_tokens"]), ("owner", 220))

    def test_later_provider_aggregate_does_not_erase_larger_disjoint_helper_usage(self):
        self.rollout("owner", [self.response("owner", "parent")])
        self.rollout("helper", [self.response("helper", "child", inp=50)], parent="owner")
        first = self.observe("codex")
        self.assertEqual(sum(row["total_tokens"] for row in first["sessions"]), 190)
        jobs = self.tmp / "workers"
        self.write(jobs / "worker.json", [{"session_id": "owner", "engine": "codex"}])
        self.write(jobs / "worker.stdout.jsonl", [{"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 20}}])
        result = self.observe("codex", cursor=first["cursor"], job_root=jobs)
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(sum(row["total_tokens"] for row in result["sessions"]), 190)

    def test_thread_owner_counter_does_not_discard_larger_unsplit_provider_aggregate(self):
        jobs = self.tmp / "workers"
        self.write(jobs / "worker.json", [{"session_id": "owner", "engine": "codex"}])
        self.write(jobs / "worker.stdout.jsonl", [{"type": "turn.completed", "usage": {"input_tokens": 140, "output_tokens": 20}}])
        first = self.observe("codex", job_root=jobs)
        response = self.response("owner", "request")
        response["payload"]["thread_token_usage"] = response["payload"]["usage"].copy()
        self.rollout("owner", [response])
        engines._TOKEN_ROLLOUT_INDEX.clear()
        row = self.observe("codex", cursor=first["cursor"], job_root=jobs)["sessions"][0]
        self.assertEqual((row["role"], row["total_tokens"]), ("provider", 160))

    def test_malformed_child_identity_never_discovers_unrelated_global_root(self):
        self.rollout("owner", [self.response("owner", "parent")])
        self.write(self.home / "sessions/2026/09/07/rollout-test-malformed.jsonl",
                   [{"type": "session_meta", "payload": {"parent_thread_id": "owner"}}])
        self.rollout("global", [self.response("global", "unrelated", inp=999999)])
        self.assertEqual([row["session_id"] for row in self.observe("codex")["sessions"]], ["owner"])

    def test_nested_legacy_helper_lower_bound_survives_new_parent_aggregate(self):
        self.rollout("owner", [self.response("owner", "parent")])
        self.rollout("helper", self.snapshot(280), parent="owner")
        first = self.observe("codex")
        self.assertEqual(sum(row["total_tokens"] for row in first["sessions"]), 420)
        jobs = self.tmp / "workers"
        self.write(jobs / "worker.json", [{"session_id": "owner", "engine": "codex"}])
        self.write(jobs / "worker.stdout.jsonl", [{"type": "turn.completed", "usage": {"input_tokens": 330, "output_tokens": 20}}])
        result = self.observe("codex", cursor=first["cursor"], job_root=jobs)
        self.assertEqual(sum(row["total_tokens"] for row in result["sessions"]), 420)

    def test_claude_owned_result_fallback_deduplicates_and_ignores_wrong_session(self):
        jobs = self.tmp / "workers"
        self.write(jobs / "worker.json", [{"session_id": "owner", "engine": "claude"}])
        result = {"type": "result", "session_id": "owner", "usage": self.message("owner", "msg")["message"]["usage"]}
        wrong = {"type": "result", "session_id": "unrelated", "usage": {"input_tokens": 999999, "output_tokens": 10}}
        self.write(jobs / "worker.stdout.jsonl", [result, result, wrong])
        first = self.observe("claude", job_root=jobs)
        row = first["sessions"][0]
        self.assertEqual((row["total_tokens"], row["role"], row["status"]), (73, "provider", "partial"))
        self.assertEqual(self.observe("claude", cursor=first["cursor"], job_root=jobs)["sessions"], first["sessions"])

    def test_claude_messages_and_helpers_cover_result_without_adding_it_again(self):
        path = self.home / "projects/project/owner.jsonl"
        self.write(path, [self.message("owner", "owner-message")])
        self.write(path.with_suffix("") / "subagents/agent-helper.jsonl", [self.message("owner", "helper-message", agent="helper")])
        jobs = self.tmp / "workers"
        self.write(jobs / "worker.json", [{"session_id": "owner", "engine": "claude"}])
        usage = self.message("owner", "msg", inp=10)["message"]["usage"]
        self.write(jobs / "worker.stdout.jsonl", [{"type": "result", "session_id": "owner", "usage": usage}])
        rows = self.observe("claude", job_root=jobs)["sessions"]
        self.assertEqual([(row["role"], row["total_tokens"]) for row in rows], [("owner", 73), ("delegated", 73)])

    def test_registered_owner_retains_provider_recorded_parentage(self):
        self.rollout("owner", [self.response("owner", "response")], parent="prior-owner", fork=True)
        row = self.observe("codex")["sessions"][0]
        self.assertEqual(row["parent_session_id"], "prior-owner")

    def test_boolean_reasoning_is_not_a_numeric_counter(self):
        event = self.message("owner", "message", output_tokens_details={"thinking_tokens": True})
        self.write(self.home / "projects/project/owner.jsonl", [event])
        self.assertIsNone(self.observe("claude")["sessions"][0]["reasoning_tokens"])

    def test_larger_legacy_snapshot_survives_partial_response_records_of_same_turn(self):
        path = self.rollout("owner", self.snapshot(180))
        first = self.observe("codex")
        self.assertEqual(first["sessions"][0]["total_tokens"], 200)
        self.write(path, [self.response("owner", "partial-response", inp=40, out=10)], append=True)
        row = self.observe("codex", cursor=first["cursor"])["sessions"][0]
        self.assertEqual((row["role"], row["total_tokens"]), ("provider", 200))

    def test_parentage_controls_aggregation_when_child_owner_registered_first(self):
        self.rollout("owner", self.snapshot(480))
        self.rollout("helper", [self.response("helper", "response", inp=50)], parent="owner")
        first = self.observe("codex", "helper")
        self.assertEqual(first["sessions"][0]["total_tokens"], 70)
        rows = self.observe("codex", "owner", first["cursor"])["sessions"]
        self.assertEqual([(row["session_id"], row["total_tokens"]) for row in rows], [("owner", 500)])

    def test_missing_later_output_retains_known_output_and_marks_partial(self):
        first, second = self.response("owner", "first"), self.response("owner", "second", inp=50)
        second["payload"]["usage"].pop("output_tokens")
        self.rollout("owner", [first, second])
        row = self.observe("codex")["sessions"][0]
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["total_tokens"]), (150, 20, 170))
        self.assertEqual(row["status"], "partial")

    def test_claude_all_zero_synthetic_usage_does_not_claim_a_zero_total(self):
        event = self.message("owner", "synthetic", inp=0, out=0, cache_read_input_tokens=0,
                             cache_creation_input_tokens=0, output_tokens_details={})
        self.write(self.home / "projects/project/owner.jsonl", [event])
        self.assertIsNone(self.observe("claude")["sessions"][0]["total_tokens"])

    def test_no_model_calls_and_cursor_has_no_transcript_text(self):
        event = self.message("owner", "msg")
        event["message"]["content"] = [{"text": "private transcript must not persist"}]
        self.write(self.home / "projects/project/owner.jsonl", [event])
        with mock.patch.object(engines.subprocess, "run", side_effect=AssertionError("no subprocess")), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=AssertionError("no subprocess")):
            result = self.observe("claude")
        self.assertNotIn("private transcript", json.dumps(result))
