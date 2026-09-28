"""Verify failure injection and refusal without operating any native user service."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest import mock

from tests.support import AltitudeCase, REPO
from tests import test_installation
from altitude import installation
from scripts.installation_lifecycle import Lifecycle, failed_archive


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
        with self.assertRaisesRegex(AssertionError, "differs from selected source"):
            harness.archive(artifacts, "mismatched")

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
        for phase in ("bootstrap", "reboot-install", "reboot-verify"):
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
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}))
        with mock.patch("scripts.installation_lifecycle.pwd.getpwuid") as user, \
                mock.patch("scripts.installation_lifecycle.os.getuid", return_value=1000), \
                mock.patch("scripts.installation_lifecycle.subprocess.run") as run:
            user.return_value.pw_name = "alt-install-1"
            with self.assertRaisesRegex(AssertionError, "did not restart"):
                harness.execute("reboot-verify")
            run.assert_not_called()
        self.assertFalse(json.loads((results / "reboot-verify-result.json").read_text())["passed"])


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
        result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/installation_vm.py"),
                                 str(self.tmp), str(self.tmp), str(self.tmp / "results"), "a" * 40],
                                capture_output=True, text=True, timeout=30,
                                env={**os.environ, "PATH": str(empty)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("qemu-system-x86_64", result.stderr)
        self.assertIn("sudo apt install qemu-system-x86 qemu-utils cloud-image-utils", result.stderr)
        self.assertFalse((self.tmp / "results").exists())
