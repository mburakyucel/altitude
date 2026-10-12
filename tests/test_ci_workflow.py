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
        parts = re.split(r'^  ([\w-]+):\n', self.workflow.split('\njobs:\n')[1], flags=re.M)
        self.jobs = dict(zip(parts[1::2], parts[2::2]))

    def steps(self, job):
        return self.jobs[job].split('\n      - ')[1:]

    def test_hosted_shards_report_through_one_required_check_for_every_pull_request(self):
        self.assertEqual(config.PR_CHECK_NAME, 'check')
        self.assertEqual(list(self.jobs), ['reuse', 'shard', config.PR_CHECK_NAME])
        for job in self.jobs.values():
            self.assertRegex(job, r'(?m)^    runs-on: ubuntu-24\.04$')
            self.assertIsNone(re.search(r'^    name:', job, re.M))
        # Only a push to main looks for a run to reuse. Shards run whenever it found none: on every pull
        # request and manual run, where `reuse` is skipped, and when it failed. The required check always runs.
        self.assertTrue(self.jobs['reuse'].startswith("    if: github.event_name == 'push'\n"))
        self.assertTrue(self.jobs['shard'].startswith(
            "    needs: reuse\n    # Runs unless `reuse` found a run: on every pull request and manual run, and whenever "
            "the lookup fails.\n    if: ${{ !cancelled() && needs.reuse.outputs.run == '' }}\n"))
        self.assertTrue(self.jobs['check'].startswith('    needs: [reuse, shard]\n    if: always()\n'))
        self.assertIn('      run: ${{ steps.lookup.outputs.run }}\n', self.jobs['reuse'])
        self.assertIn('    timeout-minutes: 30\n', self.jobs['shard'])
        self.assertIn('on:\n  workflow_dispatch:\n  pull_request:\n    branches: [main]\n'
                      '  push:\n    branches: [main]\n', self.workflow)
        self.assertNotIn('pull_request_target', self.workflow)

    def test_only_a_pull_requests_superseded_run_is_cancelled(self):
        # A landing that integrates a moved main pushes a new head; the replaced head's run cannot merge.
        self.assertIn('concurrency:\n  group: checks-${{ github.event_name }}-${{ github.event.pull_request.number || github.run_id }}\n'
                      "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n", self.workflow)

    def test_required_check_passes_only_when_every_shard_succeeded_or_reuse_named_a_run(self):
        [gate] = self.steps('check')
        self.assertIn('SHARDS: ${{ needs.shard.result }}\n          REUSE: ${{ needs.reuse.result }}\n'
                      '          REUSED: ${{ needs.reuse.outputs.run }}\n', gate)
        script = gate.split('run: ')[1].strip()
        results = ('success', 'failure', 'cancelled', 'skipped', '')
        for shards in results:
            for reuse in results:
                for reused in ('', '41'):
                    with self.subTest(shards=shards, reuse=reuse, reused=reused):
                        verdict = subprocess.run(['bash', '-eo', 'pipefail', '-c', script], capture_output=True,
                                                 env=dict(os.environ, SHARDS=shards, REUSE=reuse, REUSED=reused))
                        self.assertEqual(verdict.returncode == 0, shards == 'success' or (
                            shards == 'skipped' and reuse == 'success' and reused == '41'))

    def test_shards_cover_each_make_check_suite_exactly_once(self):
        matrix = re.findall(r'^          - \{suite: (\w+), shard: (\d+)/(\d+)\}$', self.jobs['shard'], re.M)
        suites = {}
        for suite, shard, shards in matrix:
            suites.setdefault(suite, []).append((int(shard), int(shards)))
        # `make check` runs exactly these suites; each one lists every slice i/n once.
        check = re.search(r'^check:.*\n\t.*-k (.+)$', (REPO / 'Makefile').read_text(), re.M).group(1)
        self.assertEqual(sorted(f'check-{suite}' for suite in suites), sorted(check.split()))
        for suite, slices in suites.items():
            with self.subTest(suite=suite):
                total = slices[0][1]
                self.assertGreater(total, 1)
                self.assertEqual(slices, [(index, total) for index in range(1, total + 1)])
        self.assertIn('\n      fail-fast: false\n', self.jobs['shard'])
        self.assertIn('SUITE: ${{ matrix.suite }}\n          SHARD: ${{ matrix.shard }}\n'
                      '        run: make "check-$SUITE" SHARD="$SHARD" WORKERS="$(nproc)"\n', self.jobs['shard'])

    def test_every_checkout_verifies_the_exact_candidate_first(self):
        checkouts = [job for job in self.jobs if 'actions/checkout@' in self.jobs[job]]
        self.assertEqual(checkouts, ['reuse', 'shard'])
        # The lookup itself refuses a checkout that is not the pushed commit (tests/test_ci_reuse.py).
        lookup = self.steps('reuse')[1]
        self.assertTrue(lookup.startswith('name: Find the run that tested this tree\n'))
        self.assertIn('run: python3 scripts/ci_reuse.py', lookup)
        for job in checkouts[1:]:
            steps = self.steps(job)
            self.assertTrue(steps[0].startswith('uses: actions/checkout@'))
            self.assertIn('ref: ${{ github.sha }}', steps[0])
            self.assertTrue(steps[1].startswith('name: Verify the exact candidate\n'))
        self.assertEqual(self.workflow.count('- name: Verify the exact candidate'), len(checkouts) - 1)

    def test_fork_and_owner_runs_share_a_read_only_token_without_secrets(self):
        self.assertIn('\npermissions:\n  contents: read\n', self.workflow)
        # Only the push-only lookup reads run and pull request records; nothing can write.
        self.assertIn('    permissions:\n      contents: read\n      actions: read\n      pull-requests: read\n',
                      self.jobs['reuse'])
        self.assertEqual(self.workflow.count('permissions:'), 2)
        self.assertNotIn('write', self.workflow)
        self.assertIsNone(re.search(r'\bsecrets\.\w', self.workflow))
        for job in ('reuse', 'shard'):
            self.assertIn('persist-credentials: false', self.jobs[job])
        for action in re.findall(r'uses: (\S+)', self.workflow):
            self.assertRegex(action, r'@[0-9a-f]{40}$')

    def test_shards_run_after_frozen_install_and_each_failure_keeps_its_browser_report(self):
        steps = self.steps('shard')
        install = next(step for step in steps if step.startswith('name: Install frozen dependencies'))
        # From the repository root Corepack starts its latest pnpm, which refuses web/'s pinned version.
        self.assertIn('working-directory: web\n', install)
        self.assertNotIn('--dir', install)
        self.assertIn('pnpm install --frozen-lockfile', install)
        self.assertIn('pnpm exec playwright install --with-deps chromium', install)
        converter = next(step for step in steps if step.startswith('name: Install the image converter'))
        self.assertIn('apt-get install -y --no-install-recommends ffmpeg liblcms2-2', converter)
        report = steps[-1]
        self.assertIn("if: failure() && matrix.suite == 'web'", report)
        self.assertIn('actions/upload-artifact@', report)
        self.assertIn('name: browser-report-${{ strategy.job-index }}-${{ github.run_attempt }}', report)
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

    def test_a_reused_main_run_gives_every_reader_the_evidence_it_reads(self):
        # A reused push run has the same workflow, event, head and conclusion, and its `check` job succeeds
        # only on a tested tree. Readers use exactly those, so none of them can tell it from a full run.
        from altitude import status
        self.assertEqual(set(status._RUN_FIELDS.split(',')),
                         {'databaseId', 'headSha', 'event', 'conclusion', 'status', 'workflowName'})
        self.assertIn('info = gh(["run", "view", rid, "--json", "conclusion,status"], repo)',
                      (REPO / 'altitude/verify.py').read_text())


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
