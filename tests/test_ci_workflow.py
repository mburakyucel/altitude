"""Execute the workflow's candidate assertions and evidence packaging on real, isolated Git."""
import json
import os
import subprocess
import sys
import textwrap

from tests.support import AltitudeCase, REPO, git, make_repo


class TestPrWorkflow(AltitudeCase):
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
        self.env = dict(os.environ, GITHUB_EVENT_NAME='pull_request', GITHUB_EVENT_PATH=str(self.event),
                        GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2', GITHUB_SERVER_URL='https://example.invalid',
                        GITHUB_REPOSITORY='fictional/project', RUNNER_TEMP=str(self.tmp), CHECK_OUTCOME='success')
        self.workflow = (REPO / '.github/workflows/self-hosted-checks.yml').read_text()
        self.checkout_candidate(self.tree, self.base, self.head)

    def checkout_candidate(self, tree, *parents):
        args = ['commit-tree', tree]
        for parent in parents:
            args += ['-p', parent]
        self.sha = git(*args, '-m', 'fixture merge candidate', cwd=self.repo).strip()
        git('checkout', '--detach', self.sha, cwd=self.repo)
        self.env['GITHUB_SHA'] = self.sha

    def verify(self):
        step = self.workflow.split('- name: Verify exact commit')[1].split('- name: Full deterministic')[0]
        script = textwrap.dedent(step.split('run: |\n')[1]).split('node --version')[0]
        return subprocess.run(['bash', '-eo', 'pipefail', '-c', script], cwd=self.repo,
                              env=self.env, capture_output=True, text=True)

    def package(self):
        step = self.workflow.split('- name: Retain report')[1].split('- name: Upload')[0]
        script = textwrap.dedent(step.split("python3 - <<'PY'\n")[1].rsplit('          PY', 1)[0])
        result = subprocess.run([sys.executable, '-c', script], cwd=self.repo, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.tmp / 'altitude-ci-evidence'

    def test_current_merge_tree_passes_and_records_identity(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.package() / 'ci-result.json').read_text())
        self.assertTrue(record['passed'])
        self.assertEqual((record['base'], record['head'], record['sha'], record['tree'], record['run_attempt']),
                         (self.base, self.head, self.sha, self.tree, '2'))

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

    def test_failed_report_retains_attached_trace_without_raw_duplicates(self):
        self.env['CHECK_OUTCOME'] = 'failure'
        report = self.repo / 'web/ui-artifacts/report'
        (report / 'data').mkdir(parents=True)
        (report / 'index.html').write_text('<a href="data/trace.zip">Failure trace</a>')
        (report / 'data/trace.zip').write_bytes(b'fictional trace')
        raw = self.repo / 'web/ui-artifacts/results'
        raw.mkdir()
        (raw / 'trace.zip').write_bytes(b'fictional trace')
        evidence = self.package()
        self.assertFalse(json.loads((evidence / 'ci-result.json').read_text())['passed'])
        self.assertEqual((evidence / 'report/index.html').read_text(), (report / 'index.html').read_text())
        self.assertEqual((evidence / 'report/data/trace.zip').read_bytes(), b'fictional trace')
        self.assertEqual(sorted(path.name for path in evidence.iterdir()), ['ci-result.json', 'report'])

    def test_precheck_failure_and_main_run_keep_available_identity(self):
        self.env.update(CHECK_OUTCOME='skipped', GITHUB_EVENT_NAME='push')
        self.event.write_text('{}')
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.package() / 'ci-result.json').read_text())
        self.assertFalse(record['passed'])
        self.assertIsNone(record['base'])
        self.assertIsNone(record['head'])
