"""Static guardrails for the small, base-owned remote test workflow."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "remote-tests.yml"


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
        python_job, web_job = self.workflow.split("\n  web:\n", 1)
        for job in (python_job, web_job):
            self.assertEqual(job.count("- name: Validate event identity"), 1)
            self.assertEqual(job.count("- name: Checkout exact candidate"), 1)
            self.assertEqual(job.count("- name: Verify exact checkout"), 1)
            self.assertEqual(job.count("persist-credentials: false"), 1)
            for required in (
                "github.event.pull_request.head.sha || github.sha",
                "github.event.pull_request.head.repo.full_name || github.repository",
                'test "$WORKFLOW_SHA" = "$BASE_SHA"',
                'test "$CANDIDATE_REPOSITORY" = "$BASE_REPOSITORY"',
                "repository: ${{ env.BASE_REPOSITORY }}",
                "ref: ${{ env.EXPECTED_SHA }}",
                'test "$CHECKOUT_SHA" = "$EXPECTED_SHA"',
                "-C candidate rev-parse HEAD",
            ):
                self.assertEqual(job.count(required), 1, required)

    def test_runs_only_the_fixed_suite_in_a_sanitized_environment(self):
        test_step = self.workflow.split("- name: Run Python tests without repository credentials", 1)[1]
        self.assertIn("/usr/bin/env -i", test_step)
        self.assertIn("ALTITUDE_HOME=\"$altitude_home\"", test_step)
        self.assertIn("/usr/bin/python3 -m unittest discover tests", test_step)
        self.assertNotIn("github.token", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("GITHUB_TOKEN", test_step)

    def test_runs_web_checks_in_an_independent_exact_candidate_job(self):
        web_step = self.workflow.split("- name: Run web checks without repository credentials", 1)[1]
        self.assertIn(
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
            self.workflow,
        )
        self.assertIn("node-version: 24.14.0", self.workflow)
        self.assertEqual(self.workflow.count("Checkout exact candidate"), 2)
        python_job, web_job = self.workflow.split("\n  web:\n", 1)
        self.assertIn("Run Python tests without repository credentials", python_job)
        self.assertNotIn("Run web checks without repository credentials", python_job)
        self.assertIn("Run web checks without repository credentials", web_job)
        self.assertNotIn("Run Python tests without repository credentials", web_job)
        self.assertIn("cd candidate/web", web_step)
        self.assertIn('p.packageManager!=="pnpm@10.34.5"', web_step)
        self.assertIn("npm install --global --prefix", self.workflow)
        self.assertIn("--ignore-scripts pnpm@10.34.5", self.workflow)
        self.assertIn('test "$("$tool_home/bin/pnpm" --version)" = 10.34.5', self.workflow)
        self.assertIn("/usr/bin/env -i", web_step)
        self.assertIn('HOME="$test_home"', web_step)
        self.assertIn('XDG_CACHE_HOME="$cache_home"', web_step)
        self.assertIn("NPM_CONFIG_USERCONFIG=/dev/null", web_step)
        self.assertIn("run_pnpm install --frozen-lockfile --ignore-scripts", web_step)
        self.assertIn("run_pnpm test", web_step)
        self.assertIn("run_pnpm typecheck", web_step)
        self.assertIn("run_pnpm build", web_step)
        self.assertNotIn("github.token", web_step)
        self.assertNotIn("secrets.", web_step)
        self.assertNotIn("GITHUB_TOKEN", web_step)

    def test_candidate_execution_is_terminal_in_each_job(self):
        python_job, web_job = self.workflow.split("\n  web:\n", 1)
        python_tail = python_job.split("- name: Run Python tests without repository credentials", 1)[1]
        web_tail = web_job.split("- name: Run web checks without repository credentials", 1)[1]
        self.assertNotIn("\n      - name:", python_tail)
        self.assertNotIn("\n      - uses:", python_tail)
        self.assertNotIn("\n      - name:", web_tail)
        self.assertNotIn("\n      - uses:", web_tail)
        self.assertTrue(python_job.rstrip().endswith("/usr/bin/python3 -m unittest discover tests"))
        self.assertTrue(web_job.rstrip().endswith("run_pnpm build"))


if __name__ == "__main__":
    unittest.main()
