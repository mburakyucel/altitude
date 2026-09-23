"""Deterministic disconnects at the HTTP response write boundary (issue #411)."""
import io
import json
import ssl
from unittest import mock

from tests.support import AltitudeCase
from altitude import server


class TestImageErrorDisconnects(AltitudeCase):
    def request(self, method, *, failure=None, write=1):
        body = b"{" if method == "POST" else b""
        path = "/api/chat" if method == "POST" else "/api/images/unmanaged/image"
        raw = (f"{method} {path} HTTP/1.0\r\nContent-Length: {len(body)}\r\n\r\n".encode()
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
