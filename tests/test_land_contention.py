"""Concurrent owners use real processes, task storage, worktrees and a bare Git remote.

Only GitHub and the candidate test command are fixtures. File barriers pause a real
candidate check: a hosted check outside the repository turn, a local candidate suite inside it.
"""
import contextlib
import json
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import time
import types
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
    # Landing's clock moves only when the case moves it (`advance`), so however slowly a busy machine runs the
    # owners, no wait expires that the case did not expire on purpose.
    clock = fixture / 'clock'
    # The merge line's heartbeats read one wall clock shared by every owner (`advance_wall`).
    wall = fixture.parent / 'wall'
    land.time = types.SimpleNamespace(monotonic=lambda: float(clock.read_text()), sleep=time.sleep,
                                      time=lambda: float(wall.read_text()))
    land.LINE_BEAT_SECONDS = .05
    if options.pop('required_check', False):
        (fixture / 'required-pr-check').touch()
        (fixture / 'hosted-barrier').touch()
        (fixture / 'checks.json').write_text('[{"bucket": "pass"}]')
    try:
        result = land.land('Independent fix ' + slug, cwd=worktree, wait=options.pop('wait', 20),
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
                    workflowRun={'event': 'pull_request', 'file': {'path': '.github/workflows/hosted-checks.yml'}})
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
        (self.tmp / 'wall').write_text('1000000')
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
        (fixture / 'clock').write_text('0')
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

    def advance(self, slug, seconds):
        """Move an owner's landing clock whole, so its reader never sees a partial number."""
        clock = self.owners[slug][1] / 'clock'
        moved = clock.with_name('clock-next')
        moved.write_text(str(float(clock.read_text()) + seconds))
        moved.replace(clock)

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
        """The first owner's local candidate suite holds the turn; the second publishes, then waits for it."""
        first = self.start('first')
        self.checked('first')
        second = self.start('second')
        self.waiting(second)
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])
        return first, second

    def reviewed(self, slug, *, assess=True, proposal=False, findings=()):
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
            return {'termination_confirmed': True, 'text': 'Review result', 'findings': list(findings), 'limitations': []}
        with mock.patch.object(route, 'pick_review', return_value=choice), \
                mock.patch.object(engines, 'review', side_effect=provider) as engine:
            if proposal:
                source = T.message(self.project, slug, 'l2', 'Proposal: preserve independent changes.')
                request = reviews.request(self.project, slug, actor='l2', expected_attempt=1,
                                          subject='proposal', request_id='proposal-' + slug)
                reviews.run(self.project, slug, request['id'], actor='l2', expected_attempt=1,
                            proposal_id=source['id'])
                self.assess(slug, review_id='proposal-' + slug)
            request = reviews.request(self.project, slug, actor='l2', expected_attempt=1,
                                      request_id='review-' + slug)
            reviews.run(self.project, slug, request['id'], actor='l2', expected_attempt=1)
            self.assertEqual(engine.call_count, 2 if proposal else 1)
        if assess:
            self.assess(slug)
            if proposal:
                self.assess(slug, review_id='proposal-' + slug)

    def assess(self, slug, *, review_id=None):
        return reviews.assess(self.project, slug, review_id or 'review-' + slug, actor='l2', expected_attempt=1,
                              dispositions=[], reason='Inspected the complete candidate and all current task context.')

    def assessment_wait(self, call):
        process, output = call
        self.await_condition(lambda: (output / 'notes').exists()
                             and 'waiting for owner assessment' in (output / 'notes').read_text(),
                             'explicit owner reassessment wait')
        self.assertTrue(process.is_alive())

    def test_green_current_pr_merges_while_another_owner_waits_for_its_check(self):
        # PR #748 waited behind other owners' full CI runs; PR #718: a CI wait must outlast 600 seconds.
        self.ship_check_workflow()
        first_fixture = self.owners['first'][1]
        first = self.start('first', required_check=True, wait=None)
        self.checked('first')
        (first_fixture / 'checks.json').write_text('[{"bucket": "pending"}]')
        self.release('first')
        self.await_condition(lambda: 'PR checks pending' in self.notes(first), 'pending check')
        self.advance('first', 900)  # Past the old 600-second default, within its hour.
        time.sleep(.5)  # several polls at the advanced clock
        self.assertTrue(first[0].is_alive())
        self.release('second')
        second = self.start('second', required_check=True)
        second_result = self.merged('second', self.finish(second))
        self.assertEqual((second_result['checks'], second_result['waited']), ('pass', 0))
        self.assertNotIn('waiting in the merge line', self.notes(second))
        second_merge = json.loads((self.owners['second'][1] / 'pr.json').read_text())['mergeCommit']['oid']
        # #777: main moved under the first candidate's pending check; it keeps that run and does not push.
        time.sleep(.5)
        self.assertEqual(self.notes(first).count('pushed head'), 1)
        self.assertNotIn('integrating current', self.notes(first))
        # Its check passes; its turn integrates main once and runs one fresh check.
        (first_fixture / 'checks.json').write_text('[{"bucket": "pass"}]')
        result = self.merged('first', self.finish(first))
        notes = self.notes(first)
        self.assertEqual(notes.count('integrating current'), 1)
        self.assertEqual(notes.count('pushed head'), 2)
        self.assertEqual(git('merge-base', second_merge, result['head'], cwd=self.repo).strip(), second_merge)
        evidence = json.loads((first_fixture / 'last_check_evidence.json').read_text())['pullRequest']
        self.assertEqual((evidence['headRefOid'], evidence['baseRef']['target']['oid']), (result['head'], second_merge))

    def test_reviewed_owner_reassesses_integrated_head_inside_its_turn(self):
        self.ship_check_workflow()
        self.reviewed('second', proposal=True)
        self.hold_a_second_ago(self.project, 'second', 'Operator approval of this delivery')
        approval = T.message(self.project, 'second', T.OPERATOR_MESSAGE_ROLE, 'Merge the reviewed delivery after checks.')
        self.assess('second')
        self.assess('second', review_id='proposal-second')
        old_review = S.load_task(self.project, 'second')['reviews'][0]
        first = self.start('first', required_check=True)
        self.checked('first')
        second = self.start('second', required_check=True, wait=60, approval=approval['id'])
        self.await_condition(lambda: (second[1] / 'notes').exists()
                             and 'pushed head' in self.notes(second), 'second publication')
        T.message(self.project, 'second', 'l3', 'Preserve both independent results after integration.')
        self.release('first')
        self.merged('first', self.finish(first))
        self.release('second')
        # Before its turn the owner reassesses only what changed for its own head: the task context.
        self.assessment_wait(second)
        notice = self.notes(second)
        self.assertIn('review proposal-second (proposal)', notice)
        self.assertIn('review review-second (changes)', notice)
        self.assertIn('context_hash:', notice)
        self.assertNotIn('base:', notice)
        self.assertNotIn('integrating current', notice)
        worktree, fixture = self.owners['second']
        self.assertEqual(git('rev-parse', 'HEAD', cwd=worktree).strip(), old_review['reconciled']['head'])
        # Its assessment wait holds no turn: a third owner merges meanwhile.
        self.owner('third', 103)
        git('merge', '-q', '--ff-only', 'origin/main', cwd=self.owners['third'][0])
        self.release('third')
        self.merged('third', self.finish(self.start('third', required_check=True)))
        third_merge = json.loads((self.owners['third'][1] / 'pr.json').read_text())['mergeCommit']['oid']
        self.assertEqual(self.notes(second).count('pushed head'), 1)
        self.assess('second')
        self.assess('second', review_id='proposal-second')
        # Its turn integrates current main once; the integrated head needs the owner again, within the turn. A poll
        # between the two assessments above notes the one still waiting, so the wait reads after the second push.
        self.await_condition(lambda: 'waiting for owner assessment'
                             in ''.join(self.notes(second).split('pushed head')[2:]), 'assessment of the integrated head')
        self.assertIn('head:', self.notes(second).rsplit('waiting for owner assessment', 1)[1])
        self.assertEqual(self.notes(second).count('pushed head'), 2)
        integrated = git('rev-parse', 'HEAD', cwd=worktree).strip()
        self.assertEqual(git('merge-base', third_merge, integrated, cwd=self.repo).strip(), third_merge)
        self.assertEqual(git('diff', '--name-only', 'origin/main', 'HEAD', cwd=worktree).strip(), 'second.txt')
        self.assertEqual(self.line(), [102])  # the integrated head keeps its turn while its owner reassesses
        self.assess('second')
        self.assertEqual(S.load_task(self.project, 'second')['hold_merge'], 'Operator approval of this delivery')
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])
        self.assess('second', review_id='proposal-second')
        result = self.merged('second', self.finish(second))
        self.assertEqual(result['head'], integrated)
        self.assertEqual(result['checks'], 'pass')
        evidence = json.loads((fixture / 'last_check_evidence.json').read_text())['pullRequest']
        self.assertEqual(evidence['headRefOid'], integrated)
        self.assertEqual(evidence['baseRef']['target']['oid'], third_merge)
        saved = S.load_task(self.project, 'second')['reviews'][0]
        self.assertEqual(saved['snapshot'], old_review['snapshot'])
        self.assertEqual(saved['merged_head'], integrated)
        finished = S.load_task(self.project, 'second')
        self.assertIsNone(finished['hold_merge'])
        self.assertEqual(finished['merge_approval']['approval'], approval['id'])
        self.assertEqual(self.line(), [])

    def late_context(self, *, hosted):
        if hosted:
            self.ship_check_workflow()
        slug = 'first'
        self.reviewed(slug)
        self.hold_a_second_ago(self.project, slug, 'Operator approval required')
        approval = T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, 'Merge after checks.')
        self.assess(slug)
        call = self.start(slug, required_check=hosted, wait=20, approval=approval['id'])
        self.checked(slug)
        T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, 'Retain the complete public result.')
        self.release(slug)
        self.assessment_wait(call)
        self.assertEqual(self.calls(slug, ['pr', 'merge']), [])
        with self.assertRaises(T.TransitionError):
            reviews.assess(self.project, slug, 'review-' + slug, actor='l2', expected_attempt=1,
                           dispositions=[{'finding_id': 'unknown', 'disposition': 'fixed', 'reason': 'Invalid'}],
                           reason='An invalid assessment cannot release this wait.')
        self.assertEqual(S.load_task(self.project, slug)['hold_merge'], 'Operator approval required')
        self.assertEqual(self.calls(slug, ['pr', 'merge']), [])
        self.assess(slug)
        self.merged(slug, self.finish(call))
        self.assertEqual(len(self.calls(slug, ['pr', 'create'])), 0)

    def test_message_during_hosted_validation_waits_without_requeueing(self):
        self.late_context(hosted=True)

    def test_message_during_local_validation_waits_without_requeueing(self):
        self.late_context(hosted=False)

    def test_final_assessment_does_not_restart_wait_deadline(self):
        self.reviewed('first')
        call = self.start('first', wait=.1)
        self.checked('first')
        T.message(self.project, 'first', 'l3', 'Inspect this correction before merging.')
        self.advance('first', 1)  # The local suite uses the existing file barrier past --wait.
        self.release('first')
        self.assertIn('assessment wait timed out', self.finish(call)['error'])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_review_request_remains_available_during_nonmerging_ci_read(self):
        self.ship_check_workflow()
        self.reviewed('first')
        first = self.start('first', merge=False, required_check=True)
        self.checked('first')  # GitHub response is held at a file barrier inside _checks_value.
        choice = {'engine': config.ENGINES[1], 'model': 'fixture', 'label': 'Fixture', 'allowance_known': True}
        with mock.patch.object(route, 'pick_review', return_value=choice):
            request = reviews.request(self.project, 'first', actor='l2', expected_attempt=1,
                                      subject='proposal', request_id='review-during-ci')
        self.assertEqual(request['state'], 'requested')
        self.release('first')
        result = self.finish(first)['result']
        self.assertEqual((result['checks'], result['merged']), ('pass', False))

    def test_new_context_after_assessment_prompts_again_while_ci_is_pending(self):
        self.ship_check_workflow()
        self.stale_review()
        first = self.start('first', required_check=True, wait=20)
        self.assessment_wait(first)
        fixture = self.owners['first'][1]
        (fixture / 'checks.json').write_text('[{"bucket":"pending"}]')
        self.assess('first')
        T.message(self.project, 'first', 'l3', 'Correction: inspect the final context too.')
        self.await_condition(lambda: (first[1] / 'notes').read_text().count('waiting for owner assessment') >= 2,
                             'renewed assessment notice')
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.assess('first')
        (fixture / 'checks.json').write_text('[{"bucket":"pass"}]')
        self.merged('first', self.finish(first))

    def stale_review(self):
        self.reviewed('first')
        T.message(self.project, 'first', 'l3', 'Check the current candidate before proceeding.')
        self.release('first')

    def test_assessment_timeout_releases_turn_and_keeps_published_candidate(self):
        self.stale_review()
        first = self.start('first', wait=.2)
        self.assessment_wait(first)
        self.advance('first', 1)
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

    def test_assessment_wait_leaves_the_turn_to_a_competing_owner(self):
        self.stale_review()
        first = self.start('first', wait=20)
        self.assessment_wait(first)
        self.release('second')
        self.merged('second', self.finish(self.start('second')))
        self.assertTrue(first[0].is_alive())
        os.killpg(first[0].pid, signal.SIGKILL)
        first[0].join(5)
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_ended_owner_session_ends_assessment_wait_and_tells_its_next_turn(self):
        # A usage-limited owner's job stopped while its landing waited for an assessment that could not come.
        self.stale_review()
        first = self.start('first', wait=20)
        self.assessment_wait(first)
        second = self.start('second')
        self.checked('second')  # The first owner's assessment does not hold the merge turn.
        self.assertTrue(first[0].is_alive())
        os.killpg(first[0].pid, signal.SIGTERM)
        self.assertIn("owner's session ended", self.finish(first)['error'])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        head = S.load_task(self.project, 'first')['delivery']['head']
        self.assertEqual(git('rev-parse', 'worktree-first', cwd=self.remote).strip(), head)
        notices = [row for row in T.pending(self.project, 'first') if row['by'] == 'landing']
        self.assertEqual(len(notices), 1)
        self.assertIn(f'PR #101. Head {head} remains published and unmerged', T.render_inbox(notices))
        self.assertIn('re-run `alt land --merge`', notices[0]['text'])
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

    def test_unresolved_findings_refuse_merge_until_owner_resolves_them(self):
        finding = {'id': 'F1', 'severity': 'high', 'title': 'Untested path', 'body': 'The fallback lacks coverage.'}
        self.reviewed('first', assess=False, findings=[finding])
        reviews.assess(self.project, 'first', 'review-first', actor='l2', expected_attempt=1,
                       dispositions=[{'finding_id': 'F1', 'disposition': 'open', 'reason': 'Needs a separate experiment.'}],
                       reason='Recorded the finding honestly; it remains unresolved.')
        first = self.start('first', wait=20)
        self.assertIn('has unresolved findings: F1', self.finish(first)['error'])
        self.assertNotIn('waiting for owner assessment', (first[1] / 'notes').read_text())
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        reviews.assess(self.project, 'first', 'review-first', actor='l2', expected_attempt=1,
                       dispositions=[{'finding_id': 'F1', 'disposition': 'fixed', 'reason': 'Added the fallback test.'}],
                       reason='Checked the fix against the complete candidate.')
        self.release('first')
        self.merged('first', self.finish(self.start('first')))

    def test_main_change_during_assessment_waits_for_the_turn_to_integrate(self):
        self.stale_review()
        first = self.start('first', wait=20)
        self.assessment_wait(first)
        self.outside_merge()
        time.sleep(.5)  # several polls after main moved
        self.assertNotIn('integrating current', self.notes(first))
        self.assertEqual(self.notes(first).count('waiting for owner assessment'), 1)
        self.assess('first')
        self.await_condition(lambda: 'integrating current' in self.notes(first), 'integration in the turn')
        self.await_condition(lambda: self.notes(first).count('waiting for owner assessment') >= 2,
                             'assessment of the integrated head')
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.assess('first')
        result = self.merged('first', self.finish(first))
        self.assertEqual(git('show', result['head'] + ':external.txt', cwd=self.repo), 'External main update\n')

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
        # One landing per PR: the retry takes over the PR's place in line, so the first cannot merge.
        retry = self.start('first')
        self.await_condition(lambda: (retry[1] / 'notes').exists()
                             and 'heads the merge line' in self.notes(retry), 'retry takes over the turn')
        self.release('first')
        payload = self.finish(first)
        self.assertIn('lost its merge turn', payload['error'])
        self.merged('first', self.finish(retry))
        self.assertEqual(len(self.calls('first', ['pr', 'merge'])), 1)
        self.assertEqual(self.finish(self.start('first'))['result']['checks'], 'merged')
        self.assertEqual(self.line(), [])

    def test_hosted_head_checks_use_integrated_head_after_main_moves(self):
        for _, fixture in self.owners.values():
            (fixture / 'hosted-barrier').touch()
            (fixture / 'checks.json').write_text('[{"bucket": "pass"}]')
        first = self.start('first', wait=20)
        second = self.start('second', wait=20)
        self.checked('first')
        self.checked('second')  # Both hosted checks run at once, outside the turn.
        self.release('first')
        self.merged('first', self.finish(first))
        first_merge = json.loads((self.owners['first'][1] / 'pr.json').read_text())['mergeCommit']['oid']
        self.release('second')
        result = self.merged('second', self.finish(second))
        self.assertEqual(result['checks'], 'pass')
        self.assertIsNone(result['local_tests'])
        self.assertEqual(git('merge-base', first_merge, result['head'], cwd=self.repo).strip(), first_merge)
        evidence = json.loads((self.owners['second'][1] / 'last_check_evidence.json').read_text())
        self.assertEqual(evidence['pullRequest']['headRefOid'], result['head'])
        self.assertEqual(evidence['pullRequest']['baseRef']['target']['oid'], first_merge)

    def test_turn_wait_times_out_without_merging_or_disrupting_holder(self):
        first = self.start('first')
        self.checked('first')
        second = self.start('second', lock_timeout=.05)
        self.waiting(second)
        self.assertEqual(self.line(), [101, 102])
        self.advance('second', 1)
        payload = self.finish(second)
        self.assertEqual(payload['type'], 'LandError')
        self.assertIn('merge line wait timed out with 1 candidate(s) ahead', payload['error'])
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])
        self.assertEqual(self.line(), [101])
        self.release('first')
        self.merged('first', self.finish(first))

    def test_external_main_change_rechecks_integrated_local_candidate(self):
        first = self.start('first')
        self.checked('first')
        (self.repo / 'external.txt').write_text('external\n')
        git('add', 'external.txt', cwd=self.repo)
        git('commit', '-m', 'external main movement', cwd=self.repo)
        git('push', 'origin', 'main', cwd=self.repo)
        self.release('first')
        result = self.finish(first)['result']
        self.assertTrue(result['merged'])
        self.assertTrue(result['local_tests']['passed'])
        self.assertEqual(git('show', f"{result['head']}:external.txt", cwd=self.repo), 'external\n')
        self.assertEqual(len(self.calls('first', ['pr', 'merge'])), 1)

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

    def test_killed_holder_leaves_the_line_once_its_heartbeat_is_stale(self):
        first, second = self.contending()
        os.killpg(first[0].pid, signal.SIGKILL)
        first[0].join(5)
        self.assertEqual(first[0].exitcode, -signal.SIGKILL)
        time.sleep(.5)  # the waiter keeps waiting while the head's last heartbeat is recent
        self.assertEqual(self.line(), [101, 102])
        self.assertTrue(second[0].is_alive())
        self.advance_wall(land.LINE_STALE_SECONDS + 1)
        self.release('second')
        self.merged('second', self.finish(second))
        self.assertIn('PR #101 heads the merge line without a heartbeat', self.notes(second))
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.assertEqual(self.line(), [])

    def test_paused_holder_that_lost_its_turn_cannot_merge(self):
        first, second = self.contending()
        os.killpg(first[0].pid, signal.SIGSTOP)
        self.advance_wall(land.LINE_STALE_SECONDS + 1)
        self.await_condition(lambda: 'its turn starts' in self.notes(second), 'next owner takes the turn')
        self.release('first')
        self.release('second')
        self.merged('second', self.finish(second))
        os.killpg(first[0].pid, signal.SIGCONT)
        payload = self.finish(first)
        self.assertIn('lost its merge turn', payload.get('error', ''))
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_cancelled_waiter_does_not_merge_and_holder_finishes(self):
        first, second = self.contending()
        os.killpg(second[0].pid, signal.SIGKILL)
        second[0].join(5)
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])
        self.release('second')
        self.merged('second', self.finish(self.start('second')))

    def test_waiter_rechecks_new_hold_before_merging(self):
        first = self.start('first')
        self.checked('first')
        second = self.start('second')
        self.waiting(second)
        task = S.load_task(self.project, 'second')
        task['hold_merge'] = 'Operator review still required'
        S.save_task(self.project, task)
        published = git('rev-parse', 'worktree-second', cwd=self.remote).strip()
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertIn('merge hold', self.finish(second)['error'])
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])
        # A held candidate never integrates main or pushes at its turn.
        self.assertNotIn('integrating current', self.notes(second))
        self.assertEqual(git('rev-parse', 'worktree-second', cwd=self.remote).strip(), published)
        self.assertEqual(self.line(), [])

    def test_replaced_waiter_cannot_merge(self):
        first, second = self.contending()
        task = S.load_task(self.project, 'second')
        task['attempt'] = 2
        S.save_task(self.project, task)
        self.release('first')
        self.merged('first', self.finish(first))
        self.assertIn('no longer current', self.finish(second)['error'])
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])

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
        self.assertEqual(self.calls('second', ['pr', 'merge']), [])

    def ship_check_workflow(self):
        """The base ships the check workflow, so both owners' landings require the PR `check`."""
        workflow = self.repo / land.config.PR_CHECK_WORKFLOW
        workflow.parent.mkdir(parents=True)
        workflow.write_text('on: [pull_request]\njobs:\n  check:\n    runs-on: ubuntu-latest\n')
        git('add', land.config.PR_CHECK_WORKFLOW, cwd=self.repo)
        git('commit', '-q', '-m', 'require the PR check', cwd=self.repo)
        git('push', '-q', 'origin', 'main', cwd=self.repo)
        for worktree, _ in self.owners.values():
            git('merge', '-q', '--ff-only', 'main', cwd=worktree)

    def test_nonmerging_publications_wait_for_their_checks_together(self):
        # Hosted checks share no machine; only merges take the repository turn.
        self.ship_check_workflow()
        first = self.start('first', merge=False, required_check=True)
        second = self.start('second', merge=False, required_check=True)
        self.checked('first')
        self.checked('second')
        self.release('first')
        self.release('second')
        for slug, call in (('first', first), ('second', second)):
            result = self.finish(call)['result']
            self.assertEqual((result['checks'], result['merged'], result['waited']), ('pass', False, 0))
            self.assertNotIn('waiting for another merge', (call[1] / 'notes').read_text())
            self.assertEqual(self.calls(slug, ['pr', 'merge']), [])

    def test_nonmerging_publication_reports_its_own_check_when_main_moves(self):
        self.ship_check_workflow()
        first = self.start('first', merge=False, required_check=True)
        self.checked('first')
        self.outside_merge()
        self.release('first')
        result = self.finish(first)['result']
        self.assertEqual((result['checks'], result['merged']), ('pass', False))
        head = S.load_task(self.project, 'first')['delivery']['head']
        self.assertEqual(git('rev-parse', 'worktree-first', cwd=self.remote).strip(), head)
        self.assertEqual(self.notes(first).count('pushed head'), 1)
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.assertEqual(self.line(), [])

    def test_nonmerge_publication_does_not_wait_for_another_candidate(self):
        first = self.start('first')
        self.checked('first')
        second = self.finish(self.start('second', merge=False))['result']
        self.assertFalse(second['merged'])
        self.assertTrue(first[0].is_alive())
        self.release('first')
        self.merged('first', self.finish(first))


    def outside_merge(self):
        """Another installation or a hand merge advances main; returns the new main."""
        (self.repo / 'external.txt').write_text('External main update\n')
        git('add', 'external.txt', cwd=self.repo)
        git('commit', '-qm', 'External main update', cwd=self.repo)
        git('push', '-q', 'origin', 'main', cwd=self.repo)
        return git('rev-parse', 'HEAD', cwd=self.repo).strip()


    def notes(self, call):
        return (call[1] / 'notes').read_text()

    def advance_wall(self, seconds):
        """Move the wall clock every owner's merge line heartbeats read."""
        wall = self.tmp / 'wall'
        moved = wall.with_name('wall-next')
        moved.write_text(str(float(wall.read_text()) + seconds))
        moved.replace(wall)

    def line(self, *, entries=False):
        """The merge line on the shared remote: its PR numbers in order, or its entries."""
        found = git('for-each-ref', '--format=%(objectname)', land.LINE_REF, cwd=self.remote).strip()
        if not found:
            return []
        rows = json.loads(git('log', '-1', '--format=%B', found, cwd=self.remote))['entries']
        return rows if entries else [row['pr'] for row in rows]

    def write_line(self, rows):
        """Another installation's landing writes the shared line directly."""
        if not rows:
            git('update-ref', '-d', land.LINE_REF, cwd=self.remote)
            return
        tree = git('hash-object', '-t', 'tree', '-w', os.devnull, cwd=self.remote).strip()
        commit = git('commit-tree', tree, '-m', json.dumps({'entries': rows}), cwd=self.remote).strip()
        git('update-ref', land.LINE_REF, commit, cwd=self.remote)

    def foreign_head(self, *, beat=None):
        """A landing on another installation holds the turn with a recent heartbeat."""
        wall = float((self.tmp / 'wall').read_text())
        self.write_line([{'pr': 900, 'token': 'other-installation', 'ready': wall, 'beat': wall if beat is None else beat},
                         *self.line(entries=True)])

    def ready_in_line(self, slug, number, position):
        call = self.start(slug, required_check=True, wait=60)
        self.await_condition(lambda: (call[1] / 'notes').exists()
                             and f'{position} ready candidate(s) ahead of PR #{number}' in self.notes(call),
                             f'{slug} waiting in line')
        return call

    def test_three_ready_candidates_merge_in_order_with_at_most_two_checks_each(self):
        # #777: ten ready PRs re-integrated and re-ran their checks on every merge, cancelling their own runs.
        self.ship_check_workflow()
        self.owner('third', 103)
        git('merge', '-q', '--ff-only', 'origin/main', cwd=self.owners['third'][0])
        for slug in ('first', 'second', 'third'):
            self.release(slug)
        self.foreign_head()
        calls = [self.ready_in_line('first', 101, 1), self.ready_in_line('second', 102, 2),
                 self.ready_in_line('third', 103, 3)]
        self.assertEqual(self.line(), [900, 101, 102, 103])
        outside = self.outside_merge()  # the other installation's head merges, then leaves the line
        self.write_line(self.line(entries=True)[1:])
        merges = []
        for slug, call in zip(('first', 'second', 'third'), calls):
            result = self.merged(slug, self.finish(call))
            merges.append(json.loads((self.owners[slug][1] / 'pr.json').read_text())['mergeCommit']['oid'])
            notes = self.notes(call)
            # Published once, then integrated once in its turn: two checks, none cancelled by its own push.
            self.assertEqual(notes.count('pushed head'), 2)
            self.assertEqual(notes.count('integrating current'), 1)
            self.assertEqual(git('merge-base', merges[-2] if len(merges) > 1 else outside, result['head'],
                                 cwd=self.repo).strip(), merges[-2] if len(merges) > 1 else outside)
            self.assertLess(notes.index('its turn starts'), notes.index('integrating current'))
        self.assertEqual(git('rev-list', '--first-parent', '--max-count=3', 'main', cwd=self.remote).split(),
                         merges[::-1])
        self.assertEqual(self.line(), [])

    def test_ready_candidate_on_current_main_merges_on_its_existing_check(self):
        self.ship_check_workflow()
        self.release('first')
        result = self.merged('first', self.finish(self.start('first', required_check=True)))
        self.assertEqual(self.notes(self.processes[-1]).count('pushed head'), 1)
        self.assertNotIn('integrating current', self.notes(self.processes[-1]))
        self.assertEqual(result['waited'], 0)

    def test_outside_merge_during_the_turns_fresh_check_integrates_again_in_the_turn(self):
        self.ship_check_workflow()
        fixture = self.owners['first'][1]
        self.release('first')
        self.foreign_head()
        first = self.ready_in_line('first', 101, 1)
        self.outside_merge()
        (fixture / 'checks.json').write_text('[{"bucket": "pending"}]')
        self.write_line(self.line(entries=True)[1:])
        self.await_condition(lambda: 'PR checks pending' in self.notes(first), "the turn's fresh check")
        self.assertEqual(self.notes(first).count('pushed head'), 2)
        (self.repo / 'external.txt').write_text('A second outside merge\n')
        git('commit', '-qam', 'Second outside merge', cwd=self.repo)
        git('push', '-q', 'origin', 'main', cwd=self.repo)
        second_outside = git('rev-parse', 'HEAD', cwd=self.repo).strip()
        self.await_condition(lambda: self.notes(first).count('pushed head') == 3, 'integrated again within the turn')
        self.assertIn('within the turn', self.notes(first))
        (fixture / 'checks.json').write_text('[{"bucket": "pass"}]')
        result = self.merged('first', self.finish(first))
        evidence = json.loads((fixture / 'last_check_evidence.json').read_text())['pullRequest']
        self.assertEqual((evidence['headRefOid'], evidence['baseRef']['target']['oid']), (result['head'], second_outside))

    def test_foreign_head_without_a_heartbeat_is_removed_and_the_line_moves_on(self):
        self.ship_check_workflow()
        self.release('first')
        self.foreign_head()
        first = self.ready_in_line('first', 101, 1)
        time.sleep(.5)
        self.assertEqual(self.line(), [900, 101])
        self.advance_wall(land.LINE_STALE_SECONDS + 1)
        self.merged('first', self.finish(first))
        self.assertIn('PR #900 heads the merge line without a heartbeat', self.notes(first))
        self.assertEqual(self.line(), [])

    def test_waiting_candidate_leaves_the_line_when_its_wait_is_zero(self):
        self.ship_check_workflow()
        self.release('first')
        self.foreign_head()
        payload = self.finish(self.start('first', required_check=True, wait=0))
        self.assertIn('merge line wait timed out with 1 candidate(s) ahead', payload['error'])
        self.assertEqual(self.line(), [900])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_invalid_approval_refuses_before_integrating_main(self):
        self.ship_check_workflow()
        self.release('first')
        published = self.finish(self.start('first', merge=False, required_check=True))['result']
        self.outside_merge()
        self.hold_a_second_ago(self.project, 'first', 'Operator approval required')
        call = self.start('first', required_check=True, approval='not-an-operator-message')
        self.assertIn('recorded merge approval refused', self.finish(call)['error'])
        self.assertNotIn('integrating current', self.notes(call))
        self.assertEqual(git('rev-parse', 'worktree-first', cwd=self.remote).strip(), published['head'])
        self.assertEqual(self.line(), [])

    def test_held_candidate_without_a_pr_publishes_without_integrating_main(self):
        self.ship_check_workflow()
        self.outside_merge()
        self.hold_a_second_ago(self.project, 'first', 'Operator approval required')
        self.release('first')
        call = self.start('first', required_check=True, approval='not-an-operator-message')
        self.assertIn('recorded merge approval refused', self.finish(call)['error'])
        self.assertNotIn('integrating current', self.notes(call))
        worktree = self.owners['first'][0]
        self.assertEqual(git('rev-parse', 'worktree-first', cwd=self.remote).strip(),
                         git('rev-parse', 'HEAD', cwd=worktree).strip())
        self.assertNotEqual(subprocess.run(['git', 'merge-base', '--is-ancestor', 'main', 'worktree-first'],
                                           cwd=self.remote).returncode, 0)
        self.assertEqual(self.line(), [])

    @contextlib.contextmanager
    def network(self, slug, *, flaky=False):
        """Git commands that reach origin from `slug`'s landings fail as a connection timing out does on a flaky
        network: the first of every two of each kind (`flaky`), and every one while the fixture's `offline` file
        exists. A forked landing keeps the failures; each is logged by kind."""
        fixture, real, seen = self.owners[slug][1], land._execute, {}

        def execute(args, cwd, timeout):
            if args[0] == 'git' and args[1] in land.REMOTE_GIT:
                kind = f"{args[1]} {re.sub('^[0-9a-f]{40}$', '<line commit>', args[-1].split(':')[-1])}"
                seen[kind] = seen.get(kind, 0) + 1
                if (fixture / 'offline').exists() or flaky and seen[kind] % 2:
                    with (fixture / 'network-failures').open('a') as log:
                        log.write(kind + '\n')
                    return subprocess.CompletedProcess(args, 128, '', (
                        "fatal: unable to access 'https://github.com/team/demo.git/': Failed to connect to "
                        "github.com port 443 after 75002 ms: Couldn't connect to server\n"))
            return real(args, cwd, timeout)

        with mock.patch.object(land, '_execute', execute):
            yield

    def network_failures(self, slug):
        failures = self.owners[slug][1] / 'network-failures'
        return failures.read_text().splitlines() if failures.exists() else []

    def test_flaky_network_keeps_the_waiting_candidates_place_and_merges(self):
        # A network failure of one git command dropped a waiting candidate from the line and ended its landing.
        self.ship_check_workflow()
        self.release('first')
        self.foreign_head()
        with self.network('first', flaky=True):
            first = self.ready_in_line('first', 101, 1)
        self.await_condition(lambda: self.network_failures('first').count(f'ls-remote {land.LINE_REF}') >= 3,
                             'failed line reads while waiting')
        self.assertTrue(first[0].is_alive())
        self.assertEqual(self.line(), [900, 101])
        self.outside_merge()  # the turn refreshes main, integrates it and pushes with its heartbeat
        self.write_line(self.line(entries=True)[1:])
        self.merged('first', self.finish(first))
        failed = set(self.network_failures('first'))
        for step in (f'ls-remote {land.LINE_REF}', 'fetch <line commit>', 'fetch refs/remotes/origin/main',
                     'fetch refs/remotes/origin/worktree-first', f'push {land.LINE_REF}', 'push refs/heads/worktree-first'):
            self.assertIn(step, failed)
        self.assertIn('failed on the network; trying again', self.notes(first))
        self.assertEqual(self.notes(first).count('pushed head'), 2)
        self.assertEqual(self.line(), [])

    def test_lasting_network_failure_ends_the_landing_within_its_bound(self):
        self.ship_check_workflow()
        self.release('first')
        self.foreign_head()
        fixture = self.owners['first'][1]
        with self.network('first'):
            first = self.ready_in_line('first', 101, 1)
        (fixture / 'offline').touch()
        self.await_condition(lambda: 'failed on the network' in self.notes(first), 'a failed line read')
        # Each step gives up once the bound has passed since its first failure: the read, then leaving the line.
        self.await_condition(lambda: self.advance('first', land.NETWORK_RETRY_SECONDS) or not first[0].is_alive(),
                             'the landing to end')
        payload = self.finish(first)
        self.assertRegex(payload['error'], r"read the merge line: .*Couldn't connect to server \(a network failure: git "
                                           r"ls-remote kept failing for \d+s; check this machine's connection")
        self.assertIn('could not leave the merge line', self.notes(first))
        # The entry it could not remove is dropped once it heads the line without a heartbeat.
        self.assertEqual(self.line(), [900, 101])
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])

    def test_a_timed_out_line_read_is_tried_again_and_refusals_are_not(self):
        worktree, real, notes = self.owners['first'][0], land._execute, []

        def stalls_once(args, cwd, timeout):
            if args[1] == 'ls-remote' and not notes:
                raise land.LandError('git ls-remote origin: no answer') from subprocess.TimeoutExpired(args, timeout)
            return real(args, cwd, timeout)

        with mock.patch.object(land, '_execute', stalls_once), mock.patch.object(land, 'CHECK_POLL_SECONDS', .01), \
                mock.patch.object(land, '_note', notes.append):
            land._Line(worktree, 101).join()
            self.assertEqual(len(notes), 1)
            self.assertIn('git ls-remote failed on the network', notes[0])
            self.assertEqual(self.line(), [101])
            # Refusals return at once: a branch origin lacks, and a push origin rejects.
            self.assertIsNone(land._fetch_remote_tip(worktree, 'no-such-branch'))
            refused = land._git(worktree, 'push', 'origin', 'HEAD~1:refs/heads/main')
            self.assertIn('[rejected]', refused.stderr)
            self.assertEqual(len(notes), 1)
        for refusal in ("fatal: unable to access 'https://github.com/team/demo.git/': The requested URL returned "
                        "error: 403", "remote: Repository not found.", "fatal: Authentication failed for "
                        "'https://github.com/team/demo.git/'", "error: failed to push some refs (stale info)"):
            self.assertIsNone(land._NETWORK_FAILURE.search(refusal))
        for failure in ("fatal: unable to access 'https://github.com/team/demo.git/': Could not resolve host: github.com",
                        "error: RPC failed; curl 56 Recv failure: Connection reset by peer",
                        "fatal: unable to access 'https://github.com/team/demo.git/': The requested URL returned "
                        "error: 502"):
            self.assertIsNotNone(land._NETWORK_FAILURE.search(failure))

    def test_line_write_retries_a_compare_and_swap_lost_at_the_remote(self):
        worktree, real_git, refused = self.owners['first'][0], land._git, []

        def remote_lost_once(root, *args, **kwargs):
            if args[0] == 'push' and not refused:
                refused.append(args)
                return subprocess.CompletedProcess(args, 1, '', f' ! [remote rejected] 0123 -> {land.LINE_REF} '
                                                                '(incorrect old value provided)')
            return real_git(root, *args, **kwargs)

        with mock.patch.object(land, '_git', remote_lost_once):
            land._Line(worktree, 101).join()
        self.assertEqual(len(refused), 1)
        self.assertEqual(self.line(), [101])

    def test_a_turn_that_was_taken_over_cannot_push(self):
        worktree = self.owners['first'][0]
        git('commit', '-qm', 'publishable', cwd=worktree)
        git('push', '-q', 'origin', 'worktree-first', cwd=worktree)
        published = git('rev-parse', 'HEAD', cwd=worktree).strip()
        (worktree / 'later.txt').write_text('integrated later\n')
        git('add', 'later.txt', cwd=worktree)
        git('commit', '-qm', 'integration in a lost turn', cwd=worktree)
        clock = [1000000.0]
        with mock.patch.object(land, 'time', types.SimpleNamespace(time=lambda: clock[0], sleep=time.sleep,
                                                                   monotonic=time.monotonic)):
            paused, waiter = land._Line(worktree, 101), land._Line(self.owners['second'][0], 102)
            paused.join()
            waiter.join()
            read_before_takeover = paused._read()
            clock[0] += land.LINE_STALE_SECONDS + 1
            self.assertEqual(waiter.position(), 0)
            # The paused head read the line just before the takeover: its branch push and heartbeat fail together.
            reads = iter([read_before_takeover])
            real_read = paused._read
            with mock.patch.object(paused, '_read', side_effect=lambda: next(reads, None) or real_read()), \
                    self.assertRaisesRegex(land.LandError, 'lost its merge turn'):
                paused.beat(push=('refs/heads/worktree-first:refs/heads/worktree-first',))
        self.assertEqual(git('rev-parse', 'worktree-first', cwd=self.remote).strip(), published)
        self.assertEqual(self.line(), [102])


    def test_external_main_change_refuses_green_candidate_once_the_wait_is_spent(self):
        first = self.start('first', wait=0)
        self.checked('first')
        self.outside_merge()
        self.release('first')
        self.assertRegex(self.finish(first)['error'], 'origin/main moved .* remains published and unmerged')
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])


    def test_repeated_external_main_changes_exhaust_the_original_deadline(self):
        fixture = self.owners['first'][1]
        # Each real candidate check moves the bare remote from an independent checkout.
        # A reset deadline incorrectly admits a fourth candidate, which would merge successfully.
        advance = f'''
count_path = d / 'candidate-count'
count = int(count_path.read_text()) + 1 if count_path.exists() else 1
count_path.write_text(str(count))
if count <= 3:
    root = Path({str(self.repo)!r})
    (root / 'external.txt').write_text('External merge ' + str(count) + '\\n')
    for args in [('add', 'external.txt'), ('commit', '-qm', 'External merge ' + str(count)),
                 ('push', '-q', 'origin', 'main')]:
        subprocess.run(['git', *args], cwd=root, check=True)
    clock_path = d / 'clock'
    replacement = d / 'clock-next'
    replacement.write_text(str([4, 8, 11][count - 1]))
    replacement.replace(clock_path)
'''
        (self.tmp / 'bin/fixture-candidate-check').write_text(
            RUNNER.replace("print('Ran 1 test", advance + "\nprint('Ran 1 test"))
        self.release('first')
        first = self.start('first', wait=10)
        payload = self.finish(first)
        self.assertEqual(payload['type'], 'LandError')
        self.assertIn('remains published and unmerged', payload['error'])
        self.assertEqual((fixture / 'candidate-count').read_text(), '3')
        self.assertEqual(self.notes(first).count('pushed head'), 3)
        self.assertEqual(self.notes(first).count('within the turn'), 2)
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
        self.assertEqual(json.loads((fixture / 'pr.json').read_text())['state'], 'OPEN')
        (self.tmp / 'bin/fixture-candidate-check').write_text(RUNNER)
        self.release('second')
        self.merged('second', self.finish(self.start('second')))


    def test_outside_merge_during_a_pending_first_check_waits_for_the_turn(self):
        self.ship_check_workflow()
        fixture = self.owners['first'][1]
        first = self.start('first', required_check=True, wait=20)
        self.checked('first')
        (fixture / 'checks.json').write_text('[{"bucket": "pending"}]')
        self.release('first')
        self.await_condition(lambda: 'PR checks pending' in self.notes(first), 'pending check')
        outside = self.outside_merge()
        time.sleep(.5)  # several polls after main moved: the pending run is kept, nothing is pushed
        self.assertEqual(self.notes(first).count('pushed head'), 1)
        (fixture / 'checks.json').write_text('[{"bucket": "pass"}]')
        result = self.merged('first', self.finish(first))
        self.assertEqual(self.notes(first).count('pushed head'), 2)
        evidence = json.loads((fixture / 'last_check_evidence.json').read_text())['pullRequest']
        self.assertEqual((evidence['headRefOid'], evidence['baseRef']['target']['oid']), (result['head'], outside))

    def test_foreign_push_with_a_moved_main_refuses_without_integrating(self):
        first = self.start('first', wait=20)
        self.checked('first')
        self.outside_merge()
        other = self.tmp / 'foreign'
        git('clone', '-q', str(self.remote), str(other), cwd=self.tmp)
        git('checkout', '-q', 'worktree-first', cwd=other)
        (other / 'foreign.txt').write_text('foreign\n')
        git('add', 'foreign.txt', cwd=other)
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'foreign', cwd=other)
        git('push', '-q', 'origin', 'worktree-first', cwd=other)
        self.release('first')
        result = self.finish(first)['result']
        self.assertFalse(result['merged'])
        self.assertIn('base or the head moved', result['local_tests']['error'])
        self.assertEqual(self.notes(first).count('pushed head'), 1)
        self.assertEqual(self.calls('first', ['pr', 'merge']), [])
