"""Reviewer findings exposed by the compact L1 status view."""
import unittest
from unittest.mock import patch

from altitude import l1


class TestL1ReviewView(unittest.TestCase):
    def _status(self, *, role="reviewer", summary=None, error=None, structured=None):
        record = {
            "name": "reviewer-1" if role == "reviewer" else "implementer-1",
            "role": role,
            "engine": "codex",
            "why": "test",
            "model": "test-model",
            "branch": "test-branch",
            "worktree": "/tmp/test-worktree",
            "started": "2026-08-30T00:00:00Z",
            "done": "2026-08-30T00:01:00Z",
            "result": {"pr": 42, "summary": summary, "error": error, "usage": {"input_tokens": 1},
                       "structured": structured},
        }
        with patch.object(l1, "list_runs", return_value=[record]):
            return l1.status("altitude", "review-view")[0]

    def test_reviewer_findings_and_derived_summary(self):
        severities = ["blocking"] * 2 + ["major"] * 4 + ["minor"] * 5
        findings = [{"severity": severity, "claim": f"{severity} finding"} for severity in severities]

        status = self._status(summary=None, structured={"findings": findings})

        self.assertEqual(status["findings"], findings)
        self.assertEqual(status["summary"], "11 findings: 2 blocking, 4 major, 5 minor")

    def test_reviewer_empty_findings_is_a_clean_review(self):
        status = self._status(summary=None, structured={"findings": []})

        self.assertEqual(status["findings"], [])
        self.assertEqual(status["summary"], "no findings")
        self.assertIsNone(status["error"])

    def test_implementer_compact_record_is_unchanged(self):
        status = self._status(role="implementer", summary="persisted summary", structured={"findings": []})

        self.assertNotIn("findings", status)
        self.assertEqual(status["summary"], "persisted summary")

    def test_reviewer_without_findings_or_summary_is_an_error(self):
        status = self._status(summary=None, structured=None)

        self.assertIsNone(status["findings"])
        self.assertEqual(status["error"], "reviewer returned no findings and no summary")

    def test_reviewer_existing_error_is_preserved(self):
        status = self._status(summary=None, error="engine fault", structured={"findings": "invalid"})

        self.assertIsNone(status["findings"])
        self.assertEqual(status["error"], "engine fault")


if __name__ == "__main__":
    unittest.main()
