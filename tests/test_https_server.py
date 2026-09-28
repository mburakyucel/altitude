"""HTTPS startup, identity probes and renewal use isolated sockets and certificates."""
import http.client
import json
import os
import socket
import ssl
import threading
import urllib.request
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, server, tasks as T, tls


class TestHTTPSServer(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "TLS", True)
        self.patch(config, "TLS_DIR", self.tmp / "private-tls")
        self.patch(config, "HOST", "127.0.0.1")
        self.patch(config, "PORT", 0)
        self.patch(server, "log")
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

    def test_devices_certificate_names_the_ca_a_phone_must_match(self):
        self.assertIsNone(server.certificate_view(), "no CA file of its own, nothing to match")
        server.tls_init()
        view = server.certificate_view()
        self.assertEqual(view["name"], "Altitude local CA")
        self.assertEqual(f"sha256 Fingerprint={view['sha256']}", tls.info()["ca_sha256"])
        self.assertTrue(view["scope"].startswith("Only localhost"), view["scope"])
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
