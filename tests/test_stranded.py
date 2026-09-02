"""A report whose L3 turn never finished (altd restarted under it) is handed to L3 again; handled ones are not."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-stranded-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, state as S, server, tasks as T, transcript  # noqa: E402


class TestStrandedReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP}})
        base = {"created": S.now(), "updated": S.now(), "l2_engine": "codex",
                "active_operation": {"test": "terminal-owner"},
                "verified": {"verdict": "ok", "problems": [], "signals": [], "spend": {}, "prs": [], "report": {}}}
        for slug, state, handled, report in (("stranded", "reported", None, True), ("stranded-blocked", "blocked", None, True),
                                             ("handled", "reported", S.now(), True), ("no-report", "blocked", None, False),
                                             ("still-running", "running", None, True)):
            S.task_dir("altitude", slug).mkdir(parents=True, exist_ok=True)
            if report:
                (S.task_dir("altitude", slug) / "report.json").write_text(json.dumps({"landed": {}}))
            S.save_task("altitude", {**base, "slug": slug, "title": slug, "state": state, "l3_handled": handled})

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
        (d / "report.json").write_text(report_text)
        S.save_task(project, {"slug": slug, "title": slug, "state": "blocked", "created": S.now(),
                              "updated": S.now(), "attempt": attempt, "verified": verified, "l3_handled": None,
                              "blocked_reason": "stale block reason", "l2_engine": "codex",
                              "active_operation": {"test": "terminal-owner"}})
        S.append_event(project, slug, "state", frm=block_from, to="blocked", by="test")

    def test_only_unhandled_reports_are_resumed(self):
        calls = []
        orig = server.spawn
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__, a[2]["verdict"])) or True
        try:
            server.resume_stranded_reports("altitude")
        finally:
            server.spawn = orig
        self.assertEqual(sorted(calls), [("finished:altitude:stranded", "report_turn", "ok"), ("finished:altitude:stranded-blocked", "report_turn", "ok")])

    def test_verifier_clean_blocked_report_becomes_closable(self):
        project = "altitude-clean"
        slug = "clean-blocked"
        verified = self.verified()
        self.save_blocked(project, slug, json.dumps(self.clean_report()), verified)
        calls = []
        orig = server.spawn
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__, a[1]["state"])) or True
        try:
            server.resume_stranded_reports(project)
        finally:
            server.spawn = orig
        promoted = S.load_task(project, slug)
        self.assertEqual(promoted["state"], "reported")
        self.assertIsNone(promoted["blocked_reason"])
        self.assertEqual(calls, [(f"finished:{project}:{slug}", "report_turn", "reported")])
        original_snapshot = dispatch.owner_result_snapshot
        original_ready, original_sync = dispatch.owner_archive_ready, transcript.sync
        dispatch.owner_result_snapshot = lambda _project, live: {"task": live}
        dispatch.owner_archive_ready = lambda _project, _live: True
        transcript.sync = lambda *_args, **_kwargs: None
        try:
            self.assertEqual(T.done(project, slug, digest="closed")["state"], "done")
        finally:
            dispatch.owner_result_snapshot = original_snapshot
            dispatch.owner_archive_ready, transcript.sync = original_ready, original_sync

    def test_report_stamps_the_current_attempt(self):
        project, slug = "altitude-stamp", "stamp"
        S.task_dir(project, slug).mkdir(parents=True, exist_ok=True)
        S.save_task(project, {"slug": slug, "title": slug, "state": "running", "created": S.now(),
                              "updated": S.now(), "attempt": 3, "blocked_reason": None,
                              "l2_engine": "codex"})
        reported = T.report(project, slug, {"verdict": "ok", "prs": []})
        self.assertEqual(reported["verified"]["attempt"], 3)

    def test_genuinely_blocked_reports_stay_blocked(self):
        project = "altitude-genuine"
        cases = (("blocked-verdict", "blocked", ""), ("blocked-field", "ok", "needs input"))
        for slug, verdict, blocked in cases:
            verified = self.verified(verdict=verdict, blocked=blocked)
            self.save_blocked(project, slug, json.dumps(self.clean_report(blocked)), verified)
        calls = []
        orig = server.spawn
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__, a[2]["verdict"])) or True
        try:
            server.resume_stranded_reports(project)
        finally:
            server.spawn = orig
        for slug, verdict, _ in cases:
            self.assertEqual(S.load_task(project, slug)["state"], "blocked")
            self.assertIn((f"finished:{project}:{slug}", "report_turn", verdict), calls)

    def test_invalid_and_unverified_reports_stay_blocked(self):
        project = "altitude-invalid"
        clean = json.dumps(self.clean_report())
        cases = (("report-list", json.dumps([]), self.verified()),
                 ("report-empty", "", self.verified()),
                 ("report-corrupt", "{not-json", self.verified()),
                 ("verified-missing", clean, None),
                 ("verified-fault", clean, self.verified(verdict="fault")))
        for slug, report_text, verified in cases:
            self.save_blocked(project, slug, report_text, verified)
        calls, logs = [], []
        orig_spawn, orig_log = server.spawn, server.log
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__, a[2]["verdict"])) or True
        server.log = logs.append
        try:
            server.resume_stranded_reports(project)
        finally:
            server.spawn, server.log = orig_spawn, orig_log
        for slug, _, verified in cases:
            self.assertEqual(S.load_task(project, slug)["state"], "blocked")
            self.assertIn((f"finished:{project}:{slug}", "report_turn", (verified or {"verdict": "missing"})["verdict"]), calls)
        self.assertTrue(any("report-corrupt" in line and "cannot read stranded report" in line for line in logs))

    def test_stale_attempt_and_l3_block_stay_blocked(self):
        project = "altitude-exclusions"
        cases = (("stale-attempt", self.verified(attempt=1), 2, "running"),
                 ("l3-block", self.verified(attempt=1), 1, "reported"))
        for slug, verified, attempt, block_from in cases:
            self.save_blocked(project, slug, json.dumps(self.clean_report()), verified,
                              attempt=attempt, block_from=block_from)
        calls = []
        orig = server.spawn
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__, a[1]["state"])) or True
        try:
            server.resume_stranded_reports(project)
        finally:
            server.spawn = orig
        for slug, _, _, _ in cases:
            task = S.load_task(project, slug)
            self.assertEqual(task["state"], "blocked")
            self.assertNotEqual(task["state"], "done")
            self.assertEqual(S.task_dir(project, slug).parent.name, "tasks")
            self.assertIn((f"finished:{project}:{slug}", "report_turn", "blocked"), calls)

    def test_concurrent_resume_skips_task_without_aborting_scan(self):
        project = "altitude-race"
        for slug in ("race", "z-after"):
            self.save_blocked(project, slug, json.dumps(self.clean_report()), self.verified())
        calls = []
        orig_report, orig_spawn = T.report, server.spawn

        def raced_report(project, slug, verified, **expected):
            if slug == "race":
                T.resume(project, slug, actor="burak")
            return orig_report(project, slug, verified, **expected)

        T.report = raced_report
        server.spawn = lambda key, fn, *a: calls.append((key, a[1]["state"])) or True
        try:
            server.resume_stranded_reports(project)
        finally:
            T.report, server.spawn = orig_report, orig_spawn
        self.assertEqual(S.load_task(project, "race")["state"], "running")
        self.assertEqual(S.load_task(project, "z-after")["state"], "reported")
        self.assertEqual(calls, [(f"finished:{project}:z-after", "reported")])

    def test_done_rejects_before_writing_digest(self):
        project = "altitude-done"
        slug = "illegal-done"
        d = S.task_dir(project, slug)
        d.mkdir(parents=True, exist_ok=True)
        S.save_task(project, {"slug": slug, "title": slug, "state": "blocked", "created": S.now(),
                              "updated": S.now()})
        with self.assertRaises(T.TransitionError):
            T.done(project, slug, digest="must not be written")
        self.assertFalse((d / "digest.md").exists())


if __name__ == "__main__":
    unittest.main()
