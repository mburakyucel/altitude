"""Trusted guest accounting and lifetime cleanup; no native guest or browser."""
import json
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase
from scripts import container_browser_vm as controller
from scripts import container_vm


class ContainerBrowserControllerTests(AltitudeCase):
    def test_mac_refuses_outer_linux_browser_route_before_guest_or_tools(self):
        results = self.tmp / 'results'
        with mock.patch.object(container_vm.host, 'validation_in_container', return_value=False), \
                mock.patch.object(container_vm.vm, 'Machine') as guest:
            with self.assertRaisesRegex(RuntimeError, 'alt task validate --container-browser'):
                container_vm.run(results, self.tmp / 'cache', browser=True)
        guest.assert_not_called()
        self.assertFalse(results.exists())

    def test_combined_downloads_stop_before_limit_and_counter_reset_refuses(self):
        budget = controller.Budget(self.tmp)
        machine = mock.Mock()
        machine.ssh.return_value = SimpleNamespace(stdout='500000000')
        budget.host_bytes(100_000_000)
        budget.observe(machine)
        self.assertEqual(json.loads((self.tmp / 'download-samples.json').read_text())[-1],
                         {'host_bytes': 100_000_000, 'guest_bytes': 500_000_000})
        with self.assertRaisesRegex(RuntimeError, 'shutdown margin'):
            budget.host_bytes(300_000_000)
        machine.ssh.return_value = SimpleNamespace(stdout='1')
        with self.assertRaisesRegex(RuntimeError, 'counter reset'):
            budget.observe(machine)

    def test_failed_accounting_stops_guest(self):
        budget = controller.Budget(self.tmp)
        machine = mock.Mock()
        machine.ssh.side_effect = TimeoutError('counter unavailable')
        budget.watch(machine)
        machine.halt.assert_called_once_with(force=True)
        self.assertIn('counter unavailable', budget.error)

    def test_cleanup_does_not_signal_recycled_process(self):
        process = {'pid': 234, 'start': 'old'}
        (self.tmp / 'guest-owner.json').write_text(json.dumps({'process': process}))
        (self.tmp / 'guest').mkdir()
        with mock.patch.object(controller.platform, 'process_identity_live', return_value=False), \
                mock.patch.object(controller.os, 'kill') as kill:
            self.assertEqual(controller.cleanup(self.tmp, self.tmp),
                             {'vm_stopped': True, 'work_removed': True})
            kill.assert_not_called()
        self.assertFalse((self.tmp / 'guest').exists())

    def test_unstopped_guest_retains_disk(self):
        (self.tmp / 'guest-owner.json').write_text(json.dumps({'process': {'pid': 234}}))
        (self.tmp / 'guest').mkdir()
        with mock.patch.object(controller.platform, 'process_identity_live', return_value=True), \
                mock.patch.object(controller.os, 'kill'), \
                mock.patch.object(controller.time, 'monotonic', side_effect=[0, 6, 6, 12]):
            with self.assertRaisesRegex(RuntimeError, 'remains alive'):
                controller.cleanup(self.tmp, self.tmp)
        self.assertTrue((self.tmp / 'guest').exists())

    def test_missing_startup_receipt_retains_disk_and_refuses_false_cleanup(self):
        (self.tmp / 'guest').mkdir()
        (self.tmp / 'guest/disk.img').touch()
        with self.assertRaisesRegex(RuntimeError, 'no process ownership receipt'):
            controller.cleanup(self.tmp, self.tmp)
        self.assertTrue((self.tmp / 'guest/disk.img').exists())

    def test_provisioning_denies_public_host_and_vpn_addresses_and_ipv6(self):
        machine = mock.Mock()
        controller.network_filter(machine, 8890, ['100.80.0.5', '198.51.100.3', '2001:db8::3'])
        rules = machine.ssh.call_args_list[0].args[0]
        for rule in ('100.64.0.0/10', '100.80.0.5', '198.51.100.3', 'meta nfproto ipv6 reject'):
            self.assertIn(rule, rules)
        self.assertLess(rules.index('198.51.100.3'), rules.index('ip daddr 1.1.1.1 udp dport 53 accept'))
        self.assertLess(rules.index('meta nfproto ipv6 reject'), rules.index('ip daddr 1.1.1.1 udp dport 53 accept'))
        self.assertNotIn(' udp dport 53 accept', rules.replace('ip daddr 1.1.1.1 udp dport 53 accept', ''))

    def test_spawn_receipt_precedes_waiting_for_guest_start(self):
        machine = object.__new__(controller.vm.Machine)
        machine.work = machine.image = self.tmp
        machine.disk = self.tmp / 'disk.img'
        machine.socket = self.tmp / 'ssh.sock'
        machine.lock = mock.Mock()
        machine.lock.fileno.return_value = 20
        process = mock.Mock(pid=1234)
        seen = []
        def capture(pid):
            seen.append(pid)
            raise RuntimeError('receipt captured before event wait')
        with mock.patch.object(controller.vm.subprocess, 'Popen', return_value=process), \
                mock.patch.object(controller.vm.macos, 'helper', return_value=self.tmp / 'helper'):
            with self.assertRaisesRegex(RuntimeError, 'receipt captured'):
                machine.start(on_spawn=capture)
        machine.log.close()
        self.assertEqual(seen, [1234])

    def test_host_rejects_weakened_or_missing_renderer_evidence(self):
        identity = {'run_id': 'fictional-run', 'browser_image': 'fictional-image'}
        sandbox = {'passed': True, 'options': {'chromiumSandbox': True}, 'version': '123',
                   'executable_sha256': 'a' * 64, 'sandbox': 'You are adequately sandboxed.',
                   'renderers': [{'uid': '1000', 'seccomp': '2', 'own_user_namespace': True,
                                  'own_pid_namespace': True}]}
        network = {'passed': True, 'attempts': {n: {'refused': True}
                   for n in ('gateway', 'internet')}}
        container = {'Image': 'fictional-image', 'Config': {'User': '1000:1000'},
                     'HostConfig': {'NetworkMode': 'none'},
                     'Mounts': [{'Destination': n, 'RW': n != '/candidate'}
                                for n in ('/candidate', '/results', '/sockets')]}
        for name, value in [('protection', {'passed': True, 'identity': identity}),
                            ('browser-sandbox', sandbox), ('network-controls', network),
                            ('network-container', [container]), ('sandbox-container', [container])]:
            (self.tmp / (name + '.json')).write_text(json.dumps(value))
        self.assertTrue(controller.verify_protection(self.tmp, identity)['passed'])
        for key, value in [('chromiumSandbox', False), ('chromiumSandbox', None)]:
            sandbox['options'][key] = value
            (self.tmp / 'browser-sandbox.json').write_text(json.dumps(sandbox))
            with self.assertRaisesRegex(RuntimeError, 'Chromium protection'):
                controller.verify_protection(self.tmp, identity)
        sandbox['options']['chromiumSandbox'] = True
        sandbox['renderers'][0].pop('uid')
        (self.tmp / 'browser-sandbox.json').write_text(json.dumps(sandbox))
        with self.assertRaisesRegex(RuntimeError, 'Chromium protection'):
            controller.verify_protection(self.tmp, identity)
