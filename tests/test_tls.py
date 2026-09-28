"""Real disposable TLS identities and handshakes; no host trust stores or services."""
import json
import re
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest import mock

from tests.support import SUITE
from altitude import config, installation, tls


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
        self.assertEqual(authority["name"], "Altitude local CA")
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
        self.assertEqual((info["ca_name"], info["ca_scope"]), ("Altitude local CA", described))
        self.assertIn("SHA-256", " ".join(info["trust_steps"]))

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
                arguments[arguments.index("-days") + 1] = "-1"
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
            "-days", "-1", "-out", self.directory / "expired-ca.crt")
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


class TestShare(unittest.TestCase):
    """`alt tls-share` offers only the public CA, over plain HTTP, on the address a phone reaches."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=SUITE)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in {"TLS_DIR": self.root / "configuration" / "tls", "HOST": "127.0.0.1",
                            "ROOT": self.root / "runtime", "SOURCE": self.root / "source",
                            "PROJECT_ROOTS": [self.root / "projects"],
                            "PROJECTS_FILE": self.root / "runtime" / "projects.json"}.items():
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        tls.initialize()

    def test_a_loopback_or_wildcard_address_is_refused_with_the_setting_to_change(self):
        for host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            with self.subTest(host=host), mock.patch.object(config, "HOST", host), \
                    self.assertRaisesRegex(tls.TLSFailure, "Set ALTITUDE_HOST"):
                tls.share(out=lambda line: self.fail(f"printed {line!r} before refusing"))

    def test_serves_only_the_certificate_and_prints_what_the_phone_must_match(self):
        import threading
        import urllib.error
        import urllib.request
        lines, printed = [], threading.Event()

        def out(line):
            lines.append(line)
            printed.set()

        # The suite may bind only loopback, so loopback stands in for the phone's network address here.
        with mock.patch.object(tls, "_phone_host", return_value=("IP", "127.0.0.1")):
            sharing = threading.Thread(target=tls.share, kwargs={"minutes": 0.05, "out": out})
            sharing.start()
            self.assertTrue(printed.wait(10))
            steps = lines[0]
            link = re.search(r"http://127\.0\.0\.1:\d+/ca\.crt", steps).group(0)
            with urllib.request.urlopen(link, timeout=5) as response:
                self.assertEqual(response.headers["Content-Type"], "application/x-x509-ca-cert")
                self.assertEqual(response.read(), (config.TLS_DIR / "ca.crt").read_bytes())
            for other in ("/", "/ca.key", "/server.key", "/api/health"):
                with self.subTest(path=other), self.assertRaises(urllib.error.HTTPError) as refused:
                    urllib.request.urlopen(link.replace("/ca.crt", other), timeout=5)
                self.assertEqual(refused.exception.code, 404)
            sharing.join(15)
        self.assertFalse(sharing.is_alive(), "the link closes by itself")
        authority = tls.identity(config.TLS_DIR / "ca.crt")
        pairs = authority["sha256"].split(":")
        for row in (pairs[:8], pairs[8:16], pairs[16:24], pairs[24:]):
            self.assertIn(" ".join(row), steps)
        self.assertIn("contains only a Certificate, named Altitude local CA", steps)
        self.assertIn("Before tapping Install", steps)
        self.assertIn(tls.describe_scope(authority["scope"]), steps)
        self.assertIn("Certificate Trust Settings > turn on Altitude local CA", steps)
        self.assertIn(f"open {tls.url()} in a new Private tab", steps)
        self.assertEqual(lines[1:], ["Sent the certificate to 127.0.0.1.", "The link is closed."])

    def test_a_stalled_client_neither_blocks_the_phone_nor_outlives_the_link(self):
        import socket
        import threading
        import urllib.request
        lines, printed = [], threading.Event()

        def out(line):
            lines.append(line)
            printed.set()

        with mock.patch.object(tls, "_phone_host", return_value=("IP", "127.0.0.1")):
            sharing = threading.Thread(target=tls.share, kwargs={"minutes": 0.05, "out": out})
            sharing.start()
            self.assertTrue(printed.wait(10))
            link = re.search(r"http://127\.0\.0\.1:\d+/ca\.crt", lines[0]).group(0)
            port = int(link.split(":")[2].split("/")[0])
            with socket.create_connection(("127.0.0.1", port), timeout=10) as stalled:
                stalled.sendall(b"GET /ca.crt HTTP/1.1\r\n")  # headers never finish
                with urllib.request.urlopen(link, timeout=5) as response:
                    self.assertEqual(response.read(), (config.TLS_DIR / "ca.crt").read_bytes())
                sharing.join(15)
                self.assertFalse(sharing.is_alive(), "the link closes on time despite the open request")
                self.assertEqual(stalled.recv(1024), b"", "the stalled request never receives the certificate")
        self.assertEqual(lines[-1], "The link is closed.")
