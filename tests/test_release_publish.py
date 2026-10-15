"""The one-command install script and the tag-triggered release job, run on fictional releases."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from tests.support import REPO, git, make_repo
from scripts import build_release

REPOSITORY = "https://github.com/example/altitude"
FINGERPRINT = "sha256 Fingerprint=" + ":".join(["AB"] * 32)


class InstallScript(unittest.TestCase):
    """install.sh with fixture commands on PATH: curl serves a local release; systemctl, launchctl, sw_vers and openssl
    answer as told."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-sh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.release = self.tmp / "release"
        self.release.mkdir()
        (self.release / "altitude-v0.1.0.tar.gz").write_bytes(b"fictional archive")
        (self.release / "install.py").write_text(textwrap.dedent(f"""\
            import json, pathlib, sys
            pathlib.Path({str(self.tmp / 'installer-args.json')!r}).write_text(json.dumps(sys.argv[1:]))
            lingering = json.loads(pathlib.Path({str(self.tmp / 'lingering.json')!r}).read_text() or "null")
            print(json.dumps({{"version": "v0.1.0", "service": "running", "url": "https://localhost:8443", "lingering": lingering,
                              "pairing": {{"url": "https://localhost:8443/projects#pair=ABCD-2345", "minutes": 10}},
                              "network": {{"listener_scope": "local"}}, "lifecycle": "Fictional login service lifecycle.",
                              "trust": {{"ca_cert": {str(self.tmp / 'home/.altitude/tls/ca.crt')!r}, "ca_name": "Altitude CA DEMO", "ca_sha256": {FINGERPRINT!r},
                                         "managed": not pathlib.Path({str(self.tmp / 'external')!r}).exists()}}}}))
            """))
        (self.tmp / "lingering.json").write_text("")
        digest = {name: hashlib.sha256((self.release / name).read_bytes()).hexdigest()
                  for name in ("altitude-v0.1.0.tar.gz", "install.py")}
        self.script = self.tmp / "install.sh"
        self.script.write_text(build_release.install_script((REPO / "scripts/install.sh").read_text(), "v0.1.0",
                                                            REPOSITORY, digest["altitude-v0.1.0.tar.gz"], digest["install.py"])
                               .replace("/opt/homebrew/opt/openssl@3/bin/openssl", str(self.tmp / "arm/bin/openssl"))
                               .replace("/usr/local/opt/openssl@3/bin/openssl", str(self.tmp / "intel/bin/openssl")))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("sh", "cat", "cp", "cut", "sha256sum", "mktemp", "rm"):
            (self.bin / tool).symlink_to(subprocess.check_output(["sh", "-c", f"command -v {tool}"], text=True).strip())
        (self.bin / "python3").symlink_to(sys.executable)
        self.fixture("uname", 'case "$1" in -s) echo "${FIXTURE_SYSTEM:-Linux}" ;; -m) echo "${FIXTURE_MACHINE:-x86_64}" ;; esac')
        self.fixture("id", 'echo "${FIXTURE_UID:-1000}"')
        self.fixture("systemctl", 'exit "${FIXTURE_SYSTEMD:-0}"')
        self.fixture("sw_vers", 'echo "${FIXTURE_MACOS:-15.5}"')
        self.fixture("launchctl", 'exit "${FIXTURE_LAUNCHD:-0}"')
        self.fixture("openssl", 'echo "${FIXTURE_OPENSSL:-OpenSSL 3.0.13 30 Jan 2024 (Library: OpenSSL 3.0.13 30 Jan 2024)}"')
        self.fixture("xcode-select", 'exit 1')
        self.fixture("curl", f'echo "$@" >> {self.tmp}/downloads\n'
                             'for last; do :; done\n'
                             'while [ "$1" != -o ]; do shift; done\n'
                             f'cp "{self.release}/${{last##*/}}" "$2"')
        self.env = {"PATH": str(self.bin), "HOME": str(self.tmp / "home"), "TMPDIR": str(self.tmp)}
        # The installed alt: `alt trust` answers as told and records whether it had a terminal to ask in.
        launcher = self.tmp / "home/.local/bin/alt"
        launcher.parent.mkdir(parents=True)
        launcher.write_text(f'#!/bin/sh\n{{ echo "$@"; [ -t 0 ] && echo stdin; : </dev/tty && echo terminal; }} >> {self.tmp}/alt-calls 2>/dev/null\n'
                            'echo "Trust it? [y/N] (fixture)"\nexit "${FIXTURE_TRUST:-0}"\n')
        launcher.chmod(0o755)

    def fixture(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, **env):
        # The documented form: the script arrives on standard input.
        return subprocess.run(["sh"], input=self.script.read_text(), env={**self.env, **env},
                              capture_output=True, text=True)

    def run_in_terminal(self, **env) -> tuple[int, str]:
        """`curl … | sh` typed in a terminal: the script arrives on standard input while the terminal stays the
        session's own."""
        import pty
        pid, terminal = pty.fork()
        if pid == 0:
            script = os.open(self.script, os.O_RDONLY)
            os.dup2(script, 0)
            os.execve("/bin/sh", ["sh"], {**self.env, **env})
        output = b""
        while True:
            try:
                chunk = os.read(terminal, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        os.close(terminal)
        return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]), output.decode().replace("\r\n", "\n")

    def alt_calls(self):
        path = self.tmp / "alt-calls"
        return path.read_text().splitlines() if path.exists() else []

    def downloads(self):
        path = self.tmp / "downloads"
        return path.read_text().splitlines() if path.exists() else []

    def test_installs_the_release_it_was_built_with_and_prints_next_steps(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[-1] for line in self.downloads()],
                         [f"{REPOSITORY}/releases/download/v0.1.0/altitude-v0.1.0.tar.gz",
                          f"{REPOSITORY}/releases/download/v0.1.0/install.py"])
        self.assertTrue(all("--proto =https" in line for line in self.downloads()))
        arguments = json.loads((self.tmp / "installer-args.json").read_text())
        self.assertEqual(arguments[0::2], ["--archive", "--sha256"])
        self.assertEqual(arguments[3], hashlib.sha256(b"fictional archive").hexdigest())
        self.assertEqual(Path(arguments[1]).parent.parent, self.tmp, "downloads stay in TMPDIR")
        out = result.stdout
        self.assertIn("Altitude v0.1.0 is installed and running in the background on this computer.", out)
        self.assertIn("Closing this terminal or the browser leaves it running.", out)
        self.assertIn("Fictional login service lifecycle.", out)
        self.assertIn("Name:    Altitude CA DEMO", out)
        self.assertIn("SHA-256: " + FINGERPRINT.split("=", 1)[1], out)
        self.assertIn("Certificate file: ~/.altitude/tls/ca.crt", out)
        self.assertNotIn(str(self.tmp / "home"), out)
        self.assertIn("1. Trust Altitude's certificate: run ~/.local/bin/alt trust on this computer,\n"
                      "     or follow the steps the page shows on another computer. Check it shows:", out)
        self.assertEqual(self.alt_calls(), [], "with no terminal to ask in, nothing is offered")
        self.assertIn("2. Open Altitude with this private link", out)
        self.assertIn("https://localhost:8443/projects#pair=ABCD-2345", out)
        self.assertIn("Works once, for 10 minutes. Keep it private.", out)
        self.assertIn("If it expires, run ~/.local/bin/alt pair.", out)
        self.assertLess(out.index("1. Trust"), out.index("2. Open Altitude"))
        self.assertLess(out.index("Address: https://localhost:8443 (opens only on this computer)"), out.index("Next:"))
        self.assertIn("For a phone or another computer, see Settings → Network.", out)
        self.assertNotIn("Over SSH", out)
        self.assertLess(out.index("private link"), out.index("Terminal (optional)"))
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', out)
        self.assertIn("alt doctor shows where Altitude runs and what is left to set up", out)
        self.assertIn(f"{REPOSITORY}/blob/v0.1.0/docs/SETUP.md#trust-https-on-each-device", out)
        self.assertEqual(list(self.tmp.glob("altitude-install.*")), [], "the download folder is removed")
        over_ssh = self.run_script(SSH_CONNECTION="192.0.2.10 50000 192.0.2.20 22").stdout
        tunnel = "Over SSH, from your own computer run `ssh -L 8443:127.0.0.1:8443 <this server>`, then open the link there."
        self.assertLess(over_ssh.index("2. Open Altitude"), over_ssh.index(tunnel))
        on_path = self.run_script(PATH=f"{self.bin}:{self.tmp}/home/.local/bin")
        self.assertNotIn("To use alt by name", on_path.stdout)
        self.assertIn("alt doctor shows where Altitude runs", on_path.stdout)

    def test_the_hosting_computer_is_asked_to_trust_the_certificate_in_its_terminal(self):
        status, out = self.run_in_terminal()
        self.assertEqual(status, 0, out)
        self.assertEqual(self.alt_calls(), ["trust", "terminal"], "alt trust asks in the terminal, not the piped script")
        self.assertLess(out.index("Fictional login service lifecycle."), out.index("Trust it? [y/N]"))
        self.assertLess(out.index("Trust it? [y/N]"), out.index("Next:"))
        self.assertIn("1. Restart your browser if it is open, then open Altitude with this private link. "
                      "It pairs this browser and opens First run:\n       https://localhost:8443/projects#pair=ABCD-2345", out)
        self.assertNotIn("Trust Altitude's certificate", out)
        self.assertNotIn("SHA-256", out)
        self.assertNotIn("2. Open", out)

        (self.tmp / "alt-calls").unlink()
        status, out = self.run_in_terminal(FIXTURE_TRUST="1")  # declined, or the trust step failed
        self.assertEqual((status, self.alt_calls()), (0, ["trust", "terminal"]), out)
        self.assertIn("1. Trust Altitude's certificate: run ~/.local/bin/alt trust on this computer,", out)
        self.assertIn("SHA-256: " + FINGERPRINT.split("=", 1)[1], out)
        self.assertIn("2. Open Altitude with this private link", out)

        (self.tmp / "alt-calls").unlink()
        status, out = self.run_in_terminal(SSH_CONNECTION="192.0.2.10 50000 192.0.2.20 22")
        self.assertEqual((status, self.alt_calls()), (0, []), "over SSH the browser runs on another computer")
        self.assertIn("1. Trust Altitude's certificate: run ~/.local/bin/alt trust on this computer,", out)

        (self.tmp / "external").touch()
        status, out = self.run_in_terminal()
        self.assertEqual((status, self.alt_calls()), (0, []), "alt trust trusts only a certificate Altitude created")
        self.assertIn("1. Trust Altitude's certificate in your browser. Before trusting it, check it shows:", out)

    def test_no_link_output_has_one_clear_recovery_path(self):
        script = (REPO / "scripts/install.sh").read_text()
        renderer = script.split('    "$python" - "$workdir/result.json"', 1)[1].split("\n", 1)[1].split("\nEOF", 1)[0]
        result_file = self.tmp / "result.json"
        base = {"version": "v0.1.0", "service": "running", "url": "https://localhost:8443", "trust": {}}
        for failed in (False, True):
            with self.subTest(issuance_failed=failed):
                result_file.write_text(json.dumps({**base, **({"pairing_error": "Run ~/.local/bin/alt pair to pair this browser."} if failed else {})}))
                rendered = subprocess.run([sys.executable, "-", str(result_file), "https://example.invalid/setup"],
                                          input=renderer, env=self.env, capture_output=True, text=True)
                self.assertEqual(rendered.returncode, 0, rendered.stderr)
                self.assertIn("is installed and running", rendered.stdout)
                self.assertIn("2. Open Altitude: https://localhost:8443", rendered.stdout)
                self.assertNotIn("#pair=", rendered.stdout)
                self.assertNotIn("your existing code", rendered.stdout)
                self.assertIn("not checked here", rendered.stdout)
                self.assertEqual(rendered.stdout.count("~/.local/bin/alt pair"), 1)
                if failed:
                    self.assertIn("Run ~/.local/bin/alt pair to pair this browser.", rendered.stdout)
                    self.assertNotIn("still-valid", rendered.stdout)
                    self.assertNotIn("Making a new code", rendered.stdout)
                else:
                    self.assertIn("use a still-valid code or run ~/.local/bin/alt pair", rendered.stdout)
                    self.assertIn("Making a new code replaces any earlier one.", rendered.stdout)

    def test_says_whether_altitude_keeps_running_without_a_login(self):
        refused = ("Altitude stops when alice's last login session ends and starts again only at the next login. "
                   "To keep it running after logout and start it at boot, run: sudo loginctl enable-linger alice")
        (self.tmp / "lingering.json").write_text(json.dumps({"enabled": False, "detail": refused}))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"\n{refused}\n", result.stdout)
        (self.tmp / "lingering.json").write_text("")  # a Mac: its LaunchAgent belongs to the desktop login
        self.assertNotIn("login session", self.run_script().stdout)

    def test_mismatched_download_runs_nothing(self):
        (self.release / "altitude-v0.1.0.tar.gz").write_bytes(b"substituted archive")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("altitude-v0.1.0.tar.gz does not match the checksum this v0.1.0 installer was built with",
                      result.stderr)
        self.assertFalse((self.tmp / "installer-args.json").exists())

    def test_failed_installer_stops_without_claiming_success(self):
        (self.release / "install.py").write_text("import sys\nsys.exit('Installation failed: fixture refusal')\n")
        digest = hashlib.sha256((self.release / "install.py").read_bytes()).hexdigest()
        self.script.write_text(build_release.install_script(
            (REPO / "scripts/install.sh").read_text(), "v0.1.0", REPOSITORY,
            hashlib.sha256(b"fictional archive").hexdigest(), digest))
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Installation failed: fixture refusal", result.stderr)
        self.assertIn("the installer stopped with the message above", result.stderr)
        self.assertNotIn("is installed", result.stdout)

    def test_a_mac_installs_through_the_same_verified_download(self):
        result = self.run_script(FIXTURE_SYSTEM="Darwin", FIXTURE_MACHINE="arm64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[-1] for line in self.downloads()],
                         [f"{REPOSITORY}/releases/download/v0.1.0/altitude-v0.1.0.tar.gz",
                          f"{REPOSITORY}/releases/download/v0.1.0/install.py"])
        self.assertIn("Altitude v0.1.0 is installed and running", result.stdout)
        self.assertNotIn("not yet validated", result.stdout)

    def test_a_host_releases_are_not_validated_on_installs_and_says_so_once(self):
        for env, host in (({"FIXTURE_MACHINE": "aarch64"}, "Linux aarch64"),
                          ({"FIXTURE_SYSTEM": "Darwin", "FIXTURE_MACHINE": "x86_64"}, "macOS x86_64")):
            with self.subTest(host):
                (self.tmp / "downloads").unlink(missing_ok=True)
                result = self.run_script(**env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.count("not yet validated"), 1)
                self.assertIn(f"Altitude is not yet validated on {host}; please report anything that does not work at "
                              f"{REPOSITORY}/issues.", result.stdout)
                self.assertIn("Altitude v0.1.0 is installed and running", result.stdout)
        result = self.run_script()
        self.assertNotIn("not yet validated", result.stdout)

    def test_mac_prerequisites_stop_with_the_fix_before_downloading(self):
        mac = {"FIXTURE_SYSTEM": "Darwin", "FIXTURE_MACHINE": "arm64"}
        cases = [({"FIXTURE_MACOS": "14.6.1"}, "needs macOS 15 or newer; this Mac runs macOS 14.6.1", "Software Update"),
                 ({"FIXTURE_OPENSSL": "LibreSSL 3.3.6"}, "OpenSSL 3 or newer was not found",
                  'brew install openssl@3), then run this again; no shell-profile change is needed'),
                 ({"FIXTURE_LAUNCHD": "1"}, "no logged-in desktop session of this account is reachable",
                  "Log in to this Mac's desktop")]
        for env, message, fix in cases:
            with self.subTest(message):
                result = self.run_script(**{**mac, **env})
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                if fix:
                    self.assertIn(fix, result.stderr)
                self.assertEqual(self.downloads(), [])
        (self.bin / "python3").unlink()
        self.fixture("python3", 'case "$*" in *exit*) exit 1 ;; *) echo 3.9.6 ;; esac')
        result = self.run_script(**mac)
        self.assertIn("Python 3.12 or newer was not found (3.9.6)", result.stderr)
        self.assertIn("brew install python@3.12", result.stderr)

    def test_unsupported_machines_and_missing_prerequisites_stop_with_the_fix(self):
        cases = [({"FIXTURE_SYSTEM": "FreeBSD"}, "runs on Linux with a systemd user manager or on macOS 15 or newer; "
                                                 "this machine runs FreeBSD."),
                 ({"FIXTURE_UID": "0"}, "not as root"),
                 ({"FIXTURE_SYSTEMD": "1"}, "no systemd user manager is reachable")]
        for env, message in cases:
            with self.subTest(message):
                result = self.run_script(**env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.downloads(), [])
        (self.bin / "python3").unlink()
        self.fixture("python3", 'case "$*" in *exit*) exit 1 ;; *) echo 3.10.12 ;; esac')
        result = self.run_script()
        self.assertIn("Python 3.12 or newer was not found (3.10.12)", result.stderr)
        self.assertIn("Install it first (with your distribution's package manager), then run this again.", result.stderr)
        for manager, hint in (("apt-get", "sudo apt install python3"), ("dnf", "sudo dnf install python3"),
                              ("pacman", "sudo pacman -S python"), ("zypper", "sudo zypper install python3")):
            with self.subTest(manager):
                self.fixture(manager, "exit 0")
                result = self.run_script()
                self.assertIn(f"Install it first ({hint}), then run this again.", result.stderr)
                self.assertNotIn("apt" if manager != "apt-get" else "dnf", result.stderr)
                (self.bin / manager).unlink()
        (self.bin / "python3").unlink()
        (self.bin / "python3").symlink_to(sys.executable)
        self.fixture("pacman", "exit 0")
        (self.bin / "curl").unlink()
        result = self.run_script()
        self.assertIn("curl is not installed.", result.stderr)
        self.assertIn("Install it first (sudo pacman -S curl), then run this again.", result.stderr)

    def test_the_unfilled_template_refuses(self):
        result = subprocess.run(["sh", str(REPO / "scripts/install.sh")], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("this is the release template", result.stderr)


class ReleaseNotes(unittest.TestCase):
    def test_notes_are_the_versions_dated_changelog_section(self):
        changelog = ("# Changelog\n\n## Unreleased\n\n- Next.\n\n## v0.1.1 — 2026-10-02\n\n- Fixed a fictional thing.\n\n"
                     "## v0.1.0 — 2026-10-01\n\n- First.\n")
        self.assertEqual(build_release.notes("v0.1.1", changelog), "- Fixed a fictional thing.\n")
        self.assertEqual(build_release.notes("v0.1.0", changelog), "- First.\n")
        for version in ("v0.1.2", "v0.1"):
            with self.assertRaisesRegex(ValueError, "no dated section"):
                build_release.notes(version, changelog)
        with self.assertRaisesRegex(ValueError, "no dated section"):
            build_release.notes("v0.2.0", "## v0.2.0\n\n- Undated.\n")


class ReleaseWorkflow(unittest.TestCase):
    """The job's gate: the tag's commit is on main and has a successful push `check` run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="release-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = make_repo(self.tmp / "repo")
        self.main = git("rev-parse", "HEAD", cwd=self.repo).strip()
        git("checkout", "-qb", "side", cwd=self.repo)
        (self.repo / "side").write_text("unmerged\n")
        git("add", "side", cwd=self.repo)
        git("commit", "-qm", "side", cwd=self.repo)
        self.side = git("rev-parse", "HEAD", cwd=self.repo).strip()
        git("fetch", "-q", "origin", cwd=self.repo)
        self.workflow = (REPO / ".github/workflows/release.yml").read_text()
        step = self.workflow.split("- name: Verify the approved commit")[1].split("- name:")[0]
        self.gate = textwrap.dedent(step.split("run: |\n")[1])
        commands = self.tmp / "commands"
        commands.mkdir()
        # gh answers as its --jq filter would: matching run ids, then that run's successful check job ids.
        (commands / "gh").write_text(f'#!/bin/sh\necho "$@" >> {self.tmp}/gh-args\n'
                                     'case "$2" in */jobs) printf "%s" "$FIXTURE_JOBS" ;; *) printf "%s" "$FIXTURE_RUNS" ;; esac\n')
        (commands / "gh").chmod(0o755)
        self.path = f"{commands}{os.pathsep}{os.environ['PATH']}"

    def verify(self, commit, runs="41\n", jobs="7\n", version="v0.1.0"):
        git("tag", "-f", version, commit, cwd=self.repo)
        env = {**os.environ, "PATH": self.path, "GITHUB_SHA": commit, "VERSION": version,
               "GITHUB_REPOSITORY": "example/altitude", "FIXTURE_RUNS": runs, "FIXTURE_JOBS": jobs}
        return subprocess.run(["bash", "-eo", "pipefail", "-c", self.gate], cwd=self.repo, env=env,
                              capture_output=True, text=True)

    def test_publishes_only_a_checked_commit_on_main(self):
        result = self.verify(self.main)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.tmp / "gh-args").read_text()
        self.assertIn(f"head_sha={self.main}&event=push&branch=main&status=success", calls)
        self.assertIn('select(.head_sha == $ENV.GITHUB_SHA)', calls)
        self.assertIn("repos/example/altitude/actions/runs/41/jobs", calls)
        self.assertIn('select(.name == "check" and .conclusion == "success")', calls)
        for runs, jobs in (("", "7\n"), ("41\n", "")):
            with self.subTest(runs=runs, jobs=jobs):
                unchecked = self.verify(self.main, runs=runs, jobs=jobs)
                self.assertNotEqual(unchecked.returncode, 0)
                self.assertIn("has no successful check job on main", unchecked.stdout)
        unmerged = self.verify(self.side)
        self.assertNotEqual(unmerged.returncode, 0)
        self.assertIn("does not point at a commit on main", unmerged.stdout)

    def test_job_holds_only_the_permissions_it_uses_and_uploads_every_release_file(self):
        self.assertIn("tags: ['v0.*']", self.workflow)
        self.assertIn("permissions: {}", self.workflow)
        job = self.workflow.split("jobs:")[1]
        self.assertIn("contents: write\n      actions: read\n      id-token: write\n      attestations: write", job)
        self.assertNotIn("secrets.", self.workflow)
        self.assertIn('"altitude-$VERSION.tar.gz" "altitude-$VERSION.tar.gz.sha256" install.py install.sh SHA256SUMS', job)
        self.assertIn("--notes-file", job)


if __name__ == "__main__":
    unittest.main()
