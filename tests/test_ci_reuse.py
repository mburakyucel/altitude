"""A push to main reuses only the pull request run that tested its exact tree (#777), on real Git and fixture API answers."""
import copy
import os
import subprocess
from unittest import mock

from scripts import ci_reuse
from tests.support import AltitudeCase, make_repo

REPO_NAME = 'example/altitude'


class TestCiReuse(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.before = self.git('rev-parse', 'HEAD')
        self.publish({'change': 'candidate\n'})

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()

    def publish(self, files, *, extra_parent=None):
        """A PR head with `files` on top of `before`, and its squash (or merge) commit pushed to main."""
        self.git('checkout', '-q', '--detach', self.before)
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            self.git('add', name)
        self.git('commit', '-qm', 'pull request head')
        self.head = self.git('rev-parse', 'HEAD')
        tree = self.git('rev-parse', 'HEAD^{tree}')
        parents = ['-p', self.before] + (['-p', extra_parent] if extra_parent else [])
        self.sha = self.git('commit-tree', tree, *parents, '-m', 'squash merge (#7)')
        self.git('checkout', '-q', '--detach', self.sha)
        self.answers = {
            f'repos/{REPO_NAME}/commits/{self.sha}/pulls': [self.pull()],
            f'repos/{REPO_NAME}/git/commits/{self.head}': {'tree': {'sha': tree}},
            f'repos/{REPO_NAME}/pulls?head=example:worktree-fix&state=all&per_page=100': [self.pull()],
            f'repos/{REPO_NAME}/actions/workflows/hosted-checks.yml/runs?event=pull_request&head_sha={self.head}&per_page=100':
                {'total_count': 1, 'workflow_runs': [self.workflow_run()]},
            f'repos/{REPO_NAME}/actions/runs/41/attempts/1/jobs?per_page=100': self.jobs(),
        }

    def pull(self, **changes):
        pull = {'number': 7, 'merged_at': '2026-10-12T18:27:32Z', 'merge_commit_sha': self.sha,
                'base': {'ref': 'main', 'repo': {'full_name': REPO_NAME}},
                'head': {'sha': self.head, 'ref': 'worktree-fix', 'repo': {'full_name': REPO_NAME}}}
        pull.update(changes)
        return pull

    def workflow_run(self, **changes):
        run = {'id': 41, 'run_attempt': 1, 'path': ci_reuse.WORKFLOW, 'event': 'pull_request', 'head_sha': self.head,
               'head_branch': 'worktree-fix', 'status': 'completed', 'conclusion': 'success', 'pull_requests': [],
               'repository': {'full_name': REPO_NAME}, 'head_repository': {'full_name': REPO_NAME},
               'html_url': 'https://github.com/example/altitude/actions/runs/41'}
        run.update(changes)
        return run

    @staticmethod
    def jobs(**conclusions):
        jobs = [{'name': f'shard (python, {i}/2)', 'conclusion': conclusions.get('shard', 'success')} for i in (1, 2)]
        jobs.append({'name': 'check', 'conclusion': conclusions.get('check', 'success')})
        return {'total_count': len(jobs), 'jobs': jobs}

    def api(self, path):
        if path not in self.answers:
            raise OSError(f'HTTP Error 404: {path}')
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)

    def runs_key(self):
        return next(key for key in self.answers if '/runs?' in key)

    def lookup(self, sha=None, before=None):
        return ci_reuse.reusable_run(REPO_NAME, sha or self.sha, before or self.before, api=self.api, git=self.git)

    def refuses(self, reason, **kwargs):
        with self.assertRaisesRegex(ci_reuse.NoReuse, reason):
            self.lookup(**kwargs)

    def test_the_run_that_tested_the_exact_tree_is_reused(self):
        self.assertEqual(self.lookup(), {'id': 41, 'url': 'https://github.com/example/altitude/actions/runs/41', 'pr': 7})

    def test_a_merge_commit_with_the_head_tree_is_reused(self):
        side = self.git('commit-tree', self.git('rev-parse', f'{self.before}^{{tree}}'), '-p', self.before, '-m', 'side')
        self.publish({'change': 'candidate\n'}, extra_parent=side)
        self.assertEqual(self.lookup()['id'], 41)

    def test_the_checkout_must_be_the_pushed_commit_extending_main(self):
        self.refuses('not the pushed commit', sha=self.head)
        self.git('checkout', '-q', '--detach', self.before)
        self.git('commit', '-q', '--allow-empty', '-m', 'force-pushed away')
        rewritten = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '--detach', self.sha)
        self.refuses('does not extend', before=rewritten)

    def test_a_change_to_how_ci_runs_runs_in_full(self):
        for name in (item + 'workflows/hosted-checks.yml' if item.endswith('/') else item for item in ci_reuse.CONTRACT):
            with self.subTest(name=name):
                self.publish({'change': 'candidate\n', name: 'changed\n'})
                self.refuses(f'changes how CI runs: {name}')
        self.publish({'change': 'candidate\n', 'web/src/makefile-notes.md': 'not the contract\n'})
        self.assertEqual(self.lookup()['id'], 41)

    def test_an_earlier_commit_of_the_push_counts_too(self):
        # A rebase merge pushes several commits; the push's whole range decides, not its last commit.
        self.git('checkout', '-q', '--detach', self.before)
        (self.repo / 'Makefile').write_text('changed\n')
        self.git('add', 'Makefile')
        self.git('commit', '-qm', 'first rebased commit')
        middle = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '--detach', self.sha)
        self.git('rebase', '-q', '--onto', middle, self.before)
        sha = self.git('rev-parse', 'HEAD')
        self.refuses('changes how CI runs: Makefile', sha=sha)

    def test_exactly_one_same_repository_merged_pull_request(self):
        key = f'repos/{REPO_NAME}/commits/{self.sha}/pulls'
        for pulls, reason in (([], '0 merged'), ([self.pull(), self.pull(number=8)], '2 merged'),
                              ([self.pull(merged_at=None)], '0 merged'),
                              ([self.pull(merge_commit_sha=self.head)], '0 merged'),
                              ([self.pull(base={'ref': 'release', 'repo': {'full_name': REPO_NAME}})], '0 merged'),
                              ([self.pull(head={'sha': self.head, 'ref': 'fix', 'repo': {'full_name': 'someone/altitude'}})],
                               'comes from a fork')):
            with self.subTest(reason=reason, pulls=len(pulls)):
                self.answers[key] = pulls
                self.refuses(reason)

    def test_a_branch_with_a_pull_request_to_another_base_runs_in_full(self):
        # The run names only head and branch: a PR to another base could run another workflow on that head.
        key = f'repos/{REPO_NAME}/pulls?head=example:worktree-fix&state=all&per_page=100'
        other = self.pull(number=8, merged_at=None, base={'ref': 'release', 'repo': {'full_name': REPO_NAME}})
        for pulls in ([self.pull(), other], [], [self.pull()] + [self.pull(number=n) for n in range(100, 199)]):
            with self.subTest(pulls=len(pulls)):
                self.answers[key] = pulls
                self.refuses('another base, or they cannot all be listed')

    def test_the_pushed_tree_must_be_the_pull_request_heads(self):
        self.answers[f'repos/{REPO_NAME}/git/commits/{self.head}'] = {'tree': {'sha': self.git('rev-parse', f'{self.before}^{{tree}}')}}
        self.refuses('pushed tree differs')

    def test_only_this_workflows_pull_request_run_of_that_head_and_branch_counts(self):
        for changes in ({'path': '.github/workflows/impostor.yml'}, {'event': 'push'}, {'head_sha': self.before},
                        {'head_branch': 'another-branch'}, {'repository': {'full_name': 'someone/altitude'}},
                        {'head_repository': {'full_name': 'someone/altitude'}}):
            with self.subTest(changes=changes):
                self.answers[self.runs_key()] = {'total_count': 1, 'workflow_runs': [self.workflow_run(**changes)]}
                self.refuses('no run of')

    def test_the_newest_run_and_latest_attempt_decide(self):
        listing = {'total_count': 2, 'workflow_runs': [self.workflow_run(), self.workflow_run(id=42, conclusion='failure')]}
        self.answers[self.runs_key()] = listing
        self.refuses('newest run 42 .* is failure')
        self.answers[self.runs_key()] = {'total_count': 1, 'workflow_runs': [self.workflow_run(run_attempt=2)]}
        self.answers[f'repos/{REPO_NAME}/actions/runs/41/attempts/2/jobs?per_page=100'] = self.jobs(check='failure')
        self.refuses('attempt 2 did not pass')
        self.answers[self.runs_key()] = {'total_count': 1, 'workflow_runs': [self.workflow_run(status='in_progress', conclusion=None)]}
        self.refuses('is in_progress')

    def test_every_shard_and_the_check_job_must_have_passed(self):
        jobs_key = f'repos/{REPO_NAME}/actions/runs/41/attempts/1/jobs?per_page=100'
        for jobs in (self.jobs(shard='skipped'), self.jobs(check='failure'),
                     {'total_count': 1, 'jobs': [{'name': 'check', 'conclusion': 'success'}]}):
            with self.subTest(jobs=jobs):
                self.answers[jobs_key] = jobs
                self.refuses('did not pass every shard')

    def test_an_incomplete_listing_runs_in_full(self):
        self.answers[self.runs_key()] = {'total_count': 101, 'workflow_runs': [self.workflow_run()]}
        self.refuses('more runs than one page')
        self.answers[self.runs_key()] = {'total_count': 1, 'workflow_runs': [self.workflow_run()]}
        self.answers[f'repos/{REPO_NAME}/actions/runs/41/attempts/1/jobs?per_page=100'] = {'total_count': 12, 'jobs': []}
        self.refuses('more jobs than one page')

    def test_main_writes_the_run_only_on_a_match(self):
        output, summary = self.tmp / 'output', self.tmp / 'summary'
        env = {'GITHUB_REPOSITORY': REPO_NAME, 'GITHUB_SHA': self.sha, 'BEFORE': self.before,
               'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(summary)}
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.dict(os.environ, env), mock.patch.object(ci_reuse, 'api', self.api):
            self.assertEqual(ci_reuse.main(), 0)
            self.assertEqual(output.read_text(), 'run=41\n')
            self.assertIn("PR #7's run https://github.com/example/altitude/actions/runs/41", summary.read_text())
            output.unlink()
            # The output is written last: a failed summary fails the job without naming a run.
            summary.unlink()
            summary.mkdir()
            with self.assertRaises(OSError):
                ci_reuse.main()
            self.assertFalse(output.exists())
            for failure in (OSError('HTTP Error 403: rate limited'), ValueError('Expecting value')):
                with self.subTest(failure=failure):
                    self.answers[self.runs_key()] = failure
                    self.assertEqual(ci_reuse.main(), 0)
                    self.assertFalse(output.exists())

