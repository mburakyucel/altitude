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
FINGERPRINT = "sha256 Fingerprint=AA:BB:CC"


class InstallScript(unittest.TestCase):
    """install.sh with fixture commands on PATH: curl serves a local release, systemctl answers as told."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-sh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.release = self.tmp / "release"
        self.release.mkdir()
        (self.release / "altitude-v0.1.0.tar.gz").write_bytes(b"fictional archive")
        (self.release / "install.py").write_text(textwrap.dedent(f"""\
            import json, pathlib, sys
            pathlib.Path({str(self.tmp / 'installer-args.json')!r}).write_text(json.dumps(sys.argv[1:]))
            print(json.dumps({{"version": "v0.1.0", "service": "running", "url": "https://localhost:8443",
                              "trust": {{"ca_cert": "/home/fictional/.altitude/tls/ca.crt", "ca_sha256": {FINGERPRINT!r}}}}}))
            """))
        digest = {name: hashlib.sha256((self.release / name).read_bytes()).hexdigest()
                  for name in ("altitude-v0.1.0.tar.gz", "install.py")}
        self.script = self.tmp / "install.sh"
        self.script.write_text(build_release.install_script((REPO / "scripts/install.sh").read_text(), "v0.1.0",
                                                            REPOSITORY, digest["altitude-v0.1.0.tar.gz"], digest["install.py"]))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("sh", "cat", "cp", "cut", "sha256sum", "mktemp", "rm", "openssl"):
            (self.bin / tool).symlink_to(subprocess.check_output(["sh", "-c", f"command -v {tool}"], text=True).strip())
        (self.bin / "python3").symlink_to(sys.executable)
        self.fixture("uname", 'case "$1" in -s) echo "${FIXTURE_SYSTEM:-Linux}" ;; -m) echo "${FIXTURE_MACHINE:-x86_64}" ;; esac')
        self.fixture("id", 'echo "${FIXTURE_UID:-1000}"')
        self.fixture("systemctl", 'exit "${FIXTURE_SYSTEMD:-0}"')
        self.fixture("sw_vers", 'echo 15.5')
        self.fixture("xcode-select", 'exit 1')
        self.fixture("curl", f'echo "$@" >> {self.tmp}/downloads\n'
                             'for last; do :; done\n'
                             'while [ "$1" != -o ]; do shift; done\n'
                             f'cp "{self.release}/${{last##*/}}" "$2"')
        self.env = {"PATH": str(self.bin), "HOME": str(self.tmp / "home"), "TMPDIR": str(self.tmp)}

    def fixture(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, **env):
        # The documented form: the script arrives on standard input.
        return subprocess.run(["sh"], input=self.script.read_text(), env={**self.env, **env},
                              capture_output=True, text=True)

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
        self.assertIn("Altitude v0.1.0 is installed and its service is running.", result.stdout)
        self.assertIn("Address: https://localhost:8443", result.stdout)
        self.assertIn("Its fingerprint: AA:BB:CC", result.stdout)
        self.assertIn('1. Put alt on your PATH', result.stdout)
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', result.stdout)
        self.assertIn("2. Run: alt doctor", result.stdout)
        self.assertIn(f"{REPOSITORY}/blob/v0.1.0/docs/SETUP.md#trust-https-on-each-device", result.stdout)
        self.assertEqual(list(self.tmp.glob("tmp.*")), [], "the download folder is removed")
        on_path = self.run_script(PATH=f"{self.bin}:{self.tmp}/home/.local/bin")
        self.assertNotIn("Put alt on your PATH", on_path.stdout)
        self.assertIn("1. Run: alt doctor", on_path.stdout)

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

    def test_a_mac_stops_with_what_it_found_before_downloading(self):
        result = self.run_script(FIXTURE_SYSTEM="Darwin", FIXTURE_MACHINE="arm64")
        self.assertEqual(result.returncode, 1)
        version = ".".join(map(str, sys.version_info[:3]))
        self.assertIn("Altitude cannot be installed on this Mac yet. Nothing was installed or changed.", result.stderr)
        self.assertIn(f"Detected: macOS 15.5 on arm64; Python {version} at {self.bin}/python3", result.stderr)
        self.assertIn("native macOS runtime", result.stderr)
        self.assertIn(f"Follow macOS support: {REPOSITORY}/issues/225", result.stderr)
        self.assertEqual(self.downloads(), [])
        (self.bin / "python3").unlink()
        self.fixture("python3", 'case "$*" in *exit*) exit 1 ;; *) echo 3.9.6 ;; esac')
        result = self.run_script(FIXTURE_SYSTEM="Darwin", FIXTURE_MACHINE="arm64")
        self.assertIn("Python 3.12 or newer not found (3.9.6)", result.stderr)

    def test_unsupported_machines_and_missing_prerequisites_stop_with_the_fix(self):
        cases = [({"FIXTURE_MACHINE": "aarch64"}, "runs on Linux x86_64; this machine is Linux aarch64"),
                 ({"FIXTURE_SYSTEM": "FreeBSD"}, "this machine is FreeBSD x86_64"),
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
        self.assertIn("sudo apt install python3", result.stderr)

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
        (commands / "gh").write_text(f'#!/bin/sh\necho "$@" > {self.tmp}/gh-args\necho "$FIXTURE_RUNS"\n')
        (commands / "gh").chmod(0o755)
        self.path = f"{commands}{os.pathsep}{os.environ['PATH']}"

    def verify(self, commit, runs="1", version="v0.1.0"):
        git("tag", "-f", version, commit, cwd=self.repo)
        env = {**os.environ, "PATH": self.path, "GITHUB_SHA": commit, "VERSION": version,
               "GITHUB_REPOSITORY": "example/altitude", "FIXTURE_RUNS": runs}
        return subprocess.run(["bash", "-eo", "pipefail", "-c", self.gate], cwd=self.repo, env=env,
                              capture_output=True, text=True)

    def test_publishes_only_a_checked_commit_on_main(self):
        result = self.verify(self.main)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"head_sha={self.main}&event=push&status=success", (self.tmp / "gh-args").read_text())
        unchecked = self.verify(self.main, runs="0")
        self.assertNotEqual(unchecked.returncode, 0)
        self.assertIn("has no successful check run on main", unchecked.stdout)
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
