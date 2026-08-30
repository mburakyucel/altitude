"""`alt incident amend`: a filed incident can be corrected, but never quietly — the replaced text stays
beneath a dated `amended:` line and the originating task gets an event. Runs against a throwaway
ALTITUDE_HOME; every test builds its own fixture incident in a temp dir, never the live ledger."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-amend-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, improve  # noqa: E402

ALT = Path(__file__).resolve().parent.parent / "bin" / "alt"


def fixture_incident(root: Path, project: str = "demo", iid: str = "I-001", task: str = "fix-the-thing") -> Path:
    """An incident file written from the real template, so a template change breaks these tests loudly."""
    body = (config.TEMPLATES / "incident.md").read_text().format(
        id=iid, title="the L2 skipped the reviewer", date=S.now()[:10], task=task, project=project,
        what="the reviewer pass never ran", evidence="events.log 00:15:07 running->blocked",
        cause="the envelope had no slack", generalizable="unknown", mechanism="rule", scope="project",
        rule="-", status="watch")
    p = root / project / "incidents" / f"{iid}.md"
    S.atomic_write(p, body)
    return p


class TestAmendIncident(unittest.TestCase):
    """config.ROOT is patched per test: `unittest discover` imports several modules that each point
    ALTITUDE_HOME somewhere else, so the run-time value of ROOT cannot be relied on."""

    def setUp(self):
        self._root = config.ROOT
        config.ROOT = Path(tempfile.mkdtemp(prefix="altitude-amend-case-"))
        self.path = fixture_incident(config.ROOT)
        self.original = self.path.read_text()

    def tearDown(self):
        config.ROOT = self._root

    def test_round_trip_rewrites_named_fields_and_keeps_the_original(self):
        res = improve.amend_incident("demo", "I-001", cause="the reviewer was never briefed", status="closed",
                                     reason="root cause was wrong; Burak corrected it in chat", actor="burak")
        self.assertEqual(res["amended"], ["cause", "status"])
        body = self.path.read_text()
        self.assertIn("- root cause: the reviewer was never briefed\n", body)
        self.assertIn("- status: closed\n", body)
        self.assertIn("- what happened: the reviewer pass never ran\n", body)   # untouched
        self.assertIn("- evidence: events.log 00:15:07 running->blocked\n", body)
        audit = f"amended: {S.now()[:10]} by burak: root cause was wrong; Burak corrected it in chat"
        self.assertIn(audit, body)
        self.assertLess(body.index(audit), body.index("- was root cause: the envelope had no slack"))
        self.assertLess(body.index(audit), body.index("- was status: watch"))

    def test_second_amendment_appends_and_keeps_both_histories(self):
        improve.amend_incident("demo", "I-001", status="open", reason="first pass", actor="burak")
        improve.amend_incident("demo", "I-001", status="closed", reason="second pass", actor="l3")
        body = self.path.read_text()
        self.assertIn("- status: closed\n", body)
        self.assertIn("- was status: watch", body)
        self.assertIn("- was status: open", body)
        self.assertLess(body.index("by burak: first pass"), body.index("by l3: second pass"))

    def test_unknown_incident_id_refuses(self):
        with self.assertRaises(ValueError) as e:
            improve.amend_incident("demo", "I-404", cause="x", reason="y")
        self.assertIn("unknown incident", str(e.exception))
        self.assertFalse((config.ROOT / "demo" / "incidents" / "I-404.md").exists())

    def test_empty_change_set_refuses_and_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            improve.amend_incident("demo", "I-001", reason="nothing to say")
        self.assertIn("nothing to amend", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_unsupported_status_refuses_and_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            improve.amend_incident("demo", "I-001", status="reopened", reason="wrong status word")
        self.assertIn("status in", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_unknown_field_refuses_and_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            improve.amend_incident("demo", "I-001", title="a new title", reason="wrong field")
        self.assertIn("unknown field", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_missing_reason_refuses(self):
        with self.assertRaises(ValueError):
            improve.amend_incident("demo", "I-001", cause="x", reason="  ")

    def test_event_lands_on_the_originating_task(self):
        improve.amend_incident("demo", "I-001", what="the reviewer ran but found nothing",
                               reason="misread the log", actor="burak")
        events = S.read_events("demo", "fix-the-thing")
        self.assertEqual([e["kind"] for e in events], ["incident-amended"])
        ev = events[0]
        self.assertEqual(ev["id"], "I-001")
        self.assertEqual(ev["fields"], ["what"])
        self.assertEqual(ev["by"], "burak")
        self.assertEqual(ev["reason"], "misread the log")

    def test_no_event_when_the_incident_names_no_task(self):
        fixture_incident(config.ROOT, iid="I-002", task="-")
        res = improve.amend_incident("demo", "I-002", cause="a different cause", reason="corrected", actor="burak")
        self.assertIsNone(res["task"])
        self.assertFalse((config.ROOT / "demo" / "tasks").exists())


class TestAmendCLI(unittest.TestCase):
    """The `alt` wiring: flags map to fields, ALTITUDE_ACTOR names the amender, refusals exit non-zero."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="altitude-amend-cli-"))
        self.path = fixture_incident(self.home)
        self.original = self.path.read_text()
        self.env = {**os.environ, "ALTITUDE_HOME": str(self.home), "ALTITUDE_ACTOR": "burak"}

    def alt(self, *args):
        return subprocess.run([sys.executable, str(ALT), "--project", "demo", "incident", *args],
                              capture_output=True, text=True, env=self.env)

    def test_amend_rewrites_the_field_and_names_the_actor(self):
        r = self.alt("amend", "I-001", "--status", "closed", "--reason", "fixed in PR #41")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["amended"], ["status"])
        body = self.path.read_text()
        self.assertIn("- status: closed\n", body)
        self.assertIn(f"amended: {S.now()[:10]} by burak: fixed in PR #41", body)
        self.assertIn("- was status: watch", body)

    def test_unknown_id_exits_non_zero_with_a_clear_error(self):
        r = self.alt("amend", "I-404", "--cause", "x", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown incident", r.stderr)

    def test_empty_change_set_exits_non_zero(self):
        r = self.alt("amend", "I-001", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing to amend", r.stderr)
        self.assertEqual(self.path.read_text(), self.original)

    def test_bad_status_value_is_rejected_by_the_parser(self):
        r = self.alt("amend", "I-001", "--status", "reopened", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid choice", r.stderr)
        self.assertEqual(self.path.read_text(), self.original)


if __name__ == "__main__":
    unittest.main()
