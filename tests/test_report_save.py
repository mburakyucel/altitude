"""`alt task save report` refuses a report the verifier could not read, naming the key or section and the shape it
expects, and a report that leaves `blocked` out says nothing is blocked (schemas/report.json)."""
import json
from unittest import mock

from tests.support import AltitudeCase
from altitude import state as S, tasks as T, verify

LANDED = {"prs": [{"number": 47, "title": "Fixture delivery", "merged": True, "merge_sha": "abc1234"}],
          "main_runs": [{"id": "7", "conclusion": "success"}], "deploy": "not-applicable"}


class ReportSave(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Report save", "Save a final report.")
        self.slug = task["slug"]
        task.update(state="running", attempt=1)
        S.save_task(self.project, task)
        self.path = S.task_dir(self.project, self.slug) / "report.json"

    def save(self, report: dict) -> dict:
        return T.save_owner_file(self.project, self.slug, "report", json.dumps(report), expected_attempt=1)

    def refused(self, report: dict) -> str:
        with self.assertRaises(T.TransitionError) as caught:
            self.save(report)
        self.assertFalse(self.path.exists())
        return str(caught.exception)

    def test_a_report_without_blocked_saves_and_verifies_as_not_blocked(self):
        self.save({"landed": LANDED, "review": []})
        with mock.patch.object(verify, "gh", return_value={"state": "MERGED"}):
            verdict = verify._verify(self.project, self.slug)
        self.assertEqual((verdict["verdict"], verdict["problems"]), ("ok", []))

    def test_null_or_empty_blocked_also_reads_as_not_blocked(self):
        for blocked in (None, ""):
            self.save({"landed": LANDED, "review": [], "blocked": blocked})
            with mock.patch.object(verify, "gh", return_value={"state": "MERGED"}):
                self.assertEqual(verify._verify(self.project, self.slug)["verdict"], "ok", blocked)

    def test_a_missing_required_key_is_refused_with_its_expected_shape(self):
        message = self.refused({"landed": LANDED, "blocked": ""})
        self.assertIn("report.json lacks `review`: a list of findings, each with `summary`, `disposition` and "
                      "`reason`: [] when there were none", message)
        message = self.refused({"review": []})
        self.assertIn("report.json lacks `landed`: an object with `prs`, `main_runs` and `deploy`", message)

    def test_a_wrong_shaped_section_is_refused_by_name(self):
        self.assertIn("report.review: expected a list of objects",
                      self.refused({"landed": LANDED, "review": "none"}))
        self.assertIn("report.landed.prs: expected a list of objects",
                      self.refused({"landed": {**LANDED, "prs": [47]}, "review": []}))
        self.assertIn("report.blocked: expected text",
                      self.refused({"landed": LANDED, "review": [], "blocked": True}))

    def test_the_verifier_hands_back_the_same_problems_for_a_report_written_another_way(self):
        S.write_json(self.path, {"landed": LANDED})
        verdict = verify._verify(self.project, self.slug)
        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertEqual(verdict["problems"], verify.report_problems({"landed": LANDED}))

    def test_a_valid_report_is_stored_as_written(self):
        report = {"landed": LANDED, "review": [{"summary": "Fixture finding.", "disposition": "fixed",
                                                "reason": "Fixed in the PR."}],
                  "follow_ups": ["Fixture follow-up."]}
        self.save(report)
        self.assertEqual(json.loads(self.path.read_text()), report)
