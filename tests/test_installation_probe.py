"""Activation probes bind real HTTPS responses to the owned native process and release."""
import json
import os
import shlex
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest import mock

from tests.support import AltitudeCase
from tests import test_installation
from altitude import config, installation, platform, tls


class TestInstallationProbe(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.prefix = self.tmp / "application"
        self.version = "v0.1.0"
        self.commit = "a" * 40
        release = self.prefix / "versions" / self.version / "release.json"
        release.parent.mkdir(parents=True)
        release.write_text(json.dumps({"version": self.version, "commit": self.commit}))
        self.patch(config, "INSTALL_PREFIX", None)
        self.patch(config, "TLS_DIR", self.tmp / "private-tls")
        self.patch(config, "HOST", "127.0.0.1")
        tls.initialize()
        self.health = {"version": self.version, "commit": self.commit, "pid": os.getpid()}
        self.html = '<html><script src="/assets/app.js"></script></html>'
        self.native = {"ActiveState": "active", "MainPID": str(os.getpid())}
        self.patch(platform, "status", side_effect=lambda: self.native)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (json.dumps(owner.health) if self.path == "/api/health" else owner.html).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.socket = tls.check().wrap_socket(self.httpd.socket, server_side=True)
        self.patch(config, "PORT", self.httpd.server_port)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.httpd.shutdown)

    def probe(self):
        return installation._probe(self.version, prefix=self.prefix, timeout=0)

    def test_correct_certificate_commit_process_and_bundle_pass_with_explicit_prefix(self):
        self.assertIsNone(config.INSTALL_PREFIX)
        self.assertIsNone(self.probe())

    def test_other_commit_or_native_process_cannot_satisfy_activation(self):
        for field, value in (("version", "v0.1.1"), ("commit", "b" * 40), ("pid", os.getpid() + 1)):
            with self.subTest(field=field):
                original = self.health[field]
                self.health[field] = value
                with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
                    self.probe()
                self.health[field] = original
        self.native["ActiveState"] = "inactive"
        with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
            self.probe()

    def test_valid_api_without_built_ui_does_not_satisfy_activation(self):
        self.html = "The application bundle is missing"
        with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
            self.probe()

    def test_wrong_ca_is_rejected_without_skipping_https_verification(self):
        original = config.TLS_DIR
        with mock.patch.object(config, "TLS_DIR", self.tmp / "other-ca"):
            tls.initialize()
            with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
                self.probe()
        self.assertEqual(config.TLS_DIR, original)
        self.assertIsNone(self.probe())

    def test_external_certificate_without_local_ca_uses_system_trust_context(self):
        ca = config.TLS_DIR / "ca.crt"
        trusted = ssl.create_default_context(cafile=str(ca))
        ca.rename(self.tmp / "external-trust-root.crt")
        # Represent an OS trust store containing the external issuer; the TLS handshake stays real.
        with mock.patch.object(installation.ssl, "create_default_context", return_value=trusted) as create:
            self.assertIsNone(self.probe())
        create.assert_called_once_with(cafile=None)
        self.assertTrue(trusted.check_hostname)
        self.assertEqual(trusted.verify_mode, ssl.CERT_REQUIRED)


class TestInstallationBoundaries(unittest.TestCase):
    def setUp(self):
        self.fixture = test_installation.Installation("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_source_install_passes_destination_explicitly_to_activation_probe(self):
        fixture = self.fixture
        with mock.patch.object(config, "INSTALL_PREFIX", None), \
             mock.patch.object(installation, "_probe", side_effect=fixture.probe) as probe:
            result = fixture.install()
        self.assertEqual(result["version"], "v0.1.0")
        probe.assert_called_once_with("v0.1.0", prefix=fixture.prefix)
        self.assertEqual((fixture.active, fixture.enabled), ("active", "enabled"))

    def test_protected_prefix_refusal_creates_no_application_or_changes_to_user_data(self):
        fixture = self.fixture
        archive, checksum = fixture.archive()
        for root in (config.ROOT, config.TLS_DIR, config.PROJECT_ROOTS[0]):
            with self.subTest(root=root):
                prefix = root / "forbidden-install"
                before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} if root.exists() else {}
                with self.assertRaisesRegex(RuntimeError, "separate from runtime"):
                    installation.install(archive, checksum, prefix)
                self.assertFalse(prefix.exists())
                after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} if root.exists() else {}
                self.assertEqual(after, before)
        self.assertFalse(fixture.unit.exists())
        self.assertFalse(fixture.launcher.exists())
        self.assertEqual(fixture.actions, [])

    def test_installed_unit_explicitly_overrides_inherited_http_setting(self):
        fixture = self.fixture
        with mock.patch.dict(os.environ, {"ALTITUDE_TLS": "0"}):
            fixture.install()
        # Environment= assignments take precedence over a user-manager inherited environment.
        assignments = dict(line.removeprefix("Environment=").split("=", 1)
                           for line in fixture.unit.read_text().splitlines()
                           if line.startswith("Environment=") and not line.startswith('Environment="'))
        self.assertEqual(assignments["ALTITUDE_TLS"], "1")
        self.assertEqual(json.loads(fixture.settings.read_text())["environment"]["ALTITUDE_TLS"], "1")

    def test_protected_configuration_refuses_before_application_or_service_effects(self):
        fixture = self.fixture
        archive, checksum = fixture.archive()
        for root in (config.ROOT, config.TLS_DIR, config.PROJECT_ROOTS[0]):
            with self.subTest(root=root):
                settings = root / "install.json"
                before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} if root.exists() else {}
                with mock.patch.dict(os.environ, {"ALTITUDE_CONFIG": str(settings)}):
                    with self.assertRaisesRegex(RuntimeError, "configuration must be outside"):
                        installation.install(archive, checksum, fixture.prefix)
                after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} if root.exists() else {}
                self.assertEqual(after, before)
                self.assertFalse(settings.exists())
        self.assertFalse(fixture.prefix.exists())
        self.assertFalse(fixture.unit.exists())
        self.assertFalse(fixture.launcher.exists())
        self.assertEqual(fixture.actions, [])

    def test_updated_unit_pins_saved_access_and_data_paths_over_ambient_environment(self):
        fixture = self.fixture
        fixture.install()
        saved = json.loads(fixture.settings.read_text())["environment"]
        ambient = {"ALTITUDE_HOST": "192.0.2.20", "ALTITUDE_HOME": str(fixture.tmp / "other-runtime"),
                   "ALTITUDE_ROOTS": str(fixture.tmp / "other-projects"),
                   "ALTITUDE_TLS_DIR": str(fixture.tmp / "other-certificates"),
                   "ALTITUDE_PORT": "19444", "ALTITUDE_TLS": "0"}
        with mock.patch.dict(os.environ, ambient):
            fixture.install("v0.1.1", edited=True)
        assignments = dict(shlex.split(line.removeprefix("Environment="))[0].split("=", 1)
                           for line in fixture.unit.read_text().splitlines() if line.startswith("Environment="))
        for key, conflicting_value in ambient.items():
            with self.subTest(key=key):
                self.assertEqual(assignments[key], saved[key])
                self.assertNotEqual(assignments[key], conflicting_value)
        self.assertEqual(assignments["PATH"], saved["PATH"])
        self.assertEqual(assignments["ALTITUDE_CONFIG"], str(fixture.settings))
