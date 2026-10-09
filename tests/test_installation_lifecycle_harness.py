"""Verify failure injection and refusal without operating any native user service."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import types
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
        for phase in ("bootstrap", "reboot-install", "reboot-verify", "recovery", "public-install", "public-update"):
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


    def public(self, update: bool, script: bytes = b"latest script\n"):
        """Lifecycle.public with GitHub's command, the service and doctor as fixtures; returns the run steps."""
        results = self.tmp / "results"
        results.mkdir()
        for name in ("baseline", "candidate"):
            (self.tmp / name).mkdir()
            (self.tmp / name / "install.sh").write_bytes(f"{'rc.2' if name == 'baseline' else 'latest'} script\n".encode())
        harness = Lifecycle(self.tmp / "baseline", self.tmp / "candidate", results, f"{'b' * 40}..{'c' * 40}")
        harness.home = self.tmp / "home"
        harness.alt = harness.home / ".local/bin/alt"
        harness.home.mkdir()
        before = {"version": "v0.1.0-rc.2", "repository": "https://github.com/example/altitude"}
        after = {"version": "v0.1.0", "repository": "https://github.com/example/altitude"}
        steps = []

        def run(label, *command, **kwargs):
            steps.append((label, command))
            if label == "public-script":
                Path(command[command.index("-o") + 1]).write_bytes(script)
            if label == "public-install":
                (harness.home / ".altitude").mkdir()
                (harness.home / ".altitude/update.json").write_text('{"latest": {"version": "v0.1.0"}}')
                return f"Altitude {(before if update else after)['version']} is installed and its service is active."
            return "Altitude v0.1.0 is available: run alt update (notes: …)" if label == "notice" else ""
        doctors = iter([{"update": {"available": None}}, {"update": {"available": {"version": "v0.1.0"}}},
                        {"update": {"available": None}}])
        with mock.patch.object(harness, "prepare", return_value=("old", "1", None, before, "new", "2", "package", after)), \
                mock.patch.object(harness, "run", side_effect=run), \
                mock.patch.object(harness, "healthy", side_effect=[{"pid": 1}, {"pid": 2}]), \
                mock.patch.object(harness, "doctor", side_effect=lambda label, release: next(doctors)), \
                mock.patch.object(harness, "retain", return_value={}), mock.patch.object(harness, "roll_back") as roll_back, \
                mock.patch.object(harness, "uninstall"):
            try:
                harness.execute("public-update" if update else "public-install")
            finally:
                self.result = json.loads((results / f"{'public-update' if update else 'public-install'}-result.json").read_text())
        if update:
            roll_back.assert_called_once_with("package", "2", after)
        return steps

    def test_public_install_runs_the_documented_command_for_releases_latest(self):
        steps = self.public(update=False)
        self.assertEqual([label for label, _ in steps], ["public-script", "public-install"])
        self.assertEqual(steps[1][1], ("sh", "-c", "curl --proto '=https' --tlsv1.2 -fsSL "
                                       "https://github.com/example/altitude/releases/latest/download/install.sh | sh"))
        self.assertTrue(self.result["passed"])
        self.assertIn("GitHub's own downloads", self.result["limits"][0])

    def test_public_install_stops_when_releases_latest_is_not_the_checked_release(self):
        with self.assertRaisesRegex(AssertionError, "is not v0.1.0's install.sh"):
            self.public(update=False, script=b"rc.2 script\n")
        self.assertFalse(self.result["passed"])

    def test_public_update_installs_the_baseline_by_tag_then_takes_the_offered_release_with_alt_update(self):
        steps = self.public(update=True, script=b"rc.2 script\n")
        self.assertEqual([label for label, _ in steps], ["public-script", "public-install", "notice", "update"])
        self.assertIn("releases/download/v0.1.0-rc.2/install.sh | sh", steps[1][1][2])
        self.assertEqual(steps[3][1][1:], ("update",))
        self.assertTrue(self.result["passed"])


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

    def test_recovery_and_public_runs_need_a_published_baseline_and_are_separate(self):
        for flags, refusal in ((["--recovery"], "need --baseline-release"), (["--public"], "need --baseline-release"),
                               (["--baseline-release", "v0.1.0-rc.2", "--recovery", "--public"], "separate runs")):
            with self.subTest(flags=flags):
                result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/installation_vm.py"),
                                         str(self.tmp / "results"), *flags], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2)
                self.assertIn(refusal, result.stderr)
                self.assertFalse((self.tmp / "results").exists())

    def test_latest_is_the_stable_release_githubs_redirect_names(self):
        from scripts import installation_vm as vm

        def redirected(url):
            response = mock.MagicMock()
            response.__enter__.return_value.url = url
            return response
        for target, expected in (("v0.1.0", "v0.1.0"), ("v0.1.0-rc.2", "names the release candidate"),
                                 ("main", "not a release version")):
            with self.subTest(target=target), mock.patch.object(
                    vm, "urlopen", return_value=redirected(f"https://github.com/example/altitude/releases/tag/{target}")) as opened:
                if target == expected:
                    self.assertEqual(vm.latest("https://github.com/example/altitude"), expected)
                else:
                    with self.assertRaisesRegex(SystemExit, expected):
                        vm.latest("https://github.com/example/altitude")
                self.assertEqual(opened.call_args.args[0], "https://github.com/example/altitude/releases/latest")

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
        github = "https://github.com/example/altitude"

        def run(command, **kwargs):
            if command[:2] == ["git", "ls-remote"]:
                self.assertEqual(command[3], github)  # anonymous, whatever the checkout's origin
                return subprocess.CompletedProcess(command, 0, tags["refs"], "")
            return native(command, **kwargs)

        def listing(url, timeout):
            # GitHub's anonymous release record, with every published asset.
            self.assertEqual(url, "https://api.github.com/repos/example/altitude/releases/tags/v0.1.0-rc.2")
            return io.BytesIO(json.dumps({"assets": [{"name": path.name} for path in assets.iterdir()]}).encode())

        def download(url, path):
            self.assertEqual(url, f"{github}/releases/download/v0.1.0-rc.2/{path.name}")
            shutil.copyfile(assets / path.name, path)

        def attempt(name):
            with mock.patch.object(vm.subprocess, "run", side_effect=run), mock.patch.object(vm, "urlopen", side_effect=listing), \
                    mock.patch.object(vm, "download", side_effect=download):
                return vm.published(github, "v0.1.0-rc.2", self.tmp / name)

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
        (assets / (name + ".sha256")).write_text(checksum + "\n")
        (assets / "SHA256SUMS").unlink()
        with self.assertRaisesRegex(SystemExit, "no SHA256SUMS"):
            attempt("unlisted")

    def test_public_run_downloads_both_releases_keeps_only_the_internet_and_runs_both_public_phases(self):
        from scripts import installation_vm as vm
        commands, phases = [], []
        native = subprocess.run

        class Machine:
            def __init__(self, work, image):
                pass
            start = wait_ready = stop = copy = lambda self, *args, **kwargs: None

            def ssh(self, command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "Ubuntu 24.04 fixture\n", "")

            def unplug_online_card(self):
                raise AssertionError("A public run keeps the online card")

        def published(repository, tag, folder):
            folder.mkdir(parents=True)
            return {"release": tag, "commit": ("b" if "-rc." in tag else "c") * 40, "sha256": {}}

        def harness(machine, commits, phase, log):
            phases.append((commits, phase))
            return 0
        reachable = iter([{"internet": True, "host-through-online-card": True, "host-through-network": True,
                           "host-through-offline-card": False},
                          {"internet": True, "host-through-online-card": False, "host-through-network": False,
                           "host-through-offline-card": False}])
        results = self.tmp / "results"
        with mock.patch.object(vm.subprocess, "run", side_effect=lambda command, **kw: subprocess.CompletedProcess(
                    command, 0, "QEMU emulator version 9.0 fixture\n", "") if command[0] == "qemu-system-x86_64"
                    else native(command, **kw)), \
                mock.patch.object(vm, "build", side_effect=AssertionError("A public run builds nothing")), \
                mock.patch.object(vm, "repository", return_value="https://github.com/example/altitude"), \
                mock.patch.object(vm, "latest", return_value="v0.1.0"), mock.patch.object(vm, "published", side_effect=published), \
                mock.patch.object(vm, "base_image", return_value={"image": "fixture"}), mock.patch.object(vm, "Machine", Machine), \
                mock.patch.object(vm, "harness", side_effect=harness), mock.patch.object(vm, "outbound_address", return_value="203.0.113.7"), \
                mock.patch.object(vm, "reachable", side_effect=lambda machine: next(reachable)), mock.patch("sys.stdout"):
            code = vm.run(results, "a" * 40, self.tmp / "cache", "v0.1.0-rc.2", public=True)
        record = json.loads((results / "vm.json").read_text())
        self.assertEqual((code, record["passed"], record["public"], record["latest"]), (0, True, True, "v0.1.0"))
        self.assertEqual((record["baseline"]["release"], record["candidate"]["release"]), ("v0.1.0-rc.2", "v0.1.0"))
        [blocking] = [command for command in commands if "ip route add prohibit" in command]
        self.assertEqual(record["blocked"][:2], ["10.0.3.2/32", "203.0.113.7/32"])
        for network in ("10.0.3.2/32", "203.0.113.7/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                        "100.64.0.0/10", "169.254.0.0/16"):
            self.assertIn(f"ip route add prohibit {network};" if network != "169.254.0.0/16"
                          else f"ip route add prohibit {network}'", blocking)
        self.assertEqual(phases, [(f"{'b' * 40}..{'c' * 40}", "public-install"), (f"{'b' * 40}..{'c' * 40}", "public-update")])

    def test_a_public_guest_that_still_reaches_this_host_stops_before_any_phase(self):
        from scripts import installation_vm as vm
        machine = mock.MagicMock()
        machine.ssh.return_value = subprocess.CompletedProcess([], 0, "Ubuntu 24.04 fixture\n", "")
        native = subprocess.run
        with mock.patch.object(vm.subprocess, "run", side_effect=lambda command, **kw: subprocess.CompletedProcess(
                    command, 0, "QEMU emulator version 9.0 fixture\n", "") if command[0] == "qemu-system-x86_64"
                    else native(command, **kw)), \
                mock.patch.object(vm, "repository", return_value="https://github.com/example/altitude"), \
                mock.patch.object(vm, "latest", return_value="v0.1.0"), \
                mock.patch.object(vm, "published", side_effect=lambda repository, tag, folder: {"commit": "b" * 40}), \
                mock.patch.object(vm, "base_image", return_value={}), mock.patch.object(vm, "Machine", return_value=machine), \
                mock.patch.object(vm, "outbound_address", return_value="203.0.113.7"), \
                mock.patch.object(vm, "harness") as harness, mock.patch.object(vm, "reachable", side_effect=[
                    {"internet": True, "host-through-online-card": True, "host-through-network": True,
                           "host-through-offline-card": False},
                    {"internet": True, "host-through-online-card": True, "host-through-network": True,
                           "host-through-offline-card": False}]), \
                mock.patch("sys.stdout"):
            with self.assertRaisesRegex(SystemExit, "does not reach only the internet"):
                vm.run(self.tmp / "results", "a" * 40, self.tmp / "cache", "v0.1.0-rc.2", public=True)
        harness.assert_not_called()
        machine.unplug_online_card.assert_not_called()
        self.assertFalse(json.loads((self.tmp / "results/vm.json").read_text())["passed"])

    def test_repository_is_the_checkouts_github_origin(self):
        from scripts import installation_vm as vm
        for origin, expected in (("git@github.com:example/altitude.git", "https://github.com/example/altitude"),
                                 ("https://github.com/example/altitude", "https://github.com/example/altitude")):
            with self.subTest(origin=origin), mock.patch.object(
                    vm.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, origin + "\n", "")):
                self.assertEqual(vm.repository(), expected)
        with mock.patch.object(vm.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "/srv/altitude\n", "")):
            with self.assertRaisesRegex(SystemExit, "not a GitHub repository"):
                vm.repository()


