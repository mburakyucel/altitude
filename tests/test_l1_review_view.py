"""Reviewer findings exposed by the compact L1 status view."""
import unittest
from unittest.mock import patch

from altitude import l1


class TestL1ReviewView(unittest.TestCase):
    def _status(self, *, role="reviewer", summary=None, error=None, structured=None,
                done="2026-08-30T00:01:00Z"):
        record = {
            "name": "reviewer-1" if role == "reviewer" else "implementer-1",
            "role": role,
            "engine": "codex",
            "why": "test",
            "model": "test-model",
            "branch": "test-branch",
            "worktree": "/tmp/test-worktree",
            "started": "2026-08-30T00:00:00Z",
            "done": done,
            "result": {"pr": 42, "summary": summary, "error": error, "usage": {"input_tokens": 1},
                       "structured": structured, "reviewed_base_sha": "a" * 40},
        }
        with patch.object(l1, "list_runs", return_value=[record]):
            return l1.status("altitude", "review-view")[0]

    def test_reviewer_findings_and_derived_summary(self):
        severities = ["blocking"] * 2 + ["major"] * 4 + ["minor"] * 5
        findings = [{"severity": severity, "claim": f"{severity} finding"} for severity in severities]

        status = self._status(summary=None, structured={"findings": findings})

        self.assertEqual(status["findings"], findings)
        self.assertEqual(status["summary"], "11 findings: 2 blocking, 4 major, 5 minor")

    def test_reviewer_summary_omits_zero_buckets_and_counts_other(self):
        findings = [{"severity": "blocking"}, {"severity": "nit"}, "junk"]

        status = self._status(summary=None, structured={"findings": findings})

        self.assertEqual(status["summary"], "3 findings: 1 blocking, 2 other")

    def test_reviewer_non_string_severity_counts_as_other(self):
        findings = [{"severity": ["blocking"]}]

        status = self._status(summary=None, structured={"findings": findings})

        self.assertEqual(status["summary"], "1 findings: 1 other")

    def test_reviewer_empty_findings_is_a_clean_review(self):
        status = self._status(summary=None, structured={"findings": []})

        self.assertEqual(status["findings"], [])
        self.assertEqual(status["summary"], "no findings")
        self.assertIsNone(status["error"])
        self.assertEqual(status["reviewed_base_sha"], "a" * 40)

    def test_implementer_compact_record_is_unchanged(self):
        status = self._status(role="implementer", summary="persisted summary", structured={"findings": []})

        self.assertNotIn("findings", status)
        self.assertEqual(status["summary"], "persisted summary")

    def test_reviewer_without_findings_or_summary_is_an_error(self):
        status = self._status(summary=None, structured=None)

        self.assertIsNone(status["findings"])
        self.assertEqual(status["error"], "reviewer returned no findings and no summary")

    def test_in_flight_reviewer_without_findings_or_summary_is_not_an_error(self):
        with patch.object(l1, "_alive", return_value=True):
            status = self._status(done=None, summary=None, structured=None)

        self.assertIsNone(status["findings"])
        self.assertIsNone(status["error"])

    def test_finished_reviewer_with_summary_is_not_an_error(self):
        status = self._status(structured=None, summary="PR #7 landed")

        self.assertIsNone(status["findings"])
        self.assertEqual(status["summary"], "PR #7 landed")
        self.assertIsNone(status["error"])

    def test_reviewer_existing_error_is_preserved(self):
        status = self._status(summary=None, error="engine fault", structured={"findings": "invalid"})

        self.assertIsNone(status["findings"])
        self.assertEqual(status["error"], "engine fault")


if __name__ == "__main__":
    unittest.main()
