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
from datetime import timedelta

from altitude import config, digest, engines, l3, push, server, state as S, tasks as T


class _Service(http.server.BaseHTTPRequestHandler):
    """The push service's side: record what arrived, answer with the configured status."""

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        self.server.requests.append({"path": self.path, "body": self.rfile.read(size),
                                     "headers": {name.lower(): value for name, value in self.headers.items()}})
        status = self.server.refusing.get(self.path, self.server.status)
        body = self.server.body if status >= 300 else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        self.service.requests, self.service.status, self.service.body = [], 201, b""
        self.service.refusing = {}  # path -> status, for one device refused among several
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
        # Apple refuses a contact without a real domain: altitude@localhost drew 403 BadJwtToken.
        self.assertRegex(push.CONTACT, r"^mailto:[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
        self.assertNotIn("localhost", push.CONTACT)

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
        self.assertEqual(notes[0], "push wakes 1 device(s) for 1 new decision(s)")  # the daemon log shows each wake
        self.assertIn("refused with 503", notes[1])

        # A decision no device took is still owed, and the next tick that gets through carries it.
        self.service.status = 201
        push.notify()
        self.assertEqual(len(self.service.requests), 3)
        push.notify()
        self.assertEqual(len(self.service.requests), 3)

    def test_a_refusal_is_recorded_with_its_reason_once_and_clears_when_a_push_gets_through(self):
        push.subscribe(self.endpoint)
        self.service.status, self.service.body = 403, b'{"reason":"BadJwtToken"}'
        self.decision("Choose backup retention", "How long should backups stay?")
        notes = []
        for _ in range(3):
            push.notify(notes.append)
        self.assertEqual(len(self.service.requests), 3)  # tried each tick, so a fix on either side needs no step
        self.assertEqual(notes, ["push wakes 1 device(s) for 1 new decision(s)",
                                 "push to %s refused with 403 BadJwtToken" % self.endpoint.split("/")[2]])
        self.assertEqual(push.refused(), [{"host": self.endpoint.split("/")[2], "reason": "403 BadJwtToken"}])

        self.service.status = 201
        push.notify(notes.append)
        self.assertEqual(notes[2:], ["push to %s delivered again" % self.endpoint.split("/")[2]])
        self.assertEqual(push.refused(), [])
        push.notify(notes.append)
        self.assertEqual((len(notes), len(self.service.requests)), (3, 4))

    def test_a_refusal_without_a_reason_and_a_resubscribed_device_start_clean(self):
        push.subscribe(self.endpoint)
        self.service.status, self.service.body = 400, b"Bad request\n  no detail"
        self.decision("Choose backup retention", "How long should backups stay?")
        push.notify()
        self.assertEqual(push.refused()[0]["reason"], "400 Bad request no detail")
        push.subscribe(self.endpoint)  # turning alerts off and on again subscribes afresh
        self.assertEqual(push.refused(), [])
        push.notify()
        push.forget(self.endpoint)
        self.assertNotIn(self.endpoint, (self.tmp / "push.json").read_text())

    def test_a_refusal_echoing_the_endpoint_records_only_its_status(self):
        push.subscribe(self.endpoint)
        self.service.status = 403
        self.service.body = json.dumps({"reason": f"Bad subscription {self.endpoint}"}).encode()
        self.decision("Choose backup retention", "How long should backups stay?")
        notes = []
        push.notify(notes.append)
        self.assertEqual(push.refused()[0]["reason"], "403")
        self.assertNotIn("/wake/", json.dumps([notes, push.refused()]))

    def test_a_refused_device_is_retried_while_another_device_already_took_the_decision(self):
        second = self.endpoint.replace("device-1", "device-2")
        push.subscribe(self.endpoint)
        push.subscribe(second)
        self.service.refusing, self.service.body = {"/wake/device-2": 403}, b'{"reason":"BadJwtToken"}'
        self.decision("Choose backup retention", "How long should backups stay?")
        push.notify()
        self.assertEqual([r["reason"] for r in push.refused()], ["403 BadJwtToken"])
        push.notify()  # the first device took it; only the refused one is tried again
        self.assertEqual([sent["path"] for sent in self.service.requests[2:]], ["/wake/device-2"])

        self.service.refusing = {}  # fixed on either side: the next tick gets through and stops retrying
        push.notify()
        push.notify()
        self.assertEqual(push.refused(), [])
        self.assertEqual([sent["path"] for sent in self.service.requests[2:]], ["/wake/device-2"] * 2)

    def test_a_device_subscribing_again_while_a_refused_send_is_in_flight_starts_clean(self):
        push.subscribe(self.endpoint)
        self.decision("Choose backup retention", "How long should backups stay?")
        sending = push._send

        def resubscribed_meanwhile(endpoint):
            outcome = sending(endpoint)
            push.forget(endpoint)
            push.subscribe(endpoint)
            return outcome

        self.service.status, self.service.body = 403, b'{"reason":"BadJwtToken"}'
        push.notify()
        self.patch(push, "_send", resubscribed_meanwhile)
        push.notify()
        self.assertEqual(push.refused(), [])

    def test_a_second_device_does_not_silence_a_decision_the_first_is_still_owed(self):
        push.subscribe(self.endpoint)
        self.decision("Choose backup retention", "How long should backups stay?")
        push.subscribe(self.endpoint.replace("device-1", "device-2"))  # a phone joins before the tick
        push.notify()
        self.assertEqual(sorted(sent["path"] for sent in self.service.requests),
                         ["/wake/device-1", "/wake/device-2"])

    def for_operator(self, title: str, question: str) -> str:
        """An owner publishes an operator question through `alt task block --for-operator`, which tells L3."""
        task = T.new(self.project, title, "Do it.", actor="burak")
        task.update({"state": "running", "attempt": 1})
        S.save_task(self.project, task)
        T.block(self.project, task["slug"], question, actor="l2", expected_attempt=1,
                updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, tell_l3=True)
        return task["slug"]

    def l3_turn(self, during=lambda: None, *, limited: bool = False) -> None:
        """L3 reads the queued block notification; `during` runs inside its turn, as L3's own verbs would."""
        def execute(text, **kwargs):
            if limited:
                return {"text": "", "session_id": "", "error": "fixture allowance exhausted", "usage": {},
                        "limited": {"scope": "engine", "why": "fixture allowance exhausted",
                                    "until": "2999-01-01T00:00:00+00:00"}, "safe_to_retry": True, "tools": []}
            during()
            return {"text": "Read.", "session_id": "fixture-session", "reported_session_id": "fixture-session",
                    "usage": {"input_tokens": 10}, "context_tokens": 10, "error": None, "tools": []}

        for seam in ("claude_print", "codex_exec"):
            self.patch(engines, seam, side_effect=execute)
        l3.deliver_queued(self.project)

    def test_a_question_l3_settles_during_its_turn_wakes_no_device(self):
        push.subscribe(self.endpoint)
        slug = self.for_operator("Choose backup retention", "How long should backups stay?")
        push.notify()  # queued for L3: Needs you lists it, and no device wakes yet
        self.assertEqual([row["alert_held"] for row in digest.queue()], [True])

        answers = []

        def settle():
            push.notify()  # L3 is still reading it
            answers.append(T.message(self.project, slug, "l3", "The retention policy says 30 days.", by="l3"))

        self.l3_turn(settle)
        push.notify()  # the owner L3 answered is due to resume
        T.resume(self.project, slug)
        push.notify()
        [question] = T.operator_questions(S.load_task(self.project, slug))
        T.resolve_question(self.project, slug, question["id"], question["revision"], answers[0]["id"],
                           disposition="answered", reason="30 days, per the retention policy.", expected_attempt=1,
                           l3_authority="The recorded retention policy settles it.")
        push.notify()
        self.assertEqual(digest.queue(), [])
        self.assertEqual(self.service.requests, [])

    def test_a_question_still_open_when_l3_turn_ends_wakes_each_device_once(self):
        push.subscribe(self.endpoint)
        self.for_operator("Choose backup retention", "How long should backups stay?")
        self.l3_turn(lambda: push.notify())
        self.assertEqual(self.service.requests, [])  # nothing during the turn
        push.notify()
        push.notify()
        self.assertEqual(len(self.service.requests), 1)

    def test_a_question_alerts_after_the_hold_when_l3_cannot_take_its_turn(self):
        push.subscribe(self.endpoint)
        self.for_operator("Choose backup retention", "How long should backups stay?")
        self.l3_turn(limited=True)  # every engine refuses: the notification stays queued
        self.assertEqual([row["trigger"] for row in l3._queue_rows(l3.queue_path(self.project))], ["block"])
        push.notify()
        self.assertEqual(self.service.requests, [])
        self.patch(digest, "ALERT_HOLD", timedelta(0))  # the hold has passed
        push.notify()
        push.notify()
        self.assertEqual(len(self.service.requests), 1)

    def test_an_answered_decision_wakes_no_device(self):
        # Safari shows something for every push and WebKit keeps a banner closed within 30 seconds, so a wake
        # that only cleared an answered banner left a bare "Altitude" one on the phone.
        second = self.endpoint.replace("device-1", "device-2")
        push.subscribe(self.endpoint)
        push.subscribe(second)
        task = self.decision("Choose backup retention", "How long should backups stay?")
        push.notify()
        self.assertEqual(len(self.service.requests), 2)

        T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "Thirty days.")  # answered
        self.assertEqual(digest.queue(), [])
        push.notify()
        push.notify()
        self.assertEqual(len(self.service.requests), 2)
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["seen"], [])

        # The same question asked again is a new alert.
        self.decision("Choose backup retention again", "How long should backups stay now?")
        push.notify()
        self.assertEqual(len(self.service.requests), 4)

    def test_a_device_still_owed_a_wake_is_not_woken_once_every_decision_is_settled(self):
        second = self.endpoint.replace("device-1", "device-2")
        push.subscribe(self.endpoint)
        push.subscribe(second)
        task = self.decision("Choose backup retention", "How long should backups stay?")
        self.service.refusing = {"/wake/device-2": 503}
        push.notify()  # the first device takes the alert; the second is owed it
        self.service.refusing = {}
        T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "Thirty days.")  # answered on the first
        push.notify()
        self.assertEqual([sent["path"] for sent in self.service.requests], ["/wake/device-1", "/wake/device-2"])
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["owed"], [])

    def test_a_review_held_for_the_operator_wakes_no_device(self):
        push.subscribe(self.endpoint)
        task = T.new(self.project, "Ship the release", "Do it.", actor="burak")
        self.patch(T, "review_row", lambda project, record: {
            "project": project, "slug": record["slug"], "title": record.get("title"), "kind": "review", "pr": 12,
            "question": "Review PR #12 before merge", "asked": "2026-10-10T00:00:00+00:00"}
            if record["slug"] == task["slug"] else None)
        self.assertEqual([row["kind"] for row in digest.queue()], ["review"])  # Needs you lists it
        push.notify()
        self.assertEqual(self.service.requests, [])

    def test_a_reader_while_the_question_is_published_already_sees_it_held(self):
        task = T.new(self.project, "Choose backup retention", "Do it.", actor="burak")
        task.update({"state": "running", "attempt": 1})
        S.save_task(self.project, task)
        saving, readers, read = S.save_task, [], []

        def save(project, record):
            saving(project, record)
            if record.get("state") == "blocked" and not readers:  # the question is visible, the lock still held
                readers.append(threading.Thread(target=lambda: read.append(digest.queue())))
                readers[0].start()

        self.patch(S, "save_task", save)
        T.block(self.project, task["slug"], "How long should backups stay?", actor="l2", expected_attempt=1,
                updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, tell_l3=True)
        readers[0].join(30)
        self.assertEqual([row.get("alert_held") for row in read[0]], [True])

    def test_a_decision_settled_before_it_alerted_wakes_no_device(self):
        push.subscribe(self.endpoint)
        slug = self.for_operator("Choose backup retention", "How long should backups stay?")
        push.notify()
        T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, "Thirty days.")  # read in Needs you, answered
        push.notify()
        self.assertEqual(self.service.requests, [])

    def test_an_unreachable_push_service_leaves_the_subscription_and_says_so(self):
        push.subscribe(self.endpoint)
        self.service.shutdown()
        self.service.server_close()
        self.decision("Choose backup retention", "How long should backups stay?")
        notes = []
        push.notify(notes.append)
        self.assertIn("deferred", notes[1])
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
        self.assertEqual((status, out["refused"]), (200, []))
        self.assertEqual(len(base64.urlsafe_b64decode(out["key"] + "=" * (-len(out["key"]) % 4))), 65)

        endpoint = "https://push.example/wake/device-1"
        self.assertEqual(self.call("POST", "/api/alerts/subscription", {"endpoint": endpoint}), (200, {"push": True}))
        self.assertEqual(json.loads((self.tmp / "push.json").read_text())["subscriptions"], [endpoint])
        record = json.loads((self.tmp / "push.json").read_text())
        S.atomic_write(self.tmp / "push.json", json.dumps({**record, "refused": {endpoint: "403 BadJwtToken"}}))
        self.assertEqual(self.call("GET", "/api/alerts")[1]["refused"],
                         [{"host": "push.example", "reason": "403 BadJwtToken"}])  # the page says why
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
        self.patch(server.engines, "refresh_quotas", lambda *a, **k: None)
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
