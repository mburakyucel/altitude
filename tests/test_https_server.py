"""HTTPS startup, identity probes and renewal use isolated sockets and certificates."""
import http.client
import json
import os
import plistlib
import socket
import ssl
import threading
import time
import urllib.request
from unittest import mock

from tests.support import AltitudeCase
from altitude import access, config, server, tasks as T, tls


class TestHTTPSServer(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "TLS", True)
        self.patch(config, "TLS_DIR", self.tmp / "private-tls")
        self.patch(config, "HOST", "127.0.0.1")
        self.patch(config, "PORT", 0)
        self.patch(server, "log")
        self.patch(server, "TRUST", None)  # main() sets the service's trust check
        self.setenv("ALTITUDE_SERVICE", None)
        self.setenv("ALTITUDE_TIMERS", "0")

    def test_missing_https_identity_refuses_before_binding_or_brokers(self):
        with mock.patch.object(server, "ThreadingHTTPServer") as httpd, \
             mock.patch.object(server, "ensure_l3_verb_broker") as broker, \
             mock.patch.object(server.threading, "Thread") as worker:
            with self.assertRaises(SystemExit) as stopped:
                server.main()
        self.assertEqual(stopped.exception.code, 1)
        httpd.assert_not_called()
        broker.assert_not_called()
        worker.assert_not_called()
        self.assertIn("HTTPS startup refused", server.log.call_args.args[0])

    def test_binding_needs_no_reverse_lookup_of_the_address(self):
        # A Mac without a network held http.server's getfqdn in mDNS past the installation's health deadline.
        with mock.patch.object(socket, "getfqdn", side_effect=AssertionError("reverse lookup")), \
                mock.patch.object(socket, "gethostbyaddr", side_effect=AssertionError("reverse lookup")):
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        try:
            self.assertEqual(httpd.server_port, httpd.socket.getsockname()[1])
            self.assertEqual(httpd.server_name, "127.0.0.1")
        finally:
            httpd.server_close()

    def test_devices_certificate_names_the_ca_a_phone_must_match(self):
        self.assertIsNone(server.certificate_view(), "no CA file of its own, nothing to match")
        server.tls_init()
        view = server.certificate_view()
        self.assertRegex(view["name"], r"^Altitude CA [2-9A-HJ-NP-Z]{4}$")
        self.assertEqual(f"sha256 Fingerprint={view['sha256']}", tls.info()["ca_sha256"])
        self.assertTrue(view["scope"].startswith("Names under localhost"), view["scope"])
        (config.TLS_DIR / "ca.crt").write_text("not a certificate\n")
        self.assertIn("HTTPS certificate operation failed", server.certificate_view()["error"])
        self.patch(config, "TLS", False)
        self.assertIsNone(server.certificate_view())

    def test_mismatched_bind_host_refuses_before_binding(self):
        server.tls_init()
        with mock.patch.object(server, "ThreadingHTTPServer") as httpd:
            with self.assertRaises(SystemExit):
                server.main(host="unconfigured.example")
        httpd.assert_not_called()

    def test_real_https_startup_serves_installed_and_source_health_identity(self):
        server.tls_init()
        factory = server.ThreadingHTTPServer
        observed = []

        def create(address, handler):
            httpd = factory(address, handler)
            serve = httpd.serve_forever

            def probe():
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                try:
                    context = ssl.create_default_context(cafile=str(config.TLS_DIR / "ca.crt"))
                    url = f"https://127.0.0.1:{httpd.server_port}/api/health"
                    for release in ({"version": "trial.1", "commit": "a" * 40}, None):
                        with mock.patch.object(config, "RELEASE", release), \
                             mock.patch.object(config, "load_projects", side_effect=AssertionError("health reads state")):
                            with urllib.request.urlopen(url, context=context, timeout=5) as response:
                                observed.append(json.load(response))
                finally:
                    httpd.shutdown()
                    thread.join(5)

            httpd.serve_forever = probe
            return httpd

        with mock.patch.object(server, "ThreadingHTTPServer", side_effect=create), \
             mock.patch.object(server, "ensure_l3_verb_broker"), \
             mock.patch.object(server, "stop_l3_verb_brokers") as stop:
            server.main()
        self.assertEqual(observed, [{"version": "trial.1", "commit": "a" * 40, "pid": os.getpid()},
                                    {"version": None, "commit": None, "pid": os.getpid()}])
        stop.assert_called_once()

    def test_the_service_records_where_clients_reach_it_and_a_serve_only_instance_does_not(self):
        server.tls_init()
        tls.record().unlink(missing_ok=True)
        self.addCleanup(tls.record().unlink, missing_ok=True)
        factory, served = server.ThreadingHTTPServer, []

        def create(address, handler):
            httpd = factory(address, handler)
            httpd.serve_forever = lambda: served.append(httpd.server_port)
            return httpd

        with mock.patch.object(server, "ThreadingHTTPServer", side_effect=create), \
             mock.patch.object(server, "ensure_l3_verb_broker"), mock.patch.object(server, "stop_l3_verb_brokers"), \
             mock.patch.object(server.git_policy, "activate_source"):
            server.main()
            self.assertFalse(tls.record().exists())
            self.setenv("ALTITUDE_SERVICE", "1")
            server.main()
        found = tls.service()
        self.assertEqual({key: found[key] for key in ("host", "port", "tls", "tls_dir", "pid", "url")},
                         {"host": "127.0.0.1", "port": served[-1], "tls": True, "tls_dir": config.TLS_DIR,
                          "pid": os.getpid(), "url": f"https://127.0.0.1:{served[-1]}"})

    def test_a_stalled_or_failed_handshake_never_delays_other_requests(self):
        """I-20260924-205802: one client that never sent its TLS hello timed out activation's quiet check."""
        server.tls_init()
        self.patch(server, "TLS_HANDSHAKE_SECONDS", 0.5)
        factory, observed = server.ThreadingHTTPServer, []

        def create(address, handler):
            httpd = factory(address, handler)
            serve = httpd.serve_forever

            def probe():
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                address = ("127.0.0.1", httpd.server_port)
                stalled = socket.create_connection(address)  # opens TCP, never sends a ClientHello
                plain = socket.create_connection(address)  # plain HTTP against the HTTPS port
                try:
                    plain.sendall(b"GET /api/health HTTP/1.1\r\nHost: x\r\n\r\n")
                    context = ssl.create_default_context(cafile=str(config.TLS_DIR / "ca.crt"))
                    with urllib.request.urlopen(f"https://127.0.0.1:{httpd.server_port}/api/health",
                                                context=context, timeout=2) as response:
                        observed.append(response.status)
                    stalled.settimeout(3)
                    observed.append(stalled.recv(1))  # the server drops the stalled handshake at its bound
                finally:
                    stalled.close(); plain.close()
                    httpd.shutdown()
                    thread.join(5)

            httpd.serve_forever = probe
            return httpd

        with mock.patch.object(server, "ThreadingHTTPServer", side_effect=create), \
             mock.patch.object(server, "ensure_l3_verb_broker"), \
             mock.patch.object(server, "stop_l3_verb_brokers"):
            server.main()
        self.assertEqual(observed, [200, b""])

    def test_https_change_stream_reports_a_task_and_ends_when_the_client_leaves(self):
        server.tls_init()
        self.patch(server, "CHANGE_SECONDS", 0.05)
        factory, changes, ended, observed = server.ThreadingHTTPServer, server.Handler._changes, threading.Event(), []

        def stream(handler):
            try:
                changes(handler)
            finally:
                ended.set()

        def create(address, handler):
            httpd = factory(address, handler)
            serve = httpd.serve_forever

            def probe():
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                try:
                    context = ssl.create_default_context(cafile=str(config.TLS_DIR / "ca.crt"))
                    conn = http.client.HTTPSConnection("127.0.0.1", httpd.server_port, context=context, timeout=5)
                    conn.request("GET", "/api/changes")
                    res = conn.getresponse()
                    res.fp.readline(), res.fp.readline()  # the retry field
                    T.new(self.project, "Keep pagination", "Fictional request.")
                    observed.extend(res.fp.readline() for _ in range(2))
                    res.close()
                    conn.close()
                    observed.append(ended.wait(3))
                finally:
                    httpd.shutdown()
                    thread.join(5)

            httpd.serve_forever = probe
            return httpd

        with mock.patch.object(server, "ThreadingHTTPServer", side_effect=create), \
             mock.patch.object(server.Handler, "_changes", stream), \
             mock.patch.object(server, "ensure_l3_verb_broker"), \
             mock.patch.object(server, "stop_l3_verb_brokers"):
            server.main()
        self.assertEqual(observed, [b"event: change\n", f'data: {{"projects": ["{self.project}"]}}\n'.encode(), True])

    def test_explicit_development_http_does_not_require_certificates(self):
        with mock.patch.object(config, "TLS", False), \
             mock.patch.object(server, "ThreadingHTTPServer") as httpd, \
             mock.patch.object(server, "ensure_l3_verb_broker"), \
             mock.patch.object(server, "stop_l3_verb_brokers"), \
             mock.patch.object(tls, "check") as check:
            server.main()
        httpd.return_value.serve_forever.assert_called_once()
        check.assert_not_called()

    def test_daily_refresh_uses_existing_timer_and_reports_failure_without_http(self):
        context = mock.Mock(spec=ssl.SSLContext)
        with mock.patch.object(server.time, "monotonic", side_effect=[0, 86400, 86400]), \
             mock.patch.object(server.time, "sleep", side_effect=KeyboardInterrupt), \
             mock.patch.object(server, "tick") as tick, \
             mock.patch.object(tls, "check", side_effect=tls.TLSFailure("signing key missing")) as check:
            with self.assertRaises(KeyboardInterrupt):
                server.timer_loop(context, "localhost")
        check.assert_called_once_with("localhost", context=context)
        tick.assert_called_once()
        self.assertIn("active certificate is retained", server.log.call_args.args[0])
        self.assertIn("signing key missing", server.log.call_args.args[0])

    def test_daily_refresh_renews_the_trust_checks_certificate_on_its_own(self):
        old, new = mock.Mock(spec=ssl.SSLContext), mock.Mock(spec=ssl.SSLContext)
        self.patch(server, "TRUST", tls.TrustCheck(old, "Altitude CA TEST"))

        def refresh(probe):
            with mock.patch.object(server.time, "monotonic", side_effect=[0, 86400, 86400]), \
                 mock.patch.object(server.time, "sleep", side_effect=KeyboardInterrupt), \
                 mock.patch.object(server, "tick"), mock.patch.object(tls, "check"), \
                 mock.patch.object(tls, "probe_context", **probe) as issue:
                with self.assertRaises(KeyboardInterrupt):
                    server.timer_loop(mock.Mock(spec=ssl.SSLContext), "localhost")
            issue.assert_called_once_with("localhost")

        refresh({"side_effect": tls.TLSFailure("disk full")})
        self.assertIs(server.TRUST.context, old)
        self.assertIn("trust check's certificate was not renewed", server.log.call_args.args[0])
        self.assertIn("disk full", server.log.call_args.args[0])
        refresh({"return_value": new})
        self.assertIs(server.TRUST.context, new)

    def test_incident_issues_need_an_explicit_repository(self):
        """A release archive and a source checkout both know the maintainer's repository; neither is a default."""
        with mock.patch.object(config, "RELEASE", {"repository": "fictional/altitude"}), \
             mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", None), \
             mock.patch.object(server.subprocess, "run", side_effect=AssertionError("no git origin lookup")):
            with self.assertRaisesRegex(ValueError, "stay on this machine until incident reports are turned on"):
                server.issue_repository()
            with mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", "other/product"):
                self.assertEqual(server.issue_repository(), "https://github.com/other/product")
            with mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", "https://github.com/other/product.git"):
                self.assertEqual(server.issue_repository(), "https://github.com/other/product")


