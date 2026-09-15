"""An exact committed source builds one verified, checkout-free application artifact."""
import hashlib
from pathlib import Path
import shutil
import subprocess
from unittest import mock

from tests.support import AltitudeCase, REPO, git, make_repo
from altitude import installation
from scripts import build_release


class ReleaseArchive(AltitudeCase):
    def test_builder_packages_committed_cli_daemon_resources_and_built_ui(self):
        make_repo(self.repo)
        for name in ("altitude", "bin", "personas", "hooks", "schemas", "templates"):
            shutil.copytree(REPO / name, self.repo / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (self.repo / "web").mkdir()
        (self.repo / "web/package.json").write_text('{}\n')
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
        self.assertFalse((package / ".git").exists())
        self.assertFalse((package / "web/node_modules").exists())
        (self.repo / "altitude/config.py").write_text("uncommitted change")
        with mock.patch.object(build_release, "REPO", self.repo):
            with self.assertRaisesRegex(ValueError, "committed, clean"):
                build_release.build("v0.1.1", self.tmp / "rejected")
