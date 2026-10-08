#!/usr/bin/env python3
"""Native acceptance body, launched only by test_installation_lifecycle.sh, or on a Mac by installation_mac.py.

Application commands run from verified archives, outside the source checkout.
Only engine executables are fixtures; service control, TLS and recovery are real.
The whole lifecycle runs in one invocation; `reboot-install` and `reboot-verify` split an install from
its check after the VM restarts. `recovery` installs the candidate over a baseline whose installation
failed, after the documented cleanup. `bootstrap` runs the built install.sh through its public curl | sh command against a release
server on this machine's loopback, whose name the root wrapper points here. `update` installs the baseline while that server
answers for GitHub's release list and downloads, and the app's Update request must carry it to the candidate. `mac` runs the whole macOS lifecycle under the throwaway HOME installation_mac.py gives it (see
MacLifecycle).
"""
from __future__ import annotations

import fcntl
import functools
import hashlib
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import pty
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def following(version: str) -> str:
    """The next release candidate after VERSION, so an update to it is never a downgrade."""
    match = re.fullmatch(r"(v0\.\d+\.\d+)(?:-rc\.(\d+))?", version)
    base, candidate = match.group(1), match.group(2)
    if candidate:
        return f"{base}-rc.{int(candidate) + 1}"
    minor, patch = base[3:].split(".")
    return f"v0.{minor}.{int(patch) + 1}-rc.1"


def failed_archive(package: Path, output: Path) -> tuple[Path, dict]:
    """A declared, checksum-valid startup failure, never a published artifact."""
    return synthetic_archive(package, output, following(json.loads((package / "release.json").read_text())["version"]),
                             failing=True)


def synthetic_archive(package: Path, output: Path, version: str, *, failing: bool = False) -> tuple[Path, dict]:
    """PACKAGE as a checksum-valid release of VERSION, never a published artifact; with FAILING its startup exits."""
    release = json.loads((package / "release.json").read_text())
    release["version"] = version
    if failing:
        (package / "bin/alt").write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
            "(Path.home() / 'results/failed-startup.json').write_text(json.dumps("
            f"{{'pid': os.getpid(), 'argv': sys.argv[1:], 'version': {release['version']!r}}}))\n"
            "raise SystemExit('intentional lifecycle startup failure')\n")
        release["files"]["bin/alt"] = digest(package / "bin/alt")
    write_json(package / "release.json", release)
    with tarfile.open(output, "w:gz") as bundle:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                bundle.add(path, arcname=str(path.relative_to(package)), recursive=False)
    return output, release


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


