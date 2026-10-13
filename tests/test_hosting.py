"""Actual HTTPS/process identity stays separate from the calling shell and device trust."""
import os
import shlex
from unittest import mock

from tests.test_installation_probe import ProbeCase
from altitude import config, installation, platform, tls


class Hosting(ProbeCase):
    def setUp(self):
        super().setUp()
        self.unit = self.tmp / "owned.service"
        self.unit.write_text("fixture owned service\n")
        self.native.update(LoadState="loaded", FragmentPath=str(self.unit))
        self.patch(platform, "service_path", return_value=self.unit)
        environment = {"ALTITUDE_HOST": "127.0.0.1", "ALTITUDE_PORT": str(config.PORT),
                       "ALTITUDE_TLS_DIR": str(config.TLS_DIR), "ALTITUDE_TLS": "1"}
        self.patch(platform, "service_unit", return_value={"Environment": shlex.join(
            [f"{key}={value}" for key, value in environment.items()])})
        self.patch(platform, "computer_name", return_value="Atlas host")
        self.patch(tls, "record", return_value=self.tmp / "service.json")
        tls.publish({"host": "127.0.0.1", "port": config.PORT, "tls": True, "tls_dir": config.TLS_DIR})

    def test_running_address_and_ca_ignore_conflicting_process_values(self):
        actual = tls.service()["url"]
        directory = config.TLS_DIR
        with mock.patch.object(config, "HOST", "10.0.0.99"), \
                mock.patch.object(config, "TLS_DIR", self.tmp / "wrong-certificates"), \
                mock.patch.dict(os.environ, {"ALTITUDE_HOST": "10.0.0.99", "ALTITUDE_TLS_DIR": str(self.tmp / "wrong-certificates")}), \
                mock.patch.object(installation, "_gh_signed_in", return_value=False):
            report = installation.doctor()
        self.assertEqual(report["hosting"]["url"], actual)
        self.assertTrue(report["hosting"]["verified"])
        self.assertIn("ALTITUDE_HOST", report["hosting"]["overrides"])
        self.assertEqual(report["certificate_trust"]["url"], actual)
        self.assertEqual(report["certificate_trust"]["ca_cert"], str(directory / "ca.crt"))
        self.assertEqual(report["certificate_trust"]["state"], "unknown", "local TLS does not establish browser trust")
        self.assertIn("Coding-provider access: unverified", installation.describe_doctor(report))

    def test_stopped_address_is_configured_and_never_probed(self):
        self.native["ActiveState"] = "inactive"
        with mock.patch.object(tls, "verify_service", side_effect=AssertionError("stopped service was probed")):
            status = installation.hosting_status()
        self.assertEqual(status["state"], "stopped")
        self.assertFalse(status["verified"])
        self.assertIn("Configured address (not serving)", installation.describe_hosting(status))

    def test_stale_record_other_process_bad_tls_and_foreign_unit_are_unverified(self):
        original = dict(self.native)
        directory = config.TLS_DIR
        for fault in ("record", "health", "certificate", "unit"):
            with self.subTest(fault=fault):
                self.native.update(original)
                self.health["pid"] = os.getpid()
                tls.publish({"host": "127.0.0.1", "port": config.PORT, "tls": True, "tls_dir": directory})
                self.native["MainPID"] = str(os.getpid() + 1) if fault == "record" else str(os.getpid())
                if fault == "health":
                    self.health["pid"] += 1
                if fault == "unit":
                    self.native["FragmentPath"] = str(self.tmp / "foreign.service")
                if fault == "certificate":
                    with mock.patch.object(config, "TLS_DIR", self.tmp / "other-ca"):
                        tls.initialize()
                        tls.publish({"host": "127.0.0.1", "port": config.PORT, "tls": True, "tls_dir": config.TLS_DIR})
                status = installation.hosting_status()
                self.assertEqual(status["state"], "unknown")
                self.assertIsNone(status["url"])
                self.assertFalse(status["verified"])
                self.assertIn("Configured address (not verified)", installation.describe_hosting(status))
                self.assertIn({"record": "different process", "health": "Another process",
                               "certificate": "identity", "unit": "owned service definition"}[fault], status["detail"])


class ListenerExposure(ProbeCase):
    def test_certificate_url_scope_does_not_hide_wildcard_listening(self):
        for host, expected in (("0.0.0.0", "all"), ("::", "all"), ("127.0.0.1", "local"),
                               ("::1", "local"), ("10.20.30.40", "private"), ("fd00::1", "private")):
            with self.subTest(host=host):
                view = tls.address_view({"host": host, "port": 8890, "tls": True})
                self.assertEqual(view["listener_scope"], expected)
                if expected == "all":
                    self.assertEqual(view["scope"], "local")
                    text = installation.describe_hosting({**view, "state": "running", "verified": True})
                    self.assertIn("all interfaces", text)
                    self.assertNotIn("Access: only", text)

    def test_container_public_name_is_separate_from_its_internal_listener(self):
        with mock.patch.object(platform, "containerized", return_value=True):
            for public, scope in (("127.0.0.1", "local"), ("10.20.30.40", "private"), ("altitude.example.org", "network")):
                view = tls.address_view({"host": "0.0.0.0", "public_host": public, "port": 8890, "tls": True})
                self.assertEqual(view["listener_scope"], "all")
                self.assertEqual(view["scope"], scope)
                self.assertEqual(view["computer"], "Altitude container")
                self.assertFalse(view["packaged"])
