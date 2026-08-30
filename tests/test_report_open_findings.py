"""Blocked reports may preserve unresolved review findings for resumption."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-open-findings-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, l3, server, state as S, verify  # noqa: E402

PROJECT = "open-findings"


class TestReportOpenFindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        projects = config.load_projects()
        projects[PROJECT] = {"name": PROJECT, "path": _TMP, "stacks": ["python"]}
        config.save_projects(projects)

    def _report(self, slug: str, review: list[dict], blocked: str) -> tuple[dict, dict]:
        task = {
            "slug": slug,
            "title": slug,
            "class": "S",
            "state": "blocked" if blocked else "reported",
            "created": S.now(),
            "envelope": {},
            "estimate": {},
        }
        S.save_task(PROJECT, task)
        directory = S.task_dir(PROJECT, slug)
        (directory / "progress.md").write_text("complete\n")
        report = {
            "landed": {
                "prs": [{"number": 47, "title": "Open findings", "merged": True, "merge_sha": "abc123"}],
                "main_runs": [],
                "deploy": "not-applicable",
            },
            "review": review,
            "deviations": [],
            "decisions": [],
            "fyi": [],
            "blocked": blocked,
            "follow_ups": [],
            "spend": {"turns": 3, "subagent_launches": 1, "retries": 0, "model_tiers": "coding", "reverts": 0},
            "roadmap_complete": True,
        }
        S.write_json(directory / "report.json", report)
        with mock.patch.object(verify, "gh", return_value={"state": "MERGED"}):
            return task, verify._verify(PROJECT, slug)

    def test_blocked_report_accepts_open_finding_and_surfaces_it_in_the_header(self):
        finding_text = "The retry path still loses the original finding text."
        task, verdict = self._report(
            "blocked-open",
            [{"tag": "resume-text", "severity": "major", "summary": finding_text,
              "disposition": "open", "reason": "Needs the blocked dependency."}],
            "Waiting for the dependency owner.",
        )

        self.assertEqual(verdict["verdict"], "blocked")
        self.assertEqual(verdict["problems"], [])
        self.assertIn("1 open review findings", verdict["signals"])

        with mock.patch.object(l3, "turn", return_value={}) as turn:
            server.report_turn(PROJECT, task, verdict)
        header = turn.call_args.args[1]
        self.assertIn("Post-mortem signals:", header)
        self.assertIn("1 open review findings", header)

    def test_unblocked_report_rejects_open_finding_with_named_problem(self):
        _, verdict = self._report(
            "unblocked-open",
            [{"tag": "must-block", "severity": "minor", "summary": "This remains unresolved.",
              "disposition": "open", "reason": "Deferred."}],
            "",
        )

        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertIn("open finding on an unblocked report", verdict["problems"])
        self.assertIn("1 open review findings", verdict["signals"])

    def test_fixed_and_dismissed_findings_remain_clean(self):
        _, verdict = self._report(
            "resolved-findings",
            [
                {"tag": "resolved", "severity": "major", "summary": "Resolved.",
                 "disposition": "fixed", "reason": "Fixed in the PR."},
                {"tag": "not-applicable", "severity": "minor", "summary": "Not applicable.",
                 "disposition": "dismissed", "reason": "Outside the behavior."},
            ],
            "",
        )

        self.assertEqual(verdict["verdict"], "ok")
        self.assertEqual(verdict["problems"], [])
        self.assertFalse(any("open review findings" in signal for signal in verdict["signals"]))

    def test_schema_keeps_existing_dispositions_and_allows_open(self):
        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "report.json"
        schema = json.loads(schema_path.read_text())

        self.assertEqual(
            schema["properties"]["review"]["items"]["properties"]["disposition"]["enum"],
            ["fixed", "dismissed", "open"],
        )


if __name__ == "__main__":
    unittest.main()
