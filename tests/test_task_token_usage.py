"""Task accounting persists evidence, never depends on a worker's claimed spend or a UI poll."""
import json
from unittest import mock

from tests.support import AltitudeCase
from altitude import dispatch, engines, server, state as S, status, tasks as T, usage


def reading(session, total=120, *, parent=None, role="owner", state="observed"):
    return {"session_id": session, "parent_session_id": parent, "role": role, "status": state,
            "input_tokens": total - 20, "output_tokens": 20, "total_tokens": total,
            "cache_read_tokens": 30, "cache_write_tokens": None, "reasoning_tokens": 10,
            "observed_at": "2026-09-07T10:00:00+00:00", "notes": []}


class TaskTokenUsage(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.slug = T.new(self.project, "Token usage", "Count local evidence")["slug"]
        self.observations = {}
        self.native_observe = engines.observe_token_usage
        self.observe = self.patch(engines, "observe_token_usage", side_effect=self.collect, create=True)
        self.patch(engines, "remove_l2_worker", return_value="stopped")

    def collect(self, engine, session, cursor=None, **kwargs):
        cursor = dict(cursor or {})
        cursor[session] = self.observations.get((engine, session), [reading(session)])
        return {"cursor": cursor, "sessions": [row for rows in cursor.values() for row in rows],
                "status": "partial", "notes": ["Only attributable helpers are included."], "pending": False}

    def launch(self, session="first", engine="one", attempt=1):
        return T.dispatch(self.project, self.slug, session_id=session, agent_id="worker", attempt=attempt,
                          l2_engine=engine, worktree=None, branch=None)

    def test_live_collection_is_throttled_and_reads_are_passive(self):
        self.launch()
        first = usage.refresh(self.project, self.slug)["token_usage"]
        self.assertEqual(first["total_tokens"], 120)
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["cache_read_tokens"], 30)
        self.assertEqual(first["input_tokens"] + first["output_tokens"], 120)
        calls = self.observe.call_count
        usage.refresh(self.project, self.slug)
        self.patch(server.monitor, "sessions", return_value=[])
        self.assertEqual(server.task_view(self.project, self.slug)["token_usage"], first)
        self.assertEqual(status.status(self.project, self.slug)["token_usage"], first)
        self.assertEqual(self.observe.call_count, calls)
        self.observations[("one", "first")] = [reading("first", 240)]
        with mock.patch.object(S, "now", return_value="2099-01-01T00:00:00+00:00"):
            second = usage.refresh(self.project, self.slug)["token_usage"]
        self.assertEqual(second["total_tokens"], 240)

    def test_refresh_preserves_task_activity_time(self):
        self.launch()
        blocked = T.block(self.project, self.slug, "waiting for an answer")
        with mock.patch.object(S, "now", return_value="2099-01-01T00:00:00+00:00"):
            refreshed = usage.refresh(self.project, self.slug)
        self.assertEqual(refreshed["updated"], blocked["updated"])
        self.assertEqual(S.load_task(self.project, self.slug)["updated"], blocked["updated"])

    def test_invalid_collection_time_is_retried_without_a_task_fault(self):
        task = self.launch()
        task["token_usage"] = {"checked_at": "unreadable timestamp"}
        S.save_task(self.project, task)
        refreshed = usage.refresh(self.project, self.slug)
        self.assertEqual(refreshed["state"], "running")
        self.assertEqual(refreshed["token_usage"]["total_tokens"], 120)

    def test_resume_restart_and_engine_handoff_keep_all_identities_once(self):
        self.launch()
        usage.refresh(self.project, self.slug)
        T.block(self.project, self.slug, "pause")
        T.resume(self.project, self.slug, agent_id="replacement", session_id="first")
        T.block(self.project, self.slug, "pause")
        T.resume(self.project, self.slug, agent_id="replacement-2", session_id="second")
        T.block(self.project, self.slug, "window ended")
        T.requeue(self.project, self.slug, engine="two", clear_worker=True)
        self.launch("third", "two", 2)
        self.observations[("two", "third")] = [reading("third", 220), reading("helper", 50, parent="third", role="delegated")]
        task = S.load_task(self.project, self.slug)
        usage.capture(self.project, task)
        self.assertEqual(task["token_usage"]["total_tokens"], 510)
        self.assertEqual(len(task["token_usage_sessions"]), 3)
        self.assertEqual({row["engine"] for row in task["token_usage"]["sessions"]}, {"one", "two"})
        helper = next(row for row in task["token_usage"]["sessions"] if row["role"] == "delegated")
        self.assertEqual(helper["attempt"], 2)
        # Reloading the on-disk cursor, as a restarted daemon does, is idempotent.
        usage.capture(self.project, task)
        self.assertEqual(task["token_usage"]["total_tokens"], 510)

    def test_final_refresh_archive_and_report_survive_log_cleanup(self):
        self.launch()
        T.report(self.project, self.slug, {"verdict": "ok", "prs": [], "spend": {"tokens": 999999}})
        self.observations[("one", "first")] = [reading("first", 500)]
        final = T.done(self.project, self.slug, "Delivered")["token_usage"]
        self.assertEqual(final["total_tokens"], 500)
        self.assertIsNotNone(final["finalized_at"])
        self.assertTrue((S.archive_dir(self.project) / self.slug / "token-usage.json").exists())
        self.observe.side_effect = OSError("provider logs cleaned")
        self.assertEqual(usage.refresh(self.project, self.slug)["token_usage"], final)
        self.assertEqual(status.task_report(self.project, self.slug)["token_usage"], final)
        result = self.alt("task", "report", self.slug, "--json", env={"ALTITUDE_PROJECT": self.project})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"total_tokens": 500', result.stdout)

    def test_missing_historical_attempt_is_partial_not_zero(self):
        self.launch(attempt=3)
        self.observe.return_value = None
        task = usage.refresh(self.project, self.slug)
        self.assertIn("Earlier attempt identities or usage are unavailable.", task["token_usage"]["notes"])
        self.assertEqual(task["token_usage"]["total_tokens"], 120)
        self.assertEqual(task["token_usage"]["status"], "partial")

    def test_collection_failure_preserves_evidence_and_does_not_block_finalization(self):
        self.launch()
        first = usage.refresh(self.project, self.slug)["token_usage"]
        self.observe.side_effect = PermissionError("private path must never be exposed")
        T.report(self.project, self.slug, {"verdict": "ok"})
        final = T.done(self.project, self.slug)["token_usage"]
        self.assertEqual(final["total_tokens"], first["total_tokens"])
        self.assertEqual(final["checked_at"], first["checked_at"])
        self.assertEqual(final["status"], "partial")
        self.assertNotIn("private path", str(final))
        self.assertIsNotNone(final["finalized_at"])

    def test_partially_known_counters_have_consistent_observed_sum(self):
        self.launch()
        self.observations[("one", "first")] = [reading("first"),
            {**reading("helper", parent="first", role="delegated", state="partial"),
             "output_tokens": None, "total_tokens": 100}]
        snapshot = usage.refresh(self.project, self.slug)["token_usage"]
        self.assertEqual(snapshot["input_tokens"], 200)
        self.assertEqual(snapshot["output_tokens"], 20)
        self.assertEqual(snapshot["total_tokens"], 220)
        self.assertEqual(snapshot["status"], "partial")

    def test_no_counters_is_unknown_and_rejection_collects_before_worker_removal(self):
        self.launch()
        self.observations[("one", "first")] = [{**reading("first"), **{key: None for key in usage.COUNTERS}, "status": "unknown"}]
        final = T.reject(self.project, self.slug, "cancel")["token_usage"]
        self.assertIsNone(final["total_tokens"])
        self.assertEqual(final["status"], "unknown")
        self.assertEqual(self.observe.call_count, 2)

    def test_daemon_polls_collect_but_not_archived_tasks(self):
        self.launch()
        self.patch(engines, "worker", return_value={"id": "worker", "state": "running"})
        self.patch(engines, "worker_detail", return_value=("", None))
        dispatch.poll(self.project)
        self.assertEqual(S.load_task(self.project, self.slug)["token_usage"]["total_tokens"], 120)
        T.report(self.project, self.slug, {"verdict": "ok"})
        T.done(self.project, self.slug)
        calls = self.observe.call_count
        dispatch.poll(self.project)
        self.assertEqual(self.observe.call_count, calls)

    def test_cumulative_task_consumption_is_never_context_occupancy(self):
        self.launch(engine="codex")
        self.patch(engines, "codex_worker", return_value={"usage": {"input_tokens": 5_000_000}})
        row = next(row for row in server.monitor.sessions() if row.get("slug") == self.slug)
        self.assertIsNone(row["context_percent"])

    def test_native_helper_audit_survives_attempts_passive_reads_failures_and_archive(self):
        home = self.tmp / "provider"
        self.patch(engines, "_TOKEN_ROLLOUT_INDEX", {})
        self.observe.side_effect = lambda engine, sid, cursor=None, **kw: self.native_observe(engine, sid, cursor, home=home, **kw)
        self.launch(engine="codex")
        for sid, parent in (("first", None), ("helper", "first"), ("nested", "helper")):
            path = home / f"sessions/2026/09/07/rollout-{sid}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid, "parent_thread_id": parent}}) + "\n")
        engines._TOKEN_ROLLOUT_INDEX.clear()
        task = S.load_task(self.project, self.slug)
        usage.capture(self.project, task)
        # Register the same concrete owner in another attempt: context expands, helper count does not.
        task["attempt"] = 2
        usage.capture(self.project, task)
        S.save_task(self.project, task)
        snapshot = task["token_usage"]
        helpers = snapshot["helpers"]
        self.assertEqual((helpers["observed_count"], helpers["direct_count"], helpers["descendant_count"]), (2, 1, 1))
        self.assertIsNone(helpers["total_tokens"])
        self.assertEqual([row["attempts"] for row in helpers["sessions"]], [[1, 2], [1, 2]])
        self.assertNotIn("Earlier attempt identities or usage are unavailable.", snapshot["notes"])
        calls = self.observe.call_count
        monitor_row = next(row for row in server.monitor.sessions() if row.get("slug") == self.slug)
        self.assertEqual(monitor_row["token_usage"], snapshot)
        self.assertEqual(server.task_view(self.project, self.slug)["token_usage"], snapshot)
        self.assertEqual(status.status(self.project, self.slug)["token_usage"], snapshot)
        self.assertEqual(self.observe.call_count, calls)
        self.observe.side_effect = OSError("unavailable local telemetry")
        T.report(self.project, self.slug, {"verdict": "ok", "spend": {"helpers": 9999}})
        final = T.done(self.project, self.slug)["token_usage"]
        self.assertEqual(final["helpers"], helpers)
        self.assertEqual(status.task_report(self.project, self.slug)["token_usage"], final)
        self.assertTrue((S.archive_dir(self.project) / self.slug / "token-usage.json").exists())

    def test_helper_request_summary_is_separate_from_task_and_unsplit_provider_totals(self):
        self.launch()
        owner = reading("first", total=1000, role="provider")
        helpers = [{**reading("helper", total=70, parent="first", role="delegated"),
                    "owner_session_id": "first", "depth": 1, "provider_total_tokens": 500},
                   {**reading("nested", total=50, parent="helper", role="delegated"),
                    "owner_session_id": "first", "depth": 2, "observed_at": "2026-09-07T11:00:00+00:00"}]
        self.observe.side_effect = lambda *args, **kw: {"cursor": {}, "sessions": [owner], "helpers": helpers,
                                                       "helper_status": "partial", "status": "partial"}
        task = S.load_task(self.project, self.slug)
        usage.capture(self.project, task)
        snapshot = task["token_usage"]
        self.assertEqual(snapshot["total_tokens"], 1000)
        self.assertEqual(snapshot["helpers"]["total_tokens"], 120)
        self.assertEqual(snapshot["helpers"]["observed_count"], 2)
        self.assertEqual(snapshot["helpers"]["sessions"][0]["provider_total_tokens"], 500)
        self.assertEqual(snapshot["observed_at"], "2026-09-07T11:00:00+00:00")

    def test_helper_unknown_and_observed_empty_are_distinct(self):
        task = S.load_task(self.project, self.slug)
        usage.capture(self.project, task)
        self.assertIsNone(task["token_usage"]["helpers"]["observed_count"])
        self.launch()
        task = S.load_task(self.project, self.slug)
        self.observe.side_effect = lambda *args, **kw: {"cursor": {}, "sessions": [], "helpers": [], "helper_status": "partial"}
        usage.capture(self.project, task)
        self.assertEqual(task["token_usage"]["helpers"]["observed_count"], 0)
        self.assertEqual(task["token_usage"]["helpers"]["status"], "partial")
        self.assertIsNone(task["token_usage"]["helpers"]["total_tokens"])

    def test_same_helper_identity_on_two_engines_and_unknown_depth_remain_distinct(self):
        self.launch()
        task = S.load_task(self.project, self.slug)
        usage.remember(task)
        task.update(l2_engine="two", session_id="second", attempt=2)
        def collect(engine, sid, cursor=None, **kwargs):
            helper = {**reading("same-id", parent=sid, role="delegated"), "owner_session_id": sid,
                      "depth": None, "parentage": "owner"}
            return {"cursor": {}, "sessions": [reading(sid), helper], "helpers": [helper], "helper_status": "partial"}
        self.observe.side_effect = collect
        usage.capture(self.project, task)
        helper_summary = task["token_usage"]["helpers"]
        self.assertEqual(helper_summary["observed_count"], 2)
        self.assertEqual(helper_summary["unclassified_count"], 2)
        self.assertIsNone(helper_summary["direct_count"])
        self.assertIsNone(helper_summary["descendant_count"])
        self.assertEqual(helper_summary["total_tokens"], 240)
        self.assertEqual([(row["engine"], row["attempts"]) for row in helper_summary["sessions"]],
                         [("one", [1]), ("two", [2])])
