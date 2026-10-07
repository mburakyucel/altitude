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
        self.assertEqual(github_intake.task_reference("Fix", f"Please implement {url}", ("acme", "widget")), 121)
        self.assertEqual(github_intake.task_reference("Implement GitHub issue #127", "focused brief", ("acme", "widget")), 127)
        for text in (url + "?tab=1", url + "/", url + "#fragment", url + "abc", url + "%2f",
                     "Fix https://github.com/acme/widget/pull/121", "Fix issue #121"):
            self.assertIsNone(github_intake.task_reference("ordinary task", text, ("acme", "widget")), text)
        with self.assertRaises(github_intake.IssueIntakeError):
            github_intake.task_reference("GitHub issue #1", f"and {url}", ("acme", "widget"))

    def test_external_references_stay_context_without_gh_fetch(self):
        external = "https://github.com/other/widget/issues/121"
        brief = f"Use {external} and https://github.com/another/repo/issues/2 as context."
        with mock.patch.object(github_intake.subprocess, "run", return_value=REMOTE) as run:
            task = T.new("p", "Capability change", brief, actor="burak")
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["git"])
        self.assertEqual((S.task_dir("p", task["slug"]) / "request.md").read_text(), brief + "\n")

    def test_external_context_does_not_conflict_with_local_parent(self):
        external = "https://github.com/other/repo/issues/1"
        local = "https://github.com/Acme/Widget/issues/121"
        briefs = [f"Compare {external} while implementing {local}",
                  f"Implement GitHub issue #121 with context {external}",
                  f"{local} and GitHub issue #121; context {external}"]
        gh = subprocess.CompletedProcess([], 0, json.dumps(ISSUE), "")
        for i, brief in enumerate(briefs):
            with self.subTest(brief=brief), mock.patch.object(github_intake.subprocess, "run", side_effect=[REMOTE, gh]) as run:
                task = T.new("p", f"Mixed references {i}", brief, actor="burak")
            self.assertIn(brief, (S.task_dir("p", task["slug"]) / "request.md").read_text())
            self.assertEqual(run.call_args.args[0][3:6], ["121", "--repo", "acme/widget"])

    def test_conflicting_local_parents_refuse_before_issue_fetch(self):
        with mock.patch.object(github_intake.subprocess, "run", return_value=REMOTE) as run:
            with self.assertRaisesRegex(T.TransitionError, "conflicting"):
                T.new("p", "Ambiguous local", "GitHub issue #121 and https://github.com/acme/widget/issues/122; "
                      "context https://github.com/other/repo/issues/9", actor="burak")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(list(S.tasks_dir("p").glob("ambiguous-local*")), [])

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
        cases = [("Fix https://github.com/acme/widget/issues/121",
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
