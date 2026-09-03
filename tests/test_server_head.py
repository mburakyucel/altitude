"""HEAD responses match GET metadata without sending an entity body."""
import socket
import sys
import threading
import unittest

from tests.support import AltitudeCase
from altitude import config, server


class _RecordingHTTPServer(server.ThreadingHTTPServer):
    def __init__(self, server_address, handler):
        super().__init__(server_address, handler)
        self.errors = []

    def handle_error(self, request, client_address):
        self.errors.append(sys.exc_info())


class TestHead(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.dist = self.tmp / "web" / "dist"
        self.dist.mkdir(parents=True)
        (self.dist / "index.html").write_bytes(b"<!doctype html><title>Altitude</title>")
        assets = self.dist / "assets"
        assets.mkdir()
        (assets / "app.01234567.js").write_bytes(b"console.log('altitude');")
        self.patch(config, "WEB_DIST", new=self.dist)

        (self.tmp / "digest.wav").write_bytes(b"RIFF-altitude-test-audio")
        self.patch(config, "ROOT", new=self.tmp)

        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        server.Handler._seen_clients.clear()

        self.httpd = _RecordingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _request(self, method, path):
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.sendall(
                f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
            )
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)

        raw_headers, body = b"".join(chunks).split(b"\r\n\r\n", 1)
        lines = raw_headers.split(b"\r\n")
        status = int(lines[0].split()[1])
        headers = {}
        for line in lines[1:]:
            name, value = line.decode("iso-8859-1").split(":", 1)
            headers[name.lower()] = value.strip()
        return status, headers, body

    def test_head_matches_get_without_body(self):
        for path, expected_status in (("/", 200), ("/digest.wav", 200), ("/assets/app.01234567.js", 200),
                                      ("/api/overview", 200), ("/api/definitely-unknown", 404)):
            with self.subTest(path=path):
                get_status, get_headers, get_body = self._request("GET", path)
                head_status, head_headers, head_body = self._request("HEAD", path)

                self.assertEqual(get_status, expected_status)
                self.assertEqual(head_status, expected_status)
                for name in ("content-type", "content-length", "cache-control"):
                    self.assertEqual(head_headers[name], get_headers[name])
                self.assertEqual(int(head_headers["content-length"]), len(get_body))
                self.assertEqual(head_body, b"")
                self.assertEqual(self.httpd.errors, [])
                self.assertNotIn("Traceback", "\n".join(self.logs))

    def test_base_class_error_accepts_non_string_log_argument(self):
        status, _, _ = self._request("DELETE", "/api/overview")

        self.assertEqual(status, 501)
        self.assertEqual(self.httpd.errors, [])
        self.assertNotIn("Traceback", "\n".join(self.logs))


if __name__ == "__main__":
    unittest.main()
