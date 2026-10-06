"""Relay admission and protocol fixtures never contact a Mac or use private configuration."""
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from altitude import platform
from scripts import setup_validation_relay as setup, validation_relay as relay


CONFIG = {'daemon_uid': 1001, 'daemon_cgroup': '/user.slice/daemon.service',
          'daemon_executable': '/usr/bin/python3.12', 'endpoint': 'fixture.invalid',
          'account': 'validation', 'identity_file': '/etc/fixture/key',
          'known_hosts': '/etc/fixture/known_hosts'}
REQUEST = {'operation': 'submit', 'run_id': 'a' * 32, 'argv': ['make', 'check'],
           'payload': {'commit': 'b' * 40, 'tree': 'c' * 40, 'digest': 'd' * 64, 'data': ''},
           'duration': 3600}


class TestValidationRelay(unittest.TestCase):
    def forward(self, request=REQUEST):
        with tempfile.TemporaryFile() as output:
            relay.forward(CONFIG, request, output)
            output.seek(0)
            return platform.validation_frame_read(output)

    def test_bounded_framing_round_trip_and_invalid_frames(self):
        encoded = platform.validation_frame_encode(REQUEST)
        self.assertEqual(platform.validation_frame_read(io.BytesIO(encoded)), REQUEST)
        for data in (b'', encoded[:7], encoded[:-1], struct.pack('!Q', 0),
                     struct.pack('!Q', platform.VALIDATION_FRAME_LIMIT + 1),
                     struct.pack('!Q', 2) + b'[]', struct.pack('!Q', 1) + b'{'):
            with self.subTest(data=data[:10]), self.assertRaises(ValueError):
                platform.validation_frame_read(io.BytesIO(data))
        with mock.patch.object(platform, 'VALIDATION_FRAME_LIMIT', 10), self.assertRaises(ValueError):
            platform.validation_frame_encode(REQUEST)

    def test_raw_forward_checks_complete_bounds_before_sending(self):
        frame = platform.validation_frame_encode({'evidence': 'x' * (3 << 20)})
        chunks = []
        platform.validation_frame_forward(io.BytesIO(frame), chunks.append)
        self.assertEqual(b''.join(chunks), frame)
        self.assertTrue(all(len(chunk) <= 1 << 20 for chunk in chunks))
        for invalid in (b'', frame[:7], frame[:-1], frame + b'x', struct.pack('!Q', 0),
                        struct.pack('!Q', platform.VALIDATION_FRAME_LIMIT + 1)):
            sent = []
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                platform.validation_frame_forward(io.BytesIO(invalid), sent.append)
            self.assertEqual(sent, [])

    def test_raw_result_does_not_decode_or_reencode_json(self):
        # Formatting survives unchanged; the daemon owns JSON/schema validation.
        body = b'{ "evidence" : "fixture", "status": "finished" }'
        raw = struct.pack('!Q', len(body)) + body
        def run(_argv, **kwargs):
            kwargs['stdout'].write(raw)
            return SimpleNamespace(returncode=0)
        with tempfile.TemporaryFile() as output, \
                mock.patch.object(relay.subprocess, 'run', side_effect=run), \
                mock.patch.object(platform, 'validation_frame_read', side_effect=AssertionError('JSON decoded')):
            relay.forward(CONFIG, {'operation': 'result', 'run_id': REQUEST['run_id']}, output)
            output.seek(0)
            self.assertEqual(output.read(), raw)

    def test_no_endpoint_path_or_host_command_in_protocol(self):
        relay.validate_request(REQUEST)
        for field in ('endpoint', 'identity_file', 'ssh_options', 'command', 'path'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                relay.validate_request({**REQUEST, field: 'arbitrary'})
        for change in ({'operation': 'shell'}, {'run_id': '../escape'}, {'argv': ['echo\x00bad']},
                       {'payload': 'not an envelope'}, {'duration': True}, {'duration': 120}, {'duration': 3601}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                relay.validate_request({**REQUEST, **change})
        for operation in ('status', 'cancel', 'result'):
            relay.validate_request({'operation': operation, 'run_id': REQUEST['run_id']})
        relay.validate_request({'operation': 'result', 'run_id': REQUEST['run_id'], 'received': 'e' * 64})
        with self.assertRaises(ValueError):
            relay.validate_request({'operation': 'result', 'run_id': REQUEST['run_id'], 'received': '../x'})

    def test_ssh_uses_only_fixed_endpoint_and_forced_command(self):
        command = relay.ssh_command(CONFIG)
        self.assertEqual(command[-3:], ['--', CONFIG['endpoint'], 'altitude-validation-v1'])
        for option in ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'IdentitiesOnly=yes',
                       'IdentityAgent=none', 'ForwardAgent=no', 'ForwardX11=no',
                       'ClearAllForwardings=yes', 'PermitLocalCommand=no', 'ConnectTimeout=15'):
            self.assertIn(option, command)
        self.assertEqual(command[1:3], ['-F', '/dev/null'])
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            request = platform.validation_frame_read(io.BytesIO(kwargs['input']))
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            if len(calls) == 1:
                self.assertEqual(request, {'operation': 'status', 'run_id': REQUEST['run_id']})
                self.assertEqual(kwargs['timeout'], 15)
                response = {'status': 'absent', 'run_id': REQUEST['run_id']}
            else:
                self.assertEqual(request, REQUEST)
                self.assertEqual(kwargs['timeout'], 120)
                response = {'status': 'running'}
            kwargs['stdout'].write(platform.validation_frame_encode(response))
            return SimpleNamespace(returncode=0)

        with mock.patch.object(relay.subprocess, 'run', side_effect=run):
            self.assertEqual(self.forward(), {'status': 'running'})
        self.assertEqual([call[0] for call in calls], [command, command])

    def test_unreachable_probe_proves_no_submit_and_sanitizes_private_details(self):
        for fault in (OSError('secret endpoint and key path'), subprocess.TimeoutExpired('secret', 120)):
            with mock.patch.object(relay.subprocess, 'run', side_effect=fault) as run:
                response = self.forward()
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs['timeout'], 15)
            self.assertEqual(platform.validation_frame_read(io.BytesIO(run.call_args.kwargs['input'])),
                             {'operation': 'status', 'run_id': REQUEST['run_id']})
            self.assertEqual(response['status'], 'unavailable')
            self.assertNotIn('secret', json.dumps(response))
            self.assertFalse(response['accepted'])

    def test_failure_after_reachability_preserves_uncertain_admission(self):
        calls = []
        def run(_argv, **kwargs):
            calls.append(platform.validation_frame_read(io.BytesIO(kwargs['input'])))
            if len(calls) == 2:
                raise subprocess.TimeoutExpired('secret', 120)
            kwargs['stdout'].write(platform.validation_frame_encode(
                {'status': 'absent', 'run_id': REQUEST['run_id']}))
            return SimpleNamespace(returncode=0)
        with mock.patch.object(relay.subprocess, 'run', side_effect=run):
            response = self.forward()
        self.assertEqual(calls, [{'operation': 'status', 'run_id': REQUEST['run_id']}, REQUEST])
        self.assertNotIn('secret', json.dumps(response))
        self.assertNotIn('accepted', response)

    def test_existing_identity_and_tombstone_are_not_resubmitted(self):
        for status in ('running', 'finished', 'cancelled'):
            response = {'run_id': REQUEST['run_id'], 'status': status}
            def run(_argv, **kwargs):
                kwargs['stdout'].write(platform.validation_frame_encode(response))
                return SimpleNamespace(returncode=0)
            with mock.patch.object(relay.subprocess, 'run', side_effect=run) as call:
                self.assertEqual(self.forward(), response)
            call.assert_called_once()

    def test_invalid_probe_identity_or_reply_never_sends_payload(self):
        for response in ({'run_id': 'b' * 32, 'status': 'absent'}, {'status': 'absent'},
                         {'run_id': REQUEST['run_id'], 'status': 'unexpected'}):
            def run(_argv, **kwargs):
                kwargs['stdout'].write(platform.validation_frame_encode(response))
                return SimpleNamespace(returncode=0)
            with mock.patch.object(relay.subprocess, 'run', side_effect=run) as call:
                self.assertFalse(self.forward()['accepted'])
            call.assert_called_once()

    def test_status_failure_does_not_claim_unaccepted(self):
        with mock.patch.object(relay.subprocess, 'run', side_effect=OSError('secret')):
            response = self.forward({'operation': 'status', 'run_id': REQUEST['run_id']})
            self.assertNotIn('accepted', response)

    def test_nonzero_invalid_or_trailing_remote_response_is_unavailable(self):
        for output, code in ((b'', 255), (b'private stderr', 0),
                             (platform.validation_frame_encode({'status': 'running'}) + b'x', 0)):
            def run(_argv, **kwargs):
                kwargs['stdout'].write(output)
                return SimpleNamespace(returncode=code)
            with mock.patch.object(relay.subprocess, 'run', side_effect=run):
                result = self.forward({'operation': 'result', 'run_id': REQUEST['run_id']})
            self.assertEqual(result['status'], 'unavailable')
            self.assertNotIn('private', json.dumps(result))

    def test_config_rejects_ssh_injection_and_unknown_fields(self):
        relay.validate_config(CONFIG)
        for field, value in [('endpoint', '-oProxyCommand=bad'), ('endpoint', 'host;bad'),
                             ('account', 'user@host'), ('daemon_uid', 0), ('daemon_uid', True),
                             ('identity_file', 'relative'), ('daemon_cgroup', 'daemon')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                relay.validate_config({**CONFIG, field: value})
        with self.assertRaises(ValueError):
            relay.validate_config({**CONFIG, 'ssh': '/tmp/ssh'})

    def test_config_key_and_parent_ownership_permissions_are_enforced(self):
        leaf = Path('/etc/fixture/key')
        def info(path, *, uid=0, mode=None, gid=None):
            return SimpleNamespace(st_uid=uid, st_gid=os.getgid() if gid is None else gid,
                                   st_mode=mode if mode is not None else
                                   ((stat.S_IFREG | 0o640) if path == leaf else (stat.S_IFDIR | 0o755)))
        with mock.patch.object(Path, 'lstat', autospec=True, side_effect=lambda path: info(path)):
            relay.protected_path(leaf, private=True)
        for bad in (info(leaf, uid=1001), info(leaf, mode=stat.S_IFLNK | 0o777),
                    info(leaf, mode=stat.S_IFREG | 0o644), info(leaf, mode=stat.S_IFREG | 0o660),
                    info(leaf, gid=os.getgid() + 1)):
            with mock.patch.object(Path, 'lstat', autospec=True,
                                   side_effect=lambda path: bad if path == leaf else info(path)), self.assertRaises(ValueError):
                relay.protected_path(leaf, private=True)
        with mock.patch.object(Path, 'lstat', autospec=True,
                               side_effect=lambda path: info(path, uid=1001) if path == leaf.parent else info(path)), self.assertRaises(ValueError):
            relay.protected_path(leaf, private=True)

    def test_socket_refuses_unverified_peer_without_reading_or_forwarding_request(self):
        server, client = socket.socketpair()
        with server, client, mock.patch.object(platform, 'validation_relay_peer', return_value=False), \
                mock.patch.object(relay, 'forward') as forward:
            relay.handle(server, CONFIG)
            with client.makefile('rb') as stream:
                response = platform.validation_frame_read(stream)
            self.assertFalse(response['accepted'])
            self.assertEqual(response['status'], 'unavailable')
            forward.assert_not_called()

    def test_fixed_socket_missing_proves_no_submission(self):
        with mock.patch.object(platform.sys, 'platform', 'linux'), \
                mock.patch.object(platform.socket.socket, 'connect', side_effect=FileNotFoundError('private path')):
            response = platform.validation_remote_request(REQUEST)
        self.assertFalse(response['accepted'])
        self.assertNotIn('private path', json.dumps(response))

    def test_socket_failure_after_send_preserves_uncertain_admission(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.sendall.side_effect = BrokenPipeError('private')
        with mock.patch.object(platform.sys, 'platform', 'linux'), \
                mock.patch.object(platform.socket, 'socket', return_value=connection):
            result = platform.validation_remote_request(REQUEST)
        self.assertNotIn('accepted', result)
        connection.connect.assert_called_once_with('/run/altitude-validation-relay/control.sock')

    def test_working_relay_reports_asleep_mac_without_sending_submit(self):
        server, client = socket.socketpair()
        with server, client, mock.patch.object(platform, 'validation_relay_peer', return_value=True), \
                mock.patch.object(relay.subprocess, 'run', side_effect=subprocess.TimeoutExpired('private', 15)) as run:
            client.sendall(platform.validation_frame_encode(REQUEST))
            relay.handle(server, CONFIG)
            with client.makefile('rb') as stream:
                response = platform.validation_frame_read(stream)
            self.assertEqual(response['status'], 'unavailable')
            self.assertFalse(response['accepted'])
            run.assert_called_once()
            self.assertEqual(platform.validation_frame_read(io.BytesIO(run.call_args.kwargs['input']))['operation'], 'status')

    def test_disconnected_daemon_after_forward_never_gets_false_nonadmission(self):
        connection = mock.MagicMock()
        connection.makefile.return_value = io.BytesIO(platform.validation_frame_encode(REQUEST))
        connection.sendall.side_effect = BrokenPipeError('private')
        def forwarded(_config, _request, output):
            output.write(platform.validation_frame_encode({'status': 'running'}))
        with mock.patch.object(platform, 'validation_relay_peer', return_value=True), \
                mock.patch.object(relay, 'forward', side_effect=forwarded), self.assertRaises(BrokenPipeError):
            relay.handle(connection, CONFIG)
        connection.sendall.assert_called_once()
        sent = platform.validation_frame_read(io.BytesIO(connection.sendall.call_args.args[0]))
        self.assertNotIn('accepted', sent)

    def test_default_setup_is_read_only(self):
        with mock.patch.object(setup.sys, 'argv', ['setup_validation_relay.py']), \
                mock.patch.object(setup, 'install') as install, mock.patch('builtins.print'):
            self.assertEqual(setup.main(), 0)
        install.assert_not_called()
        self.assertIn('CapabilityBoundingSet=\n', setup.SERVICE)
        self.assertNotIn('CAP_SYS_PTRACE', setup.SERVICE)


if __name__ == '__main__':
    unittest.main()
