"""Verified fixture archive -> real installer transaction/output -> real trusted HTTPS/browser.

Only native service observations/control use InstallationCase fixtures. No host lifecycle or trust
store is touched. The shell's actual result renderer runs unchanged; prerequisite/download checks
remain independently covered by release/bootstrap tests, rather than repeated here.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import threading
from unittest import mock

from service_support import configure, serve
from tests.support import REPO
from tests.test_installation import InstallationCase
from altitude import access, config, installation, platform, server, tls


def main():
    probe, info = installation._probe, tls.info
    case = InstallationCase()
    case.setUp()
    try:
        run(case, probe, info)
    finally:
        case.doCleanups()


def run(case, probe, info):
    configure()
    # Existing archive fixtures are deliberately small. This one includes the built SPA actually served.
    archive, _ = case.archive()
    bundle = archive.with_suffix("").with_suffix("")
    shutil.rmtree(bundle / "web/dist")
    shutil.copytree(config.WEB_DIST, bundle / "web/dist", ignore=shutil.ignore_patterns("*.ort"))
    manifest = json.loads((bundle / "release.json").read_text())
    manifest["files"] = {str(path.relative_to(bundle)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in bundle.rglob("*") if path.is_file() and path.name != "release.json"}
    (bundle / "release.json").write_text(json.dumps(manifest))
    with tarfile.open(archive, "w:gz") as packed:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                packed.add(path, arcname=str(path.relative_to(bundle)), recursive=False)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    case.certificates.parent.mkdir(parents=True, exist_ok=True)
    tls.fixture(case.certificates, probe=True)
    config.TLS, config.RELEASE = True, manifest
    config.WEB_DIST = case.prefix / "current/web/dist"
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    config.PORT = httpd.server_port
    context = tls._load(case.certificates)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    httpd.daemon_threads = False
    server.TRUST = tls.TrustCheck(tls._load(case.certificates / "probe"), tls.identity(case.certificates / "ca.crt")["name"])
    server.Handler._local = lambda _handler: False
    running = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01})
    running.start()
    status = case.status

    def native_status():
        import os
        return {**status(), "MainPID": str(os.getpid()) if case.active == "active" else "0"}

    try:
        with mock.patch.object(tls, "initialize", return_value=None), mock.patch.object(tls, "info", side_effect=info), \
             mock.patch.object(installation, "_probe", side_effect=probe), mock.patch.object(platform, "status", side_effect=native_status):
            result = installation.install(archive, checksum, case.prefix)
        # This is the exact shell heredoc that prints a verified install result, without a second pairing command.
        script = (REPO / "scripts/install.sh").read_text()
        renderer = script.split('    "$python" - "$workdir/result.json"', 1)[1].split("\n", 1)[1].split("\nEOF", 1)[0]
        result_file = case.tmp / "result.json"
        result_file.write_text(json.dumps(result))
        output = subprocess.run([sys.executable, "-", str(result_file), "https://example.invalid/setup"],
                                input=renderer, capture_output=True, text=True, check=True).stdout
        result_file.unlink()
        # Extract the visible link, so the browser cannot bypass the actual printed output contract.
        link = next(line.strip() for line in output.splitlines() if "/#pair=" in line)
        access.prepare()
    except BaseException:
        httpd.shutdown()
        running.join(timeout=2)
        httpd.server_close()
        raise
    httpd.shutdown()
    running.join(timeout=2)
    serve(httpd=httpd, paired=False,
          ready={"url": result["url"], "certificate": str(case.certificates / "ca.crt"),
                 "pairing": link, "output": output, "machine": access.machine_key()})


if __name__ == "__main__":
    main()