class TestMacLifecycle(AltitudeCase):
    """The macOS lane's parts that need no launchd: its release server, its refusals and its cleanup's reach."""

    def test_release_server_answers_githubs_release_addresses_and_refuses_every_other_host(self):
        from scripts.installation_lifecycle import Lifecycle, ReleaseServer
        run = lambda label, *command: subprocess.run(list(map(str, command)), capture_output=True, check=True)
        authority = Lifecycle.release_authority(types.SimpleNamespace(home=self.tmp, run=run), *ReleaseServer.HOSTS)
        server = ReleaseServer(authority, "example/altitude")
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

    def test_a_stopped_lifecycle_takes_the_installer_processes_it_started_with_it(self):
        from scripts import installation_mac as mac
        marker = self.tmp / "descendant.pid"
        # A lifecycle whose installer outlives it, as sh, curl or install.py would after a timeout.
        lifecycle = subprocess.Popen([sys.executable, "-c", "import subprocess, sys, time\n"
                                      "child = subprocess.Popen(['sleep', '300'])\n"
                                      "open(sys.argv[1], 'w').write(str(child.pid))\ntime.sleep(300)\n", str(marker)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 30
        while not (marker.exists() and marker.read_text()) and time.monotonic() < deadline:
            time.sleep(.05)
        descendant = int(marker.read_text())
        self.assertTrue(mac.stop_group(lifecycle))
        self.assertIsNotNone(lifecycle.poll())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(descendant, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            self.fail("The installer process survived its lifecycle")
        self.assertFalse(mac.stop_group(lifecycle))

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


class TestInstallationVmCapture(AltitudeCase):
    """`--capture` around a lane run whose VM, builds and harness are fixtures; the replay is real up to the encoder."""

    def lane(self, *, capture, made=None, failure=None, unreadable=False):
        from scripts import installation_vm as vm
        from altitude import capture as C
        native = subprocess.run

        class Machine:
            def __init__(self, work, image):
                pass
            start = wait_ready = unplug_online_card = stop = copy = lambda self, *args, **kwargs: None
            ssh = lambda self, *args, **kwargs: subprocess.CompletedProcess(args, 0, "Ubuntu 24.04 fixture\n", "")
            reboot = lambda self, deadline: "boot-2"

        def harness(machine, commit, phase, log):
            with log.open("w") as stream:  # written as it runs, as the real harness's ssh output is
                for step in range(3):
                    stream.write(f"\x1b[32mok\x1b[0m {phase} step {step}\n")
                    stream.flush()
                    time.sleep(0.2)
            return 0

        def terminal(lines, target, title, steps):
            made.append((lines, title, steps))
            if failure:
                raise (C.CaptureError if failure == "ffmpeg unavailable" else RuntimeError)(failure)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"GIF89a fixture")
            return {}
        reachable = iter([{"internet": True, "host-through-online-card": True, "host-through-network": True,
                           "host-through-offline-card": False}]
                         + [{"internet": False}] * 2)
        results = self.tmp / f"results-{capture}-{failure}"
        if unreadable:  # a harness log the replay cannot read
            (results / "harness-unreadable.log").mkdir(parents=True)
        with mock.patch.object(vm.subprocess, "run", side_effect=lambda command, **kw: subprocess.CompletedProcess(
                    command, 0, "QEMU emulator version 9.0 fixture\n", "") if command[0] == "qemu-system-x86_64"
                    else native(command, **kw)), \
                mock.patch.object(vm, "build"), mock.patch.object(vm, "base_image", return_value={"image": "fixture"}), \
                mock.patch.object(vm, "Machine", Machine), mock.patch.object(vm, "harness", side_effect=harness), \
                mock.patch.object(vm, "reachable", side_effect=lambda machine: next(reachable)), \
                mock.patch.object(C, "terminal", side_effect=terminal), mock.patch("sys.stdout"):
            code = vm.run(results, "0123456789abcdef", self.tmp / "cache", capture=capture)
        return code, results, json.loads((results / "vm.json").read_text())

    def test_without_capture_nothing_is_recorded(self):
        code, results, record = self.lane(capture=False)
        self.assertEqual((code, record["passed"]), (0, True))
        self.assertNotIn("capture", record)
        self.assertFalse((results / "captures").exists())

    def test_a_capture_replays_progress_and_harness_output_with_their_arrival_times(self):
        made = []
        code, results, record = self.lane(capture=True, made=made)
        self.assertEqual((code, record["passed"]), (0, True))
        self.assertEqual(record["capture"], str(results / "captures" / "installation-vm.gif"))
        [(lines, title, steps)] = made
        self.assertEqual(title, "installation-vm 0123456789ab")
        self.assertEqual(steps[:2], ("building both release versions from 0123456789ab", "verifying the Ubuntu cloud image"))
        self.assertEqual(steps[-1], f"VM deleted; passed; evidence in {results}")
        texts = [text for _, text in lines]
        for phase in ("all", "bootstrap", "update", "reboot-install", "reboot-verify"):
            self.assertIn(f"\x1b[32mok\x1b[0m {phase} step 2", texts)
        self.assertLess(texts.index("running the all phase"), texts.index("\x1b[32mok\x1b[0m all step 0"))

    def test_a_capture_that_fails_leaves_the_lanes_result_alone(self):
        for failure in ("ffmpeg unavailable", "an encoder fault"):
            with self.subTest(failure):
                code, results, record = self.lane(capture=True, made=[], failure=failure, unreadable=True)
                self.assertEqual((code, record["passed"], record["capture"]), (0, True, f"none: {failure}"))


class TestInstallationMacosVm(AltitudeCase):
    """The macOS VM lane's offline guest setup, isolation and judging; no guest, helper or restore image."""

    class Guest:
        """Answers the lane's probes as a guest whose network card is plugged in until `unplug`."""

        def __init__(self, unplug_reaches=(), failing=(), routeless=0):
            self.gateway = self.listener = None
            self.routeless = routeless
            self.plugged, self.unplug_reaches, self.failing, self.sent = True, set(unplug_reaches), dict(failing), []

        def ssh(self, command, timeout=120, check=True):
            if command.startswith("route"):
                self.routeless -= 1
                return subprocess.CompletedProcess(command, 0, "" if self.routeless >= 0 else "127.0.0.1\n", "")
            kind = "internet" if command.startswith("curl") else "host"
            if kind in self.failing:
                return subprocess.CompletedProcess(command, self.failing[kind], "", "")
            return subprocess.CompletedProcess(command, 0 if self.plugged or kind in self.unplug_reaches else 1, "", "")

        def command(self, line):
            self.sent.append(line)
            self.plugged = False

        def expect(self, prefix, timeout):
            return prefix

    def test_auto_login_password_is_padded_and_masked_as_loginwindow_reads_it(self):
        from scripts import installation_macos_vm as vm
        key = bytes([0x7D, 0x89, 0x52, 0x23, 0xD2, 0xBC, 0xDD, 0xEA, 0xA3, 0xB9, 0x1F])
        for password, size in (("short", 12), ("exactly12chr", 24)):
            masked = vm.kcpassword(password)
            self.assertEqual(len(masked), size)
            plain = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(masked))
            self.assertEqual(plain.rstrip(b"\0"), password.encode())
            self.assertTrue(plain.endswith(b"\0"))

    def test_account_password_is_stored_only_as_a_salted_pbkdf2_hash(self):
        from scripts import installation_macos_vm as vm
        import plistlib
        record = plistlib.loads(vm.shadow_hash("fixture password"))["SALTED-SHA512-PBKDF2"]
        self.assertEqual((record["iterations"], len(record["salt"]), len(record["entropy"])), (50_000, 32, 128))
        self.assertEqual(record["entropy"], hashlib.pbkdf2_hmac("sha512", b"fixture password", record["salt"], 50_000, 128))
        self.assertNotEqual(vm.shadow_hash("fixture password"), vm.shadow_hash("fixture password"))

    def test_guest_address_comes_from_its_own_lease_whatever_the_zero_padding(self):
        from scripts import installation_macos_vm as vm
        leases = self.tmp / "dhcpd_leases"
        leases.write_text("{\n\tname=other\n\tip_address=192.168.64.2\n\thw_address=1,a:b:c:d:e:f\n}\n"
                          "{\n\tname=guest\n\tip_address=192.168.64.3\n\thw_address=1,2:0:5a:1:b2:c3\n}\n")
        self.assertEqual(vm.lease("02:00:5a:01:b2:c3", leases), "192.168.64.3")
        self.assertIsNone(vm.lease("02:00:5a:01:b2:c4", leases))
        self.assertIsNone(vm.lease("02:00:5a:01:b2:c3", self.tmp / "missing"))

    def test_isolation_needs_both_destinations_before_and_neither_after_unplugging(self):
        from scripts import installation_macos_vm as vm
        guest, record = self.Guest(), {}
        try:
            vm.isolate(guest, record)
        finally:
            guest.listener.close()
        self.assertEqual(guest.sent, ["unplug"])
        self.assertEqual(record["reachable"], {"online": {"internet": True, "host": True},
                                               "isolated": {"internet": False, "host": False}})
        for leaky in ("internet", "host"):
            guest = self.Guest(unplug_reaches={leaky})
            try:
                with self.assertRaisesRegex(vm.Stop, "not isolated"):
                    vm.isolate(guest, {})
            finally:
                guest.listener.close()

    def test_isolation_waits_for_a_guest_whose_address_comes_after_ssh(self):
        from scripts import installation_macos_vm as vm
        guest, record = self.Guest(routeless=2), {}
        with mock.patch.object(vm.time, "sleep"):
            try:
                vm.isolate(guest, record)
            finally:
                guest.listener.close()
        self.assertEqual(record["reachable"]["isolated"], {"internet": False, "host": False})

    def test_a_probe_that_could_not_run_stops_the_run_instead_of_proving_isolation(self):
        from scripts import installation_macos_vm as vm
        # SSH failing, and the internet probe's TLS or other error, which says nothing about reachability.
        for failing in ({"host": 255}, {"internet": 3}):
            guest = self.Guest(failing=failing)
            try:
                with self.assertRaisesRegex(vm.Stop, "could not run"):
                    vm.isolate(guest, {})
            finally:
                guest.listener.close()
            self.assertEqual(guest.sent, [])

    def test_internet_probe_tells_unreachable_from_inconclusive_and_avoids_the_served_host(self):
        from scripts import installation_macos_vm as vm
        self.assertNotIn("github.com", vm.INTERNET)
        for code, expected in ((0, 0), (6, 1), (7, 1), (28, 1), (60, 3), (35, 3)):
            shell = vm.INTERNET.replace("curl -sS --max-time 10 -o /dev/null https://www.apple.com/", f"(exit {code})")
            self.assertEqual(subprocess.run(["sh", "-c", shell]).returncode, expected, code)

    def test_a_guest_that_does_not_start_leaves_no_helper_running(self):
        from scripts import installation_macos_vm as vm
        fake = self.tmp / "helper"
        fake.write_text("#!/bin/sh\nexec sleep 60\n")
        fake.chmod(0o755)
        started = []
        popen = subprocess.Popen
        def spawn(*args, **kwargs):
            started.append(popen(*args, **kwargs))
            return started[-1]
        for interruption in (vm.Stop("the guest helper did not say 'started' within 120s"), SystemExit(143)):
            with mock.patch.object(vm, "helper", return_value=fake), mock.patch.object(vm.subprocess, "Popen", spawn), \
                    mock.patch.object(vm.Guest, "expect", side_effect=interruption):
                with self.assertRaises(type(interruption)):
                    vm.Guest(self.tmp, self.tmp, self.tmp / "vm.log")
            self.assertIsNotNone(started[-1].poll())

    def test_refusal_passes_only_with_its_documented_fix_and_nothing_changed(self):
        from scripts import installation_macos_vm as vm
        output = "Python 3.12 or newer was not found. Install it with: brew install python@3.12"
        self.assertTrue(vm.judged({"exit": 1, "output": output, "unchanged": True}, "missing-python")["passed"])
        for outcome in ({"exit": 0, "output": output, "unchanged": True},
                        {"exit": 1, "output": output, "unchanged": False},
                        {"exit": 1, "output": "Altitude cannot be installed on this Mac yet.", "unchanged": True}):
            self.assertFalse(vm.judged(outcome, "missing-python")["passed"])

    def test_openssl_refusal_needs_the_path_fix_as_well_as_the_install(self):
        from scripts import installation_macos_vm as vm
        output = ("Altitude was not installed: the openssl on PATH is LibreSSL 3.3.6, not OpenSSL 3.\n"
                  "  Install it (brew install openssl@3), put it ahead of /usr/bin")
        path_fix = ' (export PATH="$(brew --prefix openssl@3)/bin:$PATH", also in your shell profile), then run this again.'
        self.assertFalse(vm.judged({"exit": 1, "output": output, "unchanged": True}, "openssl-not-first")["passed"])
        self.assertTrue(vm.judged({"exit": 1, "output": output + path_fix, "unchanged": True}, "openssl-not-first")["passed"])

    def test_public_command_trusts_the_test_authority_for_its_own_and_install_shs_downloads(self):
        from scripts import installation_macos_vm as vm
        command = vm.public_command("example/altitude")
        trust, _, public = command.partition("; ")
        self.assertEqual(trust, f"export SSL_CERT_FILE={vm.SHARED}/ca.pem CURL_CA_BUNDLE={vm.SHARED}/ca.pem")
        self.assertEqual(public, "curl --proto '=https' --tlsv1.2 -fsSL "
                                 "https://github.com/example/altitude/releases/latest/download/install.sh | sh")

    def test_release_is_served_where_the_public_command_and_installer_look_on_github(self):
        from scripts import installation_macos_vm as vm
        release = self.tmp / "release"
        release.mkdir()
        (release / "install.sh").write_text("#!/bin/sh\nREPOSITORY='https://github.com/example/altitude'\nVERSION='v0.0.1'\n")
        (release / "altitude-v0.0.1.tar.gz").write_bytes(b"archive")
        self.assertEqual(vm.release_tree(release, self.tmp / "serve"), "example/altitude")
        root = self.tmp / "serve/root/example/altitude/releases"
        self.assertTrue((root / "latest/download/install.sh").is_file())
        self.assertEqual(sorted(path.name for path in (root / "download/v0.0.1").iterdir()),
                         ["altitude-v0.0.1.tar.gz", "install.sh"])

    def test_other_hosts_refuse_before_touching_anything(self):
        from scripts import installation_macos_vm as vm
        with mock.patch.object(vm.sys, "platform", "linux"), \
                mock.patch.object(vm.sys, "argv", ["installation_macos_vm.py", "run", str(self.tmp / "results")]), \
                mock.patch.object(vm.sys, "stderr", io.StringIO()) as stderr, mock.patch.object(vm, "lock") as lock:
            self.assertEqual(vm.main(), 2)
        self.assertIn("needs a Mac with Apple silicon", stderr.getvalue())
        lock.assert_not_called()
        self.assertFalse((self.tmp / "results").exists())
