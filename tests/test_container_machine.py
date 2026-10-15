"""The Mac launcher drives Altitude's own Podman machine: a fictional Podman client, real records and locks (#643)."""
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from altitude import platform
from scripts import container
from tests.support import AltitudeCase

IDENT = "c" * 64
SCOPE = f"/sys/fs/cgroup/user.slice/user-501.slice/user@501.service/user.slice/libpod-{IDENT}.scope"
#: Host mechanisms the Mac route must never call; Podman in the machine owns the container.
SERVICE_MANAGERS = ("systemd-run", "systemctl", "launchctl")


class MachineCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(platform.sys, "platform", "darwin")
        self.patch(platform, "require_supported")
        self.patch(platform.host_platform, "machine", return_value="arm64")
        self.patch(platform, "_machine_verified", None)
        self.enterContext(mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")}))
        for key in ("CONTAINER_HOST", "CONTAINER_CONNECTION"):
            self.enterContext(mock.patch.dict(os.environ)).pop(key, None)
        self.version = "podman version 6.1.4"
        self.machine = {"Name": "altitude", "State": "running", "Rootful": False, "Created": "2026-10-15T09:00:00Z",
                        "Resources": {"CPUs": 4, "Memory": 6144, "DiskSize": 40},
                        "SSHConfig": {"Port": 50111, "RemoteUsername": "core", "IdentityPath": "/fictional/key"}}
        self.connections = [
            {"Name": "altitude", "URI": "ssh://core@127.0.0.1:50111/run/user/501/podman/podman.sock",
             "Identity": "/fictional/key"},
            {"Name": "altitude-root", "URI": "ssh://root@127.0.0.1:50111/run/podman/podman.sock",
             "Identity": "/fictional/key"}]
        self.info = {"host": {"arch": "arm64", "cgroupVersion": "v2",
                              "security": {"rootless": True, "seccompEnabled": True}},
                     "version": {"Version": "6.1.4"}, "store": {"graphRoot": "/home/core/.local/share/containers/storage"}}
        self.state = {"Running": True, "Pid": 4242, "Status": "running"}
        self.labels = {"io.altitude.container": "1", "io.altitude.lifecycle": "podman-machine",
                       "io.altitude.lineage": "1" * 32}
        self.ssh = {f"find /sys/fs/cgroup -type d -name libpod-{IDENT}.scope 2>/dev/null; true": SCOPE + "\n",
                    "cat /proc/4242/cgroup": "0::" + SCOPE.removeprefix("/sys/fs/cgroup") + "/container\n",
                    f"cat {SCOPE}/memory.max {SCOPE}/pids.max {SCOPE}/cpu.max": f"{4 * 1024**3}\n1024\n200000 100000\n",
                    f"cat {SCOPE}/cgroup.events": "populated 0\nfrozen 0\n"}
        self.calls = []
        self.patch(platform.subprocess, "run", side_effect=self.fake_run)

    def fake_run(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.assertNotIn(argv[0], SERVICE_MANAGERS)
        def done(stdout="", code=0, stderr=""):
            return subprocess.CompletedProcess(argv, code, stdout, stderr)
        if argv[0] == "/usr/sbin/sysctl":
            return done("00000000-0000-4000-8000-000000000001\n")  # fictional boot session
        if argv[0] != "podman":
            return done()
        args = argv[1:]
        if args == ["--version"]:
            return done(self.version + "\n")
        if args[:2] == ["machine", "inspect"]:
            return done(json.dumps([self.machine])) if self.machine else done(code=125, stderr="no such machine")
        if args[:3] == ["system", "connection", "list"]:
            return done(json.dumps(self.connections))
        if args[:2] == ["machine", "ssh"]:
            self.assertEqual(args[2], "altitude")
            return done(self.ssh[args[3]])
        self.assertEqual(args[:2], ["--connection", "altitude"])
        return done(self.container(args[2:]))

    def container(self, args):
        if args[0] == "info":
            return json.dumps(self.info)
        if args[0] == "inspect":
            return json.dumps([{"Id": IDENT, "State": dict(self.state), "Config": {"Labels": self.labels, "Env": []},
                                "Mounts": [], "HostConfig": {}}])
        if args[0] == "stop":
            self.state.update(Running=False, Pid=0, Status="exited")
        if args[0] == "start":
            self.state.update(Running=True, Pid=4242, Status="running")
        return ""

    def podman(self):
        """Container calls the launcher made, without the machine proof."""
        return [call[3:] for call in self.calls if call[1:3] == ["--connection", "altitude"] and call[3] != "info"]


class MachineIdentityTests(MachineCase):
    def test_runtime_proves_the_rootless_machine_once_and_binds_its_incarnation(self):
        runtime = platform.container_runtime()
        self.assertEqual(runtime["identity"], "podman-machine:altitude:2026-10-15T09:00:00Z")
        self.assertEqual(runtime["transfers"], [])
        proofs = len(self.calls)
        platform.container_command(["ps"])
        platform.container_command(["ps"])
        self.assertEqual(len(self.calls), proofs + 2)
        self.assertEqual(self.calls[-1], ["podman", "--connection", "altitude", "ps"])
        self.assertNotIn("--remote=false", sum(self.calls, []))

    def test_every_mismatch_refuses_before_any_container_call(self):
        cases = {
            "Podman 6.0 or newer": lambda: setattr(self, "version", "podman version 5.6.2"),
            "Create Altitude's own Podman machine: podman machine init altitude": lambda: setattr(self, "machine", None),
            "not running, so container state and cleanup are unconfirmed": lambda: self.machine.update(State="stopped"),
            "must be rootless": lambda: self.machine.update(Rootful=True),
            "at least 4 CPUs": lambda: self.machine["Resources"].update(Memory=2048),
            "does not reach the altitude machine": lambda: self.connections[0].update(
                URI="ssh://core@127.0.0.1:50999/run/user/501/podman/podman.sock"),
            "does not reach the altitude machine's rootless socket": lambda: self.connections[0].update(
                URI="ssh://core@127.0.0.1:50111/run/podman/podman.sock"),
            "does not reach": lambda: self.connections[0].update(Identity="/another/key"),
            "rootless arm64": lambda: self.info["host"]["security"].update(rootless=False),
        }
        for message, mismatch in cases.items():
            with self.subTest(message):
                self.setUp()
                mismatch()
                with self.assertRaisesRegex(RuntimeError, message):
                    platform.container_command(["inspect", "altitude"])
                self.assertEqual(self.podman(), [])

    def test_another_endpoint_in_the_environment_refuses(self):
        with mock.patch.dict(os.environ, {"CONTAINER_HOST": "unix:///fictional/docker.sock"}):
            with self.assertRaisesRegex(RuntimeError, "uses only the altitude Podman machine"):
                platform.container_command(["ps"])
        self.assertEqual(self.calls, [])


class MachineLifecycleTests(MachineCase):
    def test_ownership_refuses_a_linux_launcher_container(self):
        self.labels = {"io.altitude.container": "1", "io.altitude.unit": "altitude-container-altitude.service"}
        with self.assertRaisesRegex(RuntimeError, "not adopted"):
            platform.container_owned("altitude")

    def test_limits_are_read_from_the_owned_scope_inside_the_machine(self):
        value = platform.container_owned("altitude")
        platform.container_limits(value)
        self.ssh[f"cat {SCOPE}/memory.max {SCOPE}/pids.max {SCOPE}/cpu.max"] = "max\n1024\n200000 100000\n"
        with self.assertRaisesRegex(RuntimeError, "not enforced"):
            platform.container_limits(value)
        self.ssh["cat /proc/4242/cgroup"] = "0::/user.slice/unrelated.scope\n"
        with self.assertRaisesRegex(RuntimeError, "outside its owned machine scope"):
            platform.container_limits(value)
        self.ssh[f"find /sys/fs/cgroup -type d -name libpod-{IDENT}.scope 2>/dev/null; true"] = ""
        with self.assertRaisesRegex(RuntimeError, "cgroup is unavailable"):
            platform.container_limits(value)

    def test_start_creates_detached_under_podman_and_proves_readiness(self):
        self.state.update(Running=False, Pid=0, Status="configured")
        published = self.patch(platform, "_container_published")
        self.patch(platform, "container_copy_available")
        self.assertEqual(platform.container_launch("altitude", ["--name", "altitude", "image"]), IDENT)
        self.assertEqual(self.podman()[0], ["create", "--label", "io.altitude.lifecycle=podman-machine",
                                            "--name", "altitude", "image"])
        self.assertIn(["start", IDENT], self.podman())
        published.assert_called_once()

    def test_failed_readiness_stops_and_retains_the_container_with_its_log_hint(self):
        self.state.update(Running=False, Pid=0, Status="exited")
        self.patch(platform, "_container_published", side_effect=RuntimeError("fixture HTTPS refused"))
        self.patch(platform, "container_copy_available")
        self.patch(platform.time, "monotonic", side_effect=[0, 0, 100])
        with self.assertRaisesRegex(RuntimeError, "fixture HTTPS refused; inspect with podman --connection altitude logs altitude"):
            platform.container_launch("altitude")
        self.assertIn(["stop", "--time=30", IDENT], self.podman())
        self.assertNotIn("rm", [call[0] for call in self.podman()])

    def test_stop_is_podmans_graceful_stop_and_refuses_an_unsettled_container(self):
        platform.container_stop("altitude")
        self.assertEqual(self.podman()[-2], ["stop", "--time=30", IDENT])
        self.state.update(Running=True, Pid=4242, Status="running")
        with mock.patch.object(self, "container", side_effect=lambda args: json.dumps([{
                "Id": IDENT, "State": {"Running": False, "Pid": 4242, "Status": "stopping"},
                "Config": {"Labels": self.labels}}]) if args[0] == "inspect" else ""):
            with self.assertRaisesRegex(RuntimeError, "remains active"):
                platform.container_stop("altitude")

    def test_recreation_requires_a_stopped_container_and_an_empty_machine_scope(self):
        with self.assertRaisesRegex(RuntimeError, "Stop the container"):
            platform.container_recreatable(platform.container_owned("altitude"))
        self.state.update(Running=False, Pid=0, Status="exited")
        platform.container_recreatable(platform.container_owned("altitude"))
        self.ssh[f"cat {SCOPE}/cgroup.events"] = "populated 1\nfrozen 0\n"
        with self.assertRaisesRegex(RuntimeError, "processes survive"):
            platform.container_recreatable(platform.container_owned("altitude"))

    def test_start_selects_pasta_the_only_podman_6_network(self):
        created = []
        self.patch(container, "volume_pair", return_value={"lineage": "1" * 32, "pair": "2" * 32, "archive": ""})
        self.patch(platform, "container_command", return_value=json.dumps([{"Id": "sha256:fixture",
                                                                            "Labels": {container.LABEL: "1"}}]))
        self.patch(platform, "container_launch", side_effect=lambda instance, args: created.extend(args) or instance)
        container.start("image", "altitude", "home", "projects", "127.0.0.1", "localhost", 19443)
        self.assertIn("--network=pasta", created)
        self.assertNotIn("--network=slirp4netns", created)


class MachineBuildTests(MachineCase):
    def test_build_removes_only_external_working_containers_before_and_after_a_failure(self):
        self.patch(platform, "container_runtime", return_value={})
        self.patch(container, "prepare_context", return_value={"version": "v0", "commit": "f" * 40})
        listings = {("ps", "--all", "--quiet", "--no-trunc"): "kept\n",
                    ("ps", "--all", "--external", "--quiet", "--no-trunc"): "kept\nbuildah-working\n"}
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[0] == "build":
                raise RuntimeError("fixture build interrupted")
            return listings.get(tuple(args), "")
        self.patch(platform, "container_command", side_effect=command)
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            container.build(Path("/fictional.tar"), "a" * 64, "localhost/altitude:fixture")
        removals = [args for args in calls if args[0] == "rm"]
        self.assertEqual(removals, [["rm", "--force", "buildah-working"]] * 2)
        build = next(args for args in calls if args[0] == "build")
        self.assertIn("--force-rm", build)
        self.assertNotIn("--cgroup-parent", build)
        self.assertNotIn("prune", sum(calls, []))

    def test_concurrent_build_is_refused_by_the_build_lock(self):
        with platform.container_build_lock():
            with self.assertRaisesRegex(RuntimeError, "Another container build"):
                with platform.container_build_lock():
                    self.fail()


class MachineTransferTests(MachineCase):
    def test_foreground_client_owns_a_transfer_and_recovery_waits_for_it(self):
        identity = "podman-machine:altitude:2026-10-15T09:00:00Z"
        self.patch(platform, "container_runtime", side_effect=lambda: {
            "identity": identity, "transfers": platform.container_transfer_records(identity)})
        self.patch(container, "transfer_worker", side_effect=KeyboardInterrupt)
        output = self.tmp / "backup"
        output.mkdir(mode=0o700)
        operation = {"action": "backup", "directory": str(output), "identity": {"lineage": "1" * 32, "home": "h", "projects": "p"}}
        with self.assertRaises(KeyboardInterrupt):
            container.transfer_job(operation)
        record = platform.container_transfer_root() / operation["id"] / "operation.json"
        saved = json.loads(record.read_text())
        self.assertNotIn("unit", saved)
        self.assertEqual(saved["client"]["pid"], os.getpid())
        self.assertEqual(saved["store"], identity)
        cleanup = self.patch(container, "cleanup_transfer")
        with self.assertRaisesRegex(ValueError, "still active"):
            container.recover_transfer(operation["id"])
        cleanup.assert_not_called()
        saved["client"]["start"] = "1"  # the client has ended
        record.write_text(json.dumps(saved))
        container.recover_transfer(operation["id"])
        cleanup.assert_called_once_with(record)

    def test_finished_or_failed_transfer_cleans_its_exact_operation_in_the_foreground(self):
        self.patch(platform, "container_runtime", return_value={"identity": "podman-machine:altitude:t", "transfers": []})
        worker = self.patch(container, "transfer_worker", return_value={"complete": True})
        cleanup = self.patch(container, "cleanup_transfer")
        operation = {"action": "backup", "directory": str(self.tmp), "identity": {"lineage": "1" * 32, "home": "h", "projects": "p"}}
        self.assertEqual(container.transfer_job(dict(operation)), {"complete": True})
        cleanup.assert_called_once_with(worker.call_args.args[0])
        cleanup.reset_mock()
        worker.side_effect = RuntimeError("fixture stream truncated")
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            container.transfer_job(dict(operation))
        cleanup.assert_called_once_with(worker.call_args.args[0])

    def test_records_from_an_earlier_machine_are_reported_and_never_listed_for_recovery(self):
        root = platform.container_transfer_root(create=True) / ("d" * 32)
        root.mkdir(mode=0o700)
        (root / "operation.json").write_text(json.dumps({
            "id": "d" * 32, "action": "backup", "directory": "/fictional", "store": "podman-machine:altitude:earlier",
            "identity": {"home": "h", "projects": "p"}}))
        (root / "operation.json").chmod(0o600)
        with mock.patch("sys.stderr") as stderr:
            runtime = platform.container_runtime()
        self.assertEqual(runtime["transfers"], [])
        self.assertIn("earlier altitude machine", "".join(call.args[0] for call in stderr.write.call_args_list))
        with self.assertRaisesRegex(ValueError, "No retained operation"):
            container.recover_transfer("d" * 32)

    def test_helper_has_no_linux_cgroup_parent(self):
        created = []
        self.patch(platform, "container_command", side_effect=lambda args, **kwargs: created.append(args) or json.dumps(
            [{"HostConfig": {"LogConfig": {"Type": "none"}}}]))
        self.patch(platform, "container_binary")
        container.transfer_helper({"identity": {"lineage": "1" * 32, "pair": "2" * 32, "home": "h", "projects": "p"},
                                   "id": "e" * 32, "helper": "altitude-transfer-fixture", "image": "f" * 64}, "export")
        self.assertNotIn("--cgroup-parent", created[0])
        self.assertIn("--network=none", created[0])


if __name__ == "__main__":
    import unittest
    unittest.main()
