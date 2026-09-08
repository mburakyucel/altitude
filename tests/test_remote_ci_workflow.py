"""Static guardrails for the small, base-owned remote test workflow."""

import unittest

from tests.support import REPO

WORKFLOW = REPO / ".github" / "workflows" / "remote-tests.yml"


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
        self.assertIn('test "$CANDIDATE_REPOSITORY" = "$BASE_REPOSITORY"', self.workflow)
        self.assertIn("repository: ${{ env.BASE_REPOSITORY }}", self.workflow)
        self.assertIn('test "$CHECKOUT_SHA" = "$EXPECTED_SHA"', self.workflow)
        self.assertIn("-C candidate rev-parse HEAD", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)

    def test_runs_full_fixed_suites_in_a_sanitized_environment(self):
        test_step = self.workflow.split("- name: Run full deterministic checks without repository credentials", 1)[1]
        test_step = test_step.split("- name: Upload fictional test results", 1)[0]
        self.assertIn("/usr/bin/env -i", test_step)
        self.assertIn("ALTITUDE_HOME=\"$altitude_home\"", test_step)
        for command in ("/usr/bin/python3 -m unittest discover tests", "pnpm test", "pnpm build", "pnpm ui"):
            self.assertIn(f"/usr/bin/time -p {command}", test_step)
        self.assertIn("--noprofile --norc -euo pipefail", test_step)
        self.assertNotIn("make check", test_step)
        self.assertNotIn("github.token", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("GITHUB_TOKEN", test_step)
        self.assertNotIn("UI_BASE_URL", test_step)
        self.assertNotIn("UI_PROJECT", test_step)

    def test_pins_supported_tooling_and_keeps_results_short_lived(self):
        self.assertIn("node-version: '22.22.2'", self.workflow)
        self.assertIn("package-manager-cache: false", self.workflow)
        self.assertIn("--ignore-scripts pnpm@10.34.5", self.workflow)
        self.assertIn("pnpm --dir web install --frozen-lockfile", self.workflow)
        self.assertIn("pnpm exec playwright install --with-deps chromium", self.workflow)
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH="$test_home/browsers"', self.workflow)
        self.assertIn("retention-days: 3", self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("candidate/web/ui-artifacts/results/", self.workflow)
        for line in self.workflow.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"uses: actions/[a-z-]+@[0-9a-f]{40}(?: |$)")


if __name__ == "__main__":
    unittest.main()