class Lifecycle:
    def __init__(self, baseline: Path, candidate: Path, results: Path, commits: str):
        self.baseline, self.candidate, self.results = baseline, candidate, results
        self.state = results / "reboot-state.json"  # what reboot-verify needs from reboot-install
        # BASELINE..CANDIDATE when the baseline is a published release; one commit when both share a source.
        baseline_commit, _, candidate_commit = commits.rpartition("..")
        self.expected = {"baseline": baseline_commit or candidate_commit, "candidate": candidate_commit}
        published = self.expected["baseline"] != self.expected["candidate"]
        self.home = Path.home()
        self.prefix = self.home / ".local/share/altitude"
        self.alt = self.home / ".local/bin/alt"
        self.tls = self.home / ".config/altitude/tls"
        self.settings = self.home / ".config/altitude/install.json"
        self.env = dict(os.environ)
        self.result = {"passed": False, "steps": [], "artifacts": [], "limits": [
            "Published-release baseline updated to the candidate; the guest runs the published files offline, not its own GitHub download; "
            "no storage migration (no application state is created)"
            if published else "Same-source version transition; no cross-release storage migration",
            "No public download/bootstrap, minimal OS, login/logout or device trust acceptance",
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
        assert release["commit"] == self.expected[name], f"{name} archive commit differs from its selected source"
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

    def disposable(self):
        assert os.getuid() != 0 and pwd.getpwuid(os.getuid()).pw_name.startswith("alt-install-")
        assert self.home.parent.name.startswith("altitude-installation.")

    def prepare(self):
        """Verify both archives and configure the fixture engines on a fresh disposable account."""
        self.disposable()
        assert not self.prefix.exists() and not self.settings.exists(), "Needs a fresh disposable account"
        write_json(self.results / "environment.json", {
            "os_release": Path("/etc/os-release").read_text(), "kernel": platform.release(),
            "architecture": platform.machine(), "python": platform.python_version(),
            "uid": os.getuid(), "home": str(self.home),
        })
        old, old_sha, package, before = self.archive(self.baseline, "baseline")
        new, new_sha, new_package, after = self.archive(self.candidate, "candidate")
        assert before["version"] != after["version"]
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
        # Both packages count: a candidate that adds an engine must still reach only the fixture.
        keys = {key for step, source in (("engine-settings", package), ("candidate-engine-settings", new_package))
                for key in json.loads(self.run(step, "/usr/bin/python3", "-B", "-c",
                    "import json,sys; sys.path.insert(0,sys.argv[1]); from altitude import config; "
                    "print(json.dumps([k for k in vars(config) if k.endswith('_BIN')]))", source))}
        assert keys, "Package exposes no engine executable settings"
        self.env.update({key: str(fixture) for key in keys})
        return old, old_sha, package, before, new, new_sha, new_package, after

    def install(self, archive: Path, checksum: str):
        self.run("install", "/usr/bin/python3", "-B", self.baseline / "install.py", "--archive", archive, "--sha256", checksum)

    def exercise(self):
        old, old_sha, package, before, new, new_sha, new_package, after = self.prepare()
        self.install(old, old_sha)
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
        self.uninstall(retained)

    def uninstall(self, retained: dict):
        port = int(self.env["ALTITUDE_PORT"])
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

    def reboot_install(self):
        """Install and leave the service running for the machine to restart."""
        old, old_sha, _, before, *_ = self.prepare()
        self.install(old, old_sha)
        initial = self.healthy("installed", before)
        self.doctor("installed", before)
        write_json(self.state, {"env": self.env, "release": before, "pid": initial["pid"], "boot_id": boot_id()})
        self.result["passed"] = True

    def reboot_verify(self):
        """After the restart, the user manager started the same installation without anyone logging in."""
        self.disposable()
        state = json.loads(self.state.read_text())
        self.env.update(state["env"])
        assert boot_id() != state["boot_id"], "The machine did not restart"
        deadline = time.monotonic() + 90  # the user manager starts the service during boot, unattended
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", int(self.env["ALTITUDE_PORT"]))) == 0:
                    break
            time.sleep(1)
        started = self.healthy("rebooted", state["release"])
        assert started["pid"] != state["pid"]
        self.doctor("rebooted", state["release"])
        self.uninstall({})
        self.result["passed"] = True

    def recovery(self):
        """A baseline whose activation fails, the documented cleanup, then the candidate's installer over what it left.

        The published v0.1.0-rc.1 fails at service start and leaves its refused unit and an interrupted activation;
        after docs/SETUP.md's steps, the candidate must install and keep its settings, TLS identity and data."""
        old, old_sha, _, _, new, new_sha, _, after = self.prepare()
        self.result["limits"][0] = ("Published-release baseline whose installation fails, docs/SETUP.md's cleanup, then the candidate installed over it; "
                                    "the guest runs the published files offline, not its own GitHub download; "
                                    "the candidate installs with its install.py, which its install.sh downloads and runs")
        self.run("failed-install", "/usr/bin/python3", "-B", self.baseline / "install.py", "--archive", old,
                 "--sha256", old_sha, success=False, timeout=180)
        left = sorted(str(path.relative_to(self.home)) for root in (self.prefix, self.settings.parent)
                      if root.exists() for path in root.rglob("*"))
        write_json(self.results / "failed-install-files.json", left)
        assert self.settings.is_file() and (self.tls / "ca.crt").is_file(), "The failed installation kept no settings or TLS identity"
        sentinels = [self.home / ".altitude/fictional/history.jsonl", self.home / "Projects/fictional/worktree/notes.txt"]
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Fictional retained installation acceptance data\n")
        retained = {path: digest(path) for path in [*sentinels, self.settings, *self.tls.glob("*")] if path.is_file()}
        assert (self.prefix / "pending.json").is_file(), "The failed installation left no interrupted activation"
        refused = self.run("refused-install", "/usr/bin/python3", "-B", self.candidate / "install.py", "--archive", new,
                           "--sha256", new_sha, success=False)
        assert "Interrupted activation exists" in refused, refused
        # docs/SETUP.md's steps for a machine that ran the failed release.
        unit = self.home / ".config/systemd/user/altitude.service"
        self.run("disable-unit", "systemctl", "--user", "disable", "altitude.service")
        self.run("remove-unit", "rm", unit)
        self.run("daemon-reload", "systemctl", "--user", "daemon-reload")
        self.run("recover", self.alt, "recover", timeout=180)
        assert not (self.prefix / "pending.json").exists(), "alt recover left the interrupted activation"
        self.run("install", "/usr/bin/python3", "-B", self.candidate / "install.py", "--archive", new, "--sha256", new_sha)
        self.healthy("installed", after)
        self.doctor("installed", after)
        assert all(digest(path) == value for path, value in retained.items()), "Installing over the failed release changed retained data"
        self.uninstall(retained)

    def release_authority(self, *names: str) -> Path:
        """A throwaway certificate authority and a server certificate for NAMES, valid for a day."""
        server = self.home / "release-server"
        server.mkdir()
        self.run("server-authority", "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", "/CN=Lifecycle test release authority", "-keyout", server / "ca.key", "-out", server / "ca.crt")
        self.run("server-request", "openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={names[0]}",
                 "-keyout", server / "server.key", "-out", server / "server.csr")
        (server / "names").write_text("subjectAltName=" + ",".join(f"DNS:{name}" for name in names) + "\n")
        self.run("server-certificate", "openssl", "x509", "-req", "-in", server / "server.csr", "-CA", server / "ca.crt",
                 "-CAkey", server / "ca.key", "-CAcreateserial", "-days", "1", "-extfile", server / "names",
                 "-out", server / "server.crt")
        return server

    def release_server(self, server: Path):
        """HTTPS on 127.0.0.1:443 serving SERVER/root, with the request lines it answered."""
        requests = []

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                requests.append(" ".join(map(str, args)))

        httpd = ThreadingHTTPServer(("127.0.0.1", 443), functools.partial(Handler, directory=str(server / "root")))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(server / "server.crt", server / "server.key")
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, requests

    def bootstrap(self):
        """The built install.sh, fetched and run by its public command, downloads, verifies and installs."""
        old, old_sha, _, before, *_ = self.prepare()
        script = self.baseline / "install.sh"
        repository = re.search(r"^REPOSITORY='([^']+)'$", script.read_text(), re.M).group(1)
        address = urlsplit(repository)
        assert address.scheme == "https" and not address.port
        sums = dict(reversed(line.split()) for line in (self.baseline / "SHA256SUMS").read_text().splitlines())
        assert digest(script) == sums["install.sh"], "install.sh differs from the release's SHA256SUMS"
        # A throwaway authority that only this test's curl trusts; the machine's trust store is untouched.
        server = self.release_authority(address.hostname)
        root = server / "root"
        releases = root / address.path.strip("/") / "releases"
        (releases / "latest/download").mkdir(parents=True)
        (releases / "download" / before["version"]).mkdir(parents=True)
        (releases / "latest/download/install.sh").write_bytes(script.read_bytes())
        (releases / "download" / before["version"] / "install.py").write_bytes((self.baseline / "install.py").read_bytes())
        served = releases / "download" / before["version"] / old.name
        httpd, requests = self.release_server(server)
        self.env["CURL_CA_BUNDLE"] = str(server / "ca.crt")
        command = f"curl --proto '=https' --tlsv1.2 -fsSL {repository}/releases/latest/download/install.sh | sh"
        try:
            # A download that does not match the checksum built into install.sh runs nothing.
            tampered = bytearray(old.read_bytes())
            tampered[-1] ^= 1
            served.write_bytes(tampered)
            refused = self.run("bootstrap-tampered", "sh", "-c", command, success=False, timeout=120)
            assert "does not match the checksum" in refused, refused
            assert not self.prefix.exists() and not self.alt.exists(), "A refused download installed something"
            served.write_bytes(old.read_bytes())
            installed = self.run("bootstrap", "sh", "-c", command, timeout=300)
            assert f"Altitude {before['version']} is installed" in installed, installed
        finally:
            httpd.shutdown()
            write_json(self.results / "bootstrap-requests.json", requests)
            del self.env["CURL_CA_BUNDLE"]
        assert sum(f"GET /{address.path.strip('/')}/releases/download/{before['version']}/{old.name} " in line
                   for line in requests) == 2, requests
        self.healthy("bootstrapped", before)
        self.doctor("bootstrapped", before)
        self.uninstall({})
        self.result["passed"] = True

    def update(self):
        """An installed copy offered the candidate by GitHub's release list installs it through the app's Update request.

        The release server answers for api.github.com and github.com, which the root wrapper points at this machine's
        loopback; the account's user manager hands its authority to the service and the update job as SSL_CERT_FILE."""
        old, old_sha, _, before, new, new_sha, _, after = self.prepare()
        self.result["limits"][0] = ("Same-source versions; GitHub's release list and downloads answered by a server on the guest's "
                                    "loopback, not by GitHub; the Update request is sent as the page sends it, not from a browser")
        address = urlsplit(before["repository"])
        assert address.scheme == "https" and address.hostname == "github.com", address
        repository = address.path.strip("/")
        server = self.release_authority("api.github.com", "github.com")
        root = server / "root"
        listing = root / "repos" / repository / "releases"
        listing.parent.mkdir(parents=True)
        # Newest first, as GitHub lists them: a newer draft that must never be offered, the candidate, the baseline.
        write_json(listing, [{"tag_name": "v0.99.0", "draft": True, "prerelease": False},
                             *({"tag_name": release["version"], "draft": False, "prerelease": "-rc." in release["version"]}
                               for release in (after, before))])
        download = root / repository / "releases/download" / after["version"]
        download.mkdir(parents=True)
        (download / new.name).write_bytes(new.read_bytes())
        (download / (new.name + ".sha256")).write_text(new_sha + "\n")
        httpd, requests = self.release_server(server)
        self.run("trust-release-server", "systemctl", "--user", "set-environment", f"SSL_CERT_FILE={server / 'ca.crt'}")
        try:
            self.install(old, old_sha)
            self.healthy("installed", before)
            # The daemon looks up releases as it starts.
            record = self.home / ".altitude/update.json"
            deadline = time.monotonic() + 120
            while not (record.is_file() and "latest" in json.loads(record.read_text() or "{}")):
                assert time.monotonic() < deadline, "The daemon recorded no release lookup"
                time.sleep(1)
            write_json(self.results / "offered-update-record.json", json.loads(record.read_text()))
            offered = json.loads(self.run("offered-doctor", self.alt, "doctor"))
            write_json(self.results / "offered-doctor.json", offered)
            assert offered["update"]["available"]["version"] == after["version"], offered["update"]
            # The page's own requests: pair, read the overview, then Update for exactly the version it shows.
            base = f"https://127.0.0.1:{self.env['ALTITUDE_PORT']}"
            context = ssl.create_default_context(cafile=str(self.tls / "ca.crt"))
            code = re.search(r"Pairing code: (\S+)", self.run("pair", self.alt, "pair")).group(1)
            cookie = None

            def page(path: str, body: dict | None = None):
                request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json", "Origin": base, **({"Cookie": cookie} if cookie else {})})
                with urlopen(request, context=context, timeout=30) as response:
                    return response.headers, json.load(response)

            headers, _ = page("/api/pair", {"code": code})
            cookie = headers["Set-Cookie"].split(";")[0]
            _, overview = page("/api/overview")
            write_json(self.results / "offered-overview-update.json", overview["update"])
            assert overview["update"]["available"]["version"] == after["version"], overview["update"]
            _, started = page("/api/update", {"version": after["version"]})
            write_json(self.results / "update-request.json", started)
            assert started["update"]["attempt"]["state"] == "running", started
            deadline = time.monotonic() + 300
            while True:
                assert time.monotonic() < deadline, "The update did not activate the candidate"
                try:
                    with urlopen(base + "/api/health", context=context, timeout=10) as response:
                        if json.load(response)["version"] == after["version"]:
                            break
                except OSError:
                    pass  # the service is restarting
                time.sleep(1)
            self.healthy("updated", after)
            updated = json.loads(self.run("updated-doctor", self.alt, "doctor"))
            write_json(self.results / "updated-doctor.json", updated)
            assert updated["update"]["available"] is None and updated["update"]["attempt"] is None, updated["update"]
            write_json(self.results / "updated-update-record.json", json.loads(record.read_text()))
            self.run("update-unit-journal", "journalctl", "--user", "--no-pager", "-u", f"altitude-update-{after['version']}")
        finally:
            httpd.shutdown()
            self.run("untrust-release-server", "systemctl", "--user", "unset-environment", "SSL_CERT_FILE")
            write_json(self.results / "update-requests.json", requests)
        assert sum(f"GET /repos/{repository}/releases?per_page=30 " in line for line in requests) >= 1, requests
        downloads = [line for line in requests if "/releases/download/" in line]
        assert len(downloads) == 2 and all(f"GET /{repository}/releases/download/{after['version']}/{new.name}" in line
                                           for line in downloads), requests
        self.uninstall({})
        self.result["passed"] = True

    def execute(self, phase: str = "all"):
        try:
            {"all": self.exercise, "bootstrap": self.bootstrap, "update": self.update, "reboot-install": self.reboot_install,
             "reboot-verify": self.reboot_verify, "recovery": self.recovery}[phase]()
        except Exception as exc:
            self.result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            write_json(self.results / ("result.json" if phase == "all" else f"{phase}-result.json"), self.result)


class ReleaseServer:
    """GitHub's release API and downloads for one repository, answered on this machine.

    Clients reach it as their HTTPS proxy: a CONNECT to github.com or api.github.com is answered here with a certificate
    from a throwaway authority that only the test's processes trust (SSL_CERT_FILE, CURL_CA_BUNDLE), so the installed
    application's real requests to GitHub's addresses arrive here. Every other host is refused, and nothing leaves
    the machine. `latest` is the release the API names as latest; `files` maps (version, name) to what is published."""

    HOSTS = ("github.com", "api.github.com")

    def __init__(self, folder: Path, repository: str, run):
        self.repository, self.latest, self.files, self.requests = repository, None, {}, []
        folder.mkdir()
        self.ca = folder / "ca.crt"
        run("release-authority", "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=Lifecycle test release authority", "-keyout", folder / "ca.key", "-out", self.ca)
        run("release-request", "openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=github.com",
            "-keyout", folder / "server.key", "-out", folder / "server.csr")
        (folder / "names").write_text("subjectAltName=" + ",".join(f"DNS:{host}" for host in self.HOSTS) + "\n")
        run("release-certificate", "openssl", "x509", "-req", "-in", folder / "server.csr", "-CA", self.ca,
            "-CAkey", folder / "ca.key", "-CAcreateserial", "-days", "1", "-extfile", folder / "names",
            "-out", folder / "server.crt")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(folder / "server.crt", folder / "server.key")
        release = self

        class Release(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass

            def do_GET(self):
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
                body, status = release.answer(host, urlsplit(self.path).path)
                release.requests.append({"host": host, "path": self.path, "status": status})
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_CONNECT(self):
                host, _, port = self.path.rpartition(":")
                if host not in release.HOSTS or port != "443":
                    release.requests.append({"connect": self.path, "status": 403})
                    self.send_error(403, "Only GitHub's release addresses are served in this test")
                    return
                self.send_response(200, "Connection established")
                self.end_headers()
                self.close_connection = True
                try:
                    with context.wrap_socket(self.connection, server_side=True) as tls:
                        Release(tls, self.client_address, self.server)
                except OSError as error:
                    release.requests.append({"connect": self.path, "error": str(error)})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        self.httpd.daemon_threads = True
        self.proxy = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def answer(self, host: str, path: str) -> tuple[bytes, int]:
        releases = f"/{self.repository}/releases/"
        key = None
        if host == "api.github.com" and path == f"/repos/{self.repository}/releases/latest" and self.latest:
            return json.dumps({"tag_name": self.latest, "prerelease": False, "draft": False}).encode(), 200
        if host == "github.com" and path == releases + "latest/download/install.sh":
            key = (self.latest, "install.sh")
        elif host == "github.com" and path.startswith(releases + "download/"):
            key = tuple(path[len(releases + "download/"):].split("/", 1))
        return (self.files[key], 200) if key in self.files else (b"Not Found\n", 404)

    def publish(self, folder: Path, version: str) -> None:
        """A release's files under VERSION, as GitHub serves its assets; the newest published becomes latest."""
        for path in folder.iterdir():
            if path.is_file():
                self.files[(version, path.name)] = path.read_bytes()
        self.latest = version

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class MacLifecycle(Lifecycle):
    """The macOS lifecycle under a throwaway HOME of the running account, as installation_mac.py launches it.

    install.sh installs a built release through its public curl | sh command; the installed daemon's own release check
    finds each newer synthetic release; alt update and the app's Update button (a detached launchd job) activate one; a
    release whose startup exits is rolled back; uninstall keeps configuration, TLS identity and fictional data. GitHub's
    addresses are answered by ReleaseServer. launchd's domain is the account's own, so the installation's LaunchAgent
    label is derived from its HOME (platform.service_label) and the account's service is never addressed."""

    ACCOUNT_LABEL = "dev.altitude.altd"

    def __init__(self, baseline: Path, results: Path, commit: str):
        super().__init__(baseline, baseline, results, commit)
        self.result["limits"] = [
            "Same-source synthetic versions under a throwaway HOME of the running account on this Mac, not a fresh Mac "
            "or a second account; no cross-release storage migration",
            "GitHub's release addresses are answered on this machine through the installation's HTTPS proxy setting, "
            "not a download from GitHub's published release; the 12-hour check schedule and the once-a-day terminal "
            "notice are advanced by rewriting update.json and removing the notice marker",
            "The app's notice and Update button are proven by the payload the page renders and the request it sends, "
            "without a browser; no browser/device certificate trust",
            "No login/logout, restart, sleep, live provider or native worker confinement acceptance",
        ]
        self.label = None

    def disposable(self):
        assert sys.platform == "darwin"
        assert self.home.name == "home" and self.home.parent.name == "altitude-installation-mac"
        assert Path(pwd.getpwuid(os.getuid()).pw_dir).resolve() != self.home.resolve()

    def launchd(self, label: str) -> str | None:
        """launchd's description of LABEL in this account's domain, or None when it has no such job."""
        found = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True,
                               timeout=30)
        return found.stdout if found.returncode == 0 else None

    def prepare(self):
        self.disposable()
        assert not self.prefix.exists() and not self.settings.exists(), "Needs a fresh throwaway HOME"
        write_json(self.results / "environment.json", {
            "macos": platform.mac_ver()[0], "architecture": platform.machine(), "python": platform.python_version(),
            "uid": os.getuid(), "home": str(self.home)})
        archive, checksum, package, release = self.archive(self.baseline, "baseline")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.env.update(ALTITUDE_HOME=str(self.home / ".altitude"), ALTITUDE_PORT=str(port), ALTITUDE_HOST="127.0.0.1",
                        ALTITUDE_TLS_DIR=str(self.tls), ALTITUDE_CONFIG=str(self.settings),
                        ALTITUDE_ROOTS=str(self.home / "Projects"), PYTHONDONTWRITEBYTECODE="1")
        fixture = self.home / "fixture-engine"
        fixture.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$HOME/results/fixture-engine.log\"\n"
                           "echo 'Fictional engine: unauthenticated, no provider requests' >&2\nexit 1\n")
        fixture.chmod(0o755)
        keys = json.loads(self.run("engine-settings", sys.executable, "-B", "-c",
            "import json,sys; sys.path.insert(0,sys.argv[1]); from altitude import config; "
            "print(json.dumps([k for k in vars(config) if k.endswith('_BIN')]))", package))
        assert keys, "Package exposes no engine executable settings"
        self.env.update({key: str(fixture) for key in keys})
        self.label = self.run("service-label", sys.executable, "-B", "-c",
            "import sys; sys.path.insert(0,sys.argv[1]); from altitude import platform; print(platform.service_label())",
            package).strip()
        assert self.label.startswith(self.ACCOUNT_LABEL + ".") and self.label != self.ACCOUNT_LABEL, self.label
        assert self.launchd(self.label) is None, f"{self.label} is already loaded"
        write_json(self.results / "service-label.json", {"label": self.label})
        return archive, checksum, package, release

    def versions(self, package: Path, release: dict) -> dict:
        """Built baseline, then newer synthetic releases from its package: two healthy ones and one whose startup exits."""
        made = {}
        for offset, failing in ((1, False), (2, False), (3, True)):
            version = re.sub(r"rc\.(\d+)$", lambda match: f"rc.{int(match[1]) + offset}", release["version"])
            folder = self.home / f"release-{version}"
            folder.mkdir()
            copy = self.home / f"package-{version}"
            shutil.copytree(package, copy)
            archive, made_release = synthetic_archive(copy, folder / f"altitude-{version}.tar.gz", version, failing=failing)
            (folder / (archive.name + ".sha256")).write_text(digest(archive) + "\n")
            made[version] = {"folder": folder, "release": made_release, "failing": failing}
            self.result["artifacts"].append({"kind": "failure-injection" if failing else "synthetic", "version": version,
                                             "sha256": digest(archive), "commit": made_release["commit"],
                                             "change": "version relabelled" + ("; bin/alt records its daemon invocation then exits" if failing else "")})
        return made

    def record(self) -> dict:
        return json.loads((self.home / ".altitude/update.json").read_text())

    def advance_check(self, version: str, timeout: float = 75):
        """Make the daemon's next release check due now, as twelve hours passing would, and wait for it to record VERSION."""
        path = self.home / ".altitude/update.json"
        with (path.parent / "update.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            record = json.loads(path.read_text())
            record["next"] = 0
            path.with_name("update.json.lane").write_text(json.dumps(record))
            path.with_name("update.json.lane").replace(path)
        self.wait_check(version, timeout)

    def wait_check(self, version: str, timeout: float = 75):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if ((self.record().get("latest") or {}).get("version") == version and self.record().get("next", 0) > time.time()):
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise AssertionError(f"The daemon's release check did not record {version}")

    def base(self) -> str:
        return f"https://127.0.0.1:{self.env['ALTITUDE_PORT']}"

    def request(self, path: str, body: dict | None = None):
        """The page's own request as a paired device sends it."""
        headers = {"Cookie": self.cookie, "Origin": self.base(), "Sec-Fetch-Site": "same-origin",
                   "User-Agent": "Mozilla/5.0 (Macintosh) installation lifecycle"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        context = ssl.create_default_context(cafile=str(self.tls / "ca.crt"))
        with urlopen(Request(self.base() + path, data=data, headers=headers), context=context, timeout=30) as response:
            return json.load(response)

    def pair(self):
        code = re.search(r"Pairing code: (\S+)", self.run("pair", self.alt, "pair")).group(1)
        context = ssl.create_default_context(cafile=str(self.tls / "ca.crt"))
        request = Request(self.base() + "/api/pair", data=json.dumps({"code": code}).encode(), headers={
            "Content-Type": "application/json", "Origin": self.base(), "User-Agent": "Mozilla/5.0 (Macintosh) installation lifecycle"})
        with urlopen(request, context=context, timeout=30) as response:
            self.cookie = response.headers["Set-Cookie"].split(";", 1)[0]

    def offered(self, label: str, version: str | None) -> dict:
        """What the app, alt doctor and the terminal show for the newest published release."""
        update = self.request("/api/overview")["update"]
        write_json(self.results / f"{label}-app-update.json", update)
        report = json.loads(self.run(label + "-doctor-update", self.alt, "doctor"))
        assert report["update"] == update, (report["update"], update)
        assert update["check"] and update["command"] == "alt update" and update["checked"], update
        if version is None:
            assert update["available"] is None, update
            return update
        assert update["available"] == {"version": version,
                                       "notes": f"https://github.com/{self.repository}/releases/tag/{version}"}, update
        notice = self.terminal(label + "-notice", self.alt, "service", "status")
        assert f"Altitude {version} is available: run alt update" in notice, notice
        assert "is available" not in self.terminal(label + "-notice-again", self.alt, "service", "status"), \
            "The notice repeats within a day"
        (self.home / ".altitude/update-notice").unlink()
        return update

    def terminal(self, label: str, *command) -> str:
        """COMMAND's standard error as a terminal shows it: the notice prints only to a terminal."""
        primary, secondary = pty.openpty()
        try:
            proc = subprocess.run(list(map(str, command)), cwd=self.home, env=self.env, stdout=subprocess.DEVNULL,
                                  stderr=secondary, timeout=60)
            os.close(secondary)
            secondary = None
            chunks = []
            while True:
                try:
                    chunk = os.read(primary, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            if secondary is not None:
                os.close(secondary)
            os.close(primary)
        text = b"".join(chunks).decode(errors="replace")
        self.sequence += 1
        (self.results / f"{self.sequence:02d}-{label}.log").write_text(f"argv: {list(map(str, command))}\nexit: {proc.returncode}\n{text}")
        self.result["steps"].append({"step": label, "exit": proc.returncode})
        assert proc.returncode == 0, f"{label}: exit {proc.returncode}"
        return text

    def wait_version(self, version: str, timeout: float = 180):
        context = ssl.create_default_context(cafile=str(self.tls / "ca.crt"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urlopen(self.base() + "/api/health", context=context, timeout=5) as response:
                    if json.load(response).get("version") == version and not (self.prefix / "pending.json").exists():
                        return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise AssertionError(f"The service did not come back as {version}")

    def mac(self):
        archive, checksum, package, before = self.prepare()
        made = self.versions(package, before)
        second, third, broken = list(made)
        repository = re.search(r"^REPOSITORY='https://github\.com/([^']+)'$", (self.baseline / "install.sh").read_text(), re.M)
        self.repository = repository.group(1)
        server = ReleaseServer(self.home / "release-server", self.repository, self.run)
        bundle = str(server.ca)
        self.env.update(HTTPS_PROXY=server.proxy, NO_PROXY="127.0.0.1,localhost", SSL_CERT_FILE=bundle,
                        CURL_CA_BUNDLE=bundle)
        script = f"curl --proto '=https' --tlsv1.2 -fsSL https://github.com/{self.repository}/releases/latest/download/install.sh | sh"
        try:
            published = self.home / "release-published"
            published.mkdir()
            for name in ("install.sh", "install.py", archive.name, archive.name + ".sha256", "SHA256SUMS"):
                shutil.copyfile(self.baseline / name, published / name)
            tampered = bytearray(archive.read_bytes())
            tampered[-1] ^= 1
            (published / archive.name).write_bytes(tampered)
            server.publish(published, before["version"])
            refused = self.run("bootstrap-tampered", "sh", "-c", script, success=False, timeout=120)
            assert "does not match the checksum" in refused, refused
            assert not self.prefix.exists() and not self.alt.exists(), "A refused download installed something"
            (published / archive.name).write_bytes(archive.read_bytes())
            server.publish(published, before["version"])
            installed = self.run("bootstrap", "sh", "-c", script, timeout=300)
            assert f"Altitude {before['version']} is installed" in installed, installed
            initial = self.healthy("installed", before)
            self.doctor("installed", before)
            agent = self.home / f"Library/LaunchAgents/{self.label}.plist"
            loaded = self.launchd(self.label)
            # launchd names the definition by its resolved path (/private/var for the temporary folder).
            assert agent.is_file() and loaded and f"path = {agent.resolve()}" in loaded, loaded
            saved = json.loads(self.settings.read_text())["environment"]
            assert (saved["HTTPS_PROXY"], saved["SSL_CERT_FILE"]) == (server.proxy, bundle), saved
            sentinels = [self.home / ".altitude/fictional/history.jsonl",
                         self.home / "Projects/fictional/worktree/notes.txt",
                         self.home / ".fixture-provider/sessions/session.json"]
            for path in sentinels:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fictional retained installation acceptance data\n")
            retained = {path: digest(path) for path in [*sentinels, self.settings, *self.tls.glob("*")] if path.is_file()}

            self.wait_check(before["version"])
            self.pair()
            self.offered("current", None)

            server.publish(made[second]["folder"], second)
            self.advance_check(second)
            self.offered("detected", second)
            self.run("update", self.alt, "update", timeout=240)
            updated = self.healthy("updated", made[second]["release"])
            assert updated["pid"] != initial["pid"], "Update did not replace the daemon"
            self.doctor("updated", made[second]["release"])
            self.offered("updated", None)

            server.publish(made[third]["folder"], third)
            self.advance_check(third)
            self.offered("detected-again", third)
            requested = self.request("/api/update", {"version": third})["update"]
            write_json(self.results / "button-requested.json", requested)
            assert requested["attempt"]["version"] == third and requested["attempt"]["state"] == "running", requested
            self.wait_version(third)
            button = self.healthy("button-updated", made[third]["release"])
            assert button["pid"] != updated["pid"], "The Update button did not replace the daemon"
            self.doctor("button-updated", made[third]["release"])
            job = f"dev.altitude.job.altitude-update-{third}"
            deadline = time.monotonic() + 60
            while self.launchd(job) and time.monotonic() < deadline:
                time.sleep(1)
            assert self.launchd(job) is None, f"{job} is still loaded"
            log = self.home / f"Library/Logs/altitude/altitude-update-{third}.log"
            shutil.copyfile(log, self.results / "button-update-job.log")
            assert f'"version": "{third}"' in log.read_text(), "The detached update recorded no result"
            self.offered("button-updated", None)

            server.publish(made[broken]["folder"], broken)
            self.advance_check(broken)
            failure = self.run("failed-update", self.alt, "update", success=False, timeout=240)
            assert "previous installation restored" in failure, failure
            marker = json.loads((self.results / "failed-startup.json").read_text())
            assert marker["argv"] == ["serve"] and marker["version"] == broken and marker["pid"] > 0
            self.healthy("recovered", made[third]["release"])
            self.doctor("recovered", made[third]["release"])
            assert (self.prefix / "versions" / broken).is_dir()
            assert all(digest(path) == value for path, value in retained.items()), "Update/recovery changed retained data"
        finally:
            server.stop()
            write_json(self.results / "release-requests.json", server.requests)
        assert not [item for item in server.requests if item.get("status") == 403 or "error" in item], \
            "A request left the release addresses"
        self.uninstall(retained)

    def uninstall(self, retained: dict):
        port = int(self.env["ALTITUDE_PORT"])
        removed = json.loads(self.run("uninstall", self.alt, "uninstall"))
        assert removed["uninstalled"] and not removed["application_retained_for_project_hooks"]
        assert not self.alt.exists() and not (self.prefix / "current").exists()
        assert not (self.prefix / "versions").exists()
        assert not (self.home / f"Library/LaunchAgents/{self.label}.plist").exists()
        assert self.launchd(self.label) is None, f"{self.label} is still loaded"
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", port)) != 0, "Uninstalled service still listens"
        assert all(digest(path) == value for path, value in retained.items()), "Uninstall changed retained data"
        self.result["retained_files"] = [str(path.relative_to(self.home)) for path in retained]
        self.result["passed"] = True

    def execute(self, phase: str = "mac"):
        try:
            self.mac()
        except Exception as exc:
            self.result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            write_json(self.results / "result.json", self.result)


if __name__ == "__main__":
    if sys.argv[5:6] == ["mac"]:
        MacLifecycle(Path(sys.argv[1]).resolve(), Path(sys.argv[3]).resolve(), sys.argv[4]).execute()
    else:
        Lifecycle(*(Path(arg).resolve() for arg in sys.argv[1:4]), sys.argv[4]).execute(*sys.argv[5:6])
