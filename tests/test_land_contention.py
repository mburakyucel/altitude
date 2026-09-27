"""Concurrent owners use real processes, task storage, worktrees and a bare Git remote.

Only GitHub and the candidate test command are fixtures. File barriers pause a real
candidate check so the competing caller demonstrably reaches the landing wait.
"""
import contextlib
import json
import multiprocessing
import os
import shutil
import signal
import time
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import config, engines, land, reviews, route, state as S, tasks as T


RUNNER = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path
d = Path(os.environ['FAKE_GH_DIR'])
identity = {key: subprocess.check_output(['git', 'rev-parse', ref], text=True).strip()
            for key, ref in [('candidate', 'HEAD'), ('tree', 'HEAD^{tree}')]}
identity['cwd'] = os.getcwd()
(d / 'tested.json').write_text(json.dumps(identity))
deadline = time.monotonic() + 60
while not (d / 'release').exists():
    if time.monotonic() >= deadline:
        sys.exit('fixture check barrier timed out')
    time.sleep(.01)
print('Ran 1 test in 0.001s\n\nOK')
sys.exit(int((d / 'exit-code').read_text()) if (d / 'exit-code').exists() else 0)
'''


def run_owner(project, slug, worktree, fixture, output, options):
    os.setsid()
    os.environ.update(ALTITUDE_PROJECT=project, ALTITUDE_TASK=slug,
                      ALTITUDE_ACTOR=options.pop('actor', 'l2'), ALTITUDE_ATTEMPT='1', FAKE_GH_DIR=str(fixture))
    # Capture progress without substituting any application or storage behavior.
    def note(message):
        with (output / 'notes').open('a') as stream:
            stream.write(message + '\n')
    land._note = note
    land.LAND_WAIT_TIMEOUT = options.pop('lock_timeout', land.LAND_WAIT_TIMEOUT)
    land.CHECK_POLL_SECONDS = .05
    if options.pop('required_check', False):
        (fixture / 'required-pr-check').touch()
        (fixture / 'hosted-barrier').touch()
        (fixture / 'checks.json').write_text('[{"bucket": "pass"}]')
    invoke = land.land.__wrapped__ if options.pop('without_lock', False) else land.land
    try:
        result = invoke('Independent fix ' + slug, cwd=worktree, wait=options.pop('wait', 0),
                        test_cmd='fixture-candidate-check', **options)
        payload = {'result': result}
    except Exception as exc:
        payload = {'error': str(exc), 'type': type(exc).__name__}
    (output / 'result.json').write_text(json.dumps(payload))


def message_and_block_waiter(project, output):
    os.setsid()
    holder_message = T.message(project, 'first', 'l3', 'Continue the authorized landing.')
    T.block(project, 'second', 'Fixture lifecycle pause', expected_attempt=1)
    wake_message = T.message(project, 'second', 'l3', 'Resume after the lifecycle pause.')
    (output / 'result.json').write_text(json.dumps({'holder': holder_message, 'wake': wake_message}))


class TestLandContention(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.remote = self.tmp / 'origin.git'
        git('config', f'url.{self.remote}.insteadOf', 'https://github.com/team/demo.git', cwd=self.repo)
        git('remote', 'set-url', 'origin', 'https://github.com/team/demo.git', cwd=self.repo)
        self.fake_gh()
        # A hosted check can use the same barrier, recording its exact tested head.
        shim = self.tmp / 'bin/gh'
        shim.write_text(shim.read_text().replace('cmd = tuple(args[:2])', '''
if args[:2] == ['api', 'graphql'] and read('hosted-barrier') is not None:
    import time
    identity = {key: subprocess.check_output(['git', 'rev-parse', ref], text=True).strip()
                for key, ref in [('candidate', 'HEAD'), ('tree', 'HEAD^{tree}')]}
    open(os.path.join(d, 'tested.json'), 'w').write(json.dumps(identity))
    deadline = time.monotonic() + 60
    while read('release') is None:
        if time.monotonic() >= deadline:
            fail('fixture hosted barrier timed out')
        time.sleep(.01)
