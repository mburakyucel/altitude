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
HEAD_SHA = "b" * 40
BASE_SHA = "a" * 40
MERGE_SHA = "c" * 40
RUN_ID = 47001


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
            "dispatch_id": f"{slug}-1",
            "attempt": 1,
        }
        S.save_task(PROJECT, task)
        directory = S.task_dir(PROJECT, slug)
        (directory / "progress.md").write_text("complete\n")
        report = {
            "landed": {
                "prs": [{"number": 47, "title": "Open findings", "merged": True, "merge_sha": MERGE_SHA}],
                "main_runs": [{"id": str(RUN_ID), "conclusion": "success"}],
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
        (directory / "report.md").write_text("report\n")
        S.write_json(directory / "report.json", report)
        S.write_json(directory / "merge-request-47.json", {
            "version": 1, "project": PROJECT, "slug": slug, "pr": 47,
            "generation": f"merge-{slug}", "dispatch_id": task["dispatch_id"], "task_attempt": 1,
            "base_sha": BASE_SHA, "head_sha": HEAD_SHA, "state": "merged",
            "result": {"merged": True, "base_sha": BASE_SHA, "head_sha": HEAD_SHA,
                       "merge_sha": MERGE_SHA, "gate_mode": "github-actions", "candidate_gate": None},
        })

        def github(args, cwd):
            if args[:2] == ["pr", "view"]:
                return {"number": 47, "state": "MERGED", "mergedAt": S.now(),
                        "mergeCommit": {"oid": MERGE_SHA}, "headRefName": f"worktree-{slug}",
                        "headRefOid": HEAD_SHA}
            if args[:2] == ["run", "view"]:
                return {"databaseId": RUN_ID, "headSha": MERGE_SHA,
                        "status": "completed", "conclusion": "success"}
            raise AssertionError(args)

        with mock.patch.object(verify, "gh", side_effect=github):
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

    def test_fixed_and_dismissed_findings_add_no_review_problem(self):
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

        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertIn(verify.TRUSTED_REMOTE_PENDING, verdict["problems"])
        self.assertFalse(any("open finding" in problem for problem in verdict["problems"]))
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
