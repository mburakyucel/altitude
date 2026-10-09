"""A Chat turn belongs to L3, not to the page that started it: a client that leaves mid-stream ends the
stream and nothing else (2026-09-03 07:54Z: a page refresh unwound the turn and lost the answer)."""
import json
import socket
import threading
import unittest

from tests.support import AltitudeCase
from altitude import l3, server


class TestChatStream(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.lines = []
        self.patch(server, "log", new=self.lines.append)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def test_a_turn_finishes_and_is_recorded_after_the_page_goes_away(self):
        finished = threading.Event()
        seen = {"chunks": 0}

        def fake_turn(project, text, *, trigger, on_text, on_start=None, on_split=None):
            for _ in range(400):  # far more than the socket buffers hold once the client is gone
                on_text("x" * 20_000)
                seen["chunks"] += 1
            on_split("A Send now message the turn took in", {"state": "delivered", "at": "2026-01-01T00:00:00Z"})
            finished.set()
            return {"session_id": "s1", "context_percent": 1.0, "turns": 1, "cost": 0.0, "engine": "claude"}

        self.patch(l3, "busy", new=lambda project: False)
        self.patch(l3, "turn", new=fake_turn)
        body = json.dumps({"project": self.project, "text": "hello"}).encode()
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"POST /api/chat HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                         + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self.assertIn(b"200", sock.recv(64))  # the stream opened, then the page is refreshed
        self.assertTrue(finished.wait(10), "the turn was unwound when the client left")
        self.assertEqual(seen["chunks"], 400)
        self.assertTrue(any("client went away mid-turn" in line for line in self.lines), self.lines)
        self.assertFalse(any("Traceback" in line for line in self.lines), self.lines)


if __name__ == "__main__":
    unittest.main()
