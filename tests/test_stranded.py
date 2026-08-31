"""Blocked work is programmatically reconciled; only explicit L3 escalations reach Burak."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix="altitude-stranded-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, server, tasks as T  # noqa: E402

PROJECT = "stranded"


class TestStrandedReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        projects = config.load_projects(); projects[PROJECT] = {"name": PROJECT, "path": _TMP, "stacks": ["python"]}
        config.save_projects(projects)

    def setUp(self):
        p = server._blocked_recovery_path(PROJECT)
        if p.exists():
            p.unlink()

    def _task(self, slug, state="blocked", report=False, handled=None):
        d = S.task_dir(PROJECT, slug); d.mkdir(parents=True, exist_ok=True)
        if report:
            (d / "report.md").write_text("report\n")
            (d / "report.json").write_text(json.dumps({"landed": {}}))
        task = {"slug": slug, "title": slug, "class": "S", "state": state, "created": S.now(),
                "updated": S.now(), "attempt": 1, "blocked_reason": "worker stopped", "l3_handled": handled,
                "envelope": {}, "prs": []}
        S.save_task(PROJECT, task)
        def cleanup():
            if not S.status_path(PROJECT, slug).exists():
                return
            state = S.load_task(PROJECT, slug).get("state")
            if state == "reported":
                T.block(PROJECT, slug, "test cleanup")
                state = "blocked"
            if state in ("running", "blocked"):
                T.reject(PROJECT, slug, "test cleanup")
        self.addCleanup(cleanup)
        return task

    @staticmethod
    def clean_report(blocked=""):
        return {"landed": {"deploy": "not-applicable", "prs": [], "main_runs": []}, "blocked": blocked,
                "decisions": [], "fyi": [], "follow_ups": [], "review": []}

    @staticmethod
    def verified(verdict="ok", attempt=1, blocked=""):
        return {"verdict": verdict, "attempt": attempt, "problems": [], "signals": [], "spend": {}, "prs": [],
                "report": {"blocked": blocked}}

    def save_blocked(self, project, slug, report_text, verified, *, attempt=1, block_from="running"):
        d = S.task_dir(project, slug)
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.md").write_text("report\n")
        (d / "report.json").write_text(report_text)
        S.save_task(project, {"slug": slug, "title": slug, "class": "S", "state": "blocked", "created": S.now(),
                              "updated": S.now(), "attempt": attempt, "verified": verified, "l3_handled": None,
                              "blocked_reason": "stale block reason"})
        S.append_event(project, slug, "state", frm=block_from, to="blocked", by="test")

    def test_empty_or_nonblocked_project_does_not_spawn_a_sweep(self):
        calls = []
        with patch.object(server, "spawn", side_effect=lambda *args: calls.append(args) or True):
            with patch.object(S, "list_tasks", return_value=[]):
                server.resume_stranded_blockers(PROJECT)
            with patch.object(S, "list_tasks", return_value=[{"state": "requested"}, {"state": "running"}]):
                server.resume_stranded_blockers(PROJECT)
        self.assertEqual(calls, [])

    def test_durable_recovery_record_is_swept_without_a_live_blocker(self):
        S.write_json(server._blocked_recovery_path(PROJECT), {
            "version": 1,
            "tasks": {"old-blocker": {"fingerprint": "old"}},
        })
        calls = []
        with patch.object(S, "list_tasks", return_value=[]), \
             patch.object(server, "spawn", side_effect=lambda *args: calls.append(args) or True):
            server.resume_stranded_blockers(PROJECT)
        self.assertEqual(calls, [(f"blocked-sweep:{PROJECT}", server.reconcile_blockers, PROJECT)])

    def test_no_report_blockers_use_one_project_sweep(self):
        self._task("no-report")
        calls = []
        with patch.object(server, "spawn", side_effect=lambda key, fn, *a: calls.append((key, fn.__name__)) or True):
            server.resume_stranded_blockers(PROJECT)
        self.assertEqual(calls, [(f"blocked-sweep:{PROJECT}", "reconcile_blockers")])

    def test_blocked_reports_do_not_race_report_turn(self):
        self._task("reported", state="reported", report=True)
        self._task("blocked-report", state="blocked", report=True)
        calls = []
        with patch.object(server, "spawn", side_effect=lambda key, fn, *a: calls.append((key, fn.__name__)) or True):
            server.resume_stranded_reports(PROJECT)
        self.assertEqual(calls, [(f"finished:{PROJECT}:reported", "report_turn")])

    def test_fresh_blocked_report_uses_coordinator_not_report_turn(self):
        task = self._task("fresh-blocked-report", state="running", report=True)
        verdict = {"verdict": "blocked", "problems": [], "signals": [], "spend": {}, "prs": [],
                   "report": {"blocked": "PR needs remediation"}}
        with patch.object(server.verify, "verify", return_value=verdict), \
             patch.object(server, "report_turn", side_effect=AssertionError("blocked report must not take report-turn path")):
            server.on_l2_finished(PROJECT, {"task": task, "agent": {}})
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "blocked")


    def _nonclean_verdict(self):
        return {"verdict": "contradicted", "problems": ["report needs a disposition"], "signals": [],
                "spend": {}, "prs": [], "report": {}}

    def test_report_turn_noop_remains_unhandled_and_obeys_backoff(self):
        task = self._task("report-noop-retry", state="reported", report=True)
        with patch.object(server.improve, "index", return_value=[]), \
             patch.object(server.l3, "turn", return_value={"text": "looked", "error": None}):
            server.report_turn(PROJECT, task, self._nonclean_verdict())
        live = S.load_task(PROJECT, task["slug"])
        self.assertEqual(live["state"], "reported")
        self.assertIsNone(live.get("l3_handled"))
        calls = []
        with patch.object(server, "spawn", side_effect=lambda key, fn, *a: calls.append(key) or True):
            server.resume_stranded_reports(PROJECT)
        self.assertNotIn("finished:{}:{}".format(PROJECT, task["slug"]), calls)
        with S.project_lock(PROJECT):
            live = S.load_task(PROJECT, task["slug"])
            live["report_recovery"]["retry_after"] = "2000-01-01T00:00:00+00:00"
            S.save_task(PROJECT, live)
        with patch.object(server, "spawn", side_effect=lambda key, fn, *a: calls.append(key) or True):
            server.resume_stranded_reports(PROJECT)
        self.assertIn("finished:{}:{}".format(PROJECT, task["slug"]), calls)

    def test_report_turn_error_remains_unhandled_for_retry(self):
        task = self._task("report-error-retry", state="reported", report=True)
        with patch.object(server.improve, "index", return_value=[]), \
             patch.object(server.l3, "turn", return_value={"text": "", "error": "engine failed"}):
            server.report_turn(PROJECT, task, self._nonclean_verdict())
        live = S.load_task(PROJECT, task["slug"])
        self.assertEqual(live["state"], "reported")
        self.assertIsNone(live.get("l3_handled"))

    def test_open_breaker_durably_defers_nonclean_report_without_tick_churn(self):
        task = self._task("report-breaker-deferred", state="reported", report=True)
        with patch.object(server, "_breaker_open", return_value=True), \
             patch.object(server.l3, "turn", side_effect=AssertionError("breaker must suppress L3")):
            server.report_turn(PROJECT, task, self._nonclean_verdict())
            calls = []
            with patch.object(server, "spawn", side_effect=lambda *args: calls.append(args) or True):
                server.resume_stranded_reports(PROJECT)
                server.resume_stranded_reports(PROJECT)
        live = S.load_task(PROJECT, task["slug"])
        rec = live["report_recovery"]
        self.assertTrue(rec["breaker_deferred"])
        self.assertEqual(rec["attempts"], 0)
        self.assertIsNone(rec["claim"])
        self.assertEqual(calls, [])

        calls = []
        with patch.object(server, "_breaker_open", return_value=False), \
             patch.object(server, "spawn", side_effect=lambda *args: calls.append(args) or True):
            server.resume_stranded_reports(PROJECT)
        self.assertEqual([call[0] for call in calls], [f"finished:{PROJECT}:{task['slug']}"])

    def test_open_breaker_cannot_turn_legacy_gate_into_clean_close(self):
        task = self._task("report-breaker-clean", state="reported", report=True)
        task["dispatch_id"] = f"{task['slug']}-1"
        S.save_task(PROJECT, task)
        report = self.clean_report()
        base_sha, head_sha, merge_sha = "a" * 40, "b" * 40, "c" * 40
        report["landed"].update({
            "prs": [{"number": 47, "title": "breaker clean", "merged": True,
                     "merge_sha": merge_sha}],
            "main_runs": [],
        })
        report.update({"deviations": [], "spend": {"turns": 1, "subagent_launches": 0,
                                                     "retries": 0, "model_tiers": "coding", "reverts": 0},
                       "roadmap_complete": True})
        (S.task_dir(PROJECT, task["slug"]) / "report.md").write_text("report\n")
        (S.task_dir(PROJECT, task["slug"]) / "report.json").write_text(json.dumps(report))
        (S.task_dir(PROJECT, task["slug"]) / "progress.md").write_text("complete\n")
        gate = {"sandboxed": True, "passed": True, "base_sha": base_sha,
                "head_sha": head_sha, "tests": 42, "skipped": 0, "expected_failures": 0}
        S.write_json(S.task_dir(PROJECT, task["slug"]) / "merge-request-47.json", {
            "version": 1, "project": PROJECT, "slug": task["slug"], "pr": 47,
            "generation": "breaker-clean-merge", "dispatch_id": task["dispatch_id"],
            "task_attempt": 1, "base_sha": base_sha, "head_sha": head_sha, "state": "merged",
            "result": {"merged": True, "base_sha": base_sha, "head_sha": head_sha,
                       "merge_sha": merge_sha, "gate_mode": "local-suite", "candidate_gate": gate},
        })
        github_pr = {"number": 47, "state": "MERGED", "mergedAt": S.now(),
                     "mergeCommit": {"oid": merge_sha}, "headRefName": "breaker-clean",
                     "headRefOid": head_sha}
        with patch.object(server.verify, "gh", return_value=github_pr):
            verdict = server.verify._verify(PROJECT, task["slug"])
        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertIn(server.verify.TRUSTED_REMOTE_PENDING, verdict["problems"])
        self.assertFalse(server.verify.clean_close_provenance_matches(
            PROJECT, task["slug"], verdict, task, report))
        live = S.load_task(PROJECT, task["slug"])
        self.assertEqual(live["state"], "reported")

    def test_report_turn_stamps_only_after_explicit_task_disposition(self):
        task = self._task("report-explicit-block", state="reported", report=True)

        def disposition(project, prompt, trigger, **kwargs):
            T.block(project, task["slug"], "L2 must repair the report", actor="l3")
            return {"text": "blocked for repair", "error": None}

        with patch.object(server.improve, "index", return_value=[]), \
             patch.object(server.l3, "turn", side_effect=disposition):
            server.report_turn(PROJECT, task, self._nonclean_verdict())
        live = S.load_task(PROJECT, task["slug"])
        self.assertEqual(live["state"], "blocked")
        self.assertIsNotNone(live.get("l3_handled"))

    def test_new_block_clears_old_escalation_and_explicit_needs_user_only(self):
        slug = "changed-blocker"
        self._task(slug, state="running", handled=S.now())
        with S.project_lock(PROJECT):
            task = S.load_task(PROJECT, slug); task["needs_user"] = {"reason": "old", "asked": S.now()}; S.save_task(PROJECT, task)
        T.block(PROJECT, slug, "new engine failure")
        live = S.load_task(PROJECT, slug)
        self.assertIsNone(live.get("l3_handled")); self.assertIsNone(live.get("needs_user"))
        self.assertNotIn(slug, [row["slug"] for row in T.decisions(PROJECT)])
        T.needs_user(PROJECT, slug, "Choose whether to grant the new permission")
        self.assertIn(slug, [row["slug"] for row in T.decisions(PROJECT)])
        T.resume(PROJECT, slug)
        self.assertNotIn(slug, [row["slug"] for row in T.decisions(PROJECT)])
        T.block(PROJECT, slug, "test cleanup"); T.reject(PROJECT, slug, "test cleanup")


    def test_verifier_clean_blocked_report_is_atomically_promoted(self):
        project, slug = "stranded-clean", "clean-blocked"
        verified = self.verified()
        self.save_blocked(project, slug, json.dumps(self.clean_report()), verified)
        calls = []
        with patch.object(server, "spawn", side_effect=lambda key, fn, *args: calls.append((key, args[1]["state"])) or True):
            server.resume_stranded_reports(project)
        promoted = S.load_task(project, slug)
        self.assertEqual(promoted["state"], "reported")
        self.assertIsNone(promoted["blocked_reason"])
        self.assertEqual(calls, [(f"finished:{project}:{slug}", "reported")])
        self.assertEqual(T.done(project, slug, actor="burak", digest="closed")["state"], "done")

    def test_report_stamps_the_current_attempt(self):
        project, slug = "stranded-stamp", "stamp"
        S.task_dir(project, slug).mkdir(parents=True, exist_ok=True)
        S.save_task(project, {"slug": slug, "title": slug, "class": "S", "state": "running", "created": S.now(),
                              "updated": S.now(), "attempt": 3, "blocked_reason": None})
        reported = T.report(project, slug, {"verdict": "ok", "prs": []})
        self.assertEqual(reported["verified"]["attempt"], 3)

    def test_blocked_report_requires_clean_current_attempt_and_running_provenance(self):
        project = "stranded-exclusions"
        cases = (
            ("blocked-verdict", self.verified(verdict="blocked"), 1, "running", self.clean_report()),
            ("blocked-field", self.verified(blocked="needs input"), 1, "running", self.clean_report("needs input")),
            ("stale-attempt", self.verified(attempt=1), 2, "running", self.clean_report()),
            ("l3-block", self.verified(attempt=1), 1, "reported", self.clean_report()),
        )
        for slug, verified, attempt, block_from, report in cases:
            self.save_blocked(project, slug, json.dumps(report), verified,
                              attempt=attempt, block_from=block_from)
        calls = []
        with patch.object(server, "spawn", side_effect=lambda key, fn, *args: calls.append(key) or True):
            server.resume_stranded_reports(project)
        self.assertEqual(calls, [])
        for slug, *_ in cases:
            self.assertEqual(S.load_task(project, slug)["state"], "blocked")

    def test_invalid_or_unverified_blocked_report_stays_with_coordinator(self):
        project = "stranded-invalid"
        clean = json.dumps(self.clean_report())
        cases = (("report-list", json.dumps([]), self.verified()),
                 ("report-empty", "", self.verified()),
                 ("report-corrupt", "{not-json", self.verified()),
                 ("verified-missing", clean, None),
                 ("verified-fault", clean, self.verified(verdict="fault")))
        for slug, report_text, verified in cases:
            self.save_blocked(project, slug, report_text, verified)
        calls, logs = [], []
        with patch.object(server, "spawn", side_effect=lambda key, fn, *args: calls.append(key) or True), \
             patch.object(server, "log", side_effect=logs.append):
            server.resume_stranded_reports(project)
        self.assertEqual(calls, [])
        for slug, *_ in cases:
            self.assertEqual(S.load_task(project, slug)["state"], "blocked")
        self.assertTrue(any("report-corrupt" in line and "cannot read stranded report" in line for line in logs))

    def test_concurrent_promotion_change_skips_one_task_without_aborting_scan(self):
        project = "stranded-race"
        for slug in ("race", "z-after"):
            self.save_blocked(project, slug, json.dumps(self.clean_report()), self.verified())
        calls = []
        original_report = T.report

        def raced_report(project_name, slug, verified, **expected):
            if slug == "race":
                T.resume(project_name, slug, actor="burak")
            return original_report(project_name, slug, verified, **expected)

        with patch.object(T, "report", side_effect=raced_report), \
             patch.object(server, "spawn", side_effect=lambda key, fn, *args: calls.append((key, args[1]["state"])) or True):
            server.resume_stranded_reports(project)
        self.assertEqual(S.load_task(project, "race")["state"], "running")
        self.assertEqual(S.load_task(project, "z-after")["state"], "reported")
        self.assertEqual(calls, [(f"finished:{project}:z-after", "reported")])

    def test_done_rejects_before_writing_digest(self):
        project, slug = "stranded-done", "illegal-done"
        d = S.task_dir(project, slug)
        d.mkdir(parents=True, exist_ok=True)
        S.save_task(project, {"slug": slug, "title": slug, "class": "S", "state": "blocked", "created": S.now(),
                              "updated": S.now()})
        with self.assertRaises(T.TransitionError):
            T.done(project, slug, actor="burak", digest="must not be written")
        self.assertFalse((d / "digest.md").exists())


if __name__ == "__main__":
    unittest.main()
