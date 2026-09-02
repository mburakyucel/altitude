"""Reviewer findings exposed by the non-authoritative compact helper view."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-review-view-")

from altitude import config, engines, l1
from tests.physical_fixture import helper_record


def _record(*, done=True, summary=None, error=None, findings=None, role="reviewer"):
    with mock.patch.object(config, "project_path", return_value=Path("/tmp/fixture-repo")):
        record = helper_record("project", "task", terminal="spawned")
    record["role"] = role
    record["paths"] = [] if role == "reviewer" else ["tests"]
    physical = record["physical"]
    if done:
        unit = physical["process_unit_id"]
        if error:
            physical = engines.note_physical_transition_error(physical, error)
        physical = engines.advance_physical_transition(
            physical, "spawned", "bound",
            {"bound": True, "physical_worker_id": unit, "provider_session_id": "thread-1"})
        physical = engines.advance_physical_transition(
            physical, "bound", "result_observed", {"result_id": "result-1", "sha256": "a" * 64})
        physical = engines.advance_physical_transition(physical, "result_observed", "empty", {
            "process_unit_id": unit, "load_state": "not-found", "active_state": "inactive",
            "sub_state": "dead", "control_group": "", "population": "empty", "empty": True})
        terminal = "failed" if error else "complete"
        physical = engines.advance_physical_transition(physical, "empty", terminal, {"status": terminal})
        record["result"] = {
            "provider_session_id": "thread-1", "status": terminal, "summary": summary, "error": error,
            "patch": None, "findings": findings, "usage": {"input_tokens": 1},
        }
    record["physical"] = physical
    return record


class TestL1ReviewView(unittest.TestCase):
    def _status(self, **kwargs):
        with mock.patch.object(l1, "list_runs", return_value=[_record(**kwargs)]):
            return l1.status("project", "task")[0]

    def test_findings_and_trusted_summary_are_projected_without_a_second_reducer(self):
        findings = ([{"severity": "blocking"}] * 2 + [{"severity": "major"}] * 4
                    + [{"severity": "minor"}] * 5)
        view = self._status(findings=findings, summary="reviewed 11 findings")
        self.assertEqual(view["findings"], findings)
        self.assertEqual(view["summary"], "reviewed 11 findings")

    def test_empty_findings_is_a_clean_review(self):
        view = self._status(findings=[])
        self.assertEqual(view["findings"], [])
        self.assertIsNone(view["summary"])
        self.assertIsNone(view["error"])

    def test_missing_terminal_review_result_is_not_reinterpreted_by_the_view(self):
        view = self._status(findings=None)
        self.assertIsNone(view["error"])

    def test_in_flight_review_has_no_synthetic_error(self):
        view = self._status(done=False)
        self.assertFalse(view["done"])
        self.assertIsNone(view["error"])

    def test_parent_capability_and_result_identity_are_never_projected(self):
        view = self._status(findings=[])
        self.assertNotIn("parent", view)
        self.assertNotIn("capability_id", str(view))
        self.assertNotIn("owner_result_id", str(view))


if __name__ == "__main__":
    unittest.main()
