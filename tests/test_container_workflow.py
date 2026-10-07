"""Run the native gate's workflow offline with real fixture CLI processes.

The platform adapter alone replaces systemd; this suite never invokes host services.
"""
import os
import signal
from unittest import mock

from tests.support import AltitudeCase
from tests import container_workflow_probe as probe
from altitude import config, dispatch, engines, platform, state as S


class ContainerWorkflow(AltitudeCase):
    def setUp(self):
        super().setUp()
        # The test simulates container identity in an ordinary worker; keep its
        # real broker sockets in the disposable fixture's runtime directory.
        self.patch(platform, 'coordinator_socket_directory', return_value=self.tmp/'runtime-brokers')
        self.private_ledgers()
        self.units = {}
        self.patch(platform, "job_command", side_effect=self.command)
        self.patch(platform, "job_active", side_effect=self.active)
        self.patch(platform, "job_stop", side_effect=self.stop)
        self.addCleanup(self.cleanup_units)

    def command(self, unit, command, env, **kwargs):
        pidfile = self.tmp / (unit + ".pid")
        self.units[unit] = pidfile
        # Exec keeps the wrapper PID/session; the production Popen owns the process handle.
        wrapper = ("import os,sys; from pathlib import Path; "
                   f"Path({str(pidfile)!r}).write_text(str(os.getpid())); "
                   "os.execv(sys.argv[1], sys.argv[1:])")
        import sys
        return [sys.executable, "-c", wrapper, *command]

    def active(self, unit, env=None):
        path = self.units.get(unit)
        if not path or not path.exists():
            return False
        pid = int(path.read_text())
        for process in engines._codex_processes.values():
            if process.pid == pid:
                return process.poll() is None
        return False  # synchronous coordinator turns have already been reaped

    def stop(self, unit, env=None, **kwargs):
        path = self.units.get(unit)
        if path and path.exists() and self.active(unit):
            os.killpg(int(path.read_text()), signal.SIGTERM)
            for process in engines._codex_processes.values():
                if process.pid == int(path.read_text()):
                    process.wait(timeout=5)

    def cleanup_units(self):
        for unit in self.units:
            self.stop(unit)

    def test_registered_project_coordinator_task_and_resume_use_real_cli_protocol(self):
        repo = self.tmp / "workflow"
        with probe.fixture_engine(self.tmp):
            saved = probe.prepare(self.project, repo)
            result = probe.resume(self.project, saved)
        self.assertTrue(result["queued_message_delivered_once"])
        self.assertTrue(result["same_session_worktree_hold"])
        self.assertFalse(any(self.active(unit) for unit in self.units))

    def test_replacement_preserves_task_and_message_until_global_continue(self):
        self.patch(platform, "containerized", return_value=True)
        self.patch(platform, "CONTAINER_PROJECTS", self.tmp)
        self.patch(config, "HOME", self.tmp / "image-home")
        identity = self.patch(platform, "_container_instance", return_value="a" * 32)
        self.patch(platform, "container_ready")
        platform._lifecycle_write("a" * 32, False)
        with probe.fixture_engine(self.tmp):
            saved = probe.prepare(self.project, self.tmp / "workflow")
            identity.return_value = "b" * 32
            result = probe.resume(self.project, saved, paused=True)
        self.assertTrue(result["replacement_admission"])
        self.assertFalse(any(self.active(unit) for unit in self.units))

    def test_wrong_resume_identity_fails_the_probe_and_stops_the_replacement(self):
        with probe.fixture_engine(self.tmp):
            saved = probe.prepare(self.project, self.tmp / "workflow")
        wrong = probe.ENGINE.replace("sys.argv[-2] if 'resume' in sys.argv else str(uuid.uuid4())",
                                     "str(uuid.uuid4())")
        with mock.patch.object(probe, "ENGINE", wrong), probe.fixture_engine(self.tmp):
            with self.assertRaisesRegex(dispatch.ResumeFailure, "different .* thread"):
                probe.resume(self.project, saved)
        self.assertEqual(S.load_task(self.project, saved["slug"])["session_id"], saved["session_id"])
        self.assertFalse(any(self.active(unit) for unit in self.units))

    def interruption(self, *, launching):
        self.patch(platform, "containerized", return_value=True)
        self.patch(platform, "CONTAINER_PROJECTS", self.tmp)
        self.patch(config, "HOME", self.tmp / "image-home")
        identity = self.patch(platform, "_container_instance", return_value="a" * 32)
        self.patch(platform, "container_ready")
        platform._lifecycle_write("a" * 32, False)
        with probe.fixture_engine(self.tmp):
            saved = probe.prepare(self.project, self.tmp / "workflow")
            probe.claim_interruption(self.project, saved, launching=launching)
            identity.return_value = "b" * 32
            # Workspace simulation only. The native lane exits its real claim owner and
            # recreates the container before checking its actual process-lifetime identity.
            with mock.patch.object(platform, "process_identity_live", return_value=False):
                result = probe.recover_interruption(self.project, saved, launching=launching)
        self.assertFalse(any(self.active(unit) for unit in self.units))
        return result

    def test_prelaunch_interruption_restores_input_before_explicit_continuation(self):
        self.assertTrue(self.interruption(launching=False)["dead_prelaunch_claim_reconciled_while_paused"])

    def test_ambiguous_launch_retains_input_and_requires_recovery(self):
        self.assertTrue(self.interruption(launching=True)["uncertain_launch_faulted_without_replay"])
