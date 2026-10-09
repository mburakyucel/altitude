"""Real disposable TLS identities and handshakes; no host trust stores or services."""
import json
import os
import re
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.support import ALT, SUITE
from altitude import access, config, installation, platform, tls

ORIGINAL_STATUS = platform.status

# A validity period already over. OpenSSL 3.4 and newer refuse negative -days but take explicit dates.
_help = subprocess.run(["openssl", "x509", "-help"], capture_output=True, text=True)
EXPIRED = (["-not_before", "20200101000000Z", "-not_after", "20200102000000Z"]
           if "-not_after" in _help.stdout + _help.stderr else ["-days", "-1"])


def handshake(server_context, ca: Path | None, host="localhost"):
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if ca is not None:
        client_context.load_verify_locations(cafile=ca)
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(client_in, client_out, server_hostname=host)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    completed = set()
    for _ in range(20):
        for name, endpoint in (("client", client), ("server", server)):
            try:
                endpoint.do_handshake()
                completed.add(name)
            except ssl.SSLWantReadError:
                pass
        client_in.write(server_out.read())
        server_in.write(client_out.read())
        if len(completed) == 2:
            client.write(b"typed conversation")
            server_in.write(client_out.read())
            return server.read()
    raise AssertionError("TLS handshake did not complete")


