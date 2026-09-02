"""HEAD responses match GET metadata without sending an entity body."""
import os
import json
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-server-head-")
os.environ["ALTITUDE_HOME"] = _TMP
os.environ["ALTITUDE_TIMERS"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, server  # noqa: E402


class _RecordingHTTPServer(server.ThreadingHTTPServer):
    def __init__(self, server_address, handler):
        super().__init__(server_address, handler)
        self.errors = []

    def handle_error(self, request, client_address):
        self.errors.append(sys.exc_info())


class TestHead(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="altitude-head-case-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dist = self.tmp / "web" / "dist"
        self.dist.mkdir(parents=True)
        (self.dist / "index.html").write_bytes(b"<!doctype html><title>Altitude</title>")
        assets = self.dist / "assets"
        assets.mkdir()
        (assets / "app.01234567.js").write_bytes(b"console.log('altitude');")

        self.old_web_dist = config.WEB_DIST
        config.WEB_DIST = self.dist
        self.addCleanup(setattr, config, "WEB_DIST", self.old_web_dist)

        (self.tmp / "digest.wav").write_bytes(b"RIFF-altitude-test-audio")
        self.old_root = config.ROOT
        config.ROOT = self.tmp
        self.addCleanup(setattr, config, "ROOT", self.old_root)

        self.old_overview = server.overview
        server.overview = lambda: {"state": "ready"}
        self.addCleanup(setattr, server, "overview", self.old_overview)

        self.old_manifest = server._RUNTIME_MANIFEST_BYTES
        self.manifest_value = {"schema_version": "test-manifest", "source_commit": "abc123"}
        server._RUNTIME_MANIFEST_BYTES = json.dumps(
            self.manifest_value, sort_keys=True, separators=(",", ":")).encode()
        self.addCleanup(setattr, server, "_RUNTIME_MANIFEST_BYTES", self.old_manifest)

        self.logs = []
        self.old_log = server.log
        server.log = self.logs.append
        self.addCleanup(setattr, server, "log", self.old_log)
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

    def _assert_head_matches_get(self, path, expected_status):
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

    def test_static_head_matches_get_without_body(self):
        self._assert_head_matches_get("/", 200)

    def test_file_head_matches_get_without_body(self):
        self._assert_head_matches_get("/digest.wav", 200)

    def test_hashed_asset_head_matches_get_without_body(self):
        self._assert_head_matches_get("/assets/app.01234567.js", 200)

    def test_overview_head_matches_get_without_body(self):
        self._assert_head_matches_get("/api/overview", 200)

    def test_manifest_is_read_only_and_uses_the_startup_snapshot(self):
        with mock.patch.object(server.manifest, "runtime_manifest",
                               side_effect=AssertionError("must use startup snapshot")), \
                mock.patch.object(server.manifest, "public_manifest",
                                  side_effect=AssertionError("must serve pre-redacted bytes")):
            status, headers, body = self._request("GET", "/api/manifest")

        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(body, server._RUNTIME_MANIFEST_BYTES)
        self.assertEqual(json.loads(body), self.manifest_value)

    def test_manifest_without_startup_snapshot_is_explicitly_unavailable_and_never_fresh_reads(self):
        server._RUNTIME_MANIFEST_BYTES = None
        with mock.patch.object(server.manifest, "runtime_manifest",
                               side_effect=AssertionError("must never reconstruct startup identity")):
            status, headers, body = self._request("GET", "/api/manifest")
        payload = json.loads(body)
        self.assertEqual(status, 503)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertFalse(payload["identity_known"])
        self.assertEqual(payload["snapshot_status"], "unavailable")

    def test_main_freezes_one_canonical_redacted_byte_snapshot(self):
        full = {"private": ["mutable"]}
        public = {"identity_known": True, "source_commit": "abc", "schema_version": "test"}
        with mock.patch.object(server.manifest, "loaded_module_origins", return_value={}), \
                mock.patch.object(server.manifest, "runtime_manifest", return_value=full), \
                mock.patch.object(server.manifest, "public_manifest", return_value=public), \
                mock.patch.object(server.config, "ensure_root"), \
                mock.patch.object(server, "ThreadingHTTPServer", side_effect=RuntimeError("stop")):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                server.main(startup_source={})
        expected = json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(server._RUNTIME_MANIFEST_BYTES, expected)
        full["private"].append("changed")
        public["source_commit"] = "changed"
        self.assertEqual(server._RUNTIME_MANIFEST_BYTES, expected)

    def test_unknown_api_head_matches_get_without_body(self):
        self._assert_head_matches_get("/api/definitely-unknown", 404)

    def test_base_class_error_accepts_non_string_log_argument(self):
        status, _, _ = self._request("DELETE", "/api/overview")

        self.assertEqual(status, 501)
        self.assertEqual(self.httpd.errors, [])
        self.assertNotIn("Traceback", "\n".join(self.logs))


if __name__ == "__main__":
    unittest.main()
