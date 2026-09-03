"""Burak's chat messages queue while L3 is busy and run at the next turn boundary, in arrival order."""
import json
import socket
import threading
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, l3, server, state as S


class TestChatQueue(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.private_ledgers()
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.lines = []
        self.patch(server, "log", new=self.lines.append)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    # ---- helpers ------------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        raw = json.dumps(body).encode() if body is not None else b""
        head = f"{method} {path} HTTP/1.0\r\nHost: x\r\n"
        if body is not None:
            head += f"Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(head.encode() + b"\r\n" + raw)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        headers, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        return int(headers.split()[1]), payload

    def post_json(self, path: str, body: dict) -> tuple[int, dict]:
        status, payload = self.request("POST", path, body)
        return status, json.loads(payload)

    def queue_rows(self) -> list[dict]:
        return l3.queued(self.project)

    def hold_l3(self):
        """Hold the project's turn lock the way a running turn does; the returned call gives it back."""
        held, done = threading.Event(), threading.Event()

        def hold():
            with l3.lock(self.project):
                held.set()
                done.wait(10)
        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.addCleanup(done.set)
        self.assertTrue(held.wait(5))

        def release():
            done.set()
            thread.join(5)
        return release

    # ---- the endpoint -------------------------------------------------------

    def test_a_message_sent_while_l3_is_busy_is_queued_with_its_position(self):
        self.hold_l3()
        status, first = self.post_json("/api/chat", {"project": self.project, "text": "first"})
        self.assertEqual(status, 200, first)
        self.assertEqual((first["queued"]["text"], first["queued"]["position"]), ("first", 1))
        status, second = self.post_json("/api/chat", {"project": self.project, "text": "second"})
        self.assertEqual((status, second["queued"]["position"]), (200, 2))

        rows = self.queue_rows()
        self.assertEqual([(r["text"], r["trigger"], r["role"]) for r in rows],
                         [("first", "chat", "burak"), ("second", "chat", "burak")])
        self.assertEqual(json.loads(self.request("GET", f"/api/chat/{self.project}")[1])["queued"], rows)

    def test_a_free_l3_still_streams_the_answer(self):
        self.patch(l3, "turn", new=lambda project, text, *, trigger, on_text: (
            on_text("two tasks."), {"session_id": "s1", "engine": "claude", "error": None})[1])
        status, payload = self.request("POST", "/api/chat", {"project": self.project, "text": "status?"})
        self.assertEqual(status, 200)
        self.assertIn(b'"two tasks."', payload)
        self.assertIn(b'"done"', payload)
        self.assertNotIn(b'"queued"', payload)
        self.assertEqual(self.queue_rows(), [])

    def test_a_queued_message_is_removed_only_before_its_turn_starts(self):
        self.hold_l3()
        _, ack = self.post_json("/api/chat", {"project": self.project, "text": "never mind"})
        message_id = ack["queued"]["id"]
        status, out = self.post_json("/api/chat/remove", {"project": self.project, "id": message_id})
        self.assertEqual((status, out["queued"]), (200, []))
        self.assertEqual(self.queue_rows(), [])

        status, out = self.post_json("/api/chat/remove", {"project": self.project, "id": message_id})
        self.assertEqual(status, 409)
        self.assertIn("already started", out["error"])

    # ---- draining -----------------------------------------------------------

    def deliverable(self):
        return mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "test"})

    def test_consecutive_chat_messages_fold_into_one_turn_and_server_rows_keep_their_own(self):
        for text, trigger in (("first", "chat"), ("second", "chat"), ("a fault", "incident"), ("third", "chat")):
            l3.queue_message(self.project, text, trigger=trigger, role="burak" if trigger == "chat" else "server")
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, self.deliverable():
            server.drain_l3_queue(self.project)
        self.assertEqual([(c.args[1], c.kwargs["trigger"]) for c in turn.call_args_list],
                         [("first\n\nsecond", "chat"), ("a fault", "incident"), ("third", "chat")])
        self.assertEqual(self.queue_rows(), [])

    def test_the_queue_drains_at_the_turn_boundary_not_at_the_next_tick(self):
        drained = threading.Event()
        calls = []

        def fake_turn(project, text, *, trigger, on_text=None):
            calls.append(text)
            if on_text:  # the turn Burak started from the page; he types again while it runs
                l3.queue_message(project, "while you were busy", trigger="chat", role="burak")
                on_text("working on it")
            else:
                drained.set()
            return {"session_id": "s1", "engine": "claude", "error": None, "completed": True}

        with mock.patch.object(l3, "turn", new=fake_turn), self.deliverable():
            self.request("POST", "/api/chat", {"project": self.project, "text": "status?"})
            self.assertTrue(drained.wait(10), "the queue waited for the next tick instead of the turn boundary")
        self.assertEqual(calls, ["status?", "while you were busy"])
        self.assertEqual(self.queue_rows(), [])

    # ---- restarts -----------------------------------------------------------

    def test_a_pending_queue_neither_holds_the_restart_nor_loses_a_message(self):
        S.write_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {"at": S.now(), "files": ["altitude/l3.py"]})
        l3.queue_message(self.project, "before the restart", trigger="chat", role="burak")
        self.assertEqual(server.restart_status()["waiting_for"], [],
                         "a queued message is not a turn in flight and must not hold the restart")

        release = self.hold_l3()  # only a turn actually running holds it
        self.assertEqual(server.restart_status()["waiting_for"], [f"{self.project} L3"])
        release()

        # The queue is a file: the restarted process reads the same rows and runs each exactly once.
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, self.deliverable():
            server.drain_l3_queue(self.project)
            server.drain_l3_queue(self.project)
        turn.assert_called_once_with(self.project, "before the restart", trigger="chat")


if __name__ == "__main__":
    unittest.main()