class TestTLS(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=SUITE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "configuration" / "tls"
        for name, value in {"TLS_DIR": self.directory, "HOST": "localhost", "ROOT": self.root / "runtime",
                            "SOURCE": self.root / "source", "PROJECT_ROOTS": [self.root / "projects"],
                            "PROJECTS_FILE": self.root / "runtime" / "projects.json"}.items():
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self):
        return tls.initialize()

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()}

    def test_install_identity_is_private_idempotent_and_trust_is_unknown(self):
        result = self.create()
        before = self.snapshot()
        self.assertEqual(tls.initialize(), result)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(result["managed"])
        self.assertEqual(result["trust"], "unknown")
        self.assertIn("sha256", result["ca_sha256"].lower())
        self.assertIn("Certificate Trust Settings", " ".join(result["trust_steps"]))
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for name in ("ca.key", "server.key"):
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
        context = tls.check()
        for host in ("localhost", "127.0.0.1", "::1"):
            self.assertEqual(handshake(context, self.directory / "ca.crt", host), b"typed conversation")

    def test_doctor_names_the_ca_fingerprint_url_and_device_steps(self):
        self.create()
        with mock.patch.object(installation.shutil, "which", return_value=None), \
             mock.patch("altitude.platform.status", side_effect=RuntimeError("no service in tests")):
            trust = installation.doctor()["certificate_trust"]
        self.assertEqual(trust["state"], "unknown", "local certificate validity does not prove device trust")
        self.assertEqual(trust["url"], f"https://localhost:{config.PORT}")
        self.assertEqual(trust["ca_cert"], str(self.directory / "ca.crt"))
        self.assertEqual(trust["ca_sha256"], tls.info()["ca_sha256"])
        self.assertIn("Certificate Trust Settings", " ".join(trust["trust_steps"]))
        with mock.patch.object(installation.shutil, "which", return_value=None), \
             mock.patch("altitude.platform.status", side_effect=RuntimeError("no service in tests")), \
             mock.patch.object(tls, "info", side_effect=PermissionError("tls directory unreadable")):
            report = installation.doctor()
        self.assertEqual(report["certificate_trust"], {"state": "unavailable", "detail": "tls directory unreadable"})
        self.assertTrue(report["checks"], "the remaining diagnostics are still reported")

    def test_device_url_brackets_ipv6_and_names_localhost_for_a_wildcard_bind(self):
        for host, expected in (("fd00::1", f"https://[fd00::1]:{config.PORT}"),
                               ("0.0.0.0", f"https://localhost:{config.PORT}"),
                               ("10.1.2.3", f"https://10.1.2.3:{config.PORT}")):
            with self.subTest(host=host):
                self.assertEqual(tls.url(host), expected)

    def test_missing_trust_and_wrong_host_fail_the_real_handshake(self):
        self.create()
        context = tls.check()
        with self.assertRaises(ssl.SSLCertVerificationError):
            handshake(context, None)
        with self.assertRaises(ssl.SSLCertVerificationError):
            handshake(context, self.directory / "ca.crt", "another.example")

    def test_configured_dns_or_ip_is_in_certificate_without_replacing_localhost(self):
        tls.initialize("trial.example")
        context = tls.check("trial.example")
        self.assertEqual(handshake(context, self.directory / "ca.crt", "trial.example"), b"typed conversation")
        self.assertEqual(handshake(context, self.directory / "ca.crt"), b"typed conversation")
        with self.assertRaisesRegex(tls.TLSFailure, "other.example is outside it"):
            tls.check("other.example")
        # A configured DNS name permits its own subtree, not its parent or siblings.
        self.assertEqual(handshake(tls.check("www.trial.example"), self.directory / "ca.crt", "www.trial.example"),
                         b"typed conversation")
        with self.assertRaisesRegex(tls.TLSFailure, "example is outside it"):
            tls.check("example")

    def test_changed_host_reissues_the_leaf_under_the_same_trusted_ca(self):
        self.create()
        before = self.snapshot()
        context = tls.check("10.20.30.40")  # startup with a new private bind address
        after = self.snapshot()
        for name in ("ca.crt", "ca.key", "server.key"):
            self.assertEqual(before[name], after[name])
        self.assertNotEqual(before["server.crt"], after["server.crt"])
        self.assertEqual(handshake(context, self.directory / "ca.crt", "10.20.30.40"), b"typed conversation")
        tls.initialize("100.64.1.2")
        self.assertEqual(handshake(tls.check("100.64.1.2"), self.directory / "ca.crt", "100.64.1.2"),
                         b"typed conversation")
        self.assertEqual(self.snapshot()["ca.crt"], before["ca.crt"])

    def test_new_ca_cannot_vouch_for_public_addresses_or_names(self):
        self.create()
        constraints = tls._openssl("x509", "-in", self.directory / "ca.crt", "-noout", "-ext", "nameConstraints").stdout
        self.assertIn("critical", constraints)
        self.assertIn("IP:192.168.0.0/255.255.0.0", constraints)
        before = self.snapshot()
        for host in ("192.0.2.15", "public.example"):
            with self.subTest(host=host), self.assertRaisesRegex(tls.TLSFailure, "private-network"):
                tls.check(host)
            self.assertEqual(self.snapshot(), before)
        # A leaf the CA key signs for a public name still fails a real client's verification.
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            forged = tls._issue(self.directory, Path(temporary), "public.example")
            (self.directory / "server.crt").write_bytes(forged.read_bytes())
        context = tls._load(self.directory)
        with self.assertRaisesRegex(ssl.SSLCertVerificationError, "subtree"):
            handshake(context, self.directory / "ca.crt", "public.example")
        with tempfile.TemporaryDirectory(dir=SUITE) as other, \
                mock.patch.object(config, "TLS_DIR", Path(other) / "tls"):
            with self.assertRaisesRegex(tls.TLSFailure, "192.0.2.15 is outside it"):
                tls.initialize("192.0.2.15")
            self.assertFalse((Path(other) / "tls").exists())

    def test_private_names_and_wildcard_bind_are_covered(self):
        self.create()
        self.assertEqual(handshake(tls.check("fd00::1"), self.directory / "ca.crt", "fd00::1"), b"typed conversation")
        with self.assertRaisesRegex(tls.TLSFailure, "2001:db8::1 is outside it"):
            tls.check("2001:db8::1")
        self.assertEqual(handshake(tls.check("studio.local"), self.directory / "ca.crt", "studio.local"),
                         b"typed conversation")
        self.assertEqual(handshake(tls.check("0.0.0.0"), self.directory / "ca.crt"), b"typed conversation")

    def test_existing_unconstrained_ca_is_retained(self):
        run = tls._openssl

        def legacy(*args, **kwargs):
            arguments = list(args)
            if "-addext" in arguments and any(str(a).startswith("nameConstraints") for a in arguments):
                index = next(i for i, a in enumerate(arguments) if str(a).startswith("nameConstraints"))
                del arguments[index - 1:index + 1]
            return run(*arguments, **kwargs)

        with mock.patch.object(tls, "_openssl", side_effect=legacy):
            self.create()
        ca = (self.directory / "ca.crt").read_bytes()
        self.assertEqual(handshake(tls.check("192.0.2.15"), self.directory / "ca.crt", "192.0.2.15"),
                         b"typed conversation")
        self.assertEqual((self.directory / "ca.crt").read_bytes(), ca)
        # Its identity says so plainly instead of assuming Altitude's limits.
        self.assertIsNone(tls.identity(self.directory / "ca.crt")["scope"])
        self.assertTrue(tls.info()["ca_scope"].startswith("No limits: this CA can vouch for any website"))

    def test_identity_reads_name_fingerprint_and_scope_from_the_certificate(self):
        tls.initialize("trial.example")
        authority = tls.identity(self.directory / "ca.crt")
        # Each installation's CA carries its own random label, so a device can tell installations apart.
        self.assertRegex(authority["name"], r"^Altitude CA [2-9A-HJ-NP-Z]{4}$")
        self.assertEqual(f"sha256 Fingerprint={authority['sha256']}", tls.info("trial.example")["ca_sha256"])
        self.assertRegex(authority["sha256"], r"^[0-9A-F]{2}(:[0-9A-F]{2}){31}$")
        self.assertEqual(authority["scope"]["excluded"], [])
        for entry in ("DNS:localhost", "DNS:home.arpa", "DNS:trial.example", "IP:10.0.0.0/8", "IP:100.64.0.0/10",
                      "IP:::1/128", "IP:fc00::/7"):
            self.assertIn(entry, authority["scope"]["permitted"])
        described = tls.describe_scope(authority["scope"])
        self.assertTrue(described.startswith("Names under localhost, local, internal, home.arpa, trial.example and "
                                             "their subdomains; addresses in 127.0.0.0/8, 10.0.0.0/8"), described)
        self.assertIn("trial.example", described, "a configured, possibly public, name is part of the scope")
        info = tls.info("trial.example")
        self.assertEqual((info["ca_name"], info["ca_scope"]), (authority["name"], described))
        self.assertIn("SHA-256", " ".join(info["trust_steps"]))

    def test_a_test_devices_identity_can_carry_the_trust_checks_second_certificate(self):
        folder = self.root / "fixture"
        tested = tls.fixture(folder, probe=True)
        self.assertEqual(sorted(str(path.relative_to(folder)) for path in folder.rglob("*")),
                         ["ca.crt", "probe", "probe/server.crt", "probe/server.key", "server.crt", "server.key"])
        self.assertEqual(tested["probe"]["issuer"], tested["ca"]["subject"])
        self.assertNotEqual(tested["probe"]["sha256"], tested["server"]["sha256"])
        self.assertEqual(handshake(tls._load(folder / "probe"), folder / "ca.crt", "127.0.0.1"), b"typed conversation")

    def test_a_test_devices_identity_is_the_installation_profile_with_the_cas_key_gone(self):
        folder = self.root / "fixture"
        tested = tls.fixture(folder)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["ca.crt", "server.crt", "server.key"],
                         "nothing else can be signed with the CA")
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(handshake(tls._load(folder), folder / "ca.crt", "127.0.0.1"), b"typed conversation")
        server = tested["server"]
        self.assertEqual((server["subject"], server["issuer"], server["key"], server["signature"]),
                         ("CN=Altitude", tested["ca"]["subject"], "id-ecPublicKey (256 bit) P-256", "ecdsa-with-SHA256"))
        self.assertRegex(tested["ca"]["subject"], r"^CN=Altitude CA [2-9A-HJ-NP-Z]{4}$")
        self.assertEqual(server["extensions"]["Extended Key Usage"], "TLS Web Server Authentication")
        self.assertEqual(server["extensions"]["Subject Alternative Name"],
                         "DNS:localhost, IP Address:127.0.0.1, IP Address:0:0:0:0:0:0:0:1")
        self.assertEqual(tested["ca"]["extensions"]["Basic Constraints"], "critical; CA:TRUE, pathlen:0")
        self.assertRegex(server["sha256"], r"^[0-9A-F]{2}(:[0-9A-F]{2}){31}$")
        self.assertRegex(server["not_after"], r" GMT$")
        tls.initialize()   # an installation on localhost certifies the same names
        for name in ("ca", "server"):
            installed = tls.details(self.directory / f"{name}.crt")
            for found in (installed, tested[name]):
                for varying in ("not_before", "not_after", "sha256", "issuer") + (("subject",) if name == "ca" else ()):
                    found.pop(varying)  # the CA's name carries each installation's own random label
                for identifier in ("Subject Key Identifier", "Authority Key Identifier"):
                    found["extensions"].pop(identifier, None)
            self.assertEqual(tested[name], installed, f"the fixture's {name} certificate is the installation's profile")

    def test_identity_of_an_external_ca_keeps_its_own_name_and_limits(self):
        def external(subject, *extensions):
            certificate = self.root / f"external-{len(extensions)}.crt"
            tls._openssl("req", "-x509", "-utf8", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                         "-keyout", self.root / "external.key", "-out", certificate, "-days", "30", "-subj", subject,
                         *extensions)
            return tls.identity(certificate)

        # iOS lists the CA by this name, so escaped punctuation and non-ASCII letters read as written.
        authority = external("/O=Home\\, Inc./CN=Café studio\\, CA", "-addext",
                             "nameConstraints=critical,permitted;DNS:studio.example,excluded;IP:10.9.0.0/255.255.0.0")
        self.assertEqual(authority["name"], "Café studio, CA")
        self.assertEqual(authority["scope"], {"permitted": ["DNS:studio.example"], "excluded": ["IP:10.9.0.0/16"]})
        # Constraints limit each name type on its own: limiting names leaves every other IP address.
        self.assertEqual(tls.describe_scope(authority["scope"]),
                         "Names under studio.example and their subdomains; any IP address except 10.9.0.0/16.")
        authority = external("/CN=Addresses only", "-addext", "nameConstraints=critical,permitted;IP:10.9.0.0/255.255.0.0")
        self.assertEqual(tls.describe_scope(authority["scope"]), "Any website name; addresses in 10.9.0.0/16.")
        self.assertEqual(tls.describe_scope({"permitted": ["email:studio.example"], "excluded": []}), tls.NO_LIMITS)
        for subject in ("/CN=Studio CA+OU=Home", "/OU=Home+CN=Studio CA"):
            with self.subTest(subject=subject):
                self.assertEqual(tls._openssl("req", "-x509", "-multivalue-rdn", "-newkey", "ec", "-pkeyopt",
                                              "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", self.root / "rdn.key",
                                              "-out", self.root / "rdn.crt", "-days", "30", "-subj", subject).returncode, 0)
                self.assertEqual(tls.identity(self.root / "rdn.crt")["name"], "Studio CA")
        unnamed = external("/O=No common name")
        self.assertEqual((unnamed["name"], unnamed["scope"]), ("O=No common name", None))

    def test_leaf_renewal_keeps_ca_and_key_and_reloads_existing_context(self):
        self.create()
        before = self.snapshot()
        context = tls.check()
        with mock.patch.object(tls, "RENEW_SECONDS", 366 * 86400):
            self.assertIs(tls.check(context=context), context)
        after = self.snapshot()
        self.assertNotEqual(before["server.crt"], after["server.crt"])
        for name in ("ca.crt", "ca.key", "server.key"):
            self.assertEqual(before[name], after[name])
        self.assertEqual(handshake(context, self.directory / "ca.crt"), b"typed conversation")

    def test_expired_leaf_recovers_but_expired_ca_requires_explicit_retrust(self):
        self.create()
        ca_before = (self.directory / "ca.crt").read_bytes()
        run = tls._openssl

        def expired_leaf(*args, **kwargs):
            arguments = list(args)
            if "-days" in arguments:
                at = arguments.index("-days")
                arguments[at:at + 2] = EXPIRED
            return run(*arguments, **kwargs)

        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            with mock.patch.object(tls, "_openssl", side_effect=expired_leaf):
                expired = tls._issue(self.directory, Path(temporary), None)
            (self.directory / "server.crt").write_bytes(expired.read_bytes())
        with self.assertRaises(tls.TLSFailure):
            tls.check(renew=False)
        self.assertEqual(handshake(tls.check(), self.directory / "ca.crt"), b"typed conversation")
        self.assertEqual((self.directory / "ca.crt").read_bytes(), ca_before)
        run("x509", "-in", self.directory / "ca.crt", "-signkey", self.directory / "ca.key",
            *EXPIRED, "-out", self.directory / "expired-ca.crt")
        (self.directory / "ca.crt").write_bytes((self.directory / "expired-ca.crt").read_bytes())
        before = self.snapshot()
        with self.assertRaises(tls.TLSFailure):
            tls.initialize()
        self.assertEqual(self.snapshot(), before)

    def test_failed_renewal_retains_disk_identity_and_active_context(self):
        self.create()
        before = self.snapshot()
        context = tls.check()
        with mock.patch.object(tls, "RENEW_SECONDS", 366 * 86400), \
             mock.patch.object(tls, "_issue", side_effect=tls.TLSFailure("signing failed")):
            with self.assertRaisesRegex(tls.TLSFailure, "signing failed"):
                tls.check(context=context)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(handshake(context, self.directory / "ca.crt"), b"typed conversation")
        self.assertFalse(list(self.directory.glob(".renew-*")))

    def test_renewal_write_failure_preserves_working_certificate_and_is_actionable(self):
        self.create()
        before = self.snapshot()
        with mock.patch.object(tls, "RENEW_SECONDS", 366 * 86400), \
             mock.patch.object(tls.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(tls.TLSFailure, "disk full.*retained"):
                tls.check()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(handshake(tls.check(), self.directory / "ca.crt"), b"typed conversation")

    def test_unmanaged_external_pair_is_not_renewed_or_given_a_new_ca(self):
        self.create()
        (self.directory / tls._MARKER).unlink()
        (self.directory / "ca.key").unlink()
        before = self.snapshot()
        with mock.patch.object(tls, "RENEW_SECONDS", 366 * 86400):
            result = tls.initialize()
        self.assertFalse(result["managed"])
        self.assertEqual(self.snapshot(), before)

    def test_invalid_or_missing_keys_never_regenerate_a_trusted_identity(self):
        self.create()
        ca = (self.directory / "ca.crt").read_bytes()
        (self.directory / "ca.key").unlink()
        with self.assertRaisesRegex(tls.TLSFailure, "Restore it"):
            tls.initialize()
        self.assertEqual((self.directory / "ca.crt").read_bytes(), ca)
        self.assertFalse((self.directory / "ca.key").exists())

    def test_corrupt_certificate_and_permissive_key_fail_closed(self):
        self.create()
        (self.directory / "server.key").chmod(0o644)
        with self.assertRaisesRegex(tls.TLSFailure, "key mode 600"):
            tls.check()
        (self.directory / "server.key").chmod(0o600)
        (self.directory / "server.crt").write_text("not a certificate")
        with self.assertRaisesRegex(tls.TLSFailure, "Cannot load HTTPS"):
            tls.check()

    def test_mismatched_leaf_key_and_permissive_directory_fail_closed(self):
        self.create()
        self.directory.chmod(0o755)
        with self.assertRaisesRegex(tls.TLSFailure, "directory mode 700"):
            tls.check()
        self.directory.chmod(0o700)
        (self.directory / "server.key").write_bytes((self.directory / "ca.key").read_bytes())
        with self.assertRaisesRegex(tls.TLSFailure, "matching key"):
            tls.check()

    def test_mismatched_ca_key_is_reported_before_renewal_is_due(self):
        self.create()
        (self.directory / "ca.key").write_bytes((self.directory / "server.key").read_bytes())
        before = self.snapshot()
        with self.assertRaisesRegex(tls.TLSFailure, "signing key do not match"):
            tls.check()
        self.assertEqual(self.snapshot(), before)

    def test_missing_installation_does_not_fall_back_to_http(self):
        with self.assertRaisesRegex(tls.TLSFailure, "Missing HTTPS"):
            tls.check()

    def test_generated_keys_refuse_runtime_source_projects_and_symlink_aliases(self):
        for root in (config.ROOT, config.SOURCE, config.PROJECT_ROOTS[0]):
            with self.subTest(root=root), mock.patch.object(config, "TLS_DIR", root / "tls"):
                with self.assertRaisesRegex(tls.TLSFailure, "outside Altitude"):
                    tls.initialize()
                self.assertFalse((root / "tls").exists())
        config.ROOT.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(config.ROOT, target_is_directory=True)
        with mock.patch.object(config, "TLS_DIR", alias / "tls"):
            with self.assertRaisesRegex(tls.TLSFailure, "outside Altitude"):
                tls.initialize()

    def test_registered_project_outside_discovery_roots_is_also_unsafe(self):
        config.ROOT.mkdir()
        project = self.root / "external-project"
        config.PROJECTS_FILE.write_text(json.dumps({"trial": {"path": str(project)}}))
        before = config.PROJECTS_FILE.read_bytes()
        with mock.patch.object(config, "TLS_DIR", project / "tls"):
            with self.assertRaisesRegex(tls.TLSFailure, "outside Altitude"):
                tls.initialize()
        self.assertEqual(config.PROJECTS_FILE.read_bytes(), before)

    def test_failed_initialization_leaves_no_partial_identity(self):
        with mock.patch.object(tls, "_issue", side_effect=tls.TLSFailure("signing failed")):
            with self.assertRaisesRegex(tls.TLSFailure, "signing failed"):
                tls.initialize()
        self.assertFalse(self.directory.exists())
        self.assertFalse(list(self.directory.parent.glob(".altitude-tls-*")))
        self.create()

    def test_missing_openssl_and_unsafe_host_have_actionable_failures(self):
        with mock.patch.object(tls.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(tls.TLSFailure, "Install OpenSSL"):
                tls.initialize()
        with self.assertRaisesRegex(tls.TLSFailure, "without a port or URL"):
            tls.initialize("example.com\nDNS:another.example")
        self.assertFalse(self.directory.exists())


class ServiceCase(unittest.TestCase):
    """A fictional running service: its settings live only in the record it wrote as it started, while this
    shell carries none of them."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=SUITE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service_tls = self.root / "service-configuration" / "tls"
        for name, value in {"TLS_DIR": self.service_tls, "HOST": "127.0.0.1",
                            "ROOT": self.root / "runtime", "SOURCE": self.root / "source",
                            "PROJECT_ROOTS": [self.root / "projects"],
                            "PROJECTS_FILE": self.root / "runtime" / "projects.json"}.items():
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(access, "DIR", self.root / "home" / ".config" / "altitude" / "access")
        patcher.start()
        self.addCleanup(patcher.stop)
        tls.initialize()
        self.running({"host": "10.20.30.40", "port": 9443})

    def running(self, settings: dict, *, pid: int = 4242):
        with mock.patch.object(tls.os, "getpid", return_value=pid):
            tls.publish({"host": "127.0.0.1", "port": 8890, "tls": True, "tls_dir": self.service_tls, **settings})


class TestServiceDiscovery(ServiceCase):
    def test_container_advertised_identity_comes_from_service_record_not_shell(self):
        self.running({"host": "0.0.0.0", "port": 19443, "public_host": "container.invalid"})
        with mock.patch.object(platform, "containerized", return_value=True), \
             mock.patch.object(config, "PUBLIC_HOST", "wrong-shell.invalid"):
            found = tls.service()
        self.assertEqual(found["url"], "https://container.invalid:19443")
        self.assertEqual(found["host"], "0.0.0.0")

    def test_a_shell_finds_the_address_port_and_certificates_the_running_service_recorded(self):
        with mock.patch.dict(os.environ, {"ALTITUDE_HOST": "10.9.9.9", "ALTITUDE_PORT": "8890",
                                          "ALTITUDE_TLS_DIR": str(self.root / "other")}):
            found = tls.service()
        self.assertEqual({key: found[key] for key in ("host", "port", "tls_dir", "tls", "pid", "url")},
                         {"host": "10.20.30.40", "port": 9443, "tls_dir": self.service_tls, "tls": True,
                          "pid": 4242, "url": "https://10.20.30.40:9443"})
        self.running({"host": "fd00::7", "tls": False})
        self.assertEqual(tls.service()["url"], "http://[fd00::7]:8890")

    def test_a_missing_or_unreadable_record_is_named(self):
        tls.record().unlink()
        with self.assertRaisesRegex(tls.TLSFailure, r"^The Altitude service has not recorded where it listens"):
            tls.service()
        tls.record().write_text(json.dumps({"pid": 4242, "host": "10.20.30.40", "port": "https", "tls": True,
                                            "tls_dir": "/fictional"}))
        with self.assertRaisesRegex(tls.TLSFailure, "Cannot read the Altitude service's record .*invalid literal"):
            tls.service()


class HealthServer:
    """An HTTPS health endpoint with the service's certificate, answering as process `pid`; it answers every
    other request with a fictional issue link."""

    def __init__(self, test: unittest.TestCase, directory: Path, pid: int):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"pid": pid}).encode() if self.path == "/api/health" else b"{}"
                self.send_response(200 if self.path == "/api/health" else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                answered.append(self.path)
                body = json.dumps({"url": "https://example.invalid/issues/42"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.answered = answered = []
        with mock.patch.object(config, "TLS_DIR", directory):
            context = tls.check(renew=False)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)


class TestIdentity(ServiceCase):
    """The service proves, over HTTPS, that the selected CA certifies it under its configured name."""

    def found(self, port: int) -> dict:
        self.running({"port": port})
        return tls.service()

    def test_the_certificate_offered_is_the_one_the_running_service_proves(self):
        health = HealthServer(self, self.service_tls, 4242)
        self.assertEqual(tls._proven(self.found(health.port)), (self.service_tls / "ca.crt").read_bytes())

    def test_another_process_another_ca_or_no_answer_is_refused(self):
        health = HealthServer(self, self.service_tls, 999)
        with self.assertRaisesRegex(tls.TLSFailure, r"^Another process answers at https://127\.0\.0\.1:\d+"):
            tls._proven(self.found(health.port))
        other = self.root / "other-installation" / "tls"
        with mock.patch.object(config, "TLS_DIR", other):
            tls.initialize()
        stranger = HealthServer(self, other, 4242)
        with self.assertRaisesRegex(tls.TLSFailure, "does not prove its identity with the CA certificate "
                                    + re.escape(str(self.service_tls / "ca.crt"))):
            tls._proven(self.found(stranger.port))
        stranger.server.shutdown()
        stranger.server.server_close()
        with self.assertRaisesRegex(tls.TLSFailure, r"^The Altitude service does not answer at https://127\.0\.0\.1"):
            tls._proven(self.found(stranger.port))
        (self.service_tls / "ca.crt").unlink()
        with self.assertRaisesRegex(tls.TLSFailure, "Cannot read the Altitude service's CA certificate"):
            tls._proven(self.found(health.port))


class TestWorkerReach(ServiceCase):
    """A worker launched before the service restarted onto another certificate folder keeps that launch
    environment; its `alt` still reaches altd, and only over HTTPS the recorded folder's CA verifies."""

    def alt(self) -> subprocess.CompletedProcess:
        worker = {**os.environ, "HOME": str(self.root / "home"), "ALTITUDE_HOME": str(config.ROOT),
                  "ALTITUDE_ACTOR": config.OPERATOR_ACTOR,
                  "ALTITUDE_PROJECT": "fictional", "ALTITUDE_HOST": "127.0.0.1", "ALTITUDE_PORT": "8890",
                  "ALTITUDE_TLS": "1", "ALTITUDE_TLS_DIR": str(self.earlier)}
        return subprocess.run([sys.executable, str(ALT), "issue", "close", "42", "--reason", "completed"],
                              input="", capture_output=True, text=True, env=worker, timeout=60)

    def test_a_worker_follows_the_services_new_certificate_folder_and_refuses_one_it_cannot_verify(self):
        self.earlier = self.root / "earlier-configuration" / "tls"
        with mock.patch.object(config, "TLS_DIR", self.earlier):
            tls.initialize()
        service = HealthServer(self, self.service_tls, 4242)
        self.running({"port": service.port})
        result = self.alt()
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "https://example.invalid/issues/42\n", ""))
        self.assertEqual(service.answered, ["/api/issue"])
        # A record naming a CA the answering service cannot prove itself with is refused before any request.
        self.running({"port": service.port, "tls_dir": self.earlier})
        result = self.alt()
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn(f"alt issue: altd at https://127.0.0.1:{service.port} does not prove its identity with "
                      f"{self.earlier / 'ca.crt'}: unable to get local issuer certificate", result.stderr)
        self.assertEqual(service.answered, ["/api/issue"])


