"""`alt task save report` refuses a report that departs from schemas/report.json, naming each path and what the schema
expects there, and a report that leaves `blocked` out says nothing is blocked."""
import json
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, state as S, tasks as T, verify

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
        self.assertIn("`review` is not a list: a list of findings",
                      self.refused({"landed": LANDED, "review": "none"}))
        self.assertIn("`landed.prs[0]` is not an object",
                      self.refused({"landed": {**LANDED, "prs": [47]}, "review": []}))
        self.assertIn("`blocked` is not text or null: the exact block",
                      self.refused({"landed": LANDED, "review": [], "blocked": True}))
        self.assertIn("`review[0].disposition` is not one of fixed, dismissed, open",
                      self.refused({"landed": LANDED, "review": [{"summary": "Fixture finding.",
                                                                  "disposition": "done", "reason": None}]}))
        self.assertIn("`landed.prs[0].merged` is not true or false",
                      self.refused({"landed": {**LANDED, "prs": [{**LANDED["prs"][0], "merged": 1}]}, "review": []}))

    #: A merged pull request and a main run written with keys the schema does not name, and unknown top-level keys.
    MISNAMED = {"landed": {"prs": [{"number": 47, "title": "Fixture delivery", "merge_commit": "abc1234",
                                    "merged_at": "2026-01-01T00:00:00Z"}],
                           "main_runs": [{"conclusion": "success"}], "deploy": "not-applicable"},
                "review": [], "status": "done", "summary": "Fixture delivery landed."}

    def test_nested_missing_and_unknown_keys_are_refused_by_path(self):
        message = self.refused(self.MISNAMED)
        for problem in (
                "`landed.prs[0]` lacks `merged`: whether GitHub merged the pull request",
                "`landed.prs[0]` lacks `merge_sha`: GitHub merge commit, in full or an unambiguous prefix",
                "`landed.prs[0].merge_commit` is not a known key: `landed.prs[0]` takes `number`, `title`, `merged`, "
                "`merge_sha`",
                "`landed.prs[0].merged_at` is not a known key",
                "`landed.main_runs[0]` lacks `id`: the GitHub Actions run id on main",
                "`status` is not a known key: report.json takes `landed`, `review`",
                "`summary` is not a known key"):
            self.assertIn(problem, message)
        self.assertNotIn("differs from GitHub", message)

    def test_the_verifier_hands_back_misnamed_keys_instead_of_a_github_mismatch(self):
        S.write_json(self.path, self.MISNAMED)
        with mock.patch.object(verify, "gh") as gh:
            verdict = verify._verify(self.project, self.slug)
        gh.assert_not_called()
        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertEqual(verdict["problems"], verify.report_problems(self.MISNAMED))
        self.assertIn("`landed.prs[0]` lacks `merge_sha`: GitHub merge commit, in full or an unambiguous prefix of at "
                      "least seven characters", verdict["problems"])

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

    def test_the_schema_uses_only_what_the_check_reads(self):
        """A keyword, type or open object the check does not read would let a report through unchecked."""
        read = {"$schema", "type", "description", "enum", "required", "properties", "additionalProperties", "items"}

        def nodes(node):
            yield node
            for child in [*(node.get("properties") or {}).values(), *([node["items"]] if "items" in node else [])]:
                yield from nodes(child)

        for node in nodes(json.loads((config.SCHEMAS / "report.json").read_text())):
            self.assertLessEqual(set(node), read, node)
            types = node.get("type")
            self.assertLessEqual(set([types] if isinstance(types, str) else types or []), set(verify._TYPES), node)
            self.assertIn(node.get("additionalProperties", False), (False,), node)  # the check reads no other value
            if "properties" in node:  # an object with named keys refuses any other; spend and decisions stay open
                self.assertIs(node.get("additionalProperties"), False, node)
