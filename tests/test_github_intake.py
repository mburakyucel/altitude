"""A task that names a GitHub issue gets the issue inlined into its request at creation, from the project's repo only."""
import json
import subprocess
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import github_intake, state as S, tasks as T

ISSUE = {"number": 121, "title": "Add a live session view", "body": "## Acceptance criteria\n\n- live", "state": "OPEN"}
REMOTE = subprocess.CompletedProcess([], 0, "git@github.com:acme/widget.git\n", "")


class TestGitHubIntake(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register("p", path=self.repo)

    def test_reference_parsing(self):
        url = "https://github.com/acme/widget/issues/121"
        self.assertEqual(github_intake.task_reference("Fix", f"Please implement {url}"), ("acme", "widget", 121))
        self.assertEqual(github_intake.task_reference("Implement GitHub issue #127", "focused brief"), (None, None, 127))
        for text in (url + "?tab=1", "Fix https://github.com/acme/widget/pull/121", "Fix issue #121"):
            self.assertIsNone(github_intake.task_reference("ordinary task", text), text)
        with self.assertRaises(github_intake.IssueIntakeError):
            github_intake.task_reference("GitHub issue #1", f"and {url}")

    def test_issue_is_inlined_once_at_creation(self):
        gh = subprocess.CompletedProcess([], 0, json.dumps(ISSUE), "")
        with mock.patch.object(github_intake.subprocess, "run", side_effect=[REMOTE, gh]) as run:
            task = T.new("p", "Address GitHub issue #121", "Do it well.", actor="burak")
        self.assertEqual(run.call_args.args[0][:6], ["gh", "issue", "view", "121", "--repo", "acme/widget"])
        request = (S.task_dir("p", task["slug"]) / "request.md").read_text()
        self.assertTrue(request.startswith("Do it well.\n\n## GitHub issue\n"), request)
        self.assertIn("## Acceptance criteria\n\n- live", request)
        self.assertIn("URL: https://github.com/acme/widget/issues/121\nState: open", request)
        with mock.patch.object(github_intake.subprocess, "run") as run:
            T.new("p", "Plain task", "No issue here.", actor="burak")
        run.assert_not_called()

    def test_failures_refuse_the_task_without_leaking_output(self):
        cases = [("https://github.com/other/widget/issues/121", [REMOTE], "different repository"),
                 ("Fix https://github.com/acme/widget/issues/121",
                  [REMOTE, subprocess.CompletedProcess([], 1, "", "credential-looking-secret")], "could not be read"),
                 ("Fix GitHub issue #121", [REMOTE, subprocess.TimeoutExpired(["gh"], 120)], "timed out")]
        for request, responses, message in cases:
            with self.subTest(message=message), mock.patch.object(github_intake.subprocess, "run", side_effect=responses):
                with self.assertRaisesRegex(T.TransitionError, message) as raised:
                    T.new("p", "Issue task", request, actor="burak")
                self.assertNotIn("credential-looking-secret", str(raised.exception))
        self.assertEqual(list(S.tasks_dir("p").glob("issue-task*")), [])


if __name__ == "__main__":
    unittest.main()
