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
from altitude import config, state as S, server  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
