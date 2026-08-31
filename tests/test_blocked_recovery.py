"""Evidence-driven blocked-task reconciliation is batched, durable, and bounded."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_TMP = Path(tempfile.mkdtemp(prefix="altitude-blocked-recovery-"))
os.environ.setdefault("ALTITUDE_HOME", str(_TMP))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, server, state as S, status as task_status, tasks as T  # noqa: E402

PROJECT = "blocked-recovery"


class TestBlockedRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        projects = config.load_projects(); projects[PROJECT] = {"name": PROJECT, "path": str(_TMP), "stacks": ["python"]}
        config.save_projects(projects)

    def setUp(self):
        (config.MONITOR_DIR / "recovery-breaker.json").unlink(missing_ok=True)
        path = server._blocked_recovery_path(PROJECT)
        if path.exists():
            path.unlink()
        self.slugs = []
        self.external = {}

    def tearDown(self):
        for slug in self.slugs:
            try:
                task = S.load_task(PROJECT, slug)
            except KeyError:
                continue
            if task.get("state") == "blocked":
                T.reject(PROJECT, slug, "test cleanup")
            elif task.get("state") == "parked":
                T.reject(PROJECT, slug, "test cleanup")

    def task(self, slug, *, reason="worker stopped", handled=None, report=None, session=False):
        self.slugs.append(slug)
        d = S.task_dir(PROJECT, slug); d.mkdir(parents=True, exist_ok=True)
        if report is not None:
            (d / "report.json").write_text(report)
        wt = _TMP / f"wt-{slug}"
        if session:
            wt.mkdir(exist_ok=True)
        task = {"slug": slug, "title": slug, "class": "S", "state": "blocked", "created": S.now(),
                "updated": S.now(), "blocked_reason": reason, "l3_handled": handled, "envelope": {}, "prs": [],
                "dispatch_id": f"{slug}-1", "session_id": f"sid-{slug}" if session else None,
                "worktree": str(wt) if session else None}
        S.save_task(PROJECT, task)
        self.external[slug] = {"head": None, "checks": 0}
        return task

    def status(self, project, slug):
        task = S.load_task(project, slug)
        report = S.task_dir(project, slug) / "report.json"
        ext = self.external[slug]
        prs = [] if ext["head"] is None else [{"number": 50, "state": "OPEN", "merged": False,
                                                "head_sha": ext["head"], "merge_sha": None,
                                                "checks": {"total": ext["checks"], "passed": ext["checks"],
                                                           "failed": 0, "pending": 0, "failing": []}}]
        return {**task, "wip_hold": None,
                "gate": "local-suite",
                "repository": {"branch": "main", "dirty": False, "head": "base-head",
                               "origin_sha": "base-head", "ahead": 0, "behind": 0,
                               "determinate": True, "error": None},
                "report_json": {"exists": report.exists(), "sha256": "report-a" if report.exists() else None},
                "envelope_file": None, "counts": None, "l1_runs": {"in_flight": 0, "runs": []},
                "prs": prs, "main_run": None, "errors": []}

    def test_three_blockers_are_one_turn_and_only_explicit_escalation_reaches_inbox(self):
        for slug in ("batch-a", "batch-b", "batch-c"):
            self.task(slug)
        turns = []
        def turn(project, prompt, **kwargs):
            turns.append(prompt)
            self.assertTrue(kwargs["precheck"]())
            batch_id = server._blocked_record(PROJECT)["batch"]["id"]
            T.block(PROJECT, "batch-a", "still waiting on verified lease", actor="l3", recovery_batch=batch_id)
            T.needs_user(PROJECT, "batch-b", "Choose whether to grant a new production permission", actor="l3")
            T.reject(PROJECT, "batch-c", "duplicate task", actor="l3")
            return {"text": "triaged", "error": None}
        with patch.object(server.task_status, "status", side_effect=self.status), patch.object(server.l3, "turn", side_effect=turn):
            server.reconcile_blockers(PROJECT)
        self.assertEqual(len(turns), 1)
        self.assertTrue(all(slug in turns[0] for slug in ("batch-a", "batch-b", "batch-c")))
        self.assertEqual([row["slug"] for row in T.decisions(PROJECT)], ["batch-b"])
        self.assertEqual(S.load_task(PROJECT, "batch-a")["state"], "blocked")
        self.assertTrue(any(ev.get("kind") == "blocked-ack" for ev in S.read_events(PROJECT, "batch-a")))
        record = server._blocked_record(PROJECT)
        self.assertTrue(all(entry.get("handled_fingerprint") == entry.get("fingerprint")
                            for entry in record["tasks"].values()))

    def test_noop_is_backed_off_capped_and_rearmed_by_changed_pr_evidence(self):
        self.task("noop", handled=S.now(), report="{}")
        calls, faults = [], []
        def noop(*args, **kwargs):
            calls.append(1)
            self.assertTrue(kwargs["precheck"]())
            return {"text": "I looked", "error": None}
        with patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=noop), \
             patch.object(server.improve, "system_fault", side_effect=lambda *a, **kw: faults.append((a, kw))), \
             patch.object(config, "BLOCKED_RETRY_SECONDS", (-1, -1)), \
             patch.object(config, "BLOCKED_SCAN_SECONDS", 0):
            server.reconcile_blockers(PROJECT)
            server.reconcile_blockers(PROJECT)
            server.reconcile_blockers(PROJECT)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(faults), 1)
            self.external["noop"]["head"] = "new-head"
            server.reconcile_blockers(PROJECT)
        self.assertEqual(len(calls), 3, "new PR evidence resets the exhausted fingerprint")
        self.assertEqual(server._blocked_record(PROJECT)["tasks"]["noop"]["failures"], 1)
        self.assertNotIn("noop", [row["slug"] for row in T.decisions(PROJECT)])

    def test_temporary_wait_stays_blocked_and_rearms_only_on_changed_evidence(self):
        self.task("waiting", reason="active file lease")
        calls = []
        def acknowledge(*args, **kwargs):
            calls.append(args[1])
            self.assertIn("never park it", args[1])
            batch_id = server._blocked_record(PROJECT)["batch"]["id"]
            T.block(PROJECT, "waiting", "active file lease", actor="l3", recovery_batch=batch_id)
            return {"text": "waiting on the verified lease", "error": None}
        with patch.object(config, "BLOCKED_SCAN_SECONDS", 0), \
             patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=acknowledge):
            server.reconcile_blockers(PROJECT)
            server.reconcile_blockers(PROJECT)
            self.external["waiting"]["head"] = "new-head"
            server.reconcile_blockers(PROJECT)
        self.assertEqual(len(calls), 2, "unchanged acknowledged evidence waits; a changed PR re-arms it")
        self.assertEqual(S.load_task(PROJECT, "waiting")["state"], "blocked")
        entry = server._blocked_record(PROJECT)["tasks"]["waiting"]
        self.assertEqual(entry.get("handled_fingerprint"), entry.get("fingerprint"))
        self.assertEqual(entry.get("failures"), 0)

    def test_operational_park_can_be_returned_to_blocked_watch(self):
        self.task("misparked", reason="sandbox repair")
        T.park(PROJECT, "misparked", "temporary prerequisite", actor="l3")
        T.block(PROJECT, "misparked", "sandbox repair", actor="altd")
        task = S.load_task(PROJECT, "misparked")
        self.assertEqual(task["state"], "blocked")
        self.assertIsNone(task.get("needs_user"))

    def test_reconcile_cannot_park_a_blocked_task(self):
        self.task("no-reconcile-park", reason="temporary lease")
        with self.assertRaises(T.TransitionError):
            T.park(PROJECT, "no-reconcile-park", "wait", actor="l3", trigger="reconcile")
        self.assertEqual(S.load_task(PROJECT, "no-reconcile-park")["state"], "blocked")

    def test_wrong_batch_ack_is_not_accepted(self):
        self.task("wrong-ack", reason="worker prerequisite")
        def wrong(*args, **kwargs):
            T.block(PROJECT, "wrong-ack", "still waiting", actor="l3", recovery_batch="not-this-batch")
            return {"text": "acknowledged the wrong generation", "error": None}
        with patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=wrong):
            server.reconcile_blockers(PROJECT)
        entry = server._blocked_record(PROJECT)["tasks"]["wrong-ack"]
        self.assertIsNone(entry.get("handled_fingerprint"))
        self.assertEqual(entry.get("failures"), 1)

    def test_fresh_scan_is_durable_for_the_five_minute_interval(self):
        self.task("scan-cadence")
        statuses = []
        def counted_status(project, slug):
            statuses.append(slug)
            return self.status(project, slug)
        def acknowledge(*args, **kwargs):
            batch_id = server._blocked_record(PROJECT)["batch"]["id"]
            T.block(PROJECT, "scan-cadence", "still waiting", actor="l3", recovery_batch=batch_id)
            return {"text": "acknowledged", "error": None}
        with patch.object(server.task_status, "status", side_effect=counted_status), \
             patch.object(server.l3, "turn", side_effect=acknowledge):
            server.reconcile_blockers(PROJECT)
            server.reconcile_blockers(PROJECT)
        self.assertEqual(statuses, ["scan-cadence"], "fresh durable evidence is not rescanned before five minutes")

    def test_batch_is_bounded_to_eight_tasks(self):
        for index in range(10):
            self.task(f"bounded-{index:02d}")
        seen = []
        def acknowledge(*args, **kwargs):
            batch = server._blocked_record(PROJECT)["batch"]
            seen.extend(item["slug"] for item in batch["items"])
            for item in batch["items"]:
                T.block(PROJECT, item["slug"], "still waiting", actor="l3", recovery_batch=batch["id"])
            return {"text": "acknowledged", "error": None}
        with patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=acknowledge):
            server.reconcile_blockers(PROJECT)
        self.assertEqual(len(seen), 8)

    def test_concurrent_reconcilers_claim_one_batch(self):
        self.task("concurrent")
        entered, release, calls = threading.Event(), threading.Event(), []
        def slow(*args, **kwargs):
            calls.append(1); entered.set(); release.wait(5)
            return {"text": "no disposition", "error": None}
        with patch.object(server.task_status, "status", side_effect=self.status), patch.object(server.l3, "turn", side_effect=slow):
            worker = threading.Thread(target=server.reconcile_blockers, args=(PROJECT,)); worker.start()
            self.assertTrue(entered.wait(5))
            server.reconcile_blockers(PROJECT)
            release.set(); worker.join(5)
        self.assertEqual(calls, [1])

    def test_stale_batch_honors_durable_ack_written_before_restart(self):
        self.task("restart-ack", reason="verified lease still active")
        calls, faults = [], []

        def ack_then_crash(*args, **kwargs):
            calls.append(1)
            batch_id = server._blocked_record(PROJECT)["batch"]["id"]
            T.block(PROJECT, "restart-ack", "still active", actor="l3", recovery_batch=batch_id)
            raise KeyboardInterrupt("simulated daemon exit after durable ack")

        with patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=ack_then_crash), \
             patch.object(server.improve, "system_fault", side_effect=lambda *a, **kw: faults.append((a, kw))):
            with self.assertRaises(KeyboardInterrupt):
                server.reconcile_blockers(PROJECT)
            record = server._blocked_record(PROJECT)
            record["batch"].update({"owner_pid": None, "engine_pid": None, "started": "2000-01-01T00:00:00+00:00"})
            S.write_json(server._blocked_recovery_path(PROJECT), record)
            server.reconcile_blockers(PROJECT)

        self.assertEqual(calls, [1], "the durable ack must settle the orphaned batch without another L3 turn")
        entry = server._blocked_record(PROJECT)["tasks"]["restart-ack"]
        self.assertEqual(entry.get("handled_fingerprint"), entry.get("fingerprint"))
        self.assertEqual(entry.get("failures"), 0)
        self.assertEqual(faults, [])

    def test_stale_batch_honors_durable_state_disposition_written_before_restart(self):
        self.task("restart-disposition", reason="duplicate work")
        calls, faults = [], []

        def reject_then_crash(*args, **kwargs):
            calls.append(1)
            T.reject(PROJECT, "restart-disposition", "duplicate task", actor="l3")
            raise KeyboardInterrupt("simulated daemon exit after durable disposition")

        with patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=reject_then_crash), \
             patch.object(server.improve, "system_fault", side_effect=lambda *a, **kw: faults.append((a, kw))):
            with self.assertRaises(KeyboardInterrupt):
                server.reconcile_blockers(PROJECT)
            record = server._blocked_record(PROJECT)
            record["batch"].update({"owner_pid": None, "engine_pid": None, "started": "2000-01-01T00:00:00+00:00"})
            S.write_json(server._blocked_recovery_path(PROJECT), record)
            server.reconcile_blockers(PROJECT)

        self.assertEqual(calls, [1])
        self.assertEqual(S.load_task(PROJECT, "restart-disposition")["state"], "rejected")
        self.assertIsNone(server._blocked_record(PROJECT).get("batch"))
        self.assertEqual(faults, [])


    def test_stale_batch_precheck_skips_without_counting_a_failure(self):
        self.task("stale")
        def stale_turn(*args, **kwargs):
            with S.project_lock(PROJECT):
                task = S.load_task(PROJECT, "stale"); task["dispatch_id"] = "new-generation"; S.save_task(PROJECT, task)
            return {"skipped": not kwargs["precheck"](), "text": "", "error": None}
        with patch.object(server.task_status, "status", side_effect=self.status), patch.object(server.l3, "turn", side_effect=stale_turn):
            server.reconcile_blockers(PROJECT)
        entry = server._blocked_record(PROJECT)["tasks"]["stale"]
        self.assertEqual(entry.get("failures"), 0)
        self.assertIsNone(server._blocked_record(PROJECT).get("batch"))

    def test_scan_exception_closes_claim_and_can_retry(self):
        self.task("scan-error")
        with patch.object(server.task_status, "status", side_effect=RuntimeError("gh exploded")):
            server.reconcile_blockers(PROJECT)
        scan = server._blocked_record(PROJECT)["scan"]
        self.assertIsNone(scan.get("token")); self.assertTrue(scan.get("completed")); self.assertIn("gh exploded", scan.get("last_error", ""))
        calls = []
        with patch.object(config, "BLOCKED_SCAN_SECONDS", 0), \
             patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=lambda *a, **kw: calls.append(1) or {"text": "no-op", "error": None}):
            server.reconcile_blockers(PROJECT)
        self.assertEqual(calls, [1])

    def test_engine_exception_clears_batch_and_is_bounded(self):
        self.task("engine-error")
        faults = []
        with patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=RuntimeError("engine crashed")), \
             patch.object(server.improve, "system_fault", side_effect=lambda *a, **kw: faults.append((a, kw))), \
             patch.object(config, "BLOCKED_RETRY_SECONDS", (-1, -1)):
            server.reconcile_blockers(PROJECT)
            self.assertIsNone(server._blocked_record(PROJECT).get("batch"))
            server.reconcile_blockers(PROJECT)
            server.reconcile_blockers(PROJECT)
        self.assertEqual(server._blocked_record(PROJECT)["tasks"]["engine-error"]["failures"], 2)
        self.assertEqual(len(faults), 1)

    def test_skipped_precheck_invalidates_scan_immediately(self):
        self.task("skip-retry")
        statuses, turns = [], []
        def counted_status(project, slug):
            statuses.append(slug); return self.status(project, slug)
        def turn(*args, **kwargs):
            turns.append(1)
            if len(turns) == 1:
                with S.project_lock(PROJECT):
                    task = S.load_task(PROJECT, "skip-retry"); task["dispatch_id"] = "changed"; S.save_task(PROJECT, task)
                return {"skipped": not kwargs["precheck"](), "text": "", "error": None}
            return {"text": "no disposition", "error": None}
        with patch.object(server.task_status, "status", side_effect=counted_status), patch.object(server.l3, "turn", side_effect=turn):
            server.reconcile_blockers(PROJECT)
            server.reconcile_blockers(PROJECT)
        self.assertEqual((len(statuses), len(turns)), (2, 2))

    def test_routine_recovery_owns_worker_block_before_l3(self):
        self.task("routine", reason="L2 session died before reporting (worker changes every time)", session=True)
        with patch.object(server.task_status, "status", side_effect=self.status), \
             patch.object(server.l3, "turn", side_effect=AssertionError("L3 must not race deterministic recovery")):
            server.reconcile_blockers(PROJECT)
        self.assertIsNone(server._blocked_record(PROJECT).get("batch"))

    def test_fingerprint_ignores_volatile_details_but_tracks_pr_head(self):
        base = self.status(PROJECT, self.task("fingerprint")["slug"])
        pr = {"number": 1, "state": "OPEN", "merged": False, "head_sha": "a", "base_sha": "base-a",
              "merge_sha": None, "mergeable": "MERGEABLE", "merge_state_status": "CLEAN",
              "review_decision": "APPROVED",
              "checks": {"total": 1, "passed": 1, "failed": 0, "rejected": 0,
                         "pending": 0, "failing": [], "rejecting": []}}
        base.update({"updated": "old", "hold": {"at": "old"}, "other_leases": [{"slug": "x"}],
                     "errors": ["prs: transient one"], "prs": [pr]})
        changed_noise = {**base, "updated": "new", "hold": {"at": "new"}, "other_leases": [],
                         "errors": ["prs: transient two"]}
        baseline = server._blocked_fingerprint(server._blocked_evidence(base))
        self.assertEqual(baseline, server._blocked_fingerprint(server._blocked_evidence(changed_noise)))
        for field, value in (
            ("head_sha", "b"),
            ("base_sha", "base-b"),
            ("mergeable", "CONFLICTING"),
            ("merge_state_status", "DIRTY"),
            ("review_decision", "CHANGES_REQUESTED"),
        ):
            changed_pr = {**base, "prs": [{**pr, field: value}]}
            self.assertNotEqual(
                baseline, server._blocked_fingerprint(server._blocked_evidence(changed_pr)), field)

    def test_success_to_terminal_nonpass_changes_fingerprint_once_not_per_poll(self):
        base = self.status(PROJECT, self.task("check-fingerprint")["slug"])
        pr = {"number": 1, "state": "OPEN", "merged": False, "head_sha": "head", "base_sha": "base",
              "merge_sha": None, "mergeable": "MERGEABLE", "merge_state_status": "CLEAN",
              "review_decision": "APPROVED"}

        def fingerprint(conclusion, **noise):
            check = {"name": "ci", "status": "COMPLETED", "conclusion": conclusion, **noise}
            raw = {**base, "prs": [{**pr, "checks": task_status._check_summary([check])}]}
            return server._blocked_fingerprint(server._blocked_evidence(raw))

        success = fingerprint("SUCCESS", completedAt="2026-08-31T01:00:00Z")
        skipped = fingerprint("SKIPPED", completedAt="2026-08-31T01:01:00Z", detailsUrl="first")
        skipped_poll = fingerprint("SKIPPED", completedAt="2026-08-31T02:00:00Z", detailsUrl="second")
        neutral = fingerprint("NEUTRAL", completedAt="2026-08-31T03:00:00Z", detailsUrl="third")

        self.assertNotEqual(success, skipped)
        self.assertEqual(skipped, skipped_poll)
        self.assertEqual(skipped, neutral)

    def test_merge_request_poll_churn_does_not_change_blocked_fingerprint(self):
        base = self.status(PROJECT, self.task("merge-poll-fingerprint")["slug"])
        merge = {"pr": 71, "state": "waiting", "generation": "merge-g1",
                 "dispatch_id": "merge-poll-fingerprint-1", "task_attempt": 1,
                 "branch": "worktree-merge", "base": "main", "base_sha": "a" * 40,
                 "head_sha": "b" * 40, "candidate_tree": "c" * 40,
                 "gate_mode": None, "candidate_gate": None, "result": None,
                 "attempts": 1, "terminal_attempts": 0,
                 "retry_after": "2026-08-31T01:00:00+00:00", "last_error": "checks are pending",
                 "updated_at": "2026-08-31T00:59:00+00:00",
                 "claim": {"generation": "claim-1", "owner_pid": 100}}
        base["merge_requests"] = [merge]
        baseline = server._blocked_fingerprint(server._blocked_evidence(base))
        churned = {**base, "merge_requests": [{**merge, "state": "processing", "attempts": 28,
                                                "retry_after": "2026-08-31T02:00:00+00:00",
                                                "last_error": "temporary GitHub outage",
                                                "updated_at": "2026-08-31T01:59:00+00:00",
                                                "claim": {"generation": "claim-28", "owner_pid": 200}}]}
        self.assertEqual(baseline, server._blocked_fingerprint(server._blocked_evidence(churned)))

        for changed in (
            {**merge, "state": "merged", "result": {"merge_sha": "d" * 40}},
            {**merge, "generation": "merge-g2"},
            {**merge, "head_sha": "e" * 40},
            {**merge, "gate_mode": "local-suite",
             "candidate_gate": {"sandboxed": True, "passed": True, "head_sha": "b" * 40}},
        ):
            evidence = {**base, "merge_requests": [changed]}
            self.assertNotEqual(baseline, server._blocked_fingerprint(server._blocked_evidence(evidence)))

    def test_fingerprint_tracks_gate_and_repository_state(self):
        base = self.status(PROJECT, self.task("repository-fingerprint")["slug"])
        baseline = server._blocked_fingerprint(server._blocked_evidence(base))
        for changed in (
            {**base, "gate": "github-actions"},
            {**base, "repository": {**base["repository"], "branch": "topic"}},
            {**base, "repository": {**base["repository"], "dirty": True}},
            {**base, "repository": {**base["repository"], "head": "new-head"}},
            {**base, "repository": {**base["repository"], "origin_sha": "new-origin"}},
            {**base, "repository": {**base["repository"], "ahead": 1}},
            {**base, "repository": {**base["repository"], "behind": 1}},
            {**base, "repository": {**base["repository"], "determinate": False}},
            {**base, "repository": {**base["repository"], "error": "cannot inspect"}},
        ):
            self.assertNotEqual(baseline, server._blocked_fingerprint(server._blocked_evidence(changed)))

    def test_status_hash_changes_when_report_content_changes(self):
        task = self.task("report-hash", report='{"a":1}')
        first = task_status.status(PROJECT, task["slug"])["report_json"]["sha256"]
        report = S.task_dir(PROJECT, task["slug"]) / "report.json"
        report.write_text('{"b":2}')
        second = task_status.status(PROJECT, task["slug"])["report_json"]["sha256"]
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
