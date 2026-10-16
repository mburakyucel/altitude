"""Real update receipts/archives/API in a disposable installation with simulated native service effects.

This is browser application evidence, never a Mac service upgrade. External release downloads,
detached process launches, TLS initialization and native health are the installation suite's fixtures.
"""
import json
import os
import py_compile
import sys
from contextlib import ExitStack, contextmanager
from unittest import mock

from service_support import configure, serve
from tests.test_installation import NoticeCase
from tests.support import git
from altitude import access, config, installation, server


class BrowserInstallation(NoticeCase):
    INSTALLED = "v0.4.0"
    # Native effects are simulated, while the real server uses this host's filesystem operations.
    HOST = sys.platform


def main():
    configure()
    case = BrowserInstallation()
    case.setUp()
    project = case.home / "Projects/atlas"
    project.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=project)
    with config.add_project("atlas", path=project):
        pass
    projects = config.PROJECTS_FILE.read_bytes()
    # The first download fails its checksum, so the automatic attempt fails with that cause.
    case.publish("v0.5.0", checksum="0" * 64)
    case.listed("v0.5.0")
    installation.check_for_update()
    installation.request_update("v0.5.0", automatic=True)
    root = (case.prefix / "current").resolve()
    cache = root / "altitude/__pycache__"
    cache.mkdir()
    # A real generated cache in the installed tree, as a foreign process writes it; the installed check accepts it.
    py_compile.compile(str(root / "altitude/config.py"), cfile=str(cache / f"config.{sys.implementation.cache_tag}.pyc"), doraise=True)
    retained = case.retained_data()
    certs = {path: path.read_bytes() for path in case.certificates.iterdir()}
    original_error = None

    @contextmanager
    def installation_environment():
        # The browser service has its own ephemeral plain-HTTP endpoint. The simulated native
        # installation keeps the saved HTTPS endpoint used by the real lifecycle transaction.
        saved = json.loads(case.settings.read_text())["environment"]
        with ExitStack() as stack:
            for key, value in (("HOST", saved["ALTITUDE_HOST"]), ("PORT", int(saved["ALTITUDE_PORT"])), ("TLS", True)):
                stack.enter_context(mock.patch.object(config, key, value))
            yield

    def evidence():
        try:
            installation.metadata(root)
            verified = "valid"
        except ValueError:
            verified = "rejected"
        return {"record": installation._update_record()[1], "installed_metadata": verified,
                "caches": len(list(cache.glob("*.pyc"))),
                "current": os.readlink(case.prefix / "current"), "detached": len(case.detached),
                "failure": original_error,
                "fence": config.activation_pending(),
                "retained": all(path.read_bytes() == content for path, content in retained.items()),
                "projects_retained": config.PROJECTS_FILE.read_bytes() == projects,
                "paired_devices": [device["id"] for device in access._read()["devices"]],
                "certificate_retained": all(path.read_bytes() == content for path, content in certs.items()),
                "native_effects": "simulated; not a macOS upgrade"}

    get, post = server.Handler.do_GET, server.Handler.do_POST

    def do_GET(handler):
        if handler.path == "/fixture/evidence":
            return handler._json(evidence())
        return get(handler)

    def do_POST(handler):
        nonlocal original_error
        if handler.path == "/fixture/finish-attempt":
            with installation_environment():
                try:
                    case.run_detached()
                except (OSError, ValueError, RuntimeError) as exc:
                    original_error = str(exc)
            if os.readlink(case.prefix / "current") == "versions/v0.5.0":
                config.RELEASE = installation.metadata((case.prefix / "current").resolve())
                config.RELEASE["repository"] = "https://github.com/example/altitude"
            return handler._json(evidence())
        if handler.path == "/fixture/republish":
            case.publish("v0.5.0")
            return handler._json(evidence())
        return post(handler)

    server.Handler.do_GET, server.Handler.do_POST = do_GET, do_POST
    try:
        serve()
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
