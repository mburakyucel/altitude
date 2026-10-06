"""Real private-file transactions; account and service-manager effects are deterministic fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import setup_validation_relay as setup
from tests.test_validation_relay import CONFIG


class TestRelaySetup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination, self.private = self.root / 'installed', self.root / 'private'
        self.units = self.root / 'units'
        self.units.mkdir()
        self.runtime = (self.root / 'relay-run', self.root / 'attester-run')
        self.source_key, self.source_hosts = self.root / 'source-key', self.root / 'source-hosts'
        self.source_key.write_bytes(b'fictional-key-one')
        self.source_hosts.write_bytes(b'fictional-host-pin')
        self.config = self.root / 'input.json'
        self.input = {**CONFIG, 'identity_file': str(self.source_key), 'known_hosts': str(self.source_hosts)}
        self.config.write_text(json.dumps(self.input))
        for path in (self.source_key, self.source_hosts, self.config):
            path.chmod(0o600)
        self.account = SimpleNamespace(pw_uid=401, pw_gid=401, pw_name=setup.ACCOUNT, pw_shell='/usr/sbin/nologin')
        self.daemon = SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_name='fixture-operator')
        self.account_exists = self.group_exists = False
        self.commands, self.fail = [], None

        def get_account(name):
            if not self.account_exists:
                raise KeyError(name)
            return self.account
        def get_group(name):
            if not self.group_exists:
                raise KeyError(name)
            return SimpleNamespace(gr_name=setup.ACCOUNT)
        def command(arguments):
            self.commands.append(arguments)
            if self.fail and self.fail(arguments):
                raise subprocess.CalledProcessError(1, ['fixture'])
            if arguments[0].endswith('/useradd'):
                self.account_exists = self.group_exists = True
            elif arguments[0].endswith('/userdel'):
                self.account_exists = False
            elif arguments[0].endswith('/groupdel'):
                self.group_exists = False
        for patch in (mock.patch.object(setup, 'DESTINATION', self.destination),
                      mock.patch.object(setup, 'PRIVATE', self.private), mock.patch.object(setup, 'UNITS', self.units),
                      mock.patch.object(setup, 'RUNTIME', self.runtime), mock.patch.object(setup, 'root_directory'),
                      mock.patch.object(setup.relay, 'protected_path'),
                      mock.patch.object(setup.relay.platform, 'validation_system_bus_identity', return_value=102),
                      mock.patch.object(setup.os, 'getuid', return_value=0),
                      mock.patch.object(setup.os, 'chown'), mock.patch.object(setup.os, 'fchown'),
                      mock.patch.object(setup.os, 'getgrouplist', return_value=[1001]),
                      mock.patch.object(setup.pwd, 'getpwnam', side_effect=get_account),
                      mock.patch.object(setup.pwd, 'getpwuid', return_value=self.daemon),
                      mock.patch.object(setup.grp, 'getgrnam', side_effect=get_group),
                      mock.patch.object(setup.grp, 'getgrgid', side_effect=lambda gid: SimpleNamespace(gr_name='fixture', gr_mem=[])),
                      mock.patch.object(setup, 'command', side_effect=command)):
            patch.start()
            self.addCleanup(patch.stop)

    def assert_removed(self):
        self.assertFalse(self.account_exists)
        self.assertFalse(self.group_exists)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.private.exists())
        self.assertTrue(all(not path.exists() for path in self.runtime))
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertTrue(self.source_key.exists())

    def test_initial_install_consumes_root_private_source_only_after_activation(self):
        setup.install(self.config)
        self.assertFalse(self.source_key.exists())
        self.assertEqual((self.private / 'identity_file').read_bytes(), b'fictional-key-one')
        self.assertEqual((self.private / 'identity_file').stat().st_mode & 0o777, 0o640)
        self.assertEqual(set(p.name for p in self.units.iterdir()), set(setup.UNIT_NAMES))
        self.assertTrue((self.destination / 'scripts/__init__.py').exists())
        self.assertFalse(any('try-restart' in argv for argv in self.commands))

    def test_initial_setup_rejects_existing_state_without_touching_it(self):
        self.destination.mkdir()
        sentinel = self.destination / 'unrelated'
        sentinel.write_text('keep')
        with self.assertRaises(ValueError):
            setup.install(self.config)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertEqual(self.commands, [])
        self.assertTrue(self.source_key.exists())

    def test_worker_readable_source_is_refused_before_mutation(self):
        self.source_key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'root-only'):
            setup.install(self.config)
        self.assertEqual(self.commands, [])

    def test_partial_activation_rolls_back_files_units_account_and_group(self):
        self.fail = lambda arguments: 'enable' in arguments
        with self.assertRaises(subprocess.CalledProcessError):
            setup.install(self.config)
        self.assert_removed()
        self.assertTrue(any('disable' in argv and '--now' in argv for argv in self.commands))

    def test_incomplete_rollback_remains_explicit(self):
        self.fail = lambda arguments: 'enable' in arguments or 'disable' in arguments
        with self.assertRaisesRegex(RuntimeError, 'rollback is incomplete'):
            setup.install(self.config)
        self.assertTrue(self.source_key.exists())

    def prepare_rotation(self):
        setup.install(self.config)
        self.source_key.write_bytes(b'fictional-key-two')
        self.source_key.chmod(0o600)
        self.commands.clear()
        # Fixture files retain this test user's ownership; real installed-file checks remain separately covered.
        patch = mock.patch.object(setup, 'installed_file')
        patch.start()
        self.addCleanup(patch.stop)
        return (self.private / 'config.json').read_bytes()

    def test_rotation_verifies_before_switch_and_retains_old_key_until_explicit_retirement(self):
        old_config = self.prepare_rotation()
        old_key = self.private / 'identity_file'
        observations = []
        def probe(candidate, account):
            observations.append(json.loads((self.private / 'config.json').read_text())['identity_file'])
            self.assertTrue(old_key.exists())
            self.assertEqual(Path(candidate['identity_file']).read_bytes(), b'fictional-key-two')
        with mock.patch.object(setup, 'probe', side_effect=probe):
            setup.rotate(self.config)
        new = json.loads((self.private / 'config.json').read_text())
        self.assertEqual(observations, [str(old_key), new['identity_file']])
        self.assertEqual((self.private / 'config.previous.json').read_bytes(), old_config)
        self.assertTrue(old_key.exists())
        self.assertFalse(self.source_key.exists())
        with mock.patch.object(setup, 'probe') as verification:
            setup.retire_old()
        verification.assert_called_once()
        self.assertFalse(old_key.exists())
        self.assertFalse((self.private / 'config.previous.json').exists())
        self.assertTrue(Path(new['identity_file']).exists())

    def test_failed_replacement_probe_never_switches_or_removes_old_identity(self):
        old_config = self.prepare_rotation()
        with mock.patch.object(setup, 'probe', side_effect=ValueError('unavailable')), self.assertRaises(ValueError):
            setup.rotate(self.config)
        self.assertEqual((self.private / 'config.json').read_bytes(), old_config)
        self.assertEqual(list(self.private.glob('identity_file-*')), [])
        self.assertTrue(self.source_key.exists())
        self.assertEqual(self.commands, [])

    def test_failure_after_switch_restores_configuration_and_keeps_source(self):
        old_config = self.prepare_rotation()
        with mock.patch.object(setup, 'probe', side_effect=[None, ValueError('post-switch failure')]), self.assertRaises(ValueError):
            setup.rotate(self.config)
        self.assertEqual((self.private / 'config.json').read_bytes(), old_config)
        self.assertEqual(sum('try-restart' in argv for argv in self.commands), 2)
        self.assertFalse((self.private / 'config.previous.json').exists())
        self.assertEqual(list(self.private.glob('identity_file-*')), [])
        self.assertTrue(self.source_key.exists())

    def test_second_rotation_refuses_pending_retirement(self):
        self.prepare_rotation()
        with mock.patch.object(setup, 'probe'):
            setup.rotate(self.config)
        self.source_key.write_bytes(b'fictional-key-three')
        self.source_key.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'retirement'):
            setup.rotate(self.config)

    def test_rotation_cannot_change_the_destination_or_host_pin(self):
        old_config = self.prepare_rotation()
        self.input['endpoint'] = 'different.invalid'
        self.config.write_text(json.dumps(self.input))
        with self.assertRaisesRegex(ValueError, 'only the client key'):
            setup.rotate(self.config)
        self.assertEqual((self.private / 'config.json').read_bytes(), old_config)

    def test_probe_uses_only_fixed_status_operation_as_credential_uid(self):
        def run(argv, **kwargs):
            request = setup.relay.platform.validation_frame_read(__import__('io').BytesIO(kwargs['input']))
            self.assertEqual(set(request), {'operation', 'run_id'})
            self.assertEqual(request['operation'], 'status')
            self.assertEqual(kwargs['user'], self.account.pw_uid)
            self.assertEqual(kwargs['group'], self.account.pw_gid)
            self.assertEqual(kwargs['extra_groups'], ())
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            self.assertEqual(argv[-1], 'altitude-validation-v1')
            kwargs['stdout'].write(setup.relay.platform.validation_frame_encode({'run_id': request['run_id'], 'status': 'absent'}))
            return SimpleNamespace(returncode=0)
        with mock.patch.object(setup.subprocess, 'run', side_effect=run):
            setup.probe(self.input, self.account)


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('systemd-analyze'), 'Linux static unit analyzer')
class TestVerifierUnitParsing(unittest.TestCase):
    def test_fresh_connection_template_units_parse_without_host_manager(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            units = root / 'etc/systemd/system'
            units.mkdir(parents=True)
            binary = root / 'bin/sh'
            binary.parent.mkdir()
            binary.write_text('#!/bin/sh\n')
            binary.chmod(0o755)
            for target in ('sysinit.target', 'sockets.target', 'basic.target', 'shutdown.target'):
                (units / target).write_text('[Unit]\nDescription=Fixture target\nDefaultDependencies=no\n')
            content = (setup.SERVICE, setup.socket_unit('root'), setup.attester_service(1001), setup.ATTESTER_SOCKET)
            for name, text in zip(setup.UNIT_NAMES, content):
                (units / name).write_text(text)
            result = subprocess.run(['systemd-analyze', '--root', temporary, '--man=no', 'verify',
                                     'altitude-validation-relay.service', 'altitude-validation-relay.socket',
                                     'altitude-validation-attester@fixture.service', 'altitude-validation-attester.socket'],
                                    capture_output=True, text=True, timeout=30,
                                    env={**{key: os.environ[key] for key in ('PATH', 'LD_LIBRARY_PATH')
                                            if key in os.environ}, 'SYSTEMD_LOG_LEVEL': 'warning'})
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
