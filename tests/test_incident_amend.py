"""`alt incident amend`: a filed incident can be corrected, but never quietly — the replaced text stays
beneath a dated `amended:` line, the index row is corrected so the audit reads the new cause, and the
originating task gets an event. Runs against a throwaway ALTITUDE_HOME; every test builds its own fixture
incident in a temp dir, never the live ledger."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-amend-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, improve  # noqa: E402

ALT = Path(__file__).resolve().parent.parent / "bin" / "alt"


def fixture_incident(root: Path, project: str = "demo", iid: str = "I-001", task: str = "fix-the-thing",
                     what: str = "the reviewer pass never ran") -> Path:
    """An incident file written from the real template, so a template change breaks these tests loudly."""
    body = (config.TEMPLATES / "incident.md").read_text().format(
        id=iid, title="the L2 skipped the reviewer", date=S.now()[:10], task=task, project=project,
        what=what, evidence="events.log 00:15:07 running->blocked",
        cause="the envelope had no slack", generalizable="unknown", mechanism="rule", scope="project",
        rule="-", status="watch")
    p = root / project / "incidents" / f"{iid}.md"
    S.atomic_write(p, body)
    return p


def fixture_task(root: Path, project: str = "demo", slug: str = "fix-the-thing") -> Path:
    d = root / project / "tasks" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


class TempHome:
    """config.ROOT and the paths derived from it at import time are patched per test: `unittest discover`
    imports several modules that each point ALTITUDE_HOME somewhere else, so the run-time values cannot be
    relied on — and nothing may reach the real ledger."""

    DERIVED = ("INCIDENT_INDEX", "PROJECTS_FILE", "MONITOR_DIR")

    def use_temp_home(self) -> Path:
        self._saved = {k: getattr(config, k) for k in ("ROOT",) + self.DERIVED}
        root = Path(tempfile.mkdtemp(prefix="altitude-amend-case-"))
        config.ROOT = root
        config.INCIDENT_INDEX = root / "incidents.jsonl"
        config.PROJECTS_FILE = root / "projects.json"
        config.MONITOR_DIR = root / "monitor"
        self.addCleanup(self._restore_home)
        return root

    def _restore_home(self):
        for k, v in self._saved.items():
            setattr(config, k, v)


class TestAmendIncident(TempHome, unittest.TestCase):

    def setUp(self):
        self.root = self.use_temp_home()
        self.path = fixture_incident(self.root)
        fixture_task(self.root)
        self.original = self.path.read_text()

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

    def test_multi_line_value_is_replaced_whole_and_recoverable_verbatim(self):
        """A value with its own `- ` lines and a blank line inside it: the whole thing is the field, so the
        whole thing is replaced and the whole thing is what the history keeps."""
        old = "the run failed in three steps:\n- step one\n\n- step two"
        path = fixture_incident(self.root, iid="I-003", what=old)
        improve.amend_incident("demo", "I-003", what="the run failed once", reason="misread the log", actor="burak")
        body = path.read_text()
        head, _, history = body.partition("\namended: ")
        self.assertIn("- what happened: the run failed once\n", head)
        self.assertNotIn("step one", head)          # no orphaned lines left under the new value
        self.assertNotIn("step two", head)
        self.assertIn(f"- was what happened: {old}", history)
        # and the file still parses: a second amend finds the real fields, not a line from the history
        improve.amend_incident("demo", "I-003", what="the run failed once, at dispatch", reason="more precise")
        body = path.read_text()
        self.assertIn("- what happened: the run failed once, at dispatch\n", body)
        self.assertIn(f"- was what happened: {old}", body)
        self.assertIn("- was what happened: the run failed once\n", body)
        self.assertIn("- evidence: events.log 00:15:07 running->blocked\n", body)

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
        self.assertFalse((self.root / "demo" / "incidents" / "I-404.md").exists())

    def test_empty_change_set_refuses_and_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            improve.amend_incident("demo", "I-001", reason="nothing to say")
        self.assertIn("nothing to amend", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_empty_field_value_refuses_instead_of_blanking_the_field(self):
        with self.assertRaises(ValueError) as e:
            improve.amend_incident("demo", "I-001", what="   ", reason="oops")
        self.assertIn("--what is empty", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_a_value_that_would_read_as_another_field_refuses(self):
        with self.assertRaises(ValueError) as e:
            improve.amend_incident("demo", "I-001", what="it ran\n- status: closed", reason="smuggled a field")
        self.assertIn("would read as another field", str(e.exception))
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
        fixture_incident(self.root, iid="I-002", task="-")
        res = improve.amend_incident("demo", "I-002", cause="a different cause", reason="corrected", actor="burak")
        self.assertIsNone(res["task"])
        self.assertIsNone(res["names_task"])
        self.assertEqual(S.read_events("demo", "fix-the-thing"), [])

    def test_a_task_folder_that_does_not_exist_is_reported_not_created(self):
        fixture_incident(self.root, iid="I-002", task="ghost-task")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = improve.amend_incident("demo", "I-002", cause="a different cause", reason="corrected")
        self.assertIsNone(res["task"])
        self.assertEqual(res["names_task"], "ghost-task")
        self.assertIn("ghost-task", err.getvalue())
        self.assertFalse((self.root / "demo" / "tasks" / "ghost-task").exists())

    def test_the_correction_reaches_the_project_log_and_state_md(self):
        improve.amend_incident("demo", "I-001", cause="the reviewer was never briefed", reason="corrected",
                               actor="burak")
        kinds = [e["kind"] for e in S.read_project_log("demo")]
        self.assertIn("incident-amended", kinds)
        self.assertTrue((self.root / "demo" / "STATE.md").exists())


class TestAmendIndex(TempHome, unittest.TestCase):
    """A correction the index never sees is invisible to `alt incident list` and to the weekly audit."""

    def setUp(self):
        self.root = self.use_temp_home()
        self.repo = self.root / "repo"
        (self.repo / "docs").mkdir(parents=True)
        config.save_projects({"demo": {"path": str(self.repo), "stacks": []}})
        self.inc = improve.new_incident("demo", title="the L2 skipped the reviewer", task=None,
                                        what="the reviewer pass never ran", evidence="events.log 00:15:07",
                                        cause="the envelope had no slack", tags=["reviewer"])

    def test_amended_cause_replaces_the_indexed_cause_without_adding_a_row(self):
        before = improve.next_incident_id("demo")
        improve.amend_incident("demo", self.inc["id"], cause="the reviewer was never briefed",
                               reason="root cause was wrong", actor="burak")
        rows = [r for r in improve.index() if r["id"] == self.inc["id"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cause"], "the reviewer was never briefed")
        self.assertEqual(improve.next_incident_id("demo"), before)      # no row appended, no id burned
        per_project = (self.root / "demo" / "incidents.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(l)["cause"] for l in per_project], ["the reviewer was never briefed"])
        audited = [r for r in improve.audit_input("demo")["incidents"] if r["id"] == self.inc["id"]]
        self.assertEqual(audited[0]["cause"], "the reviewer was never briefed")

    def test_amending_an_unindexed_field_leaves_the_row_alone(self):
        improve.amend_incident("demo", self.inc["id"], status="closed", reason="fixed")
        rows = [r for r in improve.index() if r["id"] == self.inc["id"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cause"], "the envelope had no slack")


class TestAmendCLI(TempHome, unittest.TestCase):
    """The `alt` wiring: flags map to fields, ALTITUDE_ACTOR names the amender, refusals exit non-zero."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="altitude-amend-cli-"))
        self.path = fixture_incident(self.home)
        fixture_task(self.home)
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

    def test_an_empty_field_value_exits_non_zero(self):
        r = self.alt("amend", "I-001", "--what", "", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--what is empty", r.stderr)
        self.assertEqual(self.path.read_text(), self.original)

    def test_bad_status_value_is_rejected_by_the_parser(self):
        r = self.alt("amend", "I-001", "--status", "reopened", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid choice", r.stderr)
        self.assertEqual(self.path.read_text(), self.original)


class TestTemplateStaysClean(unittest.TestCase):

    def test_the_template_carries_no_amendment_note(self):
        """`propose_rule` pastes a whole incident file into the apply-rule request, and every new incident is
        a copy of this template — a maintenance comment here would ship into PR bodies and docs/incidents/."""
        self.assertNotIn("amended", (config.TEMPLATES / "incident.md").read_text())


if __name__ == "__main__":
    unittest.main()
