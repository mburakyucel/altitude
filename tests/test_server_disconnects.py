"""Deterministic disconnects at the HTTP response write boundary (issue #411)."""
import io
import json
import ssl
import fcntl
from contextlib import contextmanager
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, images, route, server, state as S


class TestImageErrorDisconnects(AltitudeCase):
    def request(self, method, *, failure=None, write=1, path=None):
        body = b"{" if method == "POST" else b""
        path = path or ("/api/chat" if method == "POST" else "/api/images/unmanaged/image")
        raw = (f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Length: {len(body)}\r\n\r\n".encode()
               + body)
        connection = mock.Mock()
        connection.makefile.return_value = io.BytesIO(raw)
        chunks = []

        def send(data):
            if failure is not None and len(chunks) == write - 1:
                raise failure
            chunks.append(data)

        connection.sendall.side_effect = send
        with mock.patch.object(server.Handler, "log_message"):
            server.Handler(connection, ("127.0.0.1", 12345), None)
        return b"".join(chunks), connection.sendall.call_count

    def test_image_error_disconnects_during_headers_and_body(self):
        for method in ("GET", "POST"):
            for error in (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                for write in (1, 2):
                    with self.subTest(method=method, error=error, write=write):
                        with mock.patch.object(server, "log") as log:
                            _, calls = self.request(method, failure=error("client left"), write=write)
                        self.assertEqual(calls, write, "do not retry a disconnected response")
                        log.assert_called_once()
                        self.assertIn("client went away", log.call_args.args[0])

    def test_connected_clients_receive_image_error_status_and_body(self):
        for method, status, message in (
            ("GET", 403, "Image access denied."),
            ("POST", 400, "The message could not be read."),
        ):
            with self.subTest(method=method), mock.patch.object(server, "log") as log:
                response, calls = self.request(method)
                headers, body = response.split(b"\r\n\r\n", 1)
                self.assertTrue(headers.startswith(f"HTTP/1.0 {status} ".encode()))
                self.assertIn(b"Content-Type: application/json; charset=utf-8", headers)
                self.assertIn(f"Content-Length: {len(body)}".encode(), headers)
                self.assertEqual(json.loads(body), {"error": message})
                self.assertEqual(calls, 2)
                log.assert_not_called()

    def test_other_error_response_write_failures_remain_visible(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method), mock.patch.object(server, "log") as log:
                with self.assertRaisesRegex(OSError, "unexpected write failure"):
                    self.request(method, failure=OSError("unexpected write failure"))
                log.assert_not_called()

    def test_server_failure_is_logged_even_if_its_error_response_disconnects(self):
        for method, route in (("GET", "_images"), ("POST", "_body")):
            with self.subTest(method=method), mock.patch.object(server, "log") as log:
                with mock.patch.object(server.Handler, route, side_effect=RuntimeError("server failure")):
                    self.request(method, failure=BrokenPipeError("client left"))
                self.assertIn("server failure", log.call_args_list[0].args[0])
                self.assertIn("Traceback", log.call_args_list[0].args[0])
                self.assertIn("client went away", log.call_args_list[-1].args[0])

    def test_removal_before_capability_lock_returns_denial_even_when_client_leaves(self):
        self.private_ledgers()
        lock = S.project_lock
        for failure in (None, BrokenPipeError("client left")):
            with self.subTest(failure=failure):
                self.register(self.project)
                removed = False

                @contextmanager
                def remove_before_lock(project):
                    nonlocal removed
                    if not removed:
                        removed = True
                        config.remove_project(project)
                    with lock(project):
                        yield

                with mock.patch.object(S, "project_lock", remove_before_lock), \
                     mock.patch.object(server, "log") as log, \
                     mock.patch.object(images, "capability", return_value={"available": False}), \
                     mock.patch.object(route, "pick_engine", return_value={"engine": config.ENGINES[0]}):
                    response, _ = self.request("GET", path=f"/api/images/{self.project}", failure=failure)
                self.assertTrue(removed)
                self.assertFalse(config.is_managed(self.project))
                if failure is None:
                    headers, body = response.split(b"\r\n\r\n", 1)
                    self.assertTrue(headers.startswith(b"HTTP/1.0 403 "))
                    self.assertEqual(json.loads(body), {"error": "Image access denied."})
                    log.assert_not_called()
                else:
                    log.assert_called_once()
                    self.assertIn("client went away", log.call_args.args[0])

    def test_capability_lookup_holds_the_removal_lock(self):
        def capability():
            # Removal uses this same cross-process lock; it cannot unregister mid-lookup.
            with (config.project_dir(self.project) / ".lock").open("a") as handle:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return {"available": False, "reason": "converter unavailable"}

        with mock.patch.object(images, "capability", side_effect=capability), \
             mock.patch.object(route, "pick_engine", return_value={"engine": config.ENGINES[0]}), \
             mock.patch.object(server, "log") as log:
            response, _ = self.request("GET", path=f"/api/images/{self.project}")
        headers, body = response.split(b"\r\n\r\n", 1)
        self.assertTrue(headers.startswith(b"HTTP/1.0 200 "))
        self.assertFalse(json.loads(body)["available"])
        self.assertEqual(json.loads(body)["reason"], "converter unavailable")
        log.assert_not_called()
        config.remove_project(self.project)
        self.assertFalse(config.is_managed(self.project))

    def test_unrelated_capability_key_error_stays_visible(self):
        with mock.patch.object(images, "capability", side_effect=KeyError("unexpected converter state")), \
             mock.patch.object(server, "log") as log:
            response, _ = self.request("GET", path=f"/api/images/{self.project}")
        headers, body = response.split(b"\r\n\r\n", 1)
        self.assertTrue(headers.startswith(b"HTTP/1.0 500 "))
        self.assertEqual(json.loads(body), {"error": "Image temporarily unavailable."})
        self.assertIn("unexpected converter state", log.call_args.args[0])
        self.assertIn("Traceback", log.call_args.args[0])
