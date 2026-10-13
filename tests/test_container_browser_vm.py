"""Trusted guest accounting and lifetime cleanup; no native guest or browser."""
import json
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase
from scripts import container_browser_vm as controller


class ContainerBrowserControllerTests(AltitudeCase):
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
        machine.halt.assert_called_once_with()
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
