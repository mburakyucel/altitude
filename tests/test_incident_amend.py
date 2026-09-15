"""`alt incident amend`: a filed incident can be corrected, but never quietly — the replaced text stays
beneath a dated `amended:` line, the evidence index receives the new cause, and the
originating task gets an event. Every case builds its own fixture incident in its own project."""
import contextlib
import io
import json
import unittest

from tests.support import AltitudeCase
from altitude import config, state as S, incidents

PROJECT = "demo"


def fixture_incident(project: str = PROJECT, iid: str = "I-001", task: str = "fix-the-thing",
                     what: str = "the worker used stale repository state"):
    """An incident file written from the real template, so a template change breaks these tests loudly."""
    body = (config.TEMPLATES / "incident.md").read_text().format(
        id=iid, title="the task used stale repository state", date=S.now()[:10], task=task, project=project,
        what=what, evidence="events.log 00:15:07 running->blocked",
        cause="the worktree base was not refreshed", status="watch")
    p = config.project_dir(project) / "incidents" / f"{iid}.md"
    S.atomic_write(p, body)
    return p


def fixture_task(project: str = PROJECT, slug: str = "fix-the-thing"):
    d = S.task_dir(project, slug)
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestAmendIncident(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.register(PROJECT)
        self.path = fixture_incident()
        fixture_task()
        self.original = self.path.read_text()

    def test_round_trip_rewrites_named_fields_and_keeps_the_original(self):
        res = incidents.amend_incident(PROJECT, "I-001", cause="the remote base was fetched too late", status="closed",
                                     reason="root cause was wrong; Burak corrected it in chat", actor="burak")
        self.assertEqual(res["amended"], ["cause", "status"])
        body = self.path.read_text()
        self.assertIn("- root cause: the remote base was fetched too late\n", body)
        self.assertIn("- status: closed\n", body)
        self.assertIn("- what happened: the worker used stale repository state\n", body)   # untouched
        self.assertIn("- evidence: events.log 00:15:07 running->blocked\n", body)
        audit = f"amended: {S.now()[:10]} by burak: root cause was wrong; Burak corrected it in chat"
        self.assertIn(audit, body)
        self.assertLess(body.index(audit), body.index("- was root cause: the worktree base was not refreshed"))
        self.assertLess(body.index(audit), body.index("- was status: watch"))

    def test_multi_line_value_is_replaced_whole_and_recoverable_verbatim(self):
        """A value with its own `- ` lines and a blank line inside it: the whole thing is the field, so the
        whole thing is replaced and the whole thing is what the history keeps."""
        old = "the run failed in three steps:\n- step one\n\n- step two"
        path = fixture_incident(iid="I-003", what=old)
        incidents.amend_incident(PROJECT, "I-003", what="the run failed once", reason="misread the log", actor="burak")
        body = path.read_text()
        head, _, history = body.partition("\namended: ")
        self.assertIn("- what happened: the run failed once\n", head)
        self.assertNotIn("step one", head)          # no orphaned lines left under the new value
        self.assertNotIn("step two", head)
        self.assertIn(f"- was what happened: {old}", history)
        # and the file still parses: a second amend finds the real fields, not a line from the history
        incidents.amend_incident(PROJECT, "I-003", what="the run failed once, at dispatch", reason="more precise")
        body = path.read_text()
        self.assertIn("- what happened: the run failed once, at dispatch\n", body)
        self.assertIn(f"- was what happened: {old}", body)
        self.assertIn("- was what happened: the run failed once\n", body)
        self.assertIn("- evidence: events.log 00:15:07 running->blocked\n", body)

    def test_second_amendment_appends_and_keeps_both_histories(self):
        incidents.amend_incident(PROJECT, "I-001", status="open", reason="first pass", actor="burak")
        incidents.amend_incident(PROJECT, "I-001", status="closed", reason="second pass", actor="l3")
        body = self.path.read_text()
        self.assertIn("- status: closed\n", body)
        self.assertIn("- was status: watch", body)
        self.assertIn("- was status: open", body)
        self.assertLess(body.index("by burak: first pass"), body.index("by l3: second pass"))

    def test_unknown_incident_id_refuses(self):
        with self.assertRaises(ValueError) as e:
            incidents.amend_incident(PROJECT, "I-404", cause="x", reason="y")
        self.assertIn("unknown incident", str(e.exception))
        self.assertFalse((config.project_dir(PROJECT) / "incidents" / "I-404.md").exists())

    def test_empty_change_set_refuses_and_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            incidents.amend_incident(PROJECT, "I-001", reason="nothing to say")
        self.assertIn("nothing to amend", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_empty_field_value_refuses_instead_of_blanking_the_field(self):
        with self.assertRaises(ValueError) as e:
            incidents.amend_incident(PROJECT, "I-001", what="   ", reason="oops")
        self.assertIn("--what is empty", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_a_value_that_would_read_as_another_field_refuses(self):
        with self.assertRaises(ValueError) as e:
            incidents.amend_incident(PROJECT, "I-001", what="it ran\n- status: closed", reason="smuggled a field")
        self.assertIn("would read as another field", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_unsupported_status_refuses_and_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            incidents.amend_incident(PROJECT, "I-001", status="reopened", reason="wrong status word")
        self.assertIn("status in", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_unknown_field_refuses_and_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            incidents.amend_incident(PROJECT, "I-001", title="a new title", reason="wrong field")
        self.assertIn("unknown field", str(e.exception))
        self.assertEqual(self.path.read_text(), self.original)

    def test_missing_reason_refuses(self):
        with self.assertRaises(ValueError):
            incidents.amend_incident(PROJECT, "I-001", cause="x", reason="  ")

    def test_event_lands_on_the_originating_task(self):
        incidents.amend_incident(PROJECT, "I-001", what="the worker detected the stale base before editing",
                               reason="misread the log", actor="burak")
        events = S.read_events(PROJECT, "fix-the-thing")
        self.assertEqual([e["kind"] for e in events], ["incident-amended"])
        ev = events[0]
        self.assertEqual(ev["id"], "I-001")
        self.assertEqual(ev["fields"], ["what"])
        self.assertEqual(ev["by"], "burak")
        self.assertEqual(ev["reason"], "misread the log")

    def test_no_event_when_the_incident_names_no_task(self):
        fixture_incident(iid="I-002", task="-")
        res = incidents.amend_incident(PROJECT, "I-002", cause="a different cause", reason="corrected", actor="burak")
        self.assertIsNone(res["task"])
        self.assertIsNone(res["names_task"])
        self.assertEqual(S.read_events(PROJECT, "fix-the-thing"), [])

    def test_a_task_folder_that_does_not_exist_is_reported_not_created(self):
        fixture_incident(iid="I-002", task="ghost-task")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = incidents.amend_incident(PROJECT, "I-002", cause="a different cause", reason="corrected")
        self.assertIsNone(res["task"])
        self.assertEqual(res["names_task"], "ghost-task")
        self.assertIn("ghost-task", err.getvalue())
        self.assertFalse((config.project_dir(PROJECT) / "tasks" / "ghost-task").exists())

    def test_the_correction_reaches_the_project_log_and_state_md(self):
        incidents.amend_incident(PROJECT, "I-001", cause="the remote base was fetched too late", reason="corrected",
                               actor="burak")
        kinds = [e["kind"] for e in S.read_project_log(PROJECT)]
        self.assertIn("incident-amended", kinds)
        self.assertTrue((config.project_dir(PROJECT) / "STATE.md").exists())


class TestAmendIndex(AltitudeCase):
    """A correction must reach both the project and global evidence indexes."""

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.register(PROJECT)
        self.inc = incidents.new_incident(PROJECT, title="the task used stale repository state", task=None,
                                        what="the worker used stale repository state", evidence="events.log 00:15:07",
                                        cause="the worktree base was not refreshed", tags=["git"])

    def test_amended_cause_replaces_the_indexed_cause_without_adding_a_row(self):
        incidents_dir = config.project_dir(PROJECT) / "incidents"
        before = sorted(p.name for p in incidents_dir.glob("I-*.md"))
        incidents.amend_incident(PROJECT, self.inc["id"], cause="the remote base was fetched too late",
                               reason="root cause was wrong", actor="burak")
        rows = [r for r in incidents.index() if r["id"] == self.inc["id"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cause"], "the remote base was fetched too late")
        self.assertEqual(sorted(p.name for p in incidents_dir.glob("I-*.md")), before)  # no new file
        per_project = (config.project_dir(PROJECT) / "incidents.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(l)["cause"] for l in per_project], ["the remote base was fetched too late"])

    def test_status_projection_keeps_the_indexed_cause(self):
        incidents.amend_incident(PROJECT, self.inc["id"], status="closed", reason="fixed")
        rows = [r for r in incidents.index() if r["id"] == self.inc["id"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cause"], "the worktree base was not refreshed")
        self.assertEqual(rows[0]["status"], "closed")

    def test_recovered_role_incident_keeps_prevention_visible_across_reads(self):
        evidence = "Recovered: owner withdrew redundant checkpoint. Prevention: https://github.com/example/altitude/issues/42; owner task role-guidance; pending delivery."
        incidents.amend_incident(PROJECT, self.inc["id"], status="watch", evidence=evidence,
                               reason="Recovery verified; durable prevention remains pending")
        for project in (PROJECT, None):
            row = next(r for r in incidents.index(project) if r["id"] == self.inc["id"])
            self.assertEqual(row["status"], "watch")
            self.assertEqual(row["evidence"], evidence)
        summary = S.regen_state_md(PROJECT)
        self.assertIn(evidence, summary)
        self.assertNotIn("events.log 00:15:07", summary)
        incidents.amend_incident(PROJECT, self.inc["id"], status="closed",
                               evidence="Recovered; prevention delivered and effective in PR #43.", reason="Verified")
        self.assertNotIn(evidence, S.regen_state_md(PROJECT))
        self.assertIn("PR #43", incidents.index(PROJECT)[0]["evidence"])

    def test_no_change_disposition_is_retained_without_publication_or_new_notifications(self):
        from altitude import l3
        before = l3.queued(PROJECT)
        evidence = "Recovered: valid refusal explained. Prevention: no change; fixture confirms requested scope was unapproved."
        incidents.amend_incident(PROJECT, self.inc["id"], status="closed", evidence=evidence,
                               reason="Evidence establishes no system or role defect")
        self.assertEqual(incidents.index(PROJECT)[0]["evidence"], evidence)
        self.assertNotIn(self.inc["id"], S.regen_state_md(PROJECT))
        self.assertEqual(l3.queued(PROJECT), before)

    def test_summary_bounds_evidence_and_keeps_unavailable_records_explicit(self):
        from pathlib import Path
        incidents.amend_incident(PROJECT, self.inc["id"], evidence="x" * 1000, reason="Long evidence")
        summary = S.regen_state_md(PROJECT)
        self.assertIn("x" * 600 + " [truncated]", summary)
        self.assertNotIn("x" * 601, summary)
        Path(self.inc["path"]).unlink()
        row = incidents.index(PROJECT)[0]
        self.assertEqual(row["status"], "unavailable")
        self.assertIn("Incident evidence unavailable", S.regen_state_md(PROJECT))


class TestAmendCLI(AltitudeCase):
    """The `alt` wiring: flags map to fields, ALTITUDE_ACTOR names the amender, refusals exit non-zero."""

    def setUp(self):
        super().setUp()
        self.register(PROJECT)
        self.path = fixture_incident()
        fixture_task()
        self.original = self.path.read_text()

    def incident(self, *args):
        return self.alt("--project", PROJECT, "incident", *args, env={"ALTITUDE_ACTOR": "burak"})

    def test_amend_rewrites_the_field_and_names_the_actor(self):
        r = self.incident("amend", "I-001", "--status", "closed", "--reason", "fixed in PR #41")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["amended"], ["status"])
        body = self.path.read_text()
        self.assertIn("- status: closed\n", body)
        self.assertIn(f"amended: {S.now()[:10]} by burak: fixed in PR #41", body)
        self.assertIn("- was status: watch", body)

    def test_unknown_id_exits_non_zero_with_a_clear_error(self):
        r = self.incident("amend", "I-404", "--cause", "x", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown incident", r.stderr)

    def test_empty_change_set_exits_non_zero(self):
        r = self.incident("amend", "I-001", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing to amend", r.stderr)
        self.assertEqual(self.path.read_text(), self.original)

    def test_an_empty_field_value_exits_non_zero(self):
        r = self.incident("amend", "I-001", "--what", "", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--what is empty", r.stderr)
        self.assertEqual(self.path.read_text(), self.original)

    def test_bad_status_value_is_rejected_by_the_parser(self):
        r = self.incident("amend", "I-001", "--status", "reopened", "--reason", "y")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid choice", r.stderr)
        self.assertEqual(self.path.read_text(), self.original)


class TestTemplateStaysClean(unittest.TestCase):

    def test_the_template_carries_no_amendment_note(self):
        """Every new incident is a copy of this template, so maintenance notes do not belong in it."""
        self.assertNotIn("amended", (config.TEMPLATES / "incident.md").read_text())


if __name__ == "__main__":
    unittest.main()
