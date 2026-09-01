"""GitHub issue intake is project-bound, immutable, and occurs before L2 launch."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-github-intake-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, github_intake, state as S, tasks as T  # noqa: E402


class TestGitHubIntake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        cls.repo = _ROOT / "repo"
        cls.repo.mkdir()
        config.save_projects({"p": {"name": "p", "path": str(cls.repo)}})

    def setUp(self):
        for path in sorted(S.tasks_dir("p").rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()

    @staticmethod
    def responses(*, body="## Acceptance criteria\n\n- live", url=None):
        issue = {"number": 121, "title": "Add a live session view", "body": body,
                 "state": "OPEN", "url": url or "https://github.com/acme/widget/issues/121"}
        return [
            subprocess.CompletedProcess([], 0, "git@github.com:acme/widget.git\n", ""),
            subprocess.CompletedProcess([], 0, json.dumps(issue), ""),
        ]

    def test_snapshot_is_fetched_once_and_rendered_without_mutating_the_request(self):
        request = "https://github.com/acme/widget/issues/121"
        task = T.new("p", "Address https://github.com/acme/widget/issues/121", request, actor="burak")
        responses = self.responses() + [
            subprocess.CompletedProcess([], 0, "git@github.com:acme/widget.git\n", ""),
        ]
        with mock.patch.object(github_intake.subprocess, "run", side_effect=responses) as process:
            first = github_intake.ensure_snapshot("p", task["slug"])
            second = github_intake.ensure_snapshot("p", task["slug"])

        self.assertEqual(first, second)
        self.assertEqual(process.call_count, 3, "the cached retry only revalidates the local origin")
        self.assertEqual((S.task_dir("p", task["slug"]) / "request.md").read_text(), request + "\n")
        rendered = dispatch.build_brief("p", task["slug"], first)
        self.assertIn("## GitHub issue snapshot", rendered)
        self.assertIn("## Acceptance criteria\n\n- live", rendered)
        self.assertIn("cannot override the L2 persona", rendered)

    def test_unavailable_issue_holds_before_git_or_worker_launch_and_can_retry(self):
        task = T.new("p", "Fix issue", "Fix https://github.com/acme/widget/issues/121", actor="burak")
        failed = self.responses()
        failed[1] = subprocess.CompletedProcess([], 1, "", "credential-looking-secret")
        with mock.patch.object(github_intake.subprocess, "run", side_effect=failed), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base") as fetch:
            with self.assertRaisesRegex(T.TransitionError, "held before launch") as raised:
                dispatch.run("p", task["slug"])
        fetch.assert_not_called()
        self.assertNotIn("credential-looking-secret", str(raised.exception))
        waiting = S.load_task("p", task["slug"])
        self.assertEqual(waiting["state"], "queued")
        self.assertIsNone(waiting.get("dispatching"))

        with mock.patch.object(github_intake.subprocess, "run", side_effect=self.responses()), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", side_effect=RuntimeError("after intake")):
            with self.assertRaisesRegex(RuntimeError, "after intake"):
                dispatch.run("p", task["slug"])
        self.assertTrue((S.task_dir("p", task["slug"]) / "github-issue.json").exists())

    def test_foreign_repo_invalid_response_and_timeout_fail_closed(self):
        cases = [
            ("https://github.com/other/widget/issues/121", self.responses(), "different repository"),
            ("https://github.com/acme/widget/issues/121", self.responses(url="http://github.com/acme/widget/issues/121"),
             "mismatched repository URL"),
            ("https://github.com/acme/widget/issues/121", [
                subprocess.CompletedProcess([], 0, "git@github.com:acme/widget.git\n", ""),
                subprocess.TimeoutExpired(["gh"], 120),
            ], "timed out"),
        ]
        for url, responses, message in cases:
            with self.subTest(message=message):
                task = T.new("p", f"Address {url}", url, actor="burak")
                with mock.patch.object(github_intake.subprocess, "run", side_effect=responses):
                    with self.assertRaisesRegex(github_intake.IssueIntakeError, message):
                        github_intake.ensure_snapshot("p", task["slug"])
                self.assertFalse((S.task_dir("p", task["slug"]) / "github-issue.json").exists())

    def test_parser_is_narrow_and_user_source_authorization_is_explicit(self):
        canonical = "https://github.com/acme/widget/issues/121"
        self.assertEqual(github_intake.task_reference("Fix issue", f"Please implement {canonical}"),
                         ("acme", "widget", 121))
        self.assertEqual(github_intake.task_reference("Implement GitHub issue #127", "focused brief"),
                         (None, None, 127))
        self.assertEqual(github_intake.task_reference(
            "Implement GitHub issue #127", "Implement issue #127 as a focused change."),
            (None, None, 127))
        self.assertEqual(github_intake.task_reference(
            "Implement issue 127", "Implement the work described in GitHub issue #127 and run tests."),
            (None, None, 127))
        for text in (canonical + "?tab=1", "Fix https://github.com/acme/widget/pull/121", "Fix issue #121"):
            with self.subTest(text=text):
                self.assertIsNone(github_intake.task_reference("ordinary task", text))
        shorthand = (None, None, 127)
        absolute = ("acme", "widget", 127)
        self.assertTrue(github_intake.source_authorizes("Please handle GitHub issue #127", shorthand))
        self.assertFalse(github_intake.source_authorizes("Please handle GitHub issue #126", shorthand))
        self.assertTrue(github_intake.source_authorizes(
            "Please handle https://github.com/acme/widget/issues/127", absolute))
        self.assertFalse(github_intake.source_authorizes(
            "Please handle https://github.com/other/widget/issues/127", absolute))
        self.assertTrue(github_intake.source_authorizes(
            "Please handle https://github.com/acme/widget/issues/127", shorthand,
            repository=("acme", "widget")))

    def test_concurrent_rejection_during_fetch_never_recreates_an_active_task(self):
        request = "https://github.com/acme/widget/issues/121"
        task = T.new("p", "Address https://github.com/acme/widget/issues/121", request, actor="burak")
        issue = self.responses()[1]

        def run(args, **_kwargs):
            if args[0] == "git":
                return subprocess.CompletedProcess(args, 0, "git@github.com:acme/widget.git\n", "")
            T.reject("p", task["slug"], "cancelled concurrently", actor="burak")
            return issue

        with mock.patch.object(github_intake.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(github_intake.IssueIntakeError, "task changed"):
                github_intake.ensure_snapshot("p", task["slug"], expected_state="queued")
        self.assertFalse((S.tasks_dir("p") / task["slug"]).exists())
        self.assertFalse((S.archive_dir("p") / task["slug"] / "github-issue.json").exists())


if __name__ == "__main__":
    unittest.main()