class TestShare(ServiceCase):
    """`alt tls-share` offers only the public CA, over plain HTTP, on the address a phone reaches."""

    def test_a_loopback_wildcard_or_plain_http_service_is_refused_before_anything_is_offered(self):
        for settings, message in (({}, "configured for 127.0.0.1, which only this computer can open. For this "
                                       "computer, run alt doctor"),
                                  ({"host": "localhost"}, "configured for localhost"),
                                  ({"host": "::1"}, "configured for ::1"),
                                  ({"host": "0.0.0.0"}, "configured for 0.0.0.0"),
                                  ({"host": "10.20.30.40", "tls": False}, "serves plain HTTP")):
            self.running(settings)
            with self.subTest(settings=settings), self.assertRaisesRegex(tls.TLSFailure, message):
                tls.share(out=lambda line: self.fail(f"printed {line!r} before refusing"))

    def sharing(self, out):
        import threading
        health = HealthServer(self, self.service_tls, 4242)
        self.running({"port": health.port})
        # The suite may bind only loopback, so loopback stands in for the phone's network address here.
        patcher = mock.patch.object(tls, "phone_address")
        patcher.start()
        self.addCleanup(patcher.stop)
        thread = threading.Thread(target=tls.share, kwargs={"minutes": 0.05, "out": out})
        thread.start()
        return thread, health

    def test_serves_the_guide_profile_and_certificate_only_and_prints_a_qr_code_and_the_checks(self):
        import plistlib
        import threading
        import urllib.error
        import urllib.request
        from altitude import qr
        lines, printed = [], threading.Event()

        def out(line):
            lines.append(line)
            printed.set()

        sharing, health = self.sharing(out)
        self.assertTrue(printed.wait(10))
        steps = lines[0]
        link = re.search(r"http://127\.0\.0\.1:\d+/", steps).group(0)
        self.assertIn(qr.terminal(link), steps)
        authority = tls.identity(self.service_tls / "ca.crt")
        certificate = (self.service_tls / "ca.crt").read_bytes()
        with urllib.request.urlopen(link, timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
            self.assertEqual(response.headers["Content-Security-Policy"],
                             "default-src 'none'; style-src 'unsafe-inline'; img-src data:")
            page = response.read().decode()
        for row in tls.fingerprint_rows(authority["sha256"]):
            self.assertIn(row, page)
        # Safari asks a page without an icon for /favicon.ico, which the policy refuses with a console error.
        for text in ('<link rel="icon" href="data:,">', 'href="/altitude.mobileconfig"', 'href="/ca.crt"', "Certificate Trust Settings",
                     f'href="https://127.0.0.1:{health.port}"', "If it differs, stop here.",
                     "Set up this device for Altitude", 'id="linux"', 'id="macos"', 'id="ios"', 'id="android"',
                     "openssl x509 -in ca.crt -noout -subject -fingerprint -sha256",
                     "Installed by you › Trusted Certificates › Import", "Secure Sockets Layer (SSL)",
                     "This HTTP download page alone cannot prove", "Never transfer", "Verify HTTPS before pairing"):
            self.assertIn(text, page)
        with urllib.request.urlopen(link + "altitude.mobileconfig", timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "application/x-apple-aspen-config")
            profile_bytes = response.read()
            self.assertEqual(int(response.headers["Content-Length"]), len(profile_bytes))
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            settings = plistlib.loads(profile_bytes)
        # A phone/browser may inspect the download before fetching it. HEAD must describe the
        # same profile without sending bytes or falsely logging a completed profile download.
        with urllib.request.urlopen(urllib.request.Request(link + "altitude.mobileconfig", method="HEAD"),
                                    timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "application/x-apple-aspen-config")
            self.assertEqual(int(response.headers["Content-Length"]), len(profile_bytes))
            self.assertEqual(response.read(), b"")
        self.assertEqual(settings["PayloadDisplayName"], authority["name"])
        [payload] = settings["PayloadContent"]
        self.assertEqual(payload["PayloadType"], "com.apple.security.root")
        self.assertEqual(payload["PayloadContent"], ssl.PEM_cert_to_DER_cert(certificate.decode()))
        with urllib.request.urlopen(link + "ca.crt", timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "application/x-x509-ca-cert")
            self.assertEqual(response.read(), certificate)
        for other in ("/index.html", "/ca.key", "/server.key", "/api/health", "/pair"):
            with self.subTest(path=other), self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(link[:-1] + other, timeout=5)
            self.assertEqual(refused.exception.code, 404)
        sharing.join(15)
        self.assertFalse(sharing.is_alive(), "the link closes by itself")
        for row in tls.fingerprint_rows(authority["sha256"]):
            self.assertIn(row, steps)
        self.assertIn(f"contains only a Certificate, named {authority['name']}", steps)
        self.assertIn("Before installing", steps)
        self.assertIn("open this link on your desktop", steps)
        self.assertIn("Linux, macOS, iPhone/iPad and Android steps", steps)
        self.assertIn(tls.describe_scope(authority["scope"]), steps)
        self.assertIn(f"Certificate Trust Settings > turn on {authority['name']}", steps)
        self.assertIn(f"open https://127.0.0.1:{health.port} in a new Private tab", steps)
        self.assertEqual(lines[1:], ["Sent the profile to 127.0.0.1.", "Sent the certificate to 127.0.0.1.",
                                     "The link is closed."])
        with self.assertRaises(OSError):
            urllib.request.urlopen(link, timeout=5)

    def test_the_profile_holds_only_the_public_certificate_and_keeps_its_identity_across_reinstalls(self):
        import plistlib
        body = (self.service_tls / "ca.crt").read_bytes()
        authority = tls.identity(self.service_tls / "ca.crt")
        first, again = tls.profile(body, authority), tls.profile(body, authority)
        self.assertEqual(first, again)
        self.assertNotIn(b"PRIVATE KEY", first)
        settings = plistlib.loads(first)
        self.assertEqual([p["PayloadType"] for p in settings["PayloadContent"]], ["com.apple.security.root"])
        self.assertEqual(settings["PayloadType"], "Configuration")

    def test_a_stalled_client_neither_blocks_the_phone_nor_outlives_the_link(self):
        import socket
        import threading
        import urllib.request
        lines, printed = [], threading.Event()

        def out(line):
            lines.append(line)
            printed.set()

        sharing, _ = self.sharing(out)
        self.assertTrue(printed.wait(10))
        link = re.search(r"http://127\.0\.0\.1:\d+/", lines[0]).group(0) + "ca.crt"
        port = int(link.split(":")[2].split("/")[0])
        with socket.create_connection(("127.0.0.1", port), timeout=10) as stalled:
            stalled.sendall(b"GET /ca.crt HTTP/1.1\r\n")  # headers never finish
            with urllib.request.urlopen(link, timeout=5) as response:
                self.assertEqual(response.read(), (self.service_tls / "ca.crt").read_bytes())
            sharing.join(15)
            self.assertFalse(sharing.is_alive(), "the link closes on time despite the open request")
            self.assertEqual(stalled.recv(1024), b"", "the stalled request never receives the certificate")
        self.assertEqual(lines[-1], "The link is closed.")


class TestShareCommand(unittest.TestCase):
    """The operator's ordinary shell runs `alt tls-share` and `alt pair`; the running service's record, not the
    shell's own settings, says where the service listens and which certificate folder it serves from."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=SUITE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def service(self, settings: dict) -> None:
        with mock.patch.object(access, "DIR", self.root / "home" / ".config" / "altitude" / "access"):
            tls.publish({"host": "127.0.0.1", "port": 8890, "tls": True, "tls_dir": self.root / "service-tls",
                         **settings})

    def alt(self, *args: str, entry: Path = ALT, **shell) -> subprocess.CompletedProcess:
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("ALTITUDE_HOST", "ALTITUDE_PORT", "ALTITUDE_TLS", "ALTITUDE_TLS_DIR")}
        environment.update({"HOME": str(self.root / "home"), "ALTITUDE_HOME": str(self.root / "runtime"),
                            "ALTITUDE_ACTOR": config.OPERATOR_ACTOR,
                            **shell})
        return subprocess.run([sys.executable, str(entry), *args], input="", capture_output=True, text=True,
                              env=environment, timeout=60)

    def test_a_release_installation_follows_the_service_over_its_saved_settings(self):
        import shutil
        source = self.root / "application" / "versions" / "trial-1"
        for directory in ("altitude", "bin"):
            shutil.copytree(ALT.parent.parent / directory, source / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (source / "release.json").write_text(json.dumps({"version": "trial-1", "commit": "a" * 40}))
        saved = self.root / "install.json"
        saved.write_text(json.dumps({"environment": {"ALTITUDE_HOST": "10.9.9.9", "ALTITUDE_PORT": "8890",
                                                     "ALTITUDE_TLS_DIR": str(self.root / "saved-tls")}}))
        folder = self.root / "service-tls"
        # A service drop-in overrode the saved settings when the service started.
        self.service({"host": "10.20.30.40", "port": 9443})
        entry = source / "bin" / "alt"
        for shell in ({}, {"ALTITUDE_PORT": "8890", "ALTITUDE_TLS_DIR": str(self.root / "saved-tls")}):
            result = self.alt("tls-share", entry=entry, ALTITUDE_CONFIG=str(saved), **shell)
            self.assertIn(f"alt tls-share: Cannot read the Altitude service's CA certificate {folder / 'ca.crt'}",
                          result.stderr)

    def test_an_ordinary_shell_selects_the_services_address_and_certificate_folder(self):
        folder = self.root / "service-tls"
        self.service({"host": "10.20.30.40", "port": 9443})
        result = self.alt("tls-share")
        # The service's own folder is chosen (it holds no CA here), before any network access or listener.
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(f"alt tls-share: Cannot read the Altitude service's CA certificate {folder / 'ca.crt'}",
                      result.stderr)
        paired = self.alt("pair")
        self.assertEqual(paired.returncode, 0, paired.stderr)
        self.assertIn("On the device, open https://10.20.30.40:9443 and type the code", paired.stdout)
        self.assertNotIn("code=", paired.stdout)  # the code never rides in a link
        self.assertIn("Or scan this with a phone to open it:", paired.stdout)
        self.assertNotIn("certificate", paired.stdout)  # no CA file of its own here: nothing to check against

    def test_true_loopback_missing_service_and_other_actors_are_refused(self):
        self.service({"port": 9443})
        self.assertIn("alt tls-share: The Altitude service is configured for 127.0.0.1, which only this computer "
                      "can open", self.alt("tls-share").stderr)
        for actor in ("l2", "l3"):
            refused = self.alt("tls-share", ALTITUDE_ACTOR=actor)
            self.assertEqual((refused.returncode, refused.stdout), (1, ""))
            self.assertNotIn("service", refused.stderr)
        (self.root / "home" / ".config" / "altitude" / "access" / "service.json").unlink()
        self.assertIn("alt tls-share: The Altitude service has not recorded where it listens",
                      self.alt("tls-share").stderr)
        paired = self.alt("pair")
        self.assertEqual(paired.returncode, 0, paired.stderr)
        self.assertIn("Type the code on the device's Pair this device screen. No address: The Altitude service has "
                      "not recorded where it listens", paired.stdout)
