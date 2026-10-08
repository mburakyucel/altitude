"""The required check's workflow shape, and its candidate assertions executed on real, isolated Git."""
import json
import os
import re
import subprocess
import textwrap

from tests.support import AltitudeCase, REPO, git, make_repo
from altitude import config

WORKFLOWS = REPO / '.github/workflows'


class TestRequiredCheckWorkflow(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.workflow = (REPO / config.PR_CHECK_WORKFLOW).read_text()
        self.job = self.workflow.split('\njobs:\n')[1]

    def test_one_hosted_job_with_the_required_id_runs_for_every_pull_request(self):
        self.assertEqual(config.PR_CHECK_NAME, 'check')
        self.assertEqual(re.findall(r'^  ([\w-]+):$', self.job, re.M), [config.PR_CHECK_NAME])
        self.assertIn('\n    runs-on: ubuntu-latest\n', self.job)
        # A job condition or `name:` would skip or rename the run `alt land` and release.yml select.
        self.assertIsNone(re.search(r'^    (if|name):', self.job, re.M))
        self.assertIn('on:\n  workflow_dispatch:\n  pull_request:\n    branches: [main]\n'
                      '  push:\n    branches: [main]\n', self.workflow)
        self.assertNotIn('pull_request_target', self.workflow)
        self.assertIn('timeout-minutes: 60', self.job)

    def test_fork_and_owner_runs_share_a_read_only_token_without_secrets(self):
        self.assertIn('\npermissions:\n  contents: read\n', self.workflow)
        self.assertEqual(self.job.count('permissions:'), 0)
        self.assertIsNone(re.search(r'\bsecrets\.\w', self.workflow))
        self.assertIn('persist-credentials: false', self.job)

    def test_full_suite_runs_after_frozen_install_and_failure_keeps_the_browser_report(self):
        steps = self.job.split('\n      - ')
        install = next(step for step in steps if step.startswith('name: Install frozen dependencies'))
        self.assertIn('pnpm --dir web install --frozen-lockfile', install)
        self.assertIn('playwright install --with-deps chromium', install)
        self.assertIn('name: Full deterministic checks\n        run: make check\n', self.job)
        report = steps[-1]
        self.assertIn('if: failure()', report)
        self.assertIn('actions/upload-artifact@', report)
        self.assertIn('path: web/ui-artifacts/report', report)

    def test_no_workflow_reaches_a_self_hosted_runner(self):
        self.assertFalse((WORKFLOWS / 'self-hosted-checks.yml').exists())
        for workflow in WORKFLOWS.glob('*.yml'):
            with self.subTest(workflow=workflow.name):
                runners = re.findall(r'runs-on:\s*(.+)', workflow.read_text())
                self.assertTrue(runners)
                self.assertTrue(all(runner.startswith(('ubuntu-', 'macos-')) for runner in runners), runners)

    def test_release_requires_the_push_run_of_this_workflow_and_job(self):
        release = (WORKFLOWS / 'release.yml').read_text()
        self.assertIn(f'actions/workflows/{os.path.basename(config.PR_CHECK_WORKFLOW)}/runs?', release)
        self.assertIn(f'select(.name == "{config.PR_CHECK_NAME}" and .conclusion == "success")', release)


class TestCandidateIdentity(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.base = git('rev-parse', 'HEAD', cwd=self.repo).strip()
        (self.repo / 'change').write_text('candidate\n')
        git('add', 'change', cwd=self.repo)
        git('commit', '-qm', 'fixture head', cwd=self.repo)
        self.head = git('rev-parse', 'HEAD', cwd=self.repo).strip()
        self.tree = git('rev-parse', 'HEAD^{tree}', cwd=self.repo).strip()
        self.event = self.tmp / 'event.json'
        self.event.write_text(json.dumps({'pull_request': {'base': {'sha': self.base},
                                                        'head': {'sha': self.head}}}))
        self.env = dict(os.environ, GITHUB_EVENT_NAME='pull_request', GITHUB_EVENT_PATH=str(self.event))
        workflow = (REPO / config.PR_CHECK_WORKFLOW).read_text()
        step = workflow.split('- name: Verify the exact candidate')[1].split('\n      - ')[0]
        self.script = textwrap.dedent(step.split('run: |\n')[1])
        self.checkout_candidate(self.tree, self.base, self.head)

    def checkout_candidate(self, tree, *parents):
        args = ['commit-tree', tree]
        for parent in parents:
            args += ['-p', parent]
        self.sha = git(*args, '-m', 'fixture merge candidate', cwd=self.repo).strip()
        git('checkout', '--detach', self.sha, cwd=self.repo)
        self.env['GITHUB_SHA'] = self.sha

    def verify(self):
        return subprocess.run(['bash', '-eo', 'pipefail', '-c', self.script], cwd=self.repo,
                              env=self.env, capture_output=True, text=True)

    def test_current_merge_tree_passes(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_sha_parent_order_or_tree_refuses(self):
        self.env['GITHUB_SHA'] = self.head
        self.assertNotEqual(self.verify().returncode, 0)
        self.checkout_candidate(self.tree, self.head, self.base)
        self.assertNotEqual(self.verify().returncode, 0)
        self.checkout_candidate(git('rev-parse', self.base + '^{tree}', cwd=self.repo).strip(),
                                self.base, self.head)
        self.assertNotEqual(self.verify().returncode, 0)

    def test_divergent_base_refuses_even_when_tree_equals_head(self):
        git('checkout', '--detach', self.base, cwd=self.repo)
        (self.repo / 'main-change').write_text('concurrent main\n')
        git('add', 'main-change', cwd=self.repo)
        git('commit', '-qm', 'main advanced', cwd=self.repo)
        new_base = git('rev-parse', 'HEAD', cwd=self.repo).strip()
        self.event.write_text(json.dumps({'pull_request': {'base': {'sha': new_base},
                                                        'head': {'sha': self.head}}}))
        self.checkout_candidate(self.tree, new_base, self.head)
        self.assertNotEqual(self.verify().returncode, 0)

    def test_main_and_dispatched_runs_check_only_the_exact_commit(self):
        self.event.write_text('{}')
        for event in ('push', 'workflow_dispatch'):
            with self.subTest(event=event):
                self.env['GITHUB_EVENT_NAME'] = event
                self.env['GITHUB_SHA'] = self.sha
                self.assertEqual(self.verify().returncode, 0, self.verify().stderr)
                self.env['GITHUB_SHA'] = self.head
                self.assertNotEqual(self.verify().returncode, 0)
