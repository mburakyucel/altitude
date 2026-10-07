"""HEAD responses match GET metadata without sending an entity body."""
import gzip
import socket
import json
import sys
import threading
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from tests.support import AltitudeCase, set_project_setting
from altitude import config, dispatch, server


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
        (assets / "model.01234567.ort").write_bytes(b"packed-weights" * 64)
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

    def _request(self, method, path, headers="", body=b""):
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.sendall(
                f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n{headers}\r\n".encode() + body
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
                if not path.startswith("/api/") and not path.endswith(".wav"):  # another site cannot frame the app
                    self.assertEqual(get_headers["content-security-policy"], "frame-ancestors 'none'")
                    self.assertEqual(get_headers["x-frame-options"], "DENY")
                self.assertEqual(self.httpd.errors, [])
                self.assertNotIn("Traceback", "\n".join(self.logs))

    def test_hashed_script_travels_gzip_only_to_a_browser_that_accepts_it(self):
        script = b"console.log('altitude');"
        for accept, encoded in (("gzip, deflate, br, zstd", True), ("deflate, gzip;q=0.5", True),
                                ("gzip;q=0", False), ("br", False), (None, False),
                                ("*;q=1, identity;q=0", True), ("*, gzip;q=0", False)):
            with self.subTest(accept=accept):
                header = f"Accept-Encoding: {accept}\r\n" if accept else ""
                status, headers, body = self._request("GET", "/assets/app.01234567.js", header)
                _, head_headers, head_body = self._request("HEAD", "/assets/app.01234567.js", header)

                self.assertEqual(status, 200)
                self.assertEqual(headers["content-type"], "text/javascript")
                self.assertEqual(headers["vary"], "Accept-Encoding")  # a shared cache keeps both forms apart
                self.assertEqual(headers["cache-control"], "public, max-age=31536000, immutable")
                self.assertEqual(headers.get("content-encoding"), "gzip" if encoded else None)
                self.assertEqual(gzip.decompress(body) if encoded else body, script)
                self.assertEqual(int(headers["content-length"]), len(body))
                self.assertEqual(head_headers["content-length"], headers["content-length"])
                self.assertEqual(head_body, b"")

    def test_page_and_punctuation_model_travel_as_stored(self):
        # index.html is tiny and never cached; the punctuation model's packed weights barely compress.
        for path in ("/", "/assets/model.01234567.ort"):
            with self.subTest(path=path):
                _, headers, body = self._request("GET", path, "Accept-Encoding: gzip\r\n")
                self.assertNotIn("content-encoding", headers)
                self.assertNotIn("vary", headers)
                self.assertEqual(int(headers["content-length"]), len(body))

    def test_large_views_travel_gzip_and_uncached_and_credential_replies_as_stored(self):
        view = {"state": "ready", "messages": ["the same words again"] * 200}
        self.patch(server, "overview", new=lambda: view)
        for accept, encoded in (("gzip, deflate, br", True), (None, False), ("gzip;q=0", False)):
            with self.subTest(accept=accept):
                header = f"Accept-Encoding: {accept}\r\n" if accept else ""
                status, headers, body = self._request("GET", "/api/overview", header)
                _, head_headers, head_body = self._request("HEAD", "/api/overview", header)

                self.assertEqual(status, 200)
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertEqual(headers["vary"], "Accept-Encoding")
                self.assertEqual(headers.get("content-encoding"), "gzip" if encoded else None)
                self.assertEqual(json.loads(gzip.decompress(body) if encoded else body), view)
                self.assertEqual(int(headers["content-length"]), len(body))
                self.assertEqual(head_headers["content-length"], headers["content-length"])
                self.assertEqual(head_body, b"")
        # Device and pairing replies can carry a pairing code: never compressed.
        for method, path in (("GET", "/api/devices"), ("POST", "/api/devices/code")):
            with self.subTest(path=path):
                status, headers, _ = self._request(method, path, "Accept-Encoding: gzip\r\nContent-Length: 2\r\n", b"{}")
                self.assertEqual(status, 200)
                self.assertNotIn("content-encoding", headers)
                self.assertNotIn("vary", headers)

    def test_base_class_error_accepts_non_string_log_argument(self):
        status, _, _ = self._request("DELETE", "/api/overview")

        self.assertEqual(status, 501)
        self.assertEqual(self.httpd.errors, [])
        self.assertNotIn("Traceback", "\n".join(self.logs))


class TestProjectRegistry(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.patch(server, "spawn", return_value=True)

    def post(self, verb, **body):
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}/api/project/{verb}",
                                         data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_sept7_http_registration_and_daemon_set_share_one_project_transaction(self):
        dispatch.request_setting(self.project, "routing", config.ENGINES[0], "test", actor="l3")
        entered, release, set_finished = threading.Event(), threading.Event(), threading.Event()
        from contextlib import contextmanager
        register = config.add_project
        @contextmanager
        def prepare(*args, **kwargs):
            with register(*args, **kwargs) as entry:
                entered.set()
                self.assertTrue(release.wait(3))
                yield entry
        def set_routing():
            result = dispatch.run_settings(self.project)
            set_finished.set()
            return result
        with mock.patch.object(config, "add_project", side_effect=prepare), ThreadPoolExecutor() as pool:
            add = pool.submit(self.post, "add", name=self.project, path=str(self.repo))
            self.assertTrue(entered.wait(3))
            setting = pool.submit(set_routing)
            try:
                self.assertFalse(set_finished.wait(.05), "the set waits for registration's project lock")
            finally:
                release.set()
            self.assertNotIn("routing", add.result()["project"])
            self.assertEqual(setting.result()["routing"]["status"], "done")
        self.assertEqual(config.project(self.project)["routing"], config.parse_routing(config.ENGINES[0]))
        with mock.patch.object(server, "remove_l3_verb_broker"):
            self.post("remove", name=self.project)
        self.assertNotIn(self.project, config.load_projects())

    def test_setup_failure_retains_registration_and_preserves_other_projects_and_engine_pins(self):
        other = self.project + "-other"
        self.register(other, approval="manual")
        def failed_setup(_project):
            set_project_setting(other, "l3_engine", config.ENGINES[0])
            raise RuntimeError("test broker unavailable")
        with mock.patch.object(server, "ensure_l3_verb_broker", side_effect=failed_setup):
            result = self.post("add", name=self.project, path=str(self.repo), approval="manual", wip=4)
            self.assertTrue(result["ok"])
            self.assertNotIn("wip", result["project"])
            server.project_setup.run(self.project)
        self.assertEqual(config.project(self.project)["approval"], "manual")
        self.assertNotIn("wip", config.project(self.project))
        self.assertNotIn("wip", json.loads(config.PROJECTS_FILE.read_text())[self.project])
        self.assertEqual(server.project_setup.observe(self.project)["operation"]["state"], "failed")
        self.assertEqual(config.project(other)["approval"], "manual")
        self.assertEqual(config.project(other)["l3_engine"], config.ENGINES[0])


if __name__ == "__main__":
    unittest.main()
