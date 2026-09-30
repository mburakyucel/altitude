"""Controller ownership and readiness with real records, fictional services and no host commands."""
import json
import subprocess
import threading
from contextlib import nullcontext
from unittest import mock

from altitude import platform
from tests.support import AltitudeCase


class ContainerSupervisorTests(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.unit = platform.container_unit("fixture")
        self.parent = "/user.slice/user-1000.slice/user@1000.service/app.slice/" + self.unit
        self.value = {"Id": "a" * 64, "State": {"Running": True, "Pid": 42},
            "Config": {"Labels": {"io.altitude.container": "1", "io.altitude.unit": self.unit,
                                   "io.altitude.cgroup-parent": self.parent}},
            "HostConfig": {"CgroupManager": "cgroupfs", "CgroupParent": self.parent}}
        self.proc = self.tmp / "proc"
        self.groups = self.tmp / "cgroups"
        self.patch(platform, "PROC", self.proc)
        self.patch(platform, "CONTAINER_CGROUP_ROOT", self.groups)
        self.patch(platform, 'container_lineage_lock', side_effect=lambda _: nullcontext())
        self.patch(platform, 'container_copy_available')

    def test_stored_ownership_rejects_old_manager_and_parent_before_action(self):
        self.patch(platform, "container_command", side_effect=lambda *a, **k: json.dumps([self.value]))
        self.assertEqual(platform.container_owned("fixture")["Id"], "a" * 64)
        self.value["HostConfig"]["CgroupManager"] = "systemd"
        with self.assertRaisesRegex(RuntimeError, "not adopted"):
            platform.container_owned("fixture")
        self.value["HostConfig"]["CgroupManager"] = "cgroupfs"
        self.value["HostConfig"]["CgroupParent"] += "/unrelated"
        with self.assertRaisesRegex(RuntimeError, "not adopted"):
            platform.container_owned("fixture")

    def test_delegation_uses_available_controllers_not_already_enabled_children(self):
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "self/cgroup").write_text("0::" + self.parent + "/supervisor\n")
        group = self.groups / self.parent.lstrip("/")
        group.mkdir(parents=True)
        (group / "cgroup.controllers").write_text("cpu memory pids\n")
        (group / "cgroup.subtree_control").write_text("")
        self.assertEqual(platform.container_parent(self.unit), self.parent)
        (group / "cgroup.controllers").write_text("cpu pids\n")
        with self.assertRaisesRegex(RuntimeError, "has not delegated"):
            platform.container_parent(self.unit)
        (self.proc / "self/cgroup").write_text("0::/unrelated/supervisor\n")
        with self.assertRaisesRegex(RuntimeError, "delegated user service"):
            platform.container_parent(self.unit)

    def test_limits_cover_systemd_init_scope_and_reject_an_unbounded_container(self):
        membership = self.parent + "/libpod-" + self.value["Id"]
        (self.proc / "42").mkdir(parents=True)
        (self.proc / "42/cgroup").write_text("0::" + membership + "/init.scope\n")
        monitor = self.tmp/'conmon.pid'
        monitor.write_text('43\n')
        self.value['ConmonPidFile'] = str(monitor)
        (self.proc/'43').mkdir()
        (self.proc/'43/cgroup').write_text('0::'+self.parent+'/conmon\n')
        group = self.groups / membership.lstrip("/")
        group.mkdir(parents=True)
        for name, value in {"memory.max": str(4 * 1024**3), "pids.max": "1024", "cpu.max": "200000 100000"}.items():
            (group / name).write_text(value)
        platform.container_limits(self.value)
        (self.proc/'43/cgroup').write_text('0::/unrelated/conmon\n')
        with self.assertRaisesRegex(RuntimeError, 'monitor is outside'):
            platform.container_limits(self.value)
        (self.proc/'43/cgroup').write_text('0::'+self.parent+'/conmon\n')
        (group / "memory.max").write_text("max")
        with self.assertRaisesRegex(RuntimeError, "not enforced"):
            platform.container_limits(self.value)
        (self.proc / "42/cgroup").write_text("0::/unrelated/libpod-" + self.value["Id"] + "\n")
        with self.assertRaisesRegex(RuntimeError, "outside its owned"):
            platform.container_limits(self.value)

    def test_service_stop_signals_supervisor_first_and_never_enables_boot_start(self):
        environment = {"HOME": "/fictional", "PATH": "/usr/bin:/bin", "PROVIDER_SECRET": "not forwarded"}
        self.patch(platform, "container_user_environment", return_value=environment)
        run = self.patch(platform.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", ""))
        platform.container_job(self.unit, ["python3", "fixture"])
        arguments = run.call_args.args[0]
        self.assertIn("--user", arguments)
        self.assertIn("--property=KillMode=mixed", arguments)
        self.assertIn("--property=TimeoutStopSec=45", arguments)
        self.assertNotIn("--property=ExitType=cgroup", arguments)  # Main-process death must stop descendants.
        self.assertFalse(any("PROVIDER_SECRET" in value or "Restart=" in value for value in arguments))
        self.assertNotIn("enable", arguments)

    def test_termination_translates_to_exact_container_stop_and_reaps_attach(self):
        self.patch(platform, "container_parent", return_value=self.parent)
        self.patch(platform, "container_owned", return_value=self.value)
        self.patch(platform, "container_user_environment", return_value={})
        event = threading.Event()
        self.patch(platform.threading, "Event", return_value=event)
        child = mock.Mock()
        child.poll.side_effect = [None, 0]
        child.wait.return_value = 0
        def attach(*args, **kwargs):
            event.set()
            return child
        self.patch(platform.subprocess, "Popen", side_effect=attach)
        command = self.patch(platform, "container_command", return_value="")
        signal = self.patch(platform.signals, "signal", return_value="previous")
        platform._container_supervise("fixture", None)
        command.assert_called_once_with(["stop", "--time=30", self.value["Id"]], timeout=35)
        child.wait.assert_called_once_with(timeout=8)
        self.assertEqual(signal.call_count, 6)

    def test_restart_cannot_relocate_or_recreate_a_stopped_instance(self):
        self.patch(platform, "container_parent", return_value=self.parent + "-different")
        self.patch(platform, "container_owned", return_value=self.value)
        command = self.patch(platform, "container_command")
        attach = self.patch(platform.subprocess, "Popen")
        self.patch(platform.signals, "signal")
        with self.assertRaisesRegex(RuntimeError, "parent changed"):
            platform._container_supervise("fixture", None)
        command.assert_not_called()
        attach.assert_not_called()

    def test_stop_during_start_waits_for_running_before_sending_stop(self):
        self.patch(platform, 'container_parent', return_value=self.parent)
        self.patch(platform, 'container_user_environment', return_value={})
        self.patch(platform.signals, 'signal')
        event=threading.Event()
        self.patch(platform.threading, 'Event', return_value=event)
        child=mock.Mock()
        child.poll.return_value=None
        child.wait.return_value=0
        def start(*args,**kwargs):
            event.set()
            return child
        self.patch(platform.subprocess,'Popen',side_effect=start)
        calls=[]
        def observed(*args,**kwargs):
            calls.append('inspect')
            return {**self.value,'State':{'Running':len(calls)>=3}}
        self.patch(platform,'container_owned',side_effect=observed)
        def stop(*args,**kwargs):
            self.assertGreaterEqual(len(calls),3)
            return ''
        command=self.patch(platform,'container_command',side_effect=stop)
        self.patch(platform.time,'sleep')
        platform._container_supervise('fixture',None)
        command.assert_called_once_with(['stop','--time=30',self.value['Id']],timeout=35)

    def test_failed_attach_still_stops_the_exact_running_payload(self):
        self.patch(platform, "container_parent", return_value=self.parent)
        self.patch(platform, "container_owned", return_value=self.value)
        self.patch(platform, "container_user_environment", return_value={})
        self.patch(platform.signals, "signal")
        child = mock.Mock()
        child.poll.return_value = 125
        child.wait.return_value = 125
        self.patch(platform.subprocess, "Popen", return_value=child)
        command = self.patch(platform, "container_command", return_value="")
        with self.assertRaisesRegex(RuntimeError, "attached container exited"):
            platform._container_supervise("fixture", None)
        command.assert_called_once_with(["stop", "--time=30", self.value["Id"]], timeout=35)

    def test_stop_timeout_is_not_retried_inside_the_same_service_grace(self):
        self.patch(platform, "container_parent", return_value=self.parent)
        self.patch(platform, "container_owned", return_value=self.value)
        self.patch(platform, "container_user_environment", return_value={})
        self.patch(platform.signals, "signal")
        event = threading.Event()
        self.patch(platform.threading, "Event", return_value=event)
        child = mock.Mock()
        child.poll.return_value = None
        def attach(*args, **kwargs):
            event.set()
            return child
        self.patch(platform.subprocess, "Popen", side_effect=attach)
        command = self.patch(platform, "container_command", side_effect=subprocess.TimeoutExpired("stop", 35))
        with self.assertRaises(subprocess.TimeoutExpired):
            platform._container_supervise("fixture", None)
        self.assertEqual(command.call_count, 1)

    def test_collected_supervisor_is_not_reported_as_failed_stop(self):
        self.patch(platform, "container_job")
        self.patch(platform, "container_user_environment", return_value={})
        self.patch(platform, "job_active", return_value=False)
        command = self.patch(platform, "container_command")
        stop = self.patch(platform.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", ""))
        with self.assertRaisesRegex(RuntimeError, "before readiness"):
            platform.container_launch("fixture", ["--name", "fixture", "image"])
        stop.assert_not_called()
        command.assert_not_called()

    def test_readiness_error_survives_stop_timeout_with_recovery_hint(self):
        self.patch(platform, 'container_job')
        self.patch(platform, 'container_user_environment', return_value={})
        self.patch(platform, 'job_active', return_value=True)
        self.patch(platform, 'container_owned', return_value=self.value)
        self.patch(platform, 'container_limits', side_effect=RuntimeError('fixture readiness failed'))
        self.patch(platform.time, 'monotonic', side_effect=[0,0,91])
        self.patch(platform.time, 'sleep')
        stop = self.patch(platform.subprocess, 'run', side_effect=subprocess.TimeoutExpired('stop',105))
        with self.assertRaisesRegex(RuntimeError, 'fixture readiness failed.*cleanup not confirmed.*timed out'):
            platform.container_launch('fixture',['--name','fixture','image'])
        self.assertEqual(stop.call_args.args[0], ['systemctl','--user','stop',self.unit])
        self.assertEqual(stop.call_args.kwargs['timeout'],platform.CONTAINER_STOP_WAIT)

    def test_missing_supervisor_never_reports_running_container_stopped(self):
        self.patch(platform, "container_owned", return_value=self.value)
        self.patch(platform, "container_user_environment", return_value={})
        self.patch(platform, "job_active", return_value=False)
        run = self.patch(platform.subprocess, "run")
        with self.assertRaisesRegex(RuntimeError, "no active supervisor"):
            platform.container_stop("fixture")
        run.assert_not_called()

    def test_stop_post_reaps_exact_owned_runtime_even_when_process_state_is_stale(self):
        settled={**self.value,'State':{'Running':False,'Status':'exited','Pid':0}}
        self.patch(platform, "container_owned", side_effect=[self.value,settled])
        command = self.patch(platform, "container_command", side_effect=[json.dumps([
            {"Names": ["fixture"]}, {"Names": ["unrelated"]}]), "", ""])
        platform._container_after_stop("fixture")
        self.assertEqual(command.call_args_list[-2:], [
            mock.call(["stop", "--time=30", self.value["Id"]], timeout=35),
            mock.call(["container", "cleanup", self.value["Id"]], timeout=8)])

    def test_stopping_is_never_accepted_as_stopped_or_backup_ready(self):
        value={**self.value,'State':{'Running':False,'Status':'stopping','Pid':42}}
        self.assertFalse(platform.container_stopped(value))
        self.patch(platform,'container_owned',return_value=value)
        self.patch(platform,'container_command',side_effect=['[{"Names":["fixture"]}]','',''])
        with self.assertRaisesRegex(RuntimeError,'did not settle'):
            platform._container_after_stop('fixture')

    def test_recreation_requires_inactive_service_and_empty_owned_cgroup(self):
        self.patch(platform,'container_user_environment',return_value={})
        active=self.patch(platform,'job_active',return_value=True)
        with self.assertRaisesRegex(RuntimeError,'Stop the container'): platform.container_recreatable(self.value)
        active.return_value=False
        path=self.groups/self.parent.lstrip('/'); path.mkdir(parents=True)
        (path/'cgroup.events').write_text('populated 1\nfrozen 0\n')
        with self.assertRaisesRegex(RuntimeError,'processes survive'): platform.container_recreatable(self.value)
        (path/'cgroup.events').write_text('populated 0\nfrozen 0\n')
        platform.container_recreatable(self.value)

    def test_stop_post_does_not_adopt_unowned_or_partially_created_instances(self):
        command = self.patch(platform, "container_command", return_value='[{"Names":["unrelated"]}]')
        owned = self.patch(platform, "container_owned", side_effect=RuntimeError("not adopted"))
        platform._container_after_stop("fixture")
        owned.assert_not_called()
        command.return_value = '[{"Names":["fixture"]}]'
        with self.assertRaisesRegex(RuntimeError, "not adopted"):
            platform._container_after_stop("fixture")
        self.assertEqual(command.call_count, 2)
