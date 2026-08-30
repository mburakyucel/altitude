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
from altitude import config, state as S, server, tasks as T  # noqa: E402


class TestStrandedReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})
        base = {"class": "S", "created": S.now(), "updated": S.now(), "verified": {"verdict": "ok", "problems": [], "signals": [], "spend": {}, "prs": [], "report": {}}}
        for slug, state, handled, report in (("stranded", "reported", None, True), ("stranded-blocked", "blocked", None, True),
                                             ("handled", "reported", S.now(), True), ("no-report", "blocked", None, False),
                                             ("still-running", "running", None, True)):
            S.task_dir("altitude", slug).mkdir(parents=True, exist_ok=True)
            if report:
                (S.task_dir("altitude", slug) / "report.json").write_text(json.dumps({"landed": {}}))
            S.save_task("altitude", {**base, "slug": slug, "title": slug, "state": state, "l3_handled": handled})

    def test_only_unhandled_reports_are_resumed(self):
        calls = []
        orig = server.spawn
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__)) or True
        try:
            server.resume_stranded_reports("altitude")
        finally:
            server.spawn = orig
        self.assertEqual(sorted(calls), [("finished:altitude:stranded", "report_turn"), ("finished:altitude:stranded-blocked", "report_turn")])

    def test_verifier_clean_blocked_report_becomes_closable(self):
        project = "altitude-clean"
        slug = "clean-blocked"
        d = S.task_dir(project, slug)
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.json").write_text(json.dumps({"landed": {}, "blocked": ""}))
        verified = {"verdict": "ok", "problems": [], "signals": [], "spend": {}, "prs": [], "report": {"blocked": ""}}
        S.save_task(project, {"slug": slug, "title": slug, "class": "S", "state": "blocked", "created": S.now(),
                              "updated": S.now(), "verified": verified, "l3_handled": None})
        calls = []
        orig = server.spawn
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__)) or True
        try:
            server.resume_stranded_reports(project)
        finally:
            server.spawn = orig
        self.assertEqual(S.load_task(project, slug)["state"], "reported")
        self.assertEqual(T.done(project, slug, digest="closed")["state"], "done")

    def test_genuinely_blocked_reports_stay_blocked(self):
        project = "altitude-genuine"
        cases = (("blocked-verdict", "blocked", ""), ("blocked-field", "ok", "needs input"))
        for slug, verdict, blocked in cases:
            d = S.task_dir(project, slug)
            d.mkdir(parents=True, exist_ok=True)
            (d / "report.json").write_text(json.dumps({"landed": {}, "blocked": blocked}))
            verified = {"verdict": verdict, "problems": [], "signals": [], "spend": {}, "prs": [],
                        "report": {"blocked": blocked}}
            S.save_task(project, {"slug": slug, "title": slug, "class": "S", "state": "blocked", "created": S.now(),
                                  "updated": S.now(), "verified": verified, "l3_handled": None})
        calls = []
        orig = server.spawn
        server.spawn = lambda key, fn, *a: calls.append((key, fn.__name__)) or True
        try:
            server.resume_stranded_reports(project)
        finally:
            server.spawn = orig
        for slug, _, _ in cases:
            self.assertEqual(S.load_task(project, slug)["state"], "blocked")
            self.assertIn((f"finished:{project}:{slug}", "report_turn"), calls)

    def test_done_rejects_before_writing_digest(self):
        project = "altitude-done"
        slug = "illegal-done"
        d = S.task_dir(project, slug)
        d.mkdir(parents=True, exist_ok=True)
        S.save_task(project, {"slug": slug, "title": slug, "class": "S", "state": "blocked", "created": S.now(),
                              "updated": S.now()})
        with self.assertRaises(T.TransitionError):
            T.done(project, slug, digest="must not be written")
        self.assertFalse((d / "digest.md").exists())


if __name__ == "__main__":
    unittest.main()
