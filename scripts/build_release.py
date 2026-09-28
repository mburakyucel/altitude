#!/usr/bin/env python3
"""Build the release files from one clean committed source revision: the application archive, its
checksum, the standalone installer, the one-command install script and SHA256SUMS. No publication."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from altitude.installation import VERSION, metadata


def build(version: str, output: Path, source: str = "HEAD") -> Path:
    if not VERSION.fullmatch(version):
        raise ValueError("Use an immutable v0.MINOR.PATCH or v0.MINOR.PATCH-rc.N version")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()
    commit = git("rev-parse", "--verify", f"{source}^{{commit}}")
    # Another commit is exported as committed; building the checked-out commit must not hide edits made on top of it.
    if commit == git("rev-parse", "HEAD") and git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Build a committed, clean release source; review and commit changes first")
    origin = git("remote", "get-url", "origin")
    from altitude.server import repository_url
    repository = repository_url(origin)
    if not repository:
        raise ValueError("Release source needs its GitHub origin identity")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"altitude-{version}.tar.gz"
    if archive.exists():
        raise ValueError("Output version already exists; do not overwrite immutable release artifacts")
    with tempfile.TemporaryDirectory(prefix="altitude-release-") as folder:
        temp = Path(folder)
        source = temp / "source"
        source.mkdir()
        export = temp / "source.tar"
        subprocess.run(["git", "archive", "--format=tar", f"--output={export}", commit], cwd=REPO, check=True)
        with tarfile.open(export) as contents:
            contents.extractall(source, filter="data")
        subprocess.run(["pnpm", "install", "--frozen-lockfile"], cwd=source / "web", check=True)
        subprocess.run(["pnpm", "build"], cwd=source / "web", check=True)
        package = temp / "package"
        package.mkdir()
        for name in ("altitude", "bin", "personas", "hooks", "schemas", "templates"):
            shutil.copytree(source / name, package / name)
        shutil.copytree(source / "web/dist", package / "web/dist")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copyfile(source / name, package / name)
        files = {str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(package.rglob("*")) if path.is_file()}
        (package / "release.json").write_text(json.dumps({"version": version, "commit": commit,
                                                         "repository": repository, "files": files}, indent=2) + "\n")
        metadata(package)
        with tarfile.open(archive, "w:gz") as bundle:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    bundle.add(path, arcname=str(path.relative_to(package)), recursive=False)
        shutil.copyfile(source / "altitude/installation.py", output / "install.py")
        script = (source / "scripts/install.sh").read_text()
    digests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (archive, output / "install.py")}
    (output / (archive.name + ".sha256")).write_text(digests[archive.name] + "\n")
    script = install_script(script, version, repository, digests[archive.name], digests["install.py"])
    (output / "install.sh").write_text(script)
    (output / "install.sh").chmod(0o755)
    digests["install.sh"] = hashlib.sha256(script.encode()).hexdigest()
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(digests.items())))
    return archive


def install_script(template: str, version: str, repository: str, archive_sha256: str, installer_sha256: str) -> str:
    """The one-command install script for this release, carrying the checksums of what it downloads."""
    for name, value in (("VERSION", version), ("REPOSITORY", repository),
                        ("ARCHIVE_SHA256", archive_sha256), ("INSTALLER_SHA256", installer_sha256)):
        template = template.replace(f"@{name}@", value)
    return template


def notes(version: str, changelog: str) -> str:
    """The release notes: CHANGELOG's dated section for exactly this version."""
    match = re.search(rf"^## {re.escape(version)} \u2014 \d{{4}}-\d{{2}}-\d{{2}}\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not match or not match.group(1).strip():
        raise ValueError(f"CHANGELOG.md has no dated section '## {version} \u2014 YYYY-MM-DD' with notes")
    return match.group(1).strip() + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", default="HEAD", help="committed revision to build and take notes from (default: the clean checkout)")
    parser.add_argument("--notes", type=Path, help="also write this version's CHANGELOG section here; required to publish")
    arguments = parser.parse_args()
    built = build(arguments.version, arguments.output, arguments.source)
    if arguments.notes:
        changelog = subprocess.check_output(["git", "show", f"{arguments.source}:CHANGELOG.md"], cwd=REPO, text=True)
        arguments.notes.write_text(notes(arguments.version, changelog))
    print(built)
