#!/usr/bin/env python3
"""Native acceptance body, launched only by test_installation_lifecycle.sh.

Application commands run from verified archives, outside the source checkout.
Only engine executables are fixtures; service control, TLS and recovery are real.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import re
import socket
import ssl
import subprocess
import sys
import tarfile
import time
from urllib.request import urlopen


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def failed_archive(package: Path, output: Path) -> tuple[Path, dict]:
    """A declared, checksum-valid startup failure, never a published artifact."""
    release = json.loads((package / "release.json").read_text())
    release["version"] = "v0.0.0-rc.3"
    (package / "bin/alt").write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
        "(Path.home() / 'results/failed-startup.json').write_text(json.dumps("
        "{'pid': os.getpid(), 'argv': sys.argv[1:], 'version': 'v0.0.0-rc.3'}))\n"
        "raise SystemExit('intentional lifecycle startup failure')\n")
    release["files"]["bin/alt"] = digest(package / "bin/alt")
    write_json(package / "release.json", release)
    with tarfile.open(output, "w:gz") as bundle:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                bundle.add(path, arcname=str(path.relative_to(package)), recursive=False)
    return output, release


class Lifecycle:
    def __init__(self, baseline: Path, candidate: Path, results: Path, expected_commit: str):
        self.baseline, self.candidate, self.results = baseline, candidate, results
        self.expected_commit = expected_commit
        self.home = Path.home()
        self.prefix = self.home / ".local/share/altitude"
        self.alt = self.home / ".local/bin/alt"
        self.tls = self.home / ".config/altitude/tls"
        self.settings = self.home / ".config/altitude/install.json"
        self.env = dict(os.environ)
        self.result = {"passed": False, "steps": [], "artifacts": [], "limits": [
            "Same-source version transition; no cross-release storage migration",
            "No public download/bootstrap, minimal OS, reboot/login/logout or device trust acceptance",
            "No live provider, native worker confinement or macOS acceptance",
        ]}
        self.sequence = 0

    def run(self, label: str, *command, success: bool = True, timeout: int = 90):
        self.sequence += 1
        path = self.results / f"{self.sequence:02d}-{label}.log"
        command = list(map(str, command))
        started = time.monotonic()
        try:
            proc = subprocess.run(command, cwd=self.home, env=self.env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            path.write_text(f"Timed out after {timeout}s: {command}\n{exc.stdout!r}\n")
            raise
        path.write_text(f"argv: {command}\nexit: {proc.returncode}\n{proc.stdout}")
        self.result["steps"].append({"step": label, "exit": proc.returncode,
                                     "seconds": round(time.monotonic() - started, 2)})
        print(f"{label}: exit {proc.returncode}", flush=True)
        if success != (proc.returncode == 0):
            raise AssertionError(f"{label}: unexpected exit {proc.returncode}; see {path.name}")
        return proc.stdout

    def archive(self, folder: Path, name: str):
        archives = list(folder.glob("altitude-*.tar.gz"))
        assert len(archives) == 1, f"{folder}: supply exactly one built release"
        archive = archives[0]
        checksum = (folder / (archive.name + ".sha256")).read_text().strip()
        assert digest(archive) == checksum, "Input checksum mismatch"
        # Import the release's standalone verifier, not application source.
        spec = importlib.util.spec_from_file_location("archive_installer", folder / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        package = self.home / f"{name}-package"
        release = installer.extract(archive, checksum, package)
        assert release["commit"] == self.expected_commit, "Archive commit differs from selected source"
        assert digest(folder / "install.py") == digest(package / "altitude/installation.py")
        write_json(self.results / f"{name}-manifest.json", release)
        self.result["artifacts"].append({"kind": name, "filename": archive.name,
                                         "sha256": checksum, "installer_sha256": digest(folder / "install.py"),
                                         "version": release["version"], "commit": release["commit"]})
        return archive, checksum, package, release

    def healthy(self, label: str, release: dict):
        native = json.loads(self.run(label + "-service", self.alt, "service", "status"))
        assert native["ActiveState"] == "active", native
        assert native["UnitFileState"] == "enabled", native
        context = ssl.create_default_context(cafile=str(self.tls / "ca.crt"))
        base = f"https://127.0.0.1:{self.env['ALTITUDE_PORT']}"
        with urlopen(base + "/api/health", context=context, timeout=10) as response:
            health = json.load(response)
        assert (health["version"], health["commit"], str(health["pid"])) == (
            release["version"], release["commit"], native["MainPID"]), health
        with urlopen(base + "/", context=context, timeout=10) as response:
            html = response.read()
        assert hashlib.sha256(html).hexdigest() == release["files"]["web/dist/index.html"]
        assets = re.findall(r'(?:src|href)="(/assets/[^"?#]+)', html.decode())
        assert assets, "Built UI references no assets"
        for asset in assets:
            with urlopen(base + asset, context=context, timeout=10) as response:
                assert hashlib.sha256(response.read()).hexdigest() == release["files"]["web/dist" + asset]
        write_json(self.results / f"{label}-health.json", {**health, "verified_assets": assets})
        assert self.prefix.joinpath("current").resolve().name == release["version"]
        assert not (self.prefix / "pending.json").exists()
        return health

    def doctor(self, label: str, release: dict):
        report = json.loads(self.run(label + "-doctor", self.alt, "doctor"))
        write_json(self.results / f"{label}-doctor.json", report)
        assert report["version"] == release["version"]
        checks = {item["name"]: item for item in report["checks"]}
        assert checks["Python"]["state"] == checks["user service"]["state"] == "tested"
        assert all(checks[name]["state"] == "configured" for name in ("git", "gh", "openssl"))
        assert checks["GitHub authentication"]["state"] == "unknown", "No GitHub credentials belong here"
        assert report["certificate_trust"]["state"] == "unknown", report
        assert report["engines"] and all(item["available"] is None for item in report["engines"])
        assert report["engine_access"].startswith("unknown")

    def exercise(self):
        assert os.getuid() != 0 and pwd.getpwuid(os.getuid()).pw_name.startswith("alt-install-")
        assert self.home.parent.name.startswith("altitude-installation.")
        assert not self.prefix.exists() and not self.settings.exists(), "Needs a fresh disposable account"
        write_json(self.results / "environment.json", {
            "os_release": Path("/etc/os-release").read_text(), "kernel": platform.release(),
            "architecture": platform.machine(), "python": platform.python_version(),
            "uid": os.getuid(), "home": str(self.home),
        })
        old, old_sha, package, before = self.archive(self.baseline, "baseline")
        new, new_sha, new_package, after = self.archive(self.candidate, "candidate")
        assert before["version"] != after["version"] and after["version"] != "v0.0.0-rc.3"
        self.run("user-manager", "systemctl", "--user", "show", "--property=Version")
        self.run("host-tools", "/usr/bin/python3", "--version")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.env.update(ALTITUDE_HOME=str(self.home / ".altitude"), ALTITUDE_PORT=str(port),
                        ALTITUDE_HOST="127.0.0.1", ALTITUDE_TLS_DIR=str(self.tls),
                        ALTITUDE_CONFIG=str(self.settings), ALTITUDE_ROOTS=str(self.home / "Projects"),
                        PYTHONDONTWRITEBYTECODE="1")
        fixture = self.home / "fixture-engine"
        fixture.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$HOME/results/fixture-engine.log\"\n"
                           "echo 'Fictional engine: unauthenticated, no provider requests' >&2\nexit 1\n")
        fixture.chmod(0o755)
        # Discover configured executable settings from the packaged engine seam.
        # No application module is mocked or patched for native acceptance.
        keys = json.loads(self.run("engine-settings", "/usr/bin/python3", "-B", "-c",
            "import json,sys; sys.path.insert(0,sys.argv[1]); from altitude import config; "
            "print(json.dumps([k for k in vars(config) if k.endswith('_BIN')]))", package))
        assert keys, "Package exposes no engine executable settings"
        self.env.update({key: str(fixture) for key in keys})
        self.run("install", "/usr/bin/python3", "-B", self.baseline / "install.py",
                 "--archive", old, "--sha256", old_sha)
        initial = self.healthy("installed", before)
        self.doctor("installed", before)
        # Keep projects unregistered: no task or coordinator may start in this test.
        sentinels = [self.home / ".altitude/fictional/history.jsonl",
                     self.home / "Projects/fictional/worktree/notes.txt",
                     self.home / ".fixture-provider/sessions/session.json"]
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Fictional retained installation acceptance data\n')
        retained = {path: digest(path) for path in [*sentinels, self.settings, *self.tls.glob("*")] if path.is_file()}
        self.run("update", self.alt, "update", "--archive", new, "--sha256", new_sha)
        updated = self.healthy("updated", after)
        assert updated["pid"] != initial["pid"], "Update did not replace the daemon"
        self.doctor("updated", after)
        bad, broken = failed_archive(new_package, self.home / "failed-startup.tar.gz")
        write_json(self.results / "failure-manifest.json", broken)
        self.result["artifacts"].append({"kind": "failure-injection", "sha256": digest(bad),
            "version": broken["version"], "commit": broken["commit"],
            "derived_from_sha256": new_sha, "change": "bin/alt records its daemon invocation then exits; manifest rehashed"})
        failure = self.run("failed-update", self.alt, "update", "--archive", bad, "--sha256", digest(bad), success=False)
        assert "previous installation restored" in failure, failure
        marker = json.loads((self.results / "failed-startup.json").read_text())
        assert marker["argv"] == ["serve"] and marker["version"] == broken["version"] and marker["pid"] > 0
        self.healthy("recovered", after)
        self.doctor("recovered", after)
        assert (self.prefix / "versions" / broken["version"]).is_dir()
        assert all(digest(path) == value for path, value in retained.items()), "Update/recovery changed retained data"
        removed = json.loads(self.run("uninstall", self.alt, "uninstall"))
        assert removed["uninstalled"] and not removed["application_retained_for_project_hooks"]
        assert not self.alt.exists() and not (self.prefix / "current").exists()
        assert not (self.prefix / "versions").exists()
        assert not (self.home / ".config/systemd/user/altitude.service").exists()
        stopped = self.run("uninstalled-service", "systemctl", "--user", "show", "altitude.service",
                           "--property=LoadState,ActiveState,MainPID")
        assert "LoadState=not-found" in stopped and "ActiveState=inactive" in stopped and "MainPID=0" in stopped
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", port)) != 0, "Uninstalled service still listens"
        assert all(digest(path) == value for path, value in retained.items()), "Uninstall changed retained data"
        self.result["retained_files"] = [str(path.relative_to(self.home)) for path in retained]
        self.result["passed"] = True

    def execute(self):
        try:
            self.exercise()
        except Exception as exc:
            self.result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            write_json(self.results / "result.json", self.result)


if __name__ == "__main__":
    Lifecycle(*(Path(arg).resolve() for arg in sys.argv[1:4]), sys.argv[4]).execute()
