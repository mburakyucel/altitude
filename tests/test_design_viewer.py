"""The wireframe viewer served from a project's own checkout.

The boards are read on every request, so the browser shows what is on main. Everything else about
the route is a refusal: two subtrees, one extension list, no listing, no cache, and a plain 404 for
a project without boards.
"""
import socket
import sys
import threading
import unittest

from tests.support import AltitudeCase
from altitude import access, server


class _RecordingHTTPServer(server.ThreadingHTTPServer):
    def __init__(self, server_address, handler):
        super().__init__(server_address, handler)
        self.errors = []

    def handle_error(self, request, client_address):
        self.errors.append(sys.exc_info())


class TestDesignViewer(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.boards = self.repo / "design" / "wireframes"
        self.boards.mkdir(parents=True)
        (self.boards / "index.html").write_bytes(b"<!doctype html><title>boards</title>")
        (self.boards / "viewer.css").write_bytes(b'@import url("../../web/design/tokens.css");')
        (self.boards / "README.md").write_bytes(b"# the board table")
        (self.boards / "serve.sh").write_bytes(b"#!/usr/bin/env bash\n")
        tokens = self.repo / "web" / "design"
        tokens.mkdir(parents=True)
        (tokens / "tokens.css").write_bytes(b":root { --page: #fff; }")
        (self.repo / "secret.css").write_bytes(b"/* outside both trees */")
        self.device_key, device = access.redeem(access.issue_code()["code"], "Test browser")
        self.pass_ = access.design_pass(self.project, device["id"])

        self.bare = "bare-project"
        self.bare_repo = self.tmp / "bare"
        self.bare_repo.mkdir()
        self.register(self.bare, path=self.bare_repo)

        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        server.Handler._seen_clients.clear()
        self.httpd = _RecordingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._assert_quiet)
        self.addCleanup(self._stop_server)

    def _assert_quiet(self):
        """No request in this case reached the handler's generic error paths."""
        self.assertEqual(self.httpd.errors, [])
        self.assertNotIn("Traceback", "\n".join(self.logs))

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _get(self, path, cookie=""):
        """One raw GET, so a traversal path reaches the server exactly as a browser would send it."""
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.sendall(f"GET {path} HTTP/1.0\r\nHost: {host}\r\n{cookie}Connection: close\r\n\r\n".encode())
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        raw_headers, _, body = b"".join(chunks).partition(b"\r\n\r\n")
        lines = raw_headers.split(b"\r\n")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.decode("iso-8859-1").partition(":")
            headers[name.lower()] = value.strip()
        return int(lines[0].split()[1]), headers, body

    def test_url_is_the_stable_link_of_a_project_that_has_boards(self):
        self.assertEqual(server.design_viewer_url(self.project), f"/design/{self.project}")
        self.assertIsNone(server.design_viewer_url(self.bare))
        self.assertIsNone(server.design_viewer_url("not-a-project"))

    def test_project_view_carries_the_viewer_url(self):
        self.assertEqual(server.project_view(self.project)["design_viewer"], f"/design/{self.project}")
        self.assertIsNone(server.project_view(self.bare)["design_viewer"])

    def test_serves_a_board_and_the_tokens_its_stylesheet_imports(self):
        for path, ctype, body in (
            (f"/design/{self.project}/{self.pass_}/design/wireframes/index.html", "text/html; charset=utf-8",
             b"<!doctype html><title>boards</title>"),
            (f"/design/{self.project}/{self.pass_}/design/wireframes/viewer.css", "text/css; charset=utf-8",
             b'@import url("../../web/design/tokens.css");'),
            (f"/design/{self.project}/{self.pass_}/web/design/tokens.css", "text/css; charset=utf-8",
             b":root { --page: #fff; }"),
        ):
            with self.subTest(path=path):
                status, headers, got = self._get(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["content-type"], ctype)
                self.assertEqual(got, body)
                self.assertEqual(headers["content-length"], str(len(body)))
                # Project content runs in an opaque origin, without Altitude's authority.
                self.assertEqual(headers["content-security-policy"], "sandbox allow-scripts")
                self.assertEqual(headers["x-content-type-options"], "nosniff")

    def test_nothing_is_cached_so_an_edit_is_never_hidden(self):
        _, headers, _ = self._get(f"/design/{self.project}/{self.pass_}/design/wireframes/index.html")
        self.assertEqual(headers["cache-control"], "no-store")

    def test_an_edited_board_is_served_without_a_restart(self):
        (self.boards / "index.html").write_bytes(b"<!doctype html><title>edited</title>")
        _, _, body = self._get(f"/design/{self.project}/{self.pass_}/design/wireframes/index.html")
        self.assertEqual(body, b"<!doctype html><title>edited</title>")

    def test_the_project_path_redirects_a_paired_browser_to_its_viewer(self):
        for path in (f"/design/{self.project}", f"/design/{self.project}/"):
            with self.subTest(path=path):
                status, headers, _ = self._get(path, f"Cookie: {access.COOKIE}={self.device_key}\r\n")
                self.assertEqual(status, 302)
                self.assertEqual(headers["location"],
                                 f"/design/{self.project}/{self.pass_}/design/wireframes/index.html")
        self.assertEqual(self._get(f"/design/{self.project}")[0], 403)  # the CLI has no browser to pass

    def test_a_project_without_boards_is_a_plain_404(self):
        for path in (f"/design/{self.bare}", f"/design/{self.bare}/design/wireframes/index.html",
                     "/design/not-a-project/design/wireframes/index.html", "/design", "/design/"):
            with self.subTest(path=path):
                status, headers, body = self._get(path)
                self.assertEqual(status, 404)
                self.assertEqual(headers["content-type"], "text/plain; charset=utf-8")
                self.assertEqual(body, b"not found\n")

    def test_only_the_listed_extensions_are_readable(self):
        for name in ("README.md", "serve.sh"):
            with self.subTest(name=name):
                status, _, _ = self._get(f"/design/{self.project}/{self.pass_}/design/wireframes/{name}")
                self.assertEqual(status, 404)

    def test_a_directory_is_not_listed(self):
        for path in (f"/design/{self.project}/{self.pass_}/design/wireframes/",
                     f"/design/{self.project}/design/",
                     f"/design/{self.project}/{self.pass_}/web/design/"):
            with self.subTest(path=path):
                status, _, body = self._get(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"index.html", body)

    def test_the_rest_of_the_checkout_stays_unreachable(self):
        for path in (
            f"/design/{self.project}/secret.css",
            f"/design/{self.project}/{self.pass_}/design/wireframes/../../secret.css",
            f"/design/{self.project}/{self.pass_}/design/wireframes/..%2f..%2fsecret.css",
            f"/design/{self.project}/%2e%2e/%2e%2e/etc/passwd",
            f"/design/{self.project}/{self.pass_}/design/wireframes/....//....//secret.css",
        ):
            with self.subTest(path=path):
                status, _, body = self._get(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"outside both trees", body)

    def test_a_symlink_out_of_the_tree_is_refused(self):
        outside = self.tmp / "elsewhere.css"
        outside.write_bytes(b"/* not a board */")
        (self.boards / "escape.css").symlink_to(outside)
        status, _, body = self._get(f"/design/{self.project}/{self.pass_}/design/wireframes/escape.css")
        self.assertEqual(status, 404)
        self.assertNotIn(b"not a board", body)


if __name__ == "__main__":
    unittest.main()
