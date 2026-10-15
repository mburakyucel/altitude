"""Actual HTTPS/process identity stays separate from the calling shell and device trust."""
import os
import json
import shlex
from unittest import mock

from tests.test_installation_probe import ProbeCase
from altitude import access, config, installation, platform, tls


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
        self.patch(platform, "trust_stores", return_value=[])  # A computer without browsers, whatever the runner has.
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

    def test_doctor_reports_browser_pairing_separately_from_certificate_trust(self):
        self.patch(access, "DIR", self.tmp / "no-paired-browsers")
        with mock.patch.object(installation, "_gh_signed_in", return_value=False):
            report = installation.doctor()
        self.assertEqual(report["pairing"], {"paired": 0, "code_expires": None})
        text = installation.describe_doctor(report)
        self.assertIn("Browser pairing: no browser is paired yet.", text)
        self.assertIn("Browser certificate trust: unverified until a browser opens Altitude without a warning.", text)
        two = [{"id": "a"}, {"id": "b"}]
        with mock.patch.object(installation, "_gh_signed_in", return_value=False), \
                mock.patch.object(access, "devices", return_value=two):
            text = installation.describe_doctor(installation.doctor())
        self.assertIn("Browser pairing: 2 browsers paired; alt pair prints a code for another.", text)
        self.assertNotIn("Browser certificate trust", text, "a paired browser already trusts the certificate")
        with mock.patch.object(installation, "_gh_signed_in", return_value=False), \
                mock.patch.object(access, "devices", side_effect=ValueError("Pairing data is invalid; the file is retained.")):
            text = installation.describe_doctor(installation.doctor())
        self.assertIn("Browser pairing: unknown; Pairing data is invalid; the file is retained.", text)

    def test_doctor_next_follows_the_first_unfinished_step(self):
        self.patch(access, "DIR", self.tmp / "pairing")
        self.patch(installation, "_gh_signed_in", return_value=True)
        missing = {"key": "agents", "label": "Install at least one coding agent: Alpha or Beta",
                   "step": "Install at least one coding agent: Alpha or Beta", "state": "unmet",
                   "command": None, "options": [{"label": "Alpha", "command": "alpha-install"}]}
        self.patch(installation, "prerequisites", return_value=[missing])
        access.issue_code()
        first = installation.describe_doctor(installation.doctor())
        self.assertRegex(first, r"Next: open the private link the installer printed\. It works once, until .+\.")
        self.assertNotIn("Next: open the URL for browser setup.", first)
        self.patch(access, "code_expires", return_value=None)
        self.assertIn("Next: compare the certificate below with what your browser shows, trust it, then run alt pair.",
                      installation.describe_doctor(installation.doctor()))
        self.patch(access, "devices", return_value=[{"id": "a"}])
        report = installation.doctor()
        self.assertEqual(report["next"], ["Next: Install at least one coding agent: Alpha or Beta.", "  Alpha: alpha-install"])
        self.assertIn({"name": "coding agent", "state": "unavailable",
                       "detail": "Install at least one coding agent: Alpha or Beta; Alpha: alpha-install"}, report["checks"])
        signed_out = {"key": "github", "label": "GitHub CLI signed in", "step": "Sign in to the GitHub CLI", "state": "unmet",
                      "command": "gh auth login"}
        installation.prerequisites.return_value = [signed_out, missing]
        self.assertEqual(installation.doctor()["next"], ["Next: Sign in to the GitHub CLI.", "  gh auth login"],
                         "Next names the action, never the state the row reaches")
        installation.prerequisites.return_value = []
        self.assertEqual(installation.doctor()["next"], ["Next: nothing is left to set up; open Altitude in your browser."])

    def test_stopped_address_is_configured_and_never_probed(self):
        self.native["ActiveState"] = "inactive"
        with mock.patch.object(tls, "verify_service", side_effect=AssertionError("stopped service was probed")):
            status = installation.hosting_status()
        self.assertEqual(status["state"], "stopped")
        self.assertFalse(status["verified"])
        self.assertIn("Configured address (not serving)", installation.describe_hosting(status))

    def test_unavailable_native_manager_keeps_saved_configuration_without_claiming_it_is_serving(self):
        saved = self.tmp / "install.json"
        saved.write_text(json.dumps({"environment": {"ALTITUDE_HOST": "127.0.0.1", "ALTITUDE_PORT": str(config.PORT),
                                    "ALTITUDE_TLS_DIR": str(config.TLS_DIR), "ALTITUDE_TLS": "1"}}))
        with mock.patch.object(config, "RELEASE", {"version": self.version}), \
             mock.patch.object(installation, "_settings", return_value=saved), \
             mock.patch.object(platform, "status", side_effect=RuntimeError("native manager unavailable")):
            status = installation.hosting_status()
        self.assertEqual(status["state"], "unknown")
        self.assertIsNone(status["url"])
        self.assertFalse(status["verified"])
        self.assertEqual(status["tls_dir"], str(config.TLS_DIR))
        self.assertIn("Configured address (not verified)", installation.describe_hosting(status))
        self.assertNotIn("not serving", installation.describe_hosting(status))

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
                               ("::1", "local"), ("10.20.30.40", "private"), ("100.100.1.2", "private"),
                               ("fd00::1", "private"), ("203.0.113.5", "network")):
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

    def test_a_vpn_address_the_ca_permits_is_a_supported_bind(self):
        with mock.patch.object(platform.socket, "socket"):  # the fixture computer has no such interface
            self.assertEqual(platform.service_address("100.100.1.2"), "100.100.1.2")
            for public in ("203.0.113.5", "169.254.1.1", "0.0.0.0"):
                with self.subTest(public=public), self.assertRaisesRegex(ValueError, "Choose loopback or a private"):
                    platform.service_address(public)

    def test_an_unlocatable_address_fails_only_the_network_card(self):
        from altitude import server
        with mock.patch.object(server, "_located", side_effect=tls.TLSFailure("fixture certificate has no name")):
            view = server.machine_view()
        self.assertEqual(view["network"], {"error": "fixture certificate has no name"})
        self.assertIn("update_check", view)

    def test_trust_guide_needs_no_source_lookup(self):
        with mock.patch.object(config, "RELEASE", None), \
                mock.patch.object(tls.subprocess, "run", side_effect=AssertionError("guide ran a process")):
            self.assertIn(f"/blob/{config.SOURCE_BRANCH}/docs/SETUP.md#", tls.setup_guide())
        with mock.patch.object(config, "RELEASE", {"version": "v9.8.7"}):
            self.assertIn("/blob/v9.8.7/docs/SETUP.md#", tls.setup_guide())
