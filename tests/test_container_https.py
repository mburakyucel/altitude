"""Real local TLS publication checks with fictional state, no Podman or host trust changes."""
import http.server
import json
import ssl
import threading

from tests.support import AltitudeCase
from altitude import config, platform, tls


class ContainerHTTPSTests(AltitudeCase):
    def test_published_endpoint_requires_correct_ca_name_and_service_process(self):
        directory = self.tmp / "certificates"
        self.patch(config, "TLS_DIR", directory)
        self.patch(config, "HOST", "localhost")
        self.patch(config, "PUBLIC_HOST", "localhost")
        tls.initialize()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"pid": 123}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.socket = tls.check().wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            facts = {"port": server.server_port, "pid": 123, "host": "localhost",
                     "ca": (directory / "ca.crt").read_text()}
            platform.container_https("127.0.0.1", facts)
            with self.assertRaisesRegex(RuntimeError, "another process"):
                platform.container_https("127.0.0.1", {**facts, "pid": 456})
            with self.assertRaises(ssl.SSLCertVerificationError):
                platform.container_https("127.0.0.1", {**facts, "host": "wrong.fixture.invalid"})
            with self.assertRaisesRegex(RuntimeError, "concrete published"):
                platform.container_https("0.0.0.0", facts)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    import unittest
    unittest.main()
