"""Static guardrails for the small, base-owned remote test workflow."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "trusted-remote-tests.yml"


class RemoteCIWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()

    def test_uses_base_owned_events_and_read_only_permissions(self):
        self.assertIn("pull_request_target:", self.workflow)
        self.assertIn("push:\n    branches: [main]", self.workflow)
        self.assertNotIn("workflow_dispatch:", self.workflow)
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)

    def test_binds_and_verifies_the_exact_event_candidate(self):
        self.assertIn("github.event.pull_request.head.sha || github.sha", self.workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name || github.repository", self.workflow)
        self.assertIn('test "$WORKFLOW_SHA" = "$BASE_SHA"', self.workflow)
        self.assertIn('test "$CHECKOUT_SHA" = "$EXPECTED_SHA"', self.workflow)
        self.assertIn("-C candidate rev-parse HEAD", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)

    def test_runs_only_the_fixed_suite_in_a_sanitized_environment(self):
        test_step = self.workflow.split("- name: Run Python tests without repository credentials", 1)[1]
        self.assertIn("/usr/bin/env -i", test_step)
        self.assertIn("ALTITUDE_HOME=\"$altitude_home\"", test_step)
        self.assertIn("/usr/bin/python3 -m unittest discover tests", test_step)
        self.assertNotIn("github.token", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("GITHUB_TOKEN", test_step)


if __name__ == "__main__":
    unittest.main()