cmd = tuple(args[:2])''').replace('        tree = subprocess.check_output', '''
        if read('required-pr-check') is not None:
            for context in contexts:
                context['name'] = 'check'
                context['checkSuite'].update(app={'databaseId': 15368},
                    workflowRun={'event': 'pull_request', 'file': {'path': '.github/workflows/self-hosted-checks.yml'}})
        tree = subprocess.check_output'''))
        for name in ('fixture-candidate-check', 'make'):
            runner = self.tmp / 'bin' / name
            runner.write_text(RUNNER)
            runner.chmod(0o755)
        install = self.tmp / 'bin/pnpm'
        install.write_text('#!/bin/sh\necho fixture frozen install\n')
        install.chmod(0o755)
        # The local-check policy installs the candidate's web dependencies before `make check`.
        (self.repo / 'web').mkdir()
        (self.repo / 'web/package.json').write_text('{}\n')
        git('add', 'web/package.json', cwd=self.repo)
        git('commit', '-q', '-m', 'web placeholder', cwd=self.repo)
        git('push', '-q', 'origin', 'main', cwd=self.repo)
        self.owners = {}
        self.processes = []
        self.addCleanup(self.stop_processes)
        self.owner('first', 101)
        self.owner('second', 102)

    def owner(self, slug, number):
        worktree = add_worktree(self.repo, slug)
        (worktree / (slug + '.txt')).write_text(slug + '\n')
        git('add', slug + '.txt', cwd=worktree)
        S.save_task(self.project, {'slug': slug, 'title': 'Independent fix ' + slug,
                                   'state': 'running', 'attempt': 1,
                                   'branch': 'worktree-' + slug, 'worktree': str(worktree)})
        fixture = self.tmp / slug
        fixture.mkdir()
        (fixture / 'checks.json').write_text('[]')
        (fixture / 'merge_git.txt').touch()
        (fixture / 'pr.json').write_text(json.dumps({
            'number': number, 'state': 'OPEN', 'url': f'https://example.invalid/pr/{number}',
            'headRefName': 'worktree-' + slug, 'baseRefName': 'main',
            'isCrossRepository': False, 'isDraft': False, 'body': 'Independent fix ' + slug,
        }))
        self.owners[slug] = (worktree, fixture)

    def start(self, slug, *, merge=True, **options):
        worktree, fixture = self.owners[slug]
        output = self.tmp / ('call-' + str(len(self.processes)))
        output.mkdir()
        process = multiprocessing.get_context('fork').Process(
            target=run_owner, args=(self.project, slug, worktree, fixture, output,
                                   {'merge': merge, **options}))
        process.start()
        self.processes.append((process, output))
        return process, output

    def stop_processes(self):
        for process, _ in self.processes:
            if process.is_alive():
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.join(5)
        # SIGKILL cannot run the application's candidate-worktree finally block.
        for _, fixture in self.owners.values():
            recorded = fixture / 'tested.json'
            if recorded.exists():
                candidate = Path(json.loads(recorded.read_text()).get('cwd', '.')).parent
                if candidate.name.startswith('alt-land-candidate-'):
                    shutil.rmtree(candidate, ignore_errors=True)

    def await_condition(self, condition, detail):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        outputs = {str(output): {p.name: p.read_text() for p in output.iterdir()}
                   for _, output in self.processes}
        self.fail(f'Timed out waiting for {detail}: {outputs}')

    def checked(self, slug):
        fixture = self.owners[slug][1]
        self.await_condition(lambda: (fixture / 'tested.json').exists(), slug + ' candidate check')

    def waiting(self, call):
        process, output = call
        self.await_condition(lambda: (output / 'notes').exists()
                             and 'waiting' in (output / 'notes').read_text().lower(), 'landing wait')
        self.assertTrue(process.is_alive())

    def release(self, slug):
        (self.owners[slug][1] / 'release').touch()

    def finish(self, call):
        process, output = call
        process.join(60)
        self.assertFalse(process.is_alive(), 'landing did not terminate')
        self.assertEqual(process.exitcode, 0)
        return json.loads((output / 'result.json').read_text())

    def calls(self, slug, command):
        log = self.owners[slug][1] / 'log.jsonl'
        return [row for row in map(json.loads, log.read_text().splitlines()) if row[:2] == command]

    def merged(self, slug, payload):
        self.assertNotIn('error', payload)
        result = payload['result']
        self.assertTrue(result['merged'], result)
        fixture = self.owners[slug][1]
        tested = json.loads((fixture / 'tested.json').read_text())
        pr = json.loads((fixture / 'pr.json').read_text())
        self.assertEqual(tested['tree'], git('rev-parse', pr['mergeCommit']['oid'] + '^{tree}',
                                           cwd=self.repo).strip())
        self.assertEqual(len(self.calls(slug, ['pr', 'merge'])), 1)
        return result

    def contending(self):
        first = self.start('first')
        self.checked('first')
        second = self.start('second')
        self.waiting(second)
        self.assertFalse((self.owners['second'][1] / 'log.jsonl').exists())
        return first, second

    def reviewed(self, slug, *, assess=True):
        worktree, _ = self.owners[slug]
        git('commit', '-qm', 'Review checkpoint', cwd=worktree)
        task = S.load_task(self.project, slug)
        task.update(l2_engine=config.ENGINES[0], agent_id='fixture-' + slug)
        S.save_task(self.project, task)
        (S.task_dir(self.project, slug) / 'request.md').write_text('Preserve independent task changes.')
        choice = {'engine': config.ENGINES[1], 'model': 'fixture', 'label': 'Fixture reviewer',
                  'allowance_known': True}
        def provider(prompt, **kwargs):
            self.assertTrue(kwargs['on_start']({'unit': 'fixture-review', 'pid': 12345, 'started_ticks': '1'}))
            return {'termination_confirmed': True, 'text': 'No findings', 'findings': [], 'limitations': []}
        with mock.patch.object(route, 'pick_review', return_value=choice), \
                mock.patch.object(engines, 'review', side_effect=provider) as engine:
            request = reviews.request(self.project, slug, actor='l2', expected_attempt=1,
                                      request_id='review-' + slug)
            reviews.run(self.project, slug, request['id'], actor='l2', expected_attempt=1)
            engine.assert_called_once()
        if assess:
            self.assess(slug)

    def assess(self, slug):
        return reviews.assess(self.project, slug, 'review-' + slug, actor='l2', expected_attempt=1,
                              dispositions=[], reason='Inspected the complete candidate and all current task context.')

    def assessment_wait(self, call):
        process, output = call
        self.await_condition(lambda: (output / 'notes').exists()
                             and 'waiting for owner assessment' in (output / 'notes').read_text(),
                             'explicit owner reassessment wait')
        self.assertTrue(process.is_alive())

    def test_reviewed_waiter_reassesses_integrated_head_without_losing_turn(self):
        self.ship_check_workflow()
        self.reviewed('second')
        old_review = S.load_task(self.project, 'second')['reviews'][0]
        first = self.start('first', required_check=True)
        self.checked('first')
        second = self.start('second', required_check=True, wait=20)
        self.waiting(second)
        self.release('first')
        self.merged('first', self.finish(first))
        self.release('second')
        self.assessment_wait(second)
        worktree, fixture = self.owners['second']
        integrated = git('rev-parse', 'HEAD', cwd=worktree).strip()
        self.assertNotEqual(integrated, old_review['reconciled']['head'])
        self.assertEqual(git('diff', '--name-only', 'origin/main', 'HEAD', cwd=worktree).strip(), 'second.txt')
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])
        self.assertEqual(S.load_task(self.project, 'second')['reviews'][0]['reconciled'], old_review['reconciled'])
        self.owner('third', 103)
        third = self.start('third', required_check=True)
        self.waiting(third)
        self.assertFalse((self.owners['third'][1] / 'log.jsonl').exists())
        # Real message handling and assess run concurrently with the process holding the turn.
        T.message(self.project, 'second', 'l3', 'Continue with the integrated candidate.')
        self.assess('second')
        result = self.merged('second', self.finish(second))
        self.assertEqual(result['head'], integrated)
        self.assertEqual(result['checks'], 'pass')
        evidence = json.loads((fixture / 'last_check_evidence.json').read_text())['pullRequest']
        self.assertEqual(evidence['headRefOid'], integrated)
        self.assertEqual(evidence['baseRef']['target']['oid'],
                         json.loads((self.owners['first'][1] / 'pr.json').read_text())['mergeCommit']['oid'])
        self.release('third')
        self.merged('third', self.finish(third))
        saved = S.load_task(self.project, 'second')['reviews'][0]
        self.assertEqual(saved['snapshot'], old_review['snapshot'])
        self.assertEqual(saved['merged_head'], integrated)

    def stale_review(self):
        self.reviewed('first')
        T.message(self.project, 'first', 'l3', 'Check the current candidate before proceeding.')
        self.release('first')

    def test_assessment_timeout_releases_turn_and_keeps_published_candidate(self):
        self.stale_review()
        first = self.start('first', wait=.2)
        payload = self.finish(first)
        self.assertIn('assessment wait timed out', payload['error'])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        head = S.load_task(self.project, 'first')['delivery']['head']
        self.assertEqual(git('rev-parse', 'worktree-first', cwd=self.remote).strip(), head)
        self.assess('first')
        result = self.merged('first', self.finish(self.start('first')))
        self.assertEqual(result['head'], head)
        self.release('second')
        self.merged('second', self.finish(self.start('second')))

    def test_killed_assessment_wait_releases_turn_to_competing_owner(self):
        self.stale_review()
        first = self.start('first', wait=20)
        self.assessment_wait(first)
        second = self.start('second')
        self.waiting(second)
        os.killpg(first[0].pid, signal.SIGKILL)
        first[0].join(5)
        self.assertEqual(first[0].exitcode, -signal.SIGKILL)
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(second))

    def test_replaced_owner_during_assessment_releases_turn(self):
        self.stale_review()
        first = self.start('first', wait=20)
        self.assessment_wait(first)
        task = S.load_task(self.project, 'first')
        task['attempt'] = 2
        S.save_task(self.project, task)
        self.assertIn('no longer current', self.finish(first)['error'])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(self.start('second')))

    def test_material_edit_during_assessment_refuses_pinned_candidate(self):
        self.stale_review()
        first = self.start('first', wait=20)
        self.assessment_wait(first)
        worktree, _ = self.owners['first']
        (worktree / 'material.txt').write_text('New behavior requires a new candidate.\n')
        git('add', 'material.txt', cwd=worktree)
        git('commit', '-qm', 'Material change', cwd=worktree)
        # Even a deliberate assessment of this different head cannot authorize the pinned head.
        self.assess('first')
        self.assertRegex(self.finish(first)['error'], 'differs from the pinned|Commit the selected task changes')
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(self.start('second')))

    def test_operator_landing_refuses_stale_assessment_without_waiting(self):
        self.stale_review()
        first = self.start('first', wait=20, actor=config.OPERATOR_ACTOR)
        self.assertIn('changed after review assessment', self.finish(first)['error'])
        self.assertNotIn('waiting for owner assessment', (first[1] / 'notes').read_text())
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_unassessed_review_refuses_without_waiting_or_merging(self):
        self.reviewed('first', assess=False)
        first = self.start('first', wait=20)
        self.assertIn("receive the owner's dispositions", self.finish(first)['error'])
        self.assertNotIn('waiting for owner assessment', (first[1] / 'notes').read_text())
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_external_main_change_during_assessment_refuses_and_releases_turn(self):
        self.stale_review()
        first = self.start('first', wait=20)
        self.assessment_wait(first)
        (self.repo / 'external.txt').write_text('External main update\n')
        git('add', 'external.txt', cwd=self.repo)
        git('commit', '-qm', 'External main update', cwd=self.repo)
        git('push', '-q', 'origin', 'main', cwd=self.repo)
        self.assertRegex(self.finish(first)['error'], 'differs from the pinned|base or head moved')
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(self.start('second')))

    def test_failed_required_check_ends_assessment_wait_without_merging(self):
        self.ship_check_workflow()
        self.stale_review()
        first = self.start('first', required_check=True, wait=20)
        self.assessment_wait(first)
        (self.owners['first'][1] / 'checks.json').write_text('[{"bucket":"fail"}]')
        result = self.finish(first)['result']
        self.assertEqual((result['checks'], result['merged']), ('fail', False))
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(self.start('second', required_check=True)))

    def test_independent_owners_validate_fresh_candidates_and_merge_once(self):
        second_worktree = self.owners['second'][0]
        (second_worktree / 'README.md').write_text('Unstaged operator notes\n')
        (second_worktree / 'private.txt').write_text('Untracked private notes\n')
        first, second = self.contending()
        self.release('first')
        first_result = self.merged('first', self.finish(first))
        self.checked('second')
        first_merge = json.loads((self.owners['first'][1] / 'pr.json').read_text())['mergeCommit']['oid']
        self.release('second')
        second_result = self.merged('second', self.finish(second))
        self.assertEqual(second_result['local_tests']['base'], first_merge)
        self.assertNotEqual(first_result['local_tests']['base'], first_merge)
        self.assertEqual(git('merge-base', first_merge, second_result['head'], cwd=self.repo).strip(), first_merge)
        self.assertEqual(git('show', 'main:first.txt', cwd=self.remote), 'first\n')
        self.assertEqual(git('show', 'main:second.txt', cwd=self.remote), 'second\n')
        self.assertEqual((second_worktree / 'README.md').read_text(), 'Unstaged operator notes\n')
        self.assertEqual((second_worktree / 'private.txt').read_text(), 'Untracked private notes\n')
        self.assertEqual(self.finish(self.start('second'))['result']['checks'], 'merged')
        self.assertEqual(len(self.calls('second', ['pr', 'merge'])), 1)

    def test_failed_candidate_releases_next_owner_without_bypassing_checks(self):
        (self.owners['first'][1] / 'exit-code').write_text('1')
        first, second = self.contending()
        self.release('first')
        result = self.finish(first)['result']
        self.assertFalse(result['merged'])
        self.assertFalse(result['local_tests']['passed'])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(second))

    def test_concurrent_same_owner_retry_does_not_merge_twice(self):
        first = self.start('first')
        self.checked('first')
        retry = self.start('first')
        self.waiting(retry)
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertEqual(self.finish(retry)['result']['checks'], 'merged')
        self.assertEqual(len(self.calls('first', ['pr', 'merge'])), 1)

    def test_without_serialization_sibling_merge_invalidates_green_candidate(self):
        first = self.start('first', without_lock=True)
        self.checked('first')
        second = self.start('second', without_lock=True)
        self.checked('second')
        self.release('first')
        self.merged('first', self.finish(first))
        self.release('second')
        result = self.finish(second)['result']
        self.assertFalse(result['merged'])
        self.assertTrue(result['local_tests']['passed'])
        self.assertIn('base or the head moved', result['local_tests']['error'])
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])

    def test_hosted_head_checks_use_integrated_head_after_waiting(self):
        for _, fixture in self.owners.values():
            (fixture / 'hosted-barrier').touch()
            (fixture / 'checks.json').write_text('[{"bucket": "pass"}]')
        first, second = self.contending()
        self.release('first')
        self.merged('first', self.finish(first))
        self.checked('second')
        first_merge = json.loads((self.owners['first'][1] / 'pr.json').read_text())['mergeCommit']['oid']
        self.release('second')
        result = self.merged('second', self.finish(second))
        self.assertEqual(result['checks'], 'pass')
        self.assertIsNone(result['local_tests'])
        self.assertEqual(git('merge-base', first_merge, result['head'], cwd=self.repo).strip(), first_merge)
        evidence = json.loads((self.owners['second'][1] / 'last_check_evidence.json').read_text())
        self.assertEqual(evidence['pullRequest']['headRefOid'], result['head'])
        self.assertEqual(evidence['pullRequest']['baseRef']['target']['oid'], first_merge)

    def test_lock_wait_times_out_without_publication_or_disrupting_holder(self):
        first = self.start('first')
        self.checked('first')
        second = self.start('second', lock_timeout=.05)
        payload = self.finish(second)
        self.assertEqual(payload['type'], 'LandError')
        self.assertIn('timed out', payload['error'].lower())
        self.assertFalse((self.owners['second'][1] / 'log.jsonl').exists())
        self.release('first')
        self.merged('first', self.finish(first))

    def test_external_main_change_still_refuses_green_candidate(self):
        first = self.start('first')
        self.checked('first')
        (self.repo / 'external.txt').write_text('external\n')
        git('add', 'external.txt', cwd=self.repo)
        git('commit', '-m', 'external main movement', cwd=self.repo)
        git('push', 'origin', 'main', cwd=self.repo)
        self.release('first')
        result = self.finish(first)['result']
        self.assertFalse(result['merged'])
        self.assertTrue(result['local_tests']['passed'])
        self.assertIn('base or the head moved', result['local_tests']['error'])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_conflicting_waiter_keeps_committed_work_and_releases_lock(self):
        for slug, (worktree, _) in self.owners.items():
            (worktree / 'README.md').write_text(slug + '\n')
            git('add', 'README.md', cwd=worktree)
        first, second = self.contending()
        self.release('first')
        self.merged('first', self.finish(first))
        payload = self.finish(second)
        self.assertEqual(payload['type'], 'LandError')
        self.assertIn('integrate current base', payload['error'])
        worktree, fixture = self.owners['second']
        self.assertEqual(git('show', 'HEAD:README.md', cwd=worktree), 'second\n')
        self.assertEqual((worktree / 'README.md').read_text(), 'second\n')
        self.assertEqual(git('status', '--porcelain', cwd=worktree), '')
        self.assertFalse(any(row[:2] == ['pr', 'merge']
                             for row in map(json.loads, (fixture / 'log.jsonl').read_text().splitlines())))
        self.owner('third', 103)
        self.release('third')
        self.merged('third', self.finish(self.start('third')))

    def test_terminated_holder_releases_os_lock_and_next_owner_finishes(self):
        first, second = self.contending()
        os.killpg(first[0].pid, signal.SIGKILL)
        first[0].join(5)
        self.assertEqual(first[0].exitcode, -signal.SIGKILL)
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(second))

    def test_cancelled_waiter_does_not_publish_and_holder_finishes(self):
        first, second = self.contending()
        os.killpg(second[0].pid, signal.SIGKILL)
        second[0].join(5)
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertFalse((self.owners['second'][1] / 'log.jsonl').exists())
        self.release('second')
        self.merged('second', self.finish(self.start('second')))

    def test_waiter_rechecks_new_hold_before_publication(self):
        first = self.start('first')
        self.checked('first')
        second = self.start('second')
        self.waiting(second)
        task = S.load_task(self.project, 'second')
        task['hold_merge'] = 'Operator review still required'
        S.save_task(self.project, task)
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertIn('merge hold', self.finish(second)['error'])
        self.assertFalse((self.owners['second'][1] / 'log.jsonl').exists())

    def test_replaced_waiter_cannot_publish_after_acquiring_lock(self):
        first, second = self.contending()
        task = S.load_task(self.project, 'second')
        task['attempt'] = 2
        S.save_task(self.project, task)
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertIn('no longer current', self.finish(second)['error'])
        self.assertFalse((self.owners['second'][1] / 'log.jsonl').exists())

    def test_task_messages_and_resume_requests_remain_live_during_landing_wait(self):
        first, second = self.contending()
        output = self.tmp / 'control-call'
        output.mkdir()
        process = multiprocessing.get_context('fork').Process(
            target=message_and_block_waiter, args=(self.project, output))
        process.start()
        control = (process, output)
        self.processes.append(control)
        messages = self.finish(control)
        self.assertTrue(first[0].is_alive())
        self.assertEqual(T.pending(self.project, 'first')[0]['id'], messages['holder']['id'])
        paused = S.load_task(self.project, 'second')
        self.assertEqual(paused['state'], 'blocked')
        self.assertEqual(paused['resume_request'], messages['wake']['id'])
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertIn('task is not running', self.finish(second)['error'])
        self.assertFalse((self.owners['second'][1] / 'log.jsonl').exists())

    def ship_check_workflow(self):
        """The base ships the check workflow, so both owners' landings require the PR `check`."""
        workflow = self.repo / land.config.PR_CHECK_WORKFLOW
        workflow.parent.mkdir(parents=True)
        workflow.write_text('on: [pull_request]\njobs:\n  check:\n    runs-on: self-hosted\n')
        git('add', land.config.PR_CHECK_WORKFLOW, cwd=self.repo)
        git('commit', '-q', '-m', 'require the PR check', cwd=self.repo)
        git('push', '-q', 'origin', 'main', cwd=self.repo)
        for worktree, _ in self.owners.values():
            git('merge', '-q', '--ff-only', 'main', cwd=worktree)

    def test_required_checks_started_together_run_one_after_the_other(self):
        # I-20260923-062538: two candidate `make check` runs on one machine timed out each other's walkthroughs.
        self.ship_check_workflow()
        first = self.start('first', merge=False, required_check=True)
        self.checked('first')
        second = self.start('second', merge=False, required_check=True)
        self.waiting(second)
        self.assertFalse((self.owners['second'][1] / 'tested.json').exists())
        self.assertFalse((self.owners['second'][1] / 'log.jsonl').exists())
        self.release('first')
        first_result = self.finish(first)['result']
        self.assertEqual((first_result['checks'], first_result['merged'], first_result['waited']), ('pass', False, 0))
        self.checked('second')
        self.release('second')
        result = self.finish(second)['result']
        self.assertEqual((result['checks'], result['merged']), ('pass', False))
        self.assertGreaterEqual(result['waited'], 0)
        for slug, number in (('first', 101), ('second', 102)):
            tested = json.loads((self.owners[slug][1] / 'tested.json').read_text())
            self.assertEqual(tested['tree'], git('rev-parse', tested['candidate'] + '^{tree}', cwd=self.repo).strip())
            self.assertFalse((S.task_dir(self.project, slug) / 'local-checks').exists())
            self.assertEqual(self.calls(slug, ['pr', 'merge']), [])
        self.assertIn('landing turn acquired after', (second[1] / 'notes').read_text())

    def test_nonmerge_publication_does_not_wait_for_another_candidate(self):
        first = self.start('first')
        self.checked('first')
        second = self.finish(self.start('second', merge=False))['result']
        self.assertFalse(second['merged'])
        self.assertTrue(first[0].is_alive())
        self.release('first')
        self.merged('first', self.finish(first))
