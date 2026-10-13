"""Guest fixture protocol and teardown, without guests, providers or browsers."""
import hashlib
import json
import socket
import struct
import subprocess
import threading
from unittest import mock

from tests.support import AltitudeCase
from scripts import container_browser_guest as guest


class ContainerBrowserGuestTests(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.protocol = guest.Protocol(lambda action, value: self.calls.append((action, value)) or {'done': True})

    def message(self, action, **value):
        return self.protocol.call({'version': 1, 'action': action, **value})

    def test_rejects_invalid_messages_before_external_effects(self):
        requests = [None, [], {'version': True, 'action': 'prepare'}, {'version': 2, 'action': 'prepare'},
                    {'version': 1, 'action': 'exec'}, {'version': 1, 'action': 'prepare', 'path': '/unrelated'},
                    {'version': 1, 'action': 'start', 'instance': 'other'},
                    {'version': 1, 'action': 'start', 'instance': 'browser-phone'},
                    {'version': 1, 'action': 'control', 'instance': 'browser-phone', 'values': {'installed': 1}},
                    {'version': 1, 'action': 'control', 'instance': 'browser-phone', 'values': {'command': True}}]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.protocol.call(request)
        self.assertEqual(self.calls, [])

    def test_viewport_order_and_replacement_continue_are_serial(self):
        self.message('prepare')
        with self.assertRaisesRegex(ValueError, 'viewport'):
            self.message('start', instance='browser-desktop')
        self.message('start', instance='browser-phone')
        self.message('pair', instance='browser-phone')
        self.message('control', instance='browser-phone', values={'installed': True})
        with self.assertRaisesRegex(ValueError, 'Replace'):
            self.message('continue', instance='browser-phone')
        self.message('replace', instance='browser-phone')
        with self.assertRaisesRegex(ValueError, 'Continue'):
            self.message('restart', instance='browser-phone')
        self.message('continue', instance='browser-phone')
        self.message('stop', instance='browser-phone')
        self.message('start', instance='browser-desktop')
        self.message('cleanup')
        with self.assertRaisesRegex(ValueError, 'cleaned'):
            self.message('prepare')

    def exchange(self, data):
        server, client = socket.socketpair()
        thread = threading.Thread(target=guest.framed_request, args=(server, self.protocol))
        thread.start()
        try:
            client.sendall(data)
            client.shutdown(socket.SHUT_WR)
            size = struct.unpack('!I', guest.receive(client, 4))[0]
            return json.loads(guest.receive(client, size))
        finally:
            thread.join(timeout=2)
            client.close()
            server.close()
            self.assertFalse(thread.is_alive())

    def test_node_control_transport_uses_the_framed_unix_contract(self):
        path = self.tmp / 'control.sock'
        server = guest.ControlServer(path, self.protocol)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(['node', str(guest.SOURCE / 'scripts/container_browser_control.mjs'), str(path),
                json.dumps({'version': 1, 'action': 'prepare'})], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'done': True})
            self.assertEqual(len(self.calls), 1)
            refused = subprocess.run(['node', str(guest.SOURCE / 'scripts/container_browser_control.mjs'), str(path),
                json.dumps({'version': 1, 'action': 'exec'})], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(len(self.calls), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_framing_refuses_oversized_truncated_and_nonjson_input(self):
        for data in [struct.pack('!I', guest.MAX_FRAME + 1), struct.pack('!I', 4) + b'xx', struct.pack('!I', 1) + b'!']:
            with self.subTest(data=data):
                self.assertFalse(self.exchange(data)['ok'])
        self.assertEqual(self.calls, [])
        body = json.dumps({'version': 1, 'action': 'prepare'}).encode()
        self.assertTrue(self.exchange(struct.pack('!I', len(body)) + body)['ok'])

    def test_launch_makes_only_fixed_isolated_mounts_and_network(self):
        self.patch(guest, 'INPUT', self.tmp)
        (self.tmp / 'validation-identity.json').write_text(json.dumps({'browser_image': 'sha256:fictional-image'}))
        args = guest.launch_arguments(['node', 'scripts/browser_sandbox.mjs', '/results/preflight.json'])
        self.assertIn('sha256:fictional-image', args)
        self.assertNotIn('localhost/altitude-browser', args)
        self.assertIn('--network=none', args)
        self.assertIn('--userns=keep-id', args)
        self.assertIn('--cgroups=disabled', args)
        mounts = [args[i + 1] for i, value in enumerate(args) if value == '--volume']
        self.assertEqual(len(mounts), 3)
        self.assertTrue(mounts[0].endswith('/candidate:ro'))
        self.assertFalse(any('/run/podman' in value or '.ssh' in value for value in args))

    def test_cleanup_failure_preserves_fixture_for_guest_teardown(self):
        self.patch(guest, 'browser_command', return_value=subprocess.CompletedProcess([], 1, '', 'fixture refusal'))
        invoke = self.patch(guest.fixture, 'run')
        with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            guest.cleanup()
        invoke.assert_not_called()

    def test_offline_prepare_refuses_missing_base_without_building(self):
        fixture = guest.fixture
        root = self.tmp / 'runtime'
        root.mkdir()
        bases = self.tmp / 'bases.json'
        archive = self.tmp / 'archive.tar.gz'
        archive.write_bytes(b'fictional archive')
        bases.write_text(json.dumps({'old': {'id': '', 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}}))
        self.patch(fixture, 'ROOT', root)
        self.patch(fixture, 'BASES', bases)
        self.patch(fixture, 'ARCHIVE', archive)
        build = self.patch(fixture.container, 'build')
        with mock.patch.object(fixture.container_acceptance, 'environment', return_value=mock.MagicMock()):
            with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                fixture.run('prepare', {}, captured_bases=True)
        build.assert_not_called()


if __name__ == '__main__':
    import unittest
    unittest.main()
