"""Verify failure injection and refusal without operating any native user service."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from unittest import mock

from tests.support import AltitudeCase, REPO
from tests import test_installation
from altitude import installation
from scripts.installation_lifecycle import Lifecycle, failed_archive, following


class TestLifecycleHarness(AltitudeCase):
    def test_injected_archive_passes_verification_and_fails_only_packaged_entry(self):
        archive, checksum = test_installation.Installation.archive(self, "v0.0.0-rc.2")
        package = self.tmp / "package"
        before = installation.extract(archive, checksum, package)
        broken_archive, broken = failed_archive(package, self.tmp / "failed.tar.gz")
        verified = installation.extract(broken_archive, hashlib.sha256(broken_archive.read_bytes()).hexdigest(),
                                        self.tmp / "verified")
        self.assertEqual(verified, broken)
        self.assertEqual(verified["version"], "v0.0.0-rc.3")
        self.assertEqual(verified["commit"], before["commit"])
        changed = [name for name in before["files"] if before["files"][name] != verified["files"][name]]
        self.assertEqual(changed, ["bin/alt"])
        (self.tmp / "results").mkdir()
        result = subprocess.run([sys.executable, "-B", str(self.tmp / "verified/bin/alt"), "serve"],
                                capture_output=True, text=True, timeout=10,
                                env={**os.environ, "HOME": str(self.tmp)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("intentional lifecycle startup failure", result.stderr)
        marker = json.loads((self.tmp / "results/failed-startup.json").read_text())
        self.assertEqual(marker["argv"], ["serve"])
        self.assertEqual(marker["version"], broken["version"])
        self.assertGreater(marker["pid"], 0)

    def test_verified_archive_must_match_selected_source_commit(self):
        archive, checksum = test_installation.Installation.archive(self, "v0.0.0-rc.2")
        artifacts = self.tmp / "artifacts"
        artifacts.mkdir()
        filename = "altitude-v0.0.0-rc.2.tar.gz"
        shutil.copyfile(archive, artifacts / filename)
        (artifacts / (filename + ".sha256")).write_text(checksum)
        shutil.copyfile(REPO / "altitude/installation.py", artifacts / "install.py")
        results = self.tmp / "results"
        results.mkdir()
        harness = Lifecycle(artifacts, artifacts, results, "f" * 40)
        harness.home = self.tmp
        with self.assertRaisesRegex(AssertionError, "differs from its selected source"):
            harness.archive(artifacts, "candidate")
        # A published baseline names its own commit; the candidate still has to match the selected source.
        commit = installation.extract(archive, checksum, self.tmp / "inspected")["commit"]
        published = Lifecycle(artifacts, artifacts, results, f"{commit}..{'f' * 40}")
        published.home = self.tmp
        self.assertEqual(published.archive(artifacts, "baseline")[3]["commit"], commit)
        self.assertIn("Published-release baseline", published.result["limits"][0])
        with self.assertRaisesRegex(AssertionError, "candidate archive commit differs"):
            published.archive(artifacts, "candidate")

    def test_injected_failure_is_always_newer_than_the_candidate(self):
        self.assertEqual([following(v) for v in ("v0.0.0-rc.2", "v0.2.0-rc.1", "v0.1.3")],
                         ["v0.0.0-rc.3", "v0.2.0-rc.2", "v0.1.4-rc.1"])

    def test_non_disposable_account_refuses_before_any_application_or_service_command(self):
        results = self.tmp / "results"
        results.mkdir()
        harness = Lifecycle(self.tmp / "baseline", self.tmp / "candidate", results, "a" * 40)
        with mock.patch("scripts.installation_lifecycle.pwd.getpwuid") as user, \
                mock.patch("scripts.installation_lifecycle.subprocess.run") as run:
            user.return_value.pw_name = "ordinary-user"
            with self.assertRaises(AssertionError):
                harness.execute()
            run.assert_not_called()
        self.assertFalse(json.loads((results / "result.json").read_text())["passed"])

    def test_shell_entry_requires_explicit_disposable_vm_invocation(self):
        result = subprocess.run(["bash", str(REPO / "scripts/test_installation_lifecycle.sh")],
                                capture_output=True, text=True, timeout=10, env=os.environ.copy())
        self.assertEqual(result.returncode, 2)
        self.assertIn("--disposable-vm", result.stderr)

    def test_shell_entry_refuses_an_unknown_phase(self):
        result = subprocess.run(["bash", str(REPO / "scripts/test_installation_lifecycle.sh"), "--disposable-vm",
                                 "b", "c", str(self.tmp / "results"), "a" * 40, "reboot"],
                                capture_output=True, text=True, timeout=10, env=os.environ.copy())
        self.assertEqual(result.returncode, 2)
        self.assertIn("reboot-install|reboot-verify", result.stderr)
        self.assertFalse((self.tmp / "results").exists())

    def test_shell_entry_accepts_each_phase_and_still_requires_root(self):
        for phase in ("bootstrap", "reboot-install", "reboot-verify", "recovery"):
            with self.subTest(phase=phase):
                result = subprocess.run(["bash", str(REPO / "scripts/test_installation_lifecycle.sh"), "--disposable-vm",
                                         "b", "c", str(self.tmp / "results"), "a" * 40, phase],
                                        capture_output=True, text=True, timeout=10,
                                        env={k: v for k, v in os.environ.items() if k != "ALTITUDE_ACTOR"})
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Usage", result.stderr)
                self.assertFalse((self.tmp / "results").exists())

    def test_reboot_verify_refuses_when_the_machine_did_not_restart(self):
        results = self.tmp / "results"
        results.mkdir()
        harness = Lifecycle(self.tmp / "baseline", self.tmp / "candidate", results, "a" * 40)
        harness.home = self.tmp / "altitude-installation.fixture/alt-install-1"
        (results / "reboot-state.json").write_text(json.dumps({"env": {}, "release": {}, "pid": "1",
                                                                "boot_id": "fixture-boot"}))
        with mock.patch("scripts.installation_lifecycle.boot_id", return_value="fixture-boot"), \
                mock.patch("scripts.installation_lifecycle.pwd.getpwuid") as user, \
                mock.patch("scripts.installation_lifecycle.os.getuid", return_value=1000), \
                mock.patch("scripts.installation_lifecycle.subprocess.run") as run:
            user.return_value.pw_name = "alt-install-1"
            with self.assertRaisesRegex(AssertionError, "did not restart"):
                harness.execute("reboot-verify")
            run.assert_not_called()
        self.assertFalse(json.loads((results / "reboot-verify-result.json").read_text())["passed"])


    def test_recovery_refuses_a_failed_installation_that_kept_nothing_to_retain(self):
        # Without settings and a TLS identity left behind, "installing over it keeps them" proves nothing.
        results = self.tmp / "results"
        results.mkdir()
        harness = Lifecycle(self.tmp / "baseline", self.tmp / "candidate", results, f"{'b' * 40}..{'a' * 40}")
        harness.home = self.tmp / "home"
        harness.prefix, harness.settings, harness.tls = (harness.home / "prefix", harness.home / "config/install.json",
                                                         harness.home / "config/tls")
        releases = ({"version": "v0.1.0-rc.1"}, {"version": "v0.2.0-rc.1"})
        with mock.patch.object(harness, "prepare", return_value=("old", "1", None, releases[0], "new", "2", None, releases[1])), \
                mock.patch.object(harness, "run", return_value="") as run:
            with self.assertRaisesRegex(AssertionError, "kept no settings or TLS identity"):
                harness.execute("recovery")
        self.assertEqual([call.args[0] for call in run.call_args_list], ["failed-install"])
        self.assertFalse(run.call_args.kwargs["success"])
        result = json.loads((results / "recovery-result.json").read_text())
        self.assertFalse(result["passed"])
        self.assertIn("installed over it", result["limits"][0])


    def test_recovery_runs_the_documented_cleanup_only_after_the_candidate_is_refused(self):
        results = self.tmp / "results"
        results.mkdir()
        harness = Lifecycle(self.tmp / "baseline", self.tmp / "candidate", results, f"{'b' * 40}..{'a' * 40}")
        harness.home = self.tmp / "home"
        harness.prefix, harness.settings, harness.tls = (harness.home / "prefix", harness.home / "config/install.json",
                                                         harness.home / "config/tls")
        for path in (harness.settings, harness.tls / "ca.crt", harness.prefix / "pending.json"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        releases = ({"version": "v0.1.0-rc.1"}, {"version": "v0.2.0-rc.1"})
        with mock.patch.object(harness, "prepare", return_value=("old", "1", None, releases[0], "new", "2", None, releases[1])), \
                mock.patch.object(harness, "run", return_value="installed") as run:
            with self.assertRaisesRegex(AssertionError, "installed"):
                harness.execute("recovery")
        self.assertEqual([call.args[0] for call in run.call_args_list], ["failed-install", "refused-install"])
        self.assertTrue((harness.prefix / "pending.json").exists())


class TestInstallationVm(AltitudeCase):
    """The VM runner's guest configuration and refusal; no VM, image download or KVM access."""

    def test_guest_gets_prerequisites_key_only_login_and_two_named_cards(self):
        from scripts import installation_vm as vm
        data = vm.user_data("ssh-ed25519 AAAA fixture")
        self.assertTrue(data.startswith("#cloud-config\n"))
        self.assertIn("ssh_pwauth: false", data)
        self.assertIn("  - ssh-ed25519 AAAA fixture", data)
        self.assertIn("packages: [git, gh, openssl]", data)
        cards = json.loads(vm.network_config())["ethernets"]
        self.assertEqual({name: card["match"]["macaddress"] for name, card in cards.items()},
                         {"offline": vm.OFFLINE_MAC, "online": vm.ONLINE_MAC})
        # Provisioning routes through the online card; once it is unplugged nothing else leaves the guest.
        self.assertLess(cards["online"]["dhcp4-overrides"]["route-metric"],
                        cards["offline"]["dhcp4-overrides"]["route-metric"])

    def test_a_probe_that_could_not_run_stops_the_run_instead_of_proving_isolation(self):
        from scripts import installation_vm as vm
        self.assertTrue(vm.reached(0))
        for blocked in (1, 7, 28, 124):  # refused, curl could not connect or timed out, timeout(1) expired
            self.assertFalse(vm.reached(blocked))
        for inconclusive in (126, 127, 255):  # not executable, not found, SSH failed
            with self.assertRaises(SystemExit):
                vm.reached(inconclusive)

    def test_missing_prerequisites_refuse_with_install_guidance_before_any_download(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/installation_vm.py"), str(self.tmp / "results")],
                                capture_output=True, text=True, timeout=30,
                                env={**os.environ, "PATH": str(empty)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("qemu-system-x86_64", result.stderr)
        self.assertIn("sudo apt install qemu-system-x86 qemu-utils cloud-image-utils", result.stderr)
        self.assertFalse((self.tmp / "results").exists())

    def test_recovery_needs_a_published_baseline(self):
        result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/installation_vm.py"), str(self.tmp / "results"),
                                 "--recovery"], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--recovery needs --baseline-release", result.stderr)
        self.assertFalse((self.tmp / "results").exists())

    def test_published_baseline_accepts_only_the_checked_release_its_tag_names(self):
        from scripts import installation_vm as vm
        archive, checksum = test_installation.Installation.archive(self, "v0.1.0-rc.2")
        commit = installation.extract(archive, checksum, self.tmp / "inspected")["commit"]
        assets = self.tmp / "assets"
        assets.mkdir()
        name = "altitude-v0.1.0-rc.2.tar.gz"
        shutil.copyfile(archive, assets / name)
        (assets / (name + ".sha256")).write_text(checksum + "\n")
        (assets / "install.py").write_text("installer\n")
        (assets / "install.sh").write_text("script\n")
        write_sums = lambda: (assets / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((assets / name).read_bytes()).hexdigest()}  {name}\n"
            for name in (name, "install.py", "install.sh")))
        write_sums()
        native = subprocess.run
        tags = {"refs": f"{'a' * 40}\trefs/tags/v0.1.0-rc.2\n{commit}\trefs/tags/v0.1.0-rc.2^{{}}\n"}

        def run(command, **kwargs):
            if command[0] == "gh":
                self.assertEqual(command[command.index("--repo") + 1], "example/altitude")
                shutil.copytree(assets, command[command.index("--dir") + 1], dirs_exist_ok=True)
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ["git", "remote", "get-url"]:
                return subprocess.CompletedProcess(command, 0, "git@github.com:example/altitude.git\n", "")
            if command[:2] == ["git", "ls-remote"]:
                return subprocess.CompletedProcess(command, 0, tags["refs"], "")
            return native(command, **kwargs)

        def attempt(name):
            with mock.patch.object(vm.subprocess, "run", side_effect=run):
                return vm.published("v0.1.0-rc.2", self.tmp / name)

        # An annotated tag's peeled commit is the one the archive must declare.
        self.assertEqual(attempt("good"), {"release": "v0.1.0-rc.2", "commit": commit,
                                           "sha256": dict(reversed(line.split()) for line in
                                                          (assets / "SHA256SUMS").read_text().splitlines())})
        self.assertEqual(vm.next_minor("v0.1.0-rc.2"), "v0.2.0-rc.1")
        tags["refs"] = f"{commit}\trefs/tags/v0.1.0-rc.2\n"  # lightweight tag
        self.assertEqual(attempt("lightweight")["commit"], commit)
        tags["refs"] = f"{'b' * 40}\trefs/tags/v0.1.0-rc.2\n"
        with self.assertRaisesRegex(SystemExit, "declares v0.1.0-rc.2 at"):
            attempt("other-commit")
        tags["refs"] = ""
        with self.assertRaisesRegex(SystemExit, "no tag"):
            attempt("no-tag")
        tags["refs"] = f"{commit}\trefs/tags/v0.1.0-rc.2\n"
        (assets / "extra.txt").write_text("unlisted\n")
        with self.assertRaisesRegex(SystemExit, "assets differ"):
            attempt("extra")
        (assets / "extra.txt").unlink()
        (assets / "install.sh").write_text("substituted\n")
        with self.assertRaisesRegex(SystemExit, "install.sh differs"):
            attempt("substituted")
        (assets / "install.sh").write_text("script\n")
        (assets / (name + ".sha256")).write_text("0" * 64 + "\n")
        with self.assertRaisesRegex(SystemExit, ".sha256 differs"):
            attempt("checksum-file")