class TestTrustCheck(AltitudeCase):
    """The pairing screen's trust check over real HTTPS: a browser that trusts the installation's CA accepts the
    second certificate, one that only let the served certificate through (a warning bypass) refuses it."""

    def setUp(self):
        super().setUp()
        self.patch(config, "TLS", True)
        self.patch(config, "TLS_DIR", self.tmp / "private-tls")
        self.patch(config, "HOST", "127.0.0.1")
        self.patch(server, "log")
        self.setenv("ALTITUDE_TIMERS", "0")
        self.setenv("ALTITUDE_ACTOR", config.OPERATOR_ACTOR)
        self.patch(access, "DIR", self.tmp / "access")
        access.prepare()
        server.tls_init()
        self.patch(server, "TRUST", tls.TrustCheck(tls.probe_context("127.0.0.1"), "Altitude CA TEST"))
        context = tls.check("127.0.0.1")
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.patch(config, "PORT", self.httpd.server_port)
        self.trusting = ssl.create_default_context(cafile=str(config.TLS_DIR / "ca.crt"))
        # What a warning bypass leaves a browser with: the one served certificate, accepted as it is.
        self.bypassed = ssl.create_default_context(cafile=str(config.TLS_DIR / "server.crt"))
        self.bypassed.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN

    def call(self, context, method, path, body=None):
        conn = http.client.HTTPSConnection("127.0.0.1", self.httpd.server_port, context=context, timeout=5)
        try:
            conn.request(method, path, None if body is None else json.dumps(body),
                         {"Content-Type": "application/json"} if body is not None else {})
            response = conn.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            conn.close()

    def eventually(self, attempt):
        """The server records a refused handshake on its own thread, just after the client gives up."""
        for _ in range(50):
            try:
                return attempt()
            except ssl.SSLError:
                time.sleep(.02)
        return attempt()

    def check(self, context):
        status, armed = self.call(context, "POST", "/api/trust", {})
        self.assertEqual(status, 200, armed)
        return armed["challenge"], self.call(context, "GET", f"/api/trust/{armed['challenge']}")

    def test_the_second_certificate_tells_ca_trust_from_a_bypassed_warning(self):
        challenge, outcome = self.check(self.trusting)
        self.assertEqual(outcome, (200, {"trusted": True}))
        status, armed = self.call(self.bypassed, "POST", "/api/trust", {})
        self.assertEqual(status, 200, "the bypassed browser reaches Altitude on the served certificate")
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.call(self.bypassed, "GET", f"/api/trust/{armed['challenge']}")
        # The refusal's alert disarms the address, so the next request is served on the usual certificate and
        # reads the recorded refusal.
        path = f"/api/trust/{armed['challenge']}"
        self.assertEqual(self.eventually(lambda: self.call(self.bypassed, "GET", path)), (200, {"trusted": False}))
        self.assertEqual(self.call(self.bypassed, "GET", "/api/health")[0], 200, "the page tells refusal from an outage")
        self.assertEqual(server.TRUST.confirm(challenge, "192.0.2.7", True), "unknown", "a challenge belongs to its address")

    def test_a_check_answered_on_a_connection_opened_before_it_is_retried_never_trusted(self):
        # A browser keeps spare connections; one opened before the challenge carries the usual certificate.
        spare = http.client.HTTPSConnection("127.0.0.1", self.httpd.server_port, context=self.trusting, timeout=5)
        spare.connect()
        self.addCleanup(spare.close)
        status, armed = self.call(self.trusting, "POST", "/api/trust", {})
        # Every new connection while armed gets the second certificate, whichever request the browser sends on it.
        self.assertEqual(self.call(self.trusting, "GET", "/api/health")[0], 200)
        path = f"/api/trust/{armed['challenge']}"
        spare.request("GET", path)
        response = spare.getresponse()
        self.assertEqual((response.status, json.loads(response.read())), (200, {"retry": True}))
        self.assertEqual(self.call(self.trusting, "GET", path), (200, {"trusted": True}), "the retry is armed again")
        self.assertEqual(self.call(self.trusting, "GET", "/api/health")[0], 200, "a pass disarms the address")
        self.assertIsNone(server.TRUST.probe("127.0.0.1"))
        self.assertEqual(self.call(self.trusting, "GET", "/api/trust/unknown")[0], 404)

    def test_an_abandoned_connection_says_nothing_about_trust(self):
        challenge = self.call(self.trusting, "POST", "/api/trust", {})[1]["challenge"]
        socket.create_connection(("127.0.0.1", self.httpd.server_port)).close()  # a spare closed mid-handshake
        self.assertEqual(self.call(self.trusting, "GET", f"/api/trust/{challenge}"), (200, {"trusted": True}))

    def test_no_tls_session_survives_to_skip_the_second_certificate(self):
        def get(path, session=None):
            conn = self.trusting.wrap_socket(socket.create_connection(("127.0.0.1", self.httpd.server_port)),
                                             server_hostname="127.0.0.1", session=session)
            facts = (conn.version(), conn.session_reused)
            conn.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
            # Read just the reply, as a browser does: the server's unannounced close would void the session.
            response = http.client.HTTPResponse(conn)
            response.begin()
            reply = f"HTTP {response.status} ".encode() + response.read()
            session = conn.session
            conn.close()
            return facts, reply.split(b" ")[1], session

        _, _, session = get("/api/health")
        challenge = self.call(self.trusting, "POST", "/api/trust", {})[1]["challenge"]
        facts, status, _ = get(f"/api/trust/{challenge}", session)
        self.assertEqual((facts, status), (("TLSv1.3", False), b"200"), "a resumed session would skip the certificate")

    def test_the_pairing_screen_learns_how_this_device_reaches_altitude(self):
        self.patch(server.Handler, "_local", new=lambda _self: False)
        _, view = self.call(self.trusting, "GET", "/api/access")
        self.assertEqual(view["trust"], {"local": False, "https": True,
                                         "check": True, "certificate": {"name": "Altitude CA TEST"}})
        self.patch(server, "TRUST", None)  # an externally supplied certificate: nothing to check with
        self.assertFalse(self.call(self.trusting, "GET", "/api/access")[1]["trust"]["check"])
        self.assertEqual(self.call(self.trusting, "POST", "/api/trust", {}),
                         (409, {"error": "Altitude cannot check certificate trust here."}))

    def test_an_unpaired_device_downloads_only_the_public_certificate(self):
        body = (config.TLS_DIR / "ca.crt").read_bytes()
        for name, kind in (("altitude.crt", "application/x-x509-ca-cert"),
                           ("altitude.mobileconfig", "application/x-apple-aspen-config")):
            with self.subTest(name=name):
                conn = http.client.HTTPSConnection("127.0.0.1", self.httpd.server_port, context=self.trusting, timeout=5)
                conn.request("GET", f"/api/certificate/{name}")
                response = conn.getresponse()
                data = response.read()
                conn.close()
                self.assertEqual((response.status, response.getheader("Content-Type")), (200, kind))
                self.assertEqual(response.getheader("Content-Disposition"), f'attachment; filename="{name}"')
                self.assertNotIn(b"PRIVATE KEY", data)
                if name.endswith(".crt"):
                    self.assertEqual(data, body)
                else:
                    payload = plistlib.loads(data)["PayloadContent"]
                    self.assertEqual([item["PayloadContent"] for item in payload], [ssl.PEM_cert_to_DER_cert(body.decode())])
        for other in ("ca.key", "server.key", "server.crt"):
            self.assertEqual(self.call(self.trusting, "GET", f"/api/certificate/{other}")[0], 404)
