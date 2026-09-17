"""Web push for decision alerts (issue #221), against a real push service on the loopback interface.

The incident class this prevents: a decision waiting unanswered because the operator's phone was
closed, and its opposite — a push service told what the decision says. Every request below is the
one Altitude really sends: an empty, signed POST to the endpoint the browser handed it.
"""
from __future__ import annotations

import base64
import http.client
import http.server
import json
import ssl
import subprocess
import threading
import unittest
import urllib.request

from tests.support import AltitudeCase
from altitude import config, push, server, state as S, tasks as T


class _Service(http.server.BaseHTTPRequestHandler):
    """The push service's side: record what arrived, answer with the configured status."""

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        self.server.requests.append({"path": self.path, "body": self.rfile.read(size),
                                     "headers": {name.lower(): value for name, value in self.headers.items()}})
        self.send_response(self.server.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def _der(raw: bytes) -> bytes:
    """The 64-byte JWT signature back in the DER form OpenSSL verifies."""
    def integer(value: bytes) -> bytes:
        value = value.lstrip(b"\x00") or b"\x00"
        if value[0] & 0x80:
            value = b"\x00" + value
        return b"\x02" + bytes([len(value)]) + value

    body = integer(raw[:32]) + integer(raw[32:])
    return b"\x30" + bytes([len(body)]) + body


def _unpad(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


class TestPush(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(push, "KEY_DIR", self.tmp / "push")
        self.patch(push, "RECORD", self.tmp / "push.json")
        mine = {self.project: config.load_projects()[self.project]}
        self.patch(config, "load_projects", lambda: dict(mine))  # only this case's decisions
        self.patch(config, "TLS_DIR", self.tmp / "tls")
        server.tls_init("127.0.0.1")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config.TLS_DIR / "server.crt", config.TLS_DIR / "server.key")
        self.service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Service)
        self.service.socket = context.wrap_socket(self.service.socket, server_side=True)
        self.service.requests, self.service.status = [], 201
        threading.Thread(target=self.service.serve_forever, daemon=True).start()
        self.addCleanup(self.service.server_close)
        self.addCleanup(self.service.shutdown)
        self.setenv("SSL_CERT_FILE", str(config.TLS_DIR / "ca.crt"))  # the fixture service's own CA
        self.patch(urllib.request, "_opener", None)  # each case has its own CA; urllib caches its opener
        self.endpoint = f"https://127.0.0.1:{self.service.server_address[1]}/wake/device-1"

    def decision(self, title: str, question: str) -> dict:
        task = T.new(self.project, title, "Do it.", actor="burak")
        task.update({"state": "running", "attempt": 1})
        S.save_task(self.project, task)
        T.block(self.project, task["slug"], question, actor="l2", updates={"waiting_on": "l3"})
        return T.escalate(self.project, task["slug"], question)

    def test_a_new_decision_wakes_the_device_once_with_a_signed_empty_nudge(self):
        push.subscribe(self.endpoint)
        self.decision("Choose backup retention", "How long should backups stay?")
        push.notify()
        [sent] = self.service.requests
        self.assertEqual((sent["path"], sent["body"]), ("/wake/device-1", b""))
        self.assertEqual(sent["headers"]["urgency"], "high")
        self.assertEqual(sent["headers"]["ttl"], str(push.TTL_SECONDS))

        token, key = (part.split("=", 1)[1] for part in sent["headers"]["authorization"][len("vapid "):].split(","))
        self.assertEqual(key, push.public_key())
        self.assertEqual(len(_unpad(key)), 65)  # the uncompressed P-256 point the browser subscribes with
        header, claims, signature = token.split(".")
        self.assertEqual(json.loads(_unpad(header)), {"typ": "JWT", "alg": "ES256"})
        self.assertEqual(json.loads(_unpad(claims))["aud"], f"https://127.0.0.1:{self.service.server_address[1]}")
        self.assertEqual(json.loads(_unpad(claims))["sub"], push.CONTACT)

        # The service can check the signature, which is the whole point of sending one.
        public = self.tmp / "vapid.pub"
        subprocess.run(["openssl", "ec", "-in", push.KEY_DIR / "vapid.key", "-pubout", "-out", public],
                       check=True, capture_output=True)
        (self.tmp / "signed").write_text(f"{header}.{claims}")
        (self.tmp / "signature").write_bytes(_der(_unpad(signature)))
        verified = subprocess.run(["openssl", "dgst", "-sha256", "-verify", public,
                                   "-signature", self.tmp / "signature", self.tmp / "signed"],
                                  capture_output=True, text=True)
        self.assertIn("Verified OK", verified.stdout)

        # Nothing of the decision travels: no body, and no trace of it anywhere in the request.
        self.assertNotIn("backup", json.dumps(sent, default=str).lower())

    def test_the_same_decision_never_wakes_the_device_twice_and_a_second_one_does(self):
        push.subscribe(self.endpoint)
        self.decision("Choose backup retention", "How long should backups stay?")
        push.notify()
        push.notify()
        push.notify()
        self.assertEqual(len(self.service.requests), 1)
        self.decision("Rotate the signing key", "Rotate now or at the next release?")
        push.notify()
        self.assertEqual(len(self.service.requests), 2)

    def test_republishing_a_waiting_decision_wakes_nobody_a_second_time(self):
        push.subscribe(self.endpoint)
        task = self.decision("Choose backup retention", "How long should backups stay?")
        push.notify()
        T.escalate(self.project, task["slug"], "How long should backups stay?")  # block, then escalation
        push.notify()
        self.assertEqual(len(self.service.requests), 1)

    def test_a_device_hears_nothing_about_the_decisions_that_were_already_waiting(self):
        self.decision("Choose backup retention", "How long should backups stay?")
        push.subscribe(self.endpoint)
        push.notify()
        self.assertEqual(self.service.requests, [])

    def test_an_expired_subscription_is_dropped_and_a_refused_one_is_kept(self):
        push.subscribe(self.endpoint)
        self.service.status = 410
        self.decision("Choose backup retention", "How long should backups stay?")
        push.notify()
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["subscriptions"], [])
        self.decision("Rotate the signing key", "Rotate now or at the next release?")
        push.notify()
        self.assertEqual(len(self.service.requests), 1)  # a forgotten device is not tried again

        push.subscribe(self.endpoint)
        self.service.status = 503
        self.decision("Archive the drill logs", "Archive or keep them?")
        notes = []
        push.notify(notes.append)
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["subscriptions"], [self.endpoint])
        self.assertIn("refused with 503", notes[0])

        # A decision no device took is still owed, and the next tick that gets through carries it.
        self.service.status = 201
        push.notify()
        self.assertEqual(len(self.service.requests), 3)
        push.notify()
        self.assertEqual(len(self.service.requests), 3)

    def test_a_second_device_does_not_silence_a_decision_the_first_is_still_owed(self):
        push.subscribe(self.endpoint)
        self.decision("Choose backup retention", "How long should backups stay?")
        push.subscribe(self.endpoint.replace("device-1", "device-2"))  # a phone joins before the tick
        push.notify()
        self.assertEqual(sorted(sent["path"] for sent in self.service.requests),
                         ["/wake/device-1", "/wake/device-2"])

    def test_an_unreachable_push_service_leaves_the_subscription_and_says_so(self):
        push.subscribe(self.endpoint)
        self.service.shutdown()
        self.service.server_close()
        self.decision("Choose backup retention", "How long should backups stay?")
        notes = []
        push.notify(notes.append)
        self.assertIn("deferred", notes[0])
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["subscriptions"], [self.endpoint])

    def test_a_subscription_needs_the_https_endpoint_a_push_service_issued(self):
        for endpoint in ("", "not-a-url", "http://127.0.0.1:9/wake", "file:///etc/passwd"):
            with self.subTest(endpoint=endpoint), self.assertRaises(push.PushFailure):
                push.subscribe(endpoint)
        self.assertFalse((self.tmp / "push.json").exists())

    def test_without_openssl_push_is_simply_unavailable(self):
        self.setenv("PATH", str(self.tmp))
        with self.assertRaises(push.PushFailure):
            push.public_key()


