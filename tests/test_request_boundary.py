"""What every request passes before routing: actions come from Altitude's own page or from a client that is no
page at all (the `alt` CLI), a plain-HTTP request names an address or localhost, and a body has a size limit."""
import json
import socket
import threading

from tests.support import AltitudeCase
from altitude import config, server


class TestRequestBoundary(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(server, "log", new=lambda _message: None)
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        settings = config.ROOT / "settings.json"
        saved = settings.read_bytes() if settings.exists() else None
        self.addCleanup(lambda: settings.write_bytes(saved) if saved is not None else settings.unlink(missing_ok=True))
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.host = "%s:%d" % self.httpd.server_address

    def request(self, method, path, body=b"", headers=None):
        """One raw request, so the test controls every header, including a false Content-Length."""
        sent = {"Host": self.host, "Content-Length": str(len(body)), **(headers or {})}
        head = "".join(f"{name}: {value}\r\n" for name, value in sent.items())
        with socket.create_connection(self.httpd.server_address, timeout=10) as sock:
            sock.sendall(f"{method} {path} HTTP/1.0\r\n{head}\r\n".encode() + body)
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        raw_headers, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        return int(raw_headers.split()[1]), json.loads(payload)

    def rename(self, name, headers):
        return self.request("POST", "/api/operator-name", json.dumps({"name": name}).encode(),
                            {"Content-Type": "application/json", **headers})

    def test_an_action_from_another_page_is_refused_before_it_runs(self):
        before = config.machine_settings().get("operator_name")
        for headers in ({"Origin": "https://elsewhere.example"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": "http://127.0.0.1:1", "Sec-Fetch-Site": "same-site"},
                        {"Origin": f"https://{self.host}"}):
            with self.subTest(headers=headers):
                status, reply = self.rename("Mallory", headers)
                self.assertEqual((status, reply), (403, {"error": "Requests must come from Altitude's own page."}))
                self.assertEqual(config.machine_settings().get("operator_name"), before)
        status, _ = self.request("POST", "/api/transcribe", b"audio", {"Content-Type": "audio/webm",
                                                                       "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_altitudes_own_page_and_the_cli_act(self):
        page = {"Origin": f"http://{self.host}", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.rename("Ada", page)[0], 200)
        self.assertEqual(config.machine_settings()["operator_name"], "Ada")
        status, _ = self.rename("Grace", {})  # the CLI's shape: JSON with neither header
        self.assertEqual(status, 200)
        self.assertEqual(config.machine_settings()["operator_name"], "Grace")

    def test_plain_http_answers_only_an_address_or_localhost(self):
        port = self.httpd.server_address[1]
        for path in ("/api/overview", "/", "/ca.crt"):
            with self.subTest(path=path):
                status, reply = self.request("GET", path, headers={"Host": f"rebind.example:{port}"})
                self.assertEqual((status, reply), (403, {"error": "Over plain HTTP, open Altitude at its address or localhost."}))
        for host in (f"localhost:{port}", self.host, f"[::1]:{port}"):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/api/overview", headers={"Host": host}), (200, {"state": "ready"}))

    def test_every_body_has_a_size_limit(self):
        for length in (str(server.BODY_LIMIT + 1), "-1"):
            with self.subTest(length=length):
                status, reply = self.request("POST", "/api/operator-name", headers={"Content-Length": length})
                self.assertEqual((status, reply), (413, {"error": "Request is too large."}))

    def test_a_silent_client_loses_its_connection(self):
        self.patch(server, "REQUEST_READ_SECONDS", new=0.2)
        with socket.create_connection(self.httpd.server_address, timeout=10) as sock:
            sock.sendall(b"GET /api/overview HTTP/1.1\r\nHost: " + self.host.encode() + b"\r\n")  # headers never end
            self.assertEqual(sock.recv(1), b"")