class TestMacLifecycle(AltitudeCase):
    """The macOS lane's parts that need no launchd: its release server, its refusals and its cleanup's reach."""

    def test_release_server_answers_githubs_release_addresses_and_refuses_every_other_host(self):
        from scripts.installation_lifecycle import ReleaseServer
        run = lambda label, *command: subprocess.run(list(map(str, command)), capture_output=True, check=True)
        server = ReleaseServer(self.tmp / "server", "example/altitude", run)
        self.addCleanup(server.stop)
        published = self.tmp / "published"
        published.mkdir()
        (published / "install.sh").write_text("echo fictional installer\n")
        (published / "altitude-v0.0.0-rc.2.tar.gz").write_bytes(b"fictional archive")
        server.publish(published, "v0.0.0-rc.2")
        fetch = ("import sys, urllib.request\n"
                 "try:\n    print(urllib.request.urlopen(sys.argv[1], timeout=10).read().decode())\n"
                 "except Exception as error:\n    print(type(error).__name__, error)\n")
        env = {**os.environ, "HTTPS_PROXY": server.proxy, "SSL_CERT_FILE": str(server.ca)}
        env.pop("https_proxy", None)
        read = lambda url: subprocess.run([sys.executable, "-c", fetch, url], env=env, capture_output=True, text=True,
                                          timeout=30).stdout.strip()
        self.assertEqual(json.loads(read("https://api.github.com/repos/example/altitude/releases/latest")),
                         {"tag_name": "v0.0.0-rc.2", "prerelease": False, "draft": False})
        self.assertEqual(read("https://github.com/example/altitude/releases/latest/download/install.sh"),
                         "echo fictional installer")
        self.assertEqual(read("https://github.com/example/altitude/releases/download/v0.0.0-rc.2/altitude-v0.0.0-rc.2.tar.gz"),
                         "fictional archive")
        self.assertIn("404", read("https://github.com/example/altitude/releases/download/v0.0.0-rc.1/install.py"))
        self.assertIn("403", read("https://example.com/"))
        self.assertEqual(server.requests[-1], {"connect": "example.com:443", "status": 403})
        # Without the throwaway authority the same answer is refused, so only the test's processes reach it.
        env.pop("SSL_CERT_FILE")
        self.assertIn("CERTIFICATE_VERIFY_FAILED", read("https://api.github.com/repos/example/altitude/releases/latest"))

    def test_a_relabelled_release_changes_only_its_version(self):
        from scripts.installation_lifecycle import synthetic_archive
        archive, checksum = test_installation.Installation.archive(self, "v0.0.0-rc.1")
        before = installation.extract(archive, checksum, self.tmp / "package")
        relabelled, release = synthetic_archive(self.tmp / "package", self.tmp / "next.tar.gz", "v0.0.0-rc.2")
        verified = installation.extract(relabelled, hashlib.sha256(relabelled.read_bytes()).hexdigest(), self.tmp / "verified")
        self.assertEqual(verified, release)
        self.assertEqual({**verified, "version": before["version"]}, before)

    def test_outside_a_throwaway_home_nothing_runs(self):
        from scripts.installation_lifecycle import MacLifecycle
        results = self.tmp / "results"
        results.mkdir()
        harness = MacLifecycle(self.tmp / "baseline", results, "a" * 40)
        with mock.patch("scripts.installation_lifecycle.subprocess.run") as run:
            with self.assertRaises(AssertionError):
                harness.execute()
            run.assert_not_called()
        self.assertFalse(json.loads((results / "result.json").read_text())["passed"])

    def test_cleanup_boots_out_only_the_throwaway_installations_own_labels(self):
        from scripts import installation_mac as mac
        work = self.tmp / "altitude-installation-mac"
        (work / "home/results").mkdir(parents=True)
        (work / "home/results/service-label.json").write_text(json.dumps({"label": "dev.altitude.altd.0123456789ab"}))
        for name in ("dev.altitude.job.altitude-update-v0.0.0-rc.3", "dev.altitude.altd"):
            (work / "home/Library/Caches/dev.altitude/jobs" / name).mkdir(parents=True)
        loaded = {"dev.altitude.altd", "dev.altitude.altd.0123456789ab", "dev.altitude.job.altitude-update-v0.0.0-rc.3",
                  "dev.altitude.job.altitude-review-elsewhere"}
        commands = []

        def launchctl(command, **kwargs):
            commands.append(command[1:])
            label = command[2].rsplit("/", 1)[1]
            if command[1] == "bootout":
                loaded.discard(label)
            return subprocess.CompletedProcess(command, 0 if label in loaded else 113, "\tstate = running\n", "")

        with mock.patch.object(mac.subprocess, "run", side_effect=launchctl):
            outcome = mac.clean_up(work)
        self.assertEqual([item["label"] for item in outcome["bootout"]],
                         ["dev.altitude.altd.0123456789ab", "dev.altitude.job.altitude-update-v0.0.0-rc.3"])
        self.assertEqual(outcome["left_loaded"], [])
        self.assertEqual(loaded, {"dev.altitude.altd", "dev.altitude.job.altitude-review-elsewhere"})
        self.assertNotIn("dev.altitude.altd", {command[1].rsplit("/", 1)[1] for command in commands})

    def test_the_runner_refuses_off_a_mac_or_inside_a_sandbox_before_building(self):
        from scripts import installation_mac as mac
        with mock.patch.object(mac.sys, "platform", "linux"), mock.patch.object(mac, "sandboxed", return_value=True):
            missing = mac.missing_prerequisites()
        self.assertIn("macOS 15 or newer on Apple silicon", missing)
        self.assertIn("a process outside the worker sandbox (alt task run under an operator grant, or a terminal)", missing)
        with mock.patch.object(mac, "missing_prerequisites", return_value=["fixture"]), \
                mock.patch.object(mac, "run") as run, mock.patch.object(sys, "argv", ["installation_mac.py", str(self.tmp / "r")]):
            self.assertEqual(mac.main(), 2)
            run.assert_not_called()
        self.assertFalse((self.tmp / "r").exists())