class TestAlertsAPI(AltitudeCase):
    """What the page calls: the key it subscribes with, and registering or dropping its endpoint."""

    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(push, "KEY_DIR", self.tmp / "push")
        self.patch(push, "RECORD", self.tmp / "push.json")
        mine = {self.project: config.load_projects()[self.project]}
        self.patch(config, "load_projects", lambda: dict(mine))
        self.patch(server, "log", new=lambda *a, **k: None)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        host, port = self.httpd.server_address
        connection = http.client.HTTPConnection(host, port, timeout=5)
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"null")

    def test_the_page_reads_the_key_and_registers_and_drops_its_endpoint(self):
        status, out = self.call("GET", "/api/alerts")
        self.assertEqual(status, 200)
        self.assertEqual(len(base64.urlsafe_b64decode(out["key"] + "=" * (-len(out["key"]) % 4))), 65)

        endpoint = "https://push.example/wake/device-1"
        self.assertEqual(self.call("POST", "/api/alerts/subscription", {"endpoint": endpoint}), (200, {"push": True}))
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["subscriptions"], [endpoint])
        self.assertEqual(self.call("POST", "/api/alerts/subscription", {"endpoint": endpoint, "remove": True}),
                         (200, {"push": False}))
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["subscriptions"], [])

    def test_only_the_operator_devices_are_kept_and_only_https_endpoints(self):
        for index in range(push.DEVICES + 2):
            self.call("POST", "/api/alerts/subscription", {"endpoint": f"https://push.example/wake/{index}"})
        kept = json.loads((self.tmp / "push.json").read_text())["subscriptions"]
        self.assertEqual(kept, [f"https://push.example/wake/{index}"
                                for index in range(2, push.DEVICES + 2)])
        status, out = self.call("POST", "/api/alerts/subscription", {"endpoint": "http://push.example/wake/9"})
        self.assertEqual(status, 400)
        self.assertIn("https endpoint", out["error"])

    def test_a_machine_that_cannot_sign_says_so_instead_of_handing_out_a_key(self):
        self.setenv("PATH", str(self.tmp))
        status, out = self.call("GET", "/api/alerts")
        self.assertEqual((status, out["key"]), (200, None))
        self.assertIn("OpenSSL", out["why"])


class TestTheTick(AltitudeCase):
    """Waking devices is a background errand of the tick, not a step dispatch waits behind."""

    def test_a_push_service_that_never_answers_holds_up_nothing(self):
        self.setenv("ALTITUDE_TIMERS", "0")
        reached, release = threading.Event(), threading.Event()

        def hang(log=None):
            reached.set()
            release.wait(10)

        self.patch(push, "notify", hang)
        for name in ("drain_hook_faults", "auto_restart", "tick_project"):
            self.patch(server, name, lambda *a, **k: None)
        self.patch(server.dispatch, "run_settings", lambda *a, **k: None)
        self.patch(server.quota_codex, "refresh_if_due", lambda *a, **k: None)
        self.patch(server, "log", new=lambda *a, **k: None)
        digested = []
        self.patch(server, "morning_digest", lambda: digested.append(True))
        self.addCleanup(release.set)

        server.tick()
        self.assertTrue(reached.wait(10))  # the push is under way
        self.assertEqual(digested, [True])  # and the tick reached its end without waiting for it
        release.set()
        server._bg["push"].join(10)


if __name__ == "__main__":
    unittest.main()
