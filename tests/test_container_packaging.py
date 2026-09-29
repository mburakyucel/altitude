"""Real volume lock contention and release/launcher contracts, without calling a host runtime."""
import json
import os
import stat
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, platform
from scripts import container, container_acceptance


class TestContainerBusIdentity(AltitudeCase):
    def test_fixture_runtime_retains_bus_for_environment_stripped_children(self):
        root = self.tmp / "gate"
        root.mkdir()
        before = dict(os.environ)
        with container_acceptance.environment(root):
            self.assertEqual(os.environ["XDG_RUNTIME_DIR"], str(root / "runtime"))
            self.assertEqual(os.environ["DBUS_SESSION_BUS_ADDRESS"],
                             f"unix:path=/run/user/{os.getuid()}/bus")
            # Podman's DeleteContainer drops the explicit bus address. Its remaining
            # XDG runtime must still resolve to the same user bus, without a fallback.
            self.assertEqual(os.readlink(root / "runtime/bus"), f"/run/user/{os.getuid()}/bus")
            for key in ('HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'):
                self.assertTrue(Path(os.environ[key]).is_relative_to(root))
        self.assertEqual(os.environ, before)

    def test_unsafe_bus_or_runtime_refuses_even_cleanup_before_podman(self):
        for variables in ({"XDG_RUNTIME_DIR": "relative"},
                          {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/dbus/system_bus_socket"}):
            with self.subTest(variables=variables), mock.patch.dict(os.environ, variables, clear=True), \
                 mock.patch.object(os, "getuid", return_value=1000), \
                 mock.patch.object(platform.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "local user runtime"):
                    platform.container_command(["rm", "--force", "fixture"])
                run.assert_not_called()

    def test_missing_bus_refuses_and_valid_user_bus_is_passed_to_podman(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(os, "getuid", return_value=1000), \
             mock.patch.object(Path, "lstat", side_effect=FileNotFoundError), \
             mock.patch.object(platform.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "user bus is unavailable"):
                platform.container_command(["stop", "fixture"])
            run.assert_not_called()
        metadata = [mock.Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=1000),
                    mock.Mock(st_mode=stat.S_IFSOCK | 0o600, st_uid=1000)]
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(os, "getuid", return_value=1000), \
             mock.patch.object(Path, "resolve", return_value=Path('/run/user/1000/bus')), \
             mock.patch.object(Path, "lstat", side_effect=metadata), \
             mock.patch.object(platform.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="ok")) as run:
            self.assertEqual(platform.container_command(["stop", "fixture"]), "ok")
            self.assertEqual(run.call_args.kwargs["env"]["XDG_RUNTIME_DIR"], "/run/user/1000")

    def test_private_runtime_cannot_link_to_another_bus(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/tmp/fixture-runtime"}, clear=True), \
             mock.patch.object(os, "getuid", return_value=1000), \
             mock.patch.object(Path, "resolve", return_value=Path('/run/dbus/system_bus_socket')), \
             mock.patch.object(platform.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "must resolve to the local user bus"):
                platform.container_command(["rm", "--force", "fixture"])
            run.assert_not_called()


class TestFixtureCleanup(AltitudeCase):
    def test_reused_pause_pid_is_not_signalled_after_identity_changes(self):
        proc = self.tmp / "proc"
        process = proc / "42"
        process.mkdir(parents=True)
        (process / "comm").write_text("podman pause\n")
        home = self.tmp / "fixture-home"
        (process / "environ").write_bytes(("HOME=" + str(home)).encode() + b"\0")
        (process / "exe").symlink_to("/usr/bin/podman")
        def acquire(pid):
            (process / "environ").write_bytes(b"HOME=/unrelated\0")
            return 123
        with mock.patch.object(platform, "PROC", proc), mock.patch.object(os, "pidfd_open", side_effect=acquire), \
             mock.patch.object(os, "close") as close, mock.patch.object(platform.shutil, "which", return_value="/usr/bin/podman"), \
             mock.patch.object(platform.signal, "pidfd_send_signal") as send:
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                platform.cleanup_container_pause(home)
            send.assert_not_called()
            close.assert_called_once_with(123)


class TestVolumeLocks(AltitudeCase):
    def test_bootstrap_recognizes_nested_same_device_mounts_before_starting_manager(self):
        home = self.tmp / "home"
        projects = home / "Projects"
        projects.mkdir(parents=True)
        proc = self.tmp / "proc"
        (proc / "self").mkdir(parents=True)
        mountinfo = proc / "self/mountinfo"
        mountinfo.write_text(f"40 30 8:2 /vol/home {home} rw - ext4 /dev/fixture rw\n"
                            f"41 40 8:2 /vol/projects {projects} rw - ext4 /dev/fixture rw\n")
        self.assertFalse(projects.is_mount())
        with mock.patch.object(platform, "CONTAINER_PROJECTS", projects), mock.patch.object(platform, "PROC", proc), \
             mock.patch.object(platform, "containerized", return_value=True), mock.patch.object(os, "getuid", return_value=0), \
             mock.patch.object(os, "chown") as chown, mock.patch.dict(os.environ, {"NOTIFY_SOCKET": "/run/fixture-notify"}), \
             mock.patch.object(platform, "_initialize_container_lifecycle") as initialize, \
             mock.patch.object(platform.socket, "socket") as channel, mock.patch.object(platform.signal, "signal"), \
             mock.patch.object(platform.signal, "pause", side_effect=InterruptedError("fixture stop")):
            with self.assertRaisesRegex(InterruptedError, "fixture stop"):
                platform.container_bootstrap()
            self.assertEqual(chown.call_args_list, [mock.call(home, 1000, 1000), mock.call(projects, 1000, 1000)])
            initialize.assert_called_once_with(home, projects)
            channel.return_value.__enter__.return_value.sendall.assert_called_once_with(b"READY=1")
            mountinfo.write_text(f"40 30 8:2 /vol/home {home} rw - ext4 /dev/fixture rw\n")
            with self.assertRaisesRegex(RuntimeError, "dedicated local Podman volume"):
                platform.container_bootstrap()
            self.assertEqual(chown.call_count, 2)  # No partial ownership changes on a missing mount.

    def test_sharing_either_volume_refuses_and_releases_partial_acquisition(self):
        home, projects, home2, projects2 = [self.tmp / name for name in ("home", "projects", "home2", "projects2")]
        for path in (home, projects, home2, projects2):
            path.mkdir()
        with platform.container_volume_locks(home, projects):
            for other_home, other_projects in ((home, projects2), (home2, projects)):
                with self.subTest(home=other_home, projects=other_projects), self.assertRaisesRegex(RuntimeError, "Another Altitude"):
                    with platform.container_volume_locks(other_home, other_projects):
                        self.fail("a shared volume must refuse")
            with platform.container_volume_locks(home2, projects2):
                pass  # The second-lock refusal released the first lock.
        with platform.container_volume_locks(home, projects):
            pass

    def test_failed_startup_releases_both_locks_and_symlinks_are_not_followed(self):
        home, projects = self.tmp / "home", self.tmp / "projects"
        home.mkdir(); projects.mkdir()
        with self.assertRaisesRegex(ValueError, "fixture"):
            with platform.container_volume_locks(home, projects):
                raise ValueError("fixture startup failure")
        with platform.container_volume_locks(home, projects):
            pass
        marker = home / ".altitude-instance.lock"
        marker.unlink()
        outside = self.tmp / "outside"
        outside.write_text("do not touch")
        marker.symlink_to(outside)
        with self.assertRaises(OSError):
            with platform.container_volume_locks(home, projects):
                self.fail("symlink lock must refuse")
        self.assertEqual(outside.read_text(), "do not touch")


class TestContainerLauncher(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.patch(platform, "container_runtime", return_value={})
        self.patch(platform, "container_command", new=self.command)

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return json.dumps([{"Id": "sha256:fixture", "Labels": {container.LABEL: "1"}}])
        if args[:2] == ["volume", "ls"]:
            return "[]"
        if args[:2] == ["volume", "inspect"]:
            return json.dumps([{"Driver": "local", "Options": {}, "Labels": {container.LABEL: "1"}}])
        return "fixture\n"

    def test_launch_has_only_named_volumes_and_the_explicit_approved_exception(self):
        self.assertEqual(container.start("image", "fixture", "fixture-home", "fixture-projects", "127.0.0.1", "localhost", 19443), "fixture")
        args = self.calls[-1]
        self.assertEqual(args[0], "run")
        self.assertIn("--network=slirp4netns", args)
        self.assertIn("--security-opt=unmask=/proc/*", args)
        self.assertIn("--cgroupns=private", args)
        self.assertIn("127.0.0.1:19443:19443", args)
        self.assertEqual([args[i + 1] for i, arg in enumerate(args) if arg == "--volume"],
                         ["fixture-home:/home/altitude:nocopy", "fixture-projects:/home/altitude/Projects:nocopy"])
        self.assertFalse(any(word in " ".join(args) for word in ("--privileged", "seccomp=unconfined", "--device", "--cap-add")))
        self.assertEqual(args[-1], "sha256:fixture")

    def test_invalid_publication_and_host_bind_requests_refuse_before_mutation(self):
        for home, bind, public, port in (("/host/home", "127.0.0.1", "localhost", 19443),
                                         ("home", "0.0.0.0", "localhost", 19443),
                                         ("home", "8.8.8.8", "8.8.8.8", 19443),
                                         ("home", "127.0.0.1", "name:443", 19443),
                                         ("home", "127.0.0.1", "private.local", 19443),
                                         ("home", "127.0.0.1", "localhost", 443)):
            with self.subTest(home=home, bind=bind, public=public, port=port), self.assertRaises(ValueError):
                container.start("image", "fixture", home, "projects", bind, public, port)
        self.assertEqual(self.calls, [])

    def test_existing_volume_driver_options_refuse_host_bind(self):
        with mock.patch.object(platform, "container_command", side_effect=[json.dumps([{"Name": "home"}]),
                             json.dumps([{"Driver": "local", "Options": {"type": "none", "device": "/host", "o": "bind"}}])]):
            with self.assertRaisesRegex(ValueError, "host binds"):
                container.local_volume("home")

    def test_engine_tools_install_in_persistent_home_and_commands_use_image_source(self):
        self.patch(platform, "containerized", return_value=True)
        self.patch(config, "RELEASE", {"version": "v0.1.0"})
        self.assertEqual(engines.clean_env()["PATH"].split(":")[0], str(config.SOURCE / "bin"))
        for engine in config.ENGINES:
            self.assertIn("--prefix ~/.local", engines.install_command(engine))

    def test_operator_shell_and_exec_find_persistent_user_tools_without_login_profile(self):
        self.patch(platform, "containerized", return_value=True)
        with mock.patch.object(container, "owned", return_value={"Id": "fixture"}):
            container.execute("fixture", ["/bin/bash", "--noprofile", "--norc"], interactive=True)
        self.assertIn("PATH=" + platform.CONTAINER_USER_PATH, self.calls[-1])
        self.assertIn("--user", self.calls[-1])
        self.assertIn("1000:1000", self.calls[-1])
        self.assertIn("--env PATH=" + platform.CONTAINER_USER_PATH, platform.container_shell_command())
        self.assertTrue(platform.container_shell_command().endswith("bash --noprofile --norc"))


class TestImageGate(AltitudeCase):
    def test_failed_payload_keeps_evidence_and_cleans_only_its_isolated_store(self):
        archive = self.tmp / "fixture-archive"
        archive.write_bytes(b"fictional archive; the build is a fixture")
        evidence = self.tmp / "evidence"
        volumes, containers, calls = [], [], []
        def command(args, **kwargs):
            calls.append(args)
            if args[:2] == ["volume", "ls"]:
                return json.dumps([{"Name": name} for name in volumes])
            if args[:2] == ["volume", "create"]:
                volumes.append(args[-1]); return args[-1]
            if args[:2] == ["volume", "inspect"]:
                return json.dumps([{"Driver": "local", "Options": {}, "Labels": {container.LABEL: "1"}}])
            if args[:2] == ["volume", "rm"]:
                volumes.remove(args[-1]); return ""
            if args[0] == "create":
                containers.append("fixture-id"); return "fixture-id"
            if args[0] == "inspect":
                return "[{}]"
            if args[0] == "ps":
                return "\n".join(containers)
            if args[0] == "rm":
                containers.remove(args[-1]); return ""
            if args[0] == "images":
                return "fixture-image"
            if "python3" in args:
                raise RuntimeError("fictional payload refusal")
            return "fixture"
        def facts():
            return {"store": {"graphRoot": str(Path(os.environ["XDG_DATA_HOME"]) / "storage"),
                              "runRoot": os.environ["XDG_RUNTIME_DIR"]}}
        before = dict(os.environ)
        with mock.patch.object(platform, "container_runtime", side_effect=facts), \
             mock.patch.object(platform, "container_command", side_effect=command) as runtime, \
             mock.patch.object(platform, "cleanup_container_pause", return_value=[]), \
             mock.patch.object(container, "build", return_value={"Id": "fixture-image"}):
            result = container_acceptance.run(archive, "0" * 64, evidence)
            self.assertIs(platform.container_command, runtime)
        self.assertFalse(result["passed"])
        self.assertIn("payload refusal", result["error"])
        self.assertEqual(volumes, [])
        self.assertEqual(containers, [])
        self.assertTrue(result["temporary_directory_removed"])
        self.assertEqual(os.environ, before)
        self.assertIn(["rmi", "--force", "fixture-image"], calls)
        errors = [json.loads(path.read_text()) for path in evidence.glob("command-*.json")]
        self.assertTrue(any("payload refusal" in row.get("error", "") for row in errors))
        self.assertFalse(json.loads((evidence / "result.json").read_text())["passed"])

    def test_nonisolated_runtime_is_refused_before_build_or_resource_cleanup(self):
        archive = self.tmp / "fixture-archive"
        archive.write_bytes(b"fixture")
        with mock.patch.object(platform, "container_runtime", return_value={"store": {"graphRoot": "/operator/store"}}), \
             mock.patch.object(platform, "container_command", side_effect=AssertionError("not the fixture store")), \
             mock.patch.object(platform, "cleanup_container_pause", return_value=[]), \
             mock.patch.object(container, "build", side_effect=AssertionError("must not build")):
            result = container_acceptance.run(archive, "0" * 64, self.tmp / "evidence")
        self.assertFalse(result["passed"])
        self.assertIn("outside its disposable runtime store", result["error"])
        self.assertTrue(result["temporary_directory_removed"])
