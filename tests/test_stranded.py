"""A report whose L3 turn never finished (altd restarted under it) is handed to L3 again; handled ones are not."""
import json
import unittest

from tests.support import AltitudeCase
from altitude import state as S, server, tasks as T


class TestStrandedReports(AltitudeCase):
    @staticmethod
    def clean_report(blocked=""):
        return {"landed": {"deploy": "not-applicable", "prs": [], "main_runs": []}, "blocked": blocked,
                "decisions": [], "fyi": [], "follow_ups": [], "review": []}

    @staticmethod
    def verified(verdict="ok", attempt=1, blocked=""):
        return {"verdict": verdict, "attempt": attempt, "problems": [], "signals": [], "spend": {}, "prs": [],
                "report": {"blocked": blocked}}

    def save_blocked(self, slug, report_text, verified, *, attempt=1, block_from="running"):
        d = S.task_dir(self.project, slug)
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.json").write_text(report_text)
        S.save_task(self.project, {"slug": slug, "title": slug, "state": "blocked", "created": S.now(),
                                   "updated": S.now(), "attempt": attempt, "verified": verified, "l3_handled": None,
                                   "blocked_reason": "stale block reason"})
        S.append_event(self.project, slug, "state", frm=block_from, to="blocked", by="test")

    def record_spawns(self) -> list[dict]:
        """What the scan hands to L3, instead of starting the background report turn."""
        calls = []
        self.patch(server, "spawn", side_effect=lambda key, fn, *a: calls.append(
            {"key": key, "fn": fn.__name__, "state": a[1]["state"], "verdict": a[2]["verdict"]}) or True)
        return calls

    def test_only_unhandled_reports_are_resumed(self):
        base = {"created": S.now(), "updated": S.now(),
                "verified": {"verdict": "ok", "problems": [], "signals": [], "spend": {}, "prs": [], "report": {}}}
        for slug, state, handled, report in (("stranded", "reported", None, True), ("stranded-blocked", "blocked", None, True),
                                             ("handled", "reported", S.now(), True), ("no-report", "blocked", None, False),
                                             ("still-running", "running", None, True)):
            S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
            if report:
                (S.task_dir(self.project, slug) / "report.json").write_text(json.dumps({"landed": {}}))
            S.save_task(self.project, {**base, "slug": slug, "title": slug, "state": state, "l3_handled": handled})
        calls = self.record_spawns()

        server.resume_stranded_reports(self.project)

        self.assertEqual(sorted((c["key"], c["fn"], c["verdict"]) for c in calls),
                         [(f"finished:{self.project}:stranded", "report_turn", "ok"),
                          (f"finished:{self.project}:stranded-blocked", "report_turn", "ok")])

    def test_verifier_clean_blocked_report_becomes_closable(self):
        slug = "clean-blocked"
        self.save_blocked(slug, json.dumps(self.clean_report()), self.verified())
        calls = self.record_spawns()

        server.resume_stranded_reports(self.project)

        promoted = S.load_task(self.project, slug)
        self.assertEqual(promoted["state"], "reported")
        self.assertIsNone(promoted["blocked_reason"])
        self.assertEqual([(c["key"], c["fn"], c["state"]) for c in calls],
                         [(f"finished:{self.project}:{slug}", "report_turn", "reported")])
        self.assertEqual(T.done(self.project, slug, digest="closed")["state"], "done")

    def test_report_stamps_the_current_attempt(self):
        slug = "stamp"
        S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
        S.save_task(self.project, {"slug": slug, "title": slug, "state": "running", "created": S.now(),
                                   "updated": S.now(), "attempt": 3, "blocked_reason": None})
        reported = T.report(self.project, slug, {"verdict": "ok", "prs": []})
        self.assertEqual(reported["verified"]["attempt"], 3)

    def test_genuinely_blocked_reports_stay_blocked(self):
        cases = (("blocked-verdict", "blocked", ""), ("blocked-field", "ok", "needs input"))
        for slug, verdict, blocked in cases:
            self.save_blocked(slug, json.dumps(self.clean_report(blocked)), self.verified(verdict=verdict, blocked=blocked))
        calls = self.record_spawns()

        server.resume_stranded_reports(self.project)

        for slug, verdict, _ in cases:
            self.assertEqual(S.load_task(self.project, slug)["state"], "blocked")
            self.assertIn((f"finished:{self.project}:{slug}", "report_turn", verdict),
                          [(c["key"], c["fn"], c["verdict"]) for c in calls])

    def test_invalid_and_unverified_reports_stay_blocked(self):
        clean = json.dumps(self.clean_report())
        cases = (("report-list", json.dumps([]), self.verified()),
                 ("report-empty", "", self.verified()),
                 ("report-corrupt", "{not-json", self.verified()),
                 ("verified-missing", clean, None),
                 ("verified-fault", clean, self.verified(verdict="fault")))
        for slug, report_text, verified in cases:
            self.save_blocked(slug, report_text, verified)
        calls, logs = self.record_spawns(), []
        self.patch(server, "log", side_effect=logs.append)

        server.resume_stranded_reports(self.project)

        for slug, _, verified in cases:
            self.assertEqual(S.load_task(self.project, slug)["state"], "blocked")
            self.assertIn((f"finished:{self.project}:{slug}", "report_turn", (verified or {"verdict": "missing"})["verdict"]),
                          [(c["key"], c["fn"], c["verdict"]) for c in calls])
        self.assertTrue(any("report-corrupt" in line and "cannot read stranded report" in line for line in logs))

    def test_stale_attempt_and_l3_block_stay_blocked(self):
        cases = (("stale-attempt", self.verified(attempt=1), 2, "running"),
                 ("l3-block", self.verified(attempt=1), 1, "reported"))
        for slug, verified, attempt, block_from in cases:
            self.save_blocked(slug, json.dumps(self.clean_report()), verified, attempt=attempt, block_from=block_from)
        calls = self.record_spawns()

        server.resume_stranded_reports(self.project)

        for slug, _, _, _ in cases:
            task = S.load_task(self.project, slug)
            self.assertEqual(task["state"], "blocked")
            self.assertEqual(S.task_dir(self.project, slug).parent.name, "tasks")  # never archived by the scan
            self.assertIn((f"finished:{self.project}:{slug}", "report_turn", "blocked"),
                          [(c["key"], c["fn"], c["state"]) for c in calls])

    def test_concurrent_resume_skips_task_without_aborting_scan(self):
        for slug in ("race", "z-after"):
            self.save_blocked(slug, json.dumps(self.clean_report()), self.verified())
        T.resume(self.project, "race", actor="burak")  # a resume landed between the report and this scan
        calls = self.record_spawns()

        server.resume_stranded_reports(self.project)

        self.assertEqual(S.load_task(self.project, "race")["state"], "running")
        self.assertEqual(S.load_task(self.project, "z-after")["state"], "reported")
        self.assertEqual([(c["key"], c["state"]) for c in calls], [(f"finished:{self.project}:z-after", "reported")])

    def test_done_rejects_before_writing_digest(self):
        slug = "illegal-done"
        d = S.task_dir(self.project, slug)
        d.mkdir(parents=True, exist_ok=True)
        S.save_task(self.project, {"slug": slug, "title": slug, "state": "blocked", "created": S.now(),
                                   "updated": S.now()})
        with self.assertRaises(T.TransitionError):
            T.done(self.project, slug, digest="must not be written")
        self.assertFalse((d / "digest.md").exists())


if __name__ == "__main__":
    unittest.main()
