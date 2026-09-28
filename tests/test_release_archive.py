"""An exact committed source builds one verified, checkout-free application artifact."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from unittest import mock

from tests.support import AltitudeCase, REPO, git, make_repo
from altitude import installation
from scripts import build_release


class ReleaseArchive(AltitudeCase):
    def test_standalone_tls_preparation_imports_only_the_verified_archive_without_installing(self):
        package = self.tmp / "package"
        for name in installation.REQUIRED:
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        (package / "altitude/source_tls.py").write_text(
            "def prepare(directory, *, apply=False):\n"
            "    return {'directory': str(directory), 'applied': apply, 'fixture': 'verified archive'}\n")
        release = {"version": "v0.1.0-rc.1", "commit": "a" * 40, "files": {
            str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in package.rglob("*") if path.is_file()}}
        (package / "release.json").write_text(json.dumps(release))
        archive = self.tmp / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for path in package.rglob("*"):
                if path.is_file():
                    bundle.add(path, arcname=str(path.relative_to(package)), recursive=False)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        installer = self.tmp / "install.py"
        shutil.copyfile(REPO / "altitude/installation.py", installer)
        command = [sys.executable, "-B", str(installer), "--archive", str(archive),
                   "--sha256", checksum, "--prepare-source-tls", str(self.tmp / "existing-tls")]
        env = {key: value for key, value in os.environ.items() if key != "ALTITUDE_ACTOR"}
        for applied in (False, True):
            result = subprocess.run(command + (["--apply"] if applied else []), cwd=self.tmp,
                                    env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), {
                "directory": str(self.tmp / "existing-tls"), "applied": applied, "fixture": "verified archive"})
        command[command.index(checksum)] = "0" * 64
        result = subprocess.run(command, cwd=self.tmp, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertNotIn("verified archive", result.stdout)
        self.assertFalse((package / "__pycache__").exists())
        (package / "altitude/source_tls.py").unlink()
        del release["files"]["altitude/source_tls.py"]
        (package / "release.json").write_text(json.dumps(release))
        with tarfile.open(archive, "w:gz") as bundle:
            for path in package.rglob("*"):
                if path.is_file():
                    bundle.add(path, arcname=str(path.relative_to(package)), recursive=False)
        command[command.index("0" * 64)] = hashlib.sha256(archive.read_bytes()).hexdigest()
        result = subprocess.run(command, cwd=self.tmp, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("obtain a newer reviewed archive", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_builder_packages_committed_cli_daemon_resources_and_built_ui(self):
        make_repo(self.repo)
        for name in ("altitude", "bin", "personas", "hooks", "schemas", "templates"):
            shutil.copytree(REPO / name, self.repo / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (self.repo / "web").mkdir()
        (self.repo / "web/package.json").write_text('{}\n')
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "scripts/install.sh"):
            (self.repo / name).parent.mkdir(exist_ok=True)
            (self.repo / name).write_text((REPO / name).read_text())
        git("add", ".", cwd=self.repo)
        git("commit", "-m", "Fictional release source", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/example/altitude.git", cwd=self.repo)
        expected = git("rev-parse", "HEAD", cwd=self.repo).strip()
        native_run = subprocess.run
        builds = []

        def run(command, **kwargs):
            if command[0] != "pnpm":
                return native_run(command, **kwargs)
            builds.append(command[1:])
            if command[1] == "build":
                dist = Path(kwargs["cwd"]) / "dist"
                (dist / "assets").mkdir(parents=True)
                (dist / "index.html").write_text('<script src="/assets/app.js"></script>')
                (dist / "assets/app.js").write_text("/* deterministic built UI fixture */")
            return subprocess.CompletedProcess(command, 0)

        output = self.tmp / "artifacts"
        with mock.patch.object(build_release, "REPO", self.repo), mock.patch.object(subprocess, "run", side_effect=run):
            archive = build_release.build("v0.1.0-rc.1", output)
            with self.assertRaisesRegex(ValueError, "already exists"):
                build_release.build("v0.1.0-rc.1", output)
        checksum = (output / (archive.name + ".sha256")).read_text().strip()
        self.assertEqual(checksum, hashlib.sha256(archive.read_bytes()).hexdigest())
        package = self.tmp / "package"
        package.mkdir()
        release = installation.extract(archive, checksum, package)
        self.assertEqual((release["commit"], release["repository"]), (expected, "https://github.com/example/altitude"))
        self.assertEqual(builds, [["install", "--frozen-lockfile"], ["build"]])
        self.assertEqual((output / "install.py").read_bytes(), (package / "altitude/installation.py").read_bytes())
        installer = hashlib.sha256((output / "install.py").read_bytes()).hexdigest()
        script = (output / "install.sh").read_text()
        self.assertIn("VERSION='v0.1.0-rc.1'", script)
        self.assertIn("REPOSITORY='https://github.com/example/altitude'", script)
        self.assertIn(f"ARCHIVE_SHA256='{checksum}'", script)
        self.assertIn(f"INSTALLER_SHA256='{installer}'", script)
        self.assertNotIn("@", script.split("case")[0].split("set -eu")[1])
        self.assertTrue(os.access(output / "install.sh", os.X_OK))
        self.assertEqual((output / "SHA256SUMS").read_text().splitlines(), [
            f"{checksum}  {archive.name}", f"{installer}  install.py",
            f"{hashlib.sha256(script.encode()).hexdigest()}  install.sh"])
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            self.assertEqual((package / name).read_text(), (REPO / name).read_text())
        self.assertFalse((package / ".git").exists())
        self.assertFalse((package / "web/node_modules").exists())
        (self.repo / "altitude/config.py").write_text("uncommitted change")
        with mock.patch.object(build_release, "REPO", self.repo):
            with self.assertRaisesRegex(ValueError, "committed, clean"):
                build_release.build("v0.1.1", self.tmp / "rejected")
        with mock.patch.object(build_release, "REPO", self.repo):
            with self.assertRaisesRegex(ValueError, "committed, clean"):
                build_release.build("v0.1.1", self.tmp / "rejected", expected)
        # An earlier named commit builds exactly that commit, whatever the checkout holds.
        (self.repo / "altitude/config.py").write_text("later commit")
        git("commit", "-qam", "Later change", cwd=self.repo)
        (self.repo / "altitude/config.py").write_text("uncommitted change")
        with mock.patch.object(build_release, "REPO", self.repo), mock.patch.object(subprocess, "run", side_effect=run):
            named = build_release.build("v0.1.1", self.tmp / "named", "HEAD~1")
        named_package = self.tmp / "named-package"
        named_package.mkdir()
        release = installation.extract(named, (self.tmp / "named" / (named.name + ".sha256")).read_text().strip(), named_package)
        self.assertEqual(release["commit"], expected)
        self.assertEqual((named_package / "altitude/config.py").read_text(), (REPO / "altitude/config.py").read_text())
