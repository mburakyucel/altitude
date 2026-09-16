"""Real disposable TLS identities and handshakes; no host trust stores or services."""
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest import mock

from tests.support import SUITE
from altitude import config, tls


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
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for name in ("ca.key", "server.key"):
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
        context = tls.check()
        for host in ("localhost", "127.0.0.1", "::1"):
            self.assertEqual(handshake(context, self.directory / "ca.crt", host), b"typed conversation")

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
        with self.assertRaises(tls.TLSFailure):
            tls.check("other.example")

    def test_explicit_host_change_keeps_the_trusted_ca_and_leaf_key(self):
        self.create()
        before = self.snapshot()
        tls.initialize("192.0.2.15")
        after = self.snapshot()
        for name in ("ca.crt", "ca.key", "server.key"):
            self.assertEqual(before[name], after[name])
        self.assertNotEqual(before["server.crt"], after["server.crt"])
        self.assertEqual(handshake(tls.check("192.0.2.15"), self.directory / "ca.crt", "192.0.2.15"),
                         b"typed conversation")

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
