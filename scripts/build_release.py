#!/usr/bin/env python3
"""Build a private application archive from one clean committed source revision. No publication."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from altitude.installation import VERSION, metadata


def build(version: str, output: Path) -> Path:
    if not VERSION.fullmatch(version):
        raise ValueError("Use an immutable v0.MINOR.PATCH or v0.MINOR.PATCH-rc.N version")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Build a committed, clean release source; review and commit changes first")
    commit = git("rev-parse", "HEAD")
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
    (output / (archive.name + ".sha256")).write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(build(arguments.version, arguments.output))
