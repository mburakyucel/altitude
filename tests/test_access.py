"""Who may use Altitude: a browser pairs once with a one-time code from `alt pair` and is remembered by an
HttpOnly cookie until revoked; the `alt` CLI proves it runs on this machine as the operator with the private
machine key; the page itself, health, the CA and pairing stay open so an unpaired browser can pair."""
import contextlib
import io
import json
import runpy
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import ALT, AltitudeCase
from tests.test_restart_command import load_script
from altitude import access, config, server, tls, state as S, tasks as T

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
DESKTOP = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def alt_pair() -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runpy.run_path(str(ALT))["main"](["pair"])
    return output.getvalue().split("Pairing code: ")[1].split()[0]


class TestFirstBrowserCode(AltitudeCase):
    """Installer eligibility uses the same locked, hashed store as manual pairing."""

    def setUp(self):
        super().setUp()
        self.patch(access, "DIR", self.tmp / "first-browser-access")
        self.now = datetime(2026, 10, 13, tzinfo=timezone.utc)
        self.patch(access, "_now", new=lambda: self.now)

    def test_first_browser_code_keeps_manual_policy_and_is_single_use(self):
        issued = access.issue_code(first_browser=True)
        self.assertRegex(issued["code"], r"^[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$")
        self.assertEqual(issued["minutes"], 10)
        self.assertEqual(datetime.fromisoformat(issued["expires"]), self.now + timedelta(minutes=10))
        stored = (access.DIR / "devices.json").read_text()
        self.assertNotIn(issued["code"], stored)
        self.assertNotIn(issued["code"].replace("-", ""), stored)
        key, row = access.redeem(issued["code"], "Fixture browser")
        self.assertEqual(access.device(key), row)
        self.assertNotIn(key, (access.DIR / "devices.json").read_text())
        with self.assertRaises(access.AccessError) as used:
            access.redeem(issued["code"], "Another fixture browser")
        self.assertEqual(used.exception.status, 410)
        self.assertEqual(len(access.devices()), 1)

    def test_first_browser_preserves_active_manual_code_and_its_wrong_tries(self):
        manual = access.issue_code()
        with self.assertRaises(access.AccessError):
            access.redeem("not-a-valid-code", "Fixture browser")
        before = (access.DIR / "devices.json").read_bytes()
        self.assertIsNone(access.issue_code(first_browser=True))
        self.assertEqual((access.DIR / "devices.json").read_bytes(), before)
        access.redeem(manual["code"], "Fixture browser")

    def test_first_browser_preserves_devices_even_with_no_active_code(self):
        manual = access.issue_code()
        key, row = access.redeem(manual["code"], "Retained fixture browser")
        before = (access.DIR / "devices.json").read_bytes()
        self.assertIsNone(access.issue_code(first_browser=True))
        self.assertEqual((access.DIR / "devices.json").read_bytes(), before)
        self.assertEqual(access.device(key), row)

    def test_expiry_allows_a_fresh_first_browser_code_but_not_redemption(self):
        manual = access.issue_code()
        self.now += timedelta(minutes=10)
        fresh = access.issue_code(first_browser=True)
        self.assertIsNotNone(fresh)
        with self.assertRaises(access.AccessError) as expired:
            access.redeem(manual["code"], "Fixture browser")
        self.assertEqual(expired.exception.status, 403)
        self.now += timedelta(minutes=10)
        with self.assertRaises(access.AccessError) as expired:
            access.redeem(fresh["code"], "Fixture browser")
        self.assertEqual(expired.exception.status, 410)
        self.assertEqual(access.devices(), [])

    def test_first_browser_wrong_codes_cancel_on_the_fifth_attempt(self):
        issued = access.issue_code(first_browser=True)
        for left in range(4, -1, -1):
            with self.assertRaises(access.AccessError) as wrong:
                access.redeem("not-a-valid-code", "Fixture browser")
            self.assertEqual(wrong.exception.status, 403)
            if left:
                self.assertIn(f"{left} ", str(wrong.exception))
        with self.assertRaises(access.AccessError) as cancelled:
            access.redeem(issued["code"], "Fixture browser")
        self.assertEqual(cancelled.exception.status, 410)

    def _concurrent_first_browser(self, operation):
        """Pause a real store read under its lock, then start installer eligibility behind it."""
        read_started, contender_started, release = threading.Event(), threading.Event(), threading.Event()
        results, failures = {}, []
        original_read, original_locked = access._read, access._locked

        def read():
            record = original_read()
            if threading.current_thread().name == "store-owner":
                read_started.set()
                if not release.wait(5):
                    raise AssertionError("store owner was not released")
            return record

        @contextlib.contextmanager
        def locked():
            if threading.current_thread().name == "installer-contender":
                contender_started.set()
            with original_locked():
                yield

        def run(name, action):
            try:
                results[name] = action()
            except BaseException as error:
                failures.append(error)

        with mock.patch.object(access, "_read", side_effect=read), mock.patch.object(access, "_locked", new=locked):
            owner = threading.Thread(target=run, args=("owner", operation), name="store-owner")
            contender = threading.Thread(target=run, args=("first", lambda: access.issue_code(first_browser=True)),
                                         name="installer-contender")
            owner.start()
            try:
                self.assertTrue(read_started.wait(5), "store owner never read its record")
                contender.start()
                self.assertTrue(contender_started.wait(5), "installer did not enter the access lock")
            finally:
                release.set()
                owner.join(5)
                if contender.ident is not None:
                    contender.join(5)
            self.assertFalse(owner.is_alive())
            self.assertFalse(contender.is_alive())
        if failures:
            raise failures[0]
        self.assertIsNone(results["first"])
        return results["owner"]

    def test_manual_issuance_wins_before_concurrent_installer_eligibility(self):
        manual = self._concurrent_first_browser(access.issue_code)
        access.redeem(manual["code"], "Fixture browser")

    def test_redemption_wins_before_concurrent_installer_eligibility(self):
        manual = access.issue_code()
        key, row = self._concurrent_first_browser(lambda: access.redeem(manual["code"], "Fixture browser"))
        self.assertEqual(access.device(key), row)
        self.assertIsNone(access._read()["code"])

    def test_only_one_concurrent_first_browser_issuance_succeeds(self):
        issued = self._concurrent_first_browser(lambda: access.issue_code(first_browser=True))
        access.redeem(issued["code"], "Fixture browser")


class TestAccess(AltitudeCase):
    gated = True

    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.setenv("ALTITUDE_ACTOR", config.OPERATOR_ACTOR)
        self.patch(access, "DIR", self.tmp / "access")
        access.prepare()
        self.patch(server, "log", new=lambda _message: None)
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.host = "%s:%d" % self.httpd.server_address

    def request(self, method, path, body=None, *, device=None, headers=None):
        """One raw request; returns status, parsed reply and the Set-Cookie header, if any."""
        data = json.dumps(body).encode() if body is not None else b""
        sent = {"Host": self.host, "Content-Length": str(len(data)), "Content-Type": "application/json",
                "User-Agent": IPHONE, **({"Cookie": f"{access.COOKIE}={device}"} if device else {}), **(headers or {})}
        head = "".join(f"{name}: {value}\r\n" for name, value in sent.items())
        with socket.create_connection(self.httpd.server_address, timeout=10) as sock:
            sock.sendall(f"{method} {path} HTTP/1.0\r\n{head}\r\n".encode() + data)
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        raw_headers, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        lines = raw_headers.decode().split("\r\n")
        cookie = next((line.split(": ", 1)[1] for line in lines if line.lower().startswith("set-cookie:")), None)
        return int(lines[0].split()[1]), json.loads(payload or b"null"), cookie

    def pair(self, code, headers=None, **fields):
        return self.request("POST", "/api/pair", {"code": code, **fields}, headers=headers)

    def paired(self, **fields):
        status, reply, cookie = self.pair(alt_pair(), **fields)
        self.assertEqual(status, 200, reply)
        return cookie.split(";")[0].split("=", 1)[1], reply["device"]

    def recorded(self) -> dict:
        """What the operator's authority writes: this project's records, the devices and the machine's settings."""
        files = [path for root in (config.project_dir(self.project), access.DIR) for path in root.rglob("*")]
        files += list(config.ROOT.glob("*.json"))
        return {str(path): path.read_bytes() for path in files if path.is_file()}

    def test_altitudes_own_agents_cannot_act_with_a_paired_devices_sign_in(self):
        from altitude import terminal
        slug = T.new(self.project, "Fictional task", "A fictional request.")["slug"]
        browser, _ = self.paired()
        task = {"project": self.project, "slug": slug}
        routes = [("/api/chat", {"project": self.project, "text": "Approve the merge."}),
                  ("/api/chat/send-now", {"project": self.project, "id": "x"}),
                  ("/api/chat/remove", {"project": self.project, "id": "x"}),
                  ("/api/l2/message", {**task, "text": "Approved."}), ("/api/l2/send-now", {**task, "id": "x"}),
                  ("/api/l2/remove", {**task, "id": "x"}),
                  ("/api/decide", {**task, "question": "q", "choice": "approve"}),
                  ("/api/task/action", {**task, "action": "merge"}), ("/api/task/review", {**task, "action": "skip"}),
                  ("/api/improve", {"project": self.project}), ("/api/pr/close", {"project": self.project}),
                  ("/api/issue/close", {"project": self.project}), ("/api/project/setup", {"project": self.project}),
                  ("/api/project/add", {"path": "/fictional"}), ("/api/project/remove", {"project": self.project}),
                  ("/api/defaults", {}), ("/api/project-model-settings/reset", {"project": self.project}),
                  ("/api/new-tasks", {}), ("/api/projects-folder", {}), ("/api/operator-name", {"name": "Agent"}),
                  ("/api/incident-reports", {}), ("/api/alerts/subscription", {}), ("/api/alerts/pace", {}),
                  ("/api/l3/reset", {"project": self.project}), ("/api/l3/start", {"project": self.project}),
                  ("/api/devices/code", {}), ("/api/devices/revoke", {"id": "x"}), ("/api/devices/share", {}),
                  ("/api/sign-in/start", {}), ("/api/trust", {}), ("/api/pair", {"code": "x"}), ("/api/update", {}),
                  ("/api/validation-access", {}), ("/api/terminal-access", {"enabled": True}),
                  (f"/api/terminal/{self.project}/open", {}), ("/api/voice", {}), ("/api/voice/service", None),
                  ("/api/voice/live", None), ("/api/a-route-added-later", {})]
        before = self.recorded()
        with mock.patch.object(terminal, "agent_connection", return_value=True) as agent:
            for path, body in routes:
                for device in (browser, None):
                    with self.subTest(path=path, paired=bool(device)):
                        status, reply, _ = self.request("POST", path, body, device=device)
                        if device or path in ("/api/trust", "/api/pair"):  # open to anyone, so refused here too
                            self.assertEqual((status, reply), (403, {"error": server.AGENT_REFUSED}))
                        else:
                            self.assertEqual(status, 401)
            self.assertEqual(self.recorded(), before, "an agent's request records nothing")
            # This machine's key (the `alt` CLI) and a task's own owner keep their routes; the terminal still refuses.
            key = {access.KEY_HEADER: access.machine_key()}
            unmanaged = {"project": "not-managed", "text": "Hello."}
            self.assertEqual(self.request("POST", "/api/chat", unmanaged, headers=key)[0], 409)  # routed
            status, reply, _ = self.request("POST", "/api/terminal-access", {"enabled": True}, headers=key)
            self.assertEqual((status, reply), (403, {"error": "Terminal requests from Altitude's own agents are refused."}))
            self.assertNotEqual(self.request("POST", "/api/task/door", {})[1], {"error": server.AGENT_REFUSED})
            agent.reset_mock()
            self.request("POST", "/api/chat", {"project": self.project}, device=browser,
                         headers={"Connection": "keep-alive"})
            agent.assert_called_once()  # decided once per connection, by the platform's attribution
        with mock.patch.object(terminal, "agent_connection", return_value=False):
            for path, body in routes[:3]:
                status, reply, _ = self.request("POST", path, {**body, "project": "not-managed"}, device=browser)
                self.assertNotEqual(status, 403, reply)  # the operator's browser reaches the route

    def test_a_container_installation_does_not_infer_agents(self):
        from altitude import platform, terminal
        browser, _ = self.paired()
        self.patch(platform, "containerized", return_value=True)
        with mock.patch.object(terminal, "agent_connection", side_effect=AssertionError("must not infer origin")):
            status, reply, _ = self.request("POST", "/api/chat", {"project": "not-managed", "text": "Hello."},
                                            device=browser)
        self.assertEqual(status, 409, reply)  # routed: this project is not managed

    def test_malformed_invitation_does_not_revoke_paired_access_or_prevent_manual_replacement(self):
        key, row = self.paired()
        for invalid in ([], {"expires": "2026-10-13T12:00:00"},
                        {"hash": "fixture", "expires": "2026-10-13T12:00:00", "wrong": 0}):
            with self.subTest(code=invalid):
                record = access._read()
                record["code"] = invalid
                access._write(record)
                retained = (access.DIR / "devices.json").read_bytes()
                status, body, _ = self.request("GET", "/api/access", device=key)
                self.assertEqual((status, body["paired"]), (200, True))
                self.assertTrue(access.known(row["id"]))
                self.assertIsNone(access.issue_code(first_browser=True))
                self.assertEqual((access.DIR / "devices.json").read_bytes(), retained)
                manual = access.issue_code()
                self.assertEqual(access.device(key)["id"], row["id"])
                access.redeem(manual["code"], "Manual recovery fixture")

    def test_an_unpaired_browser_sees_only_the_page_and_the_way_to_pair(self):
        for method, path in (("GET", "/api/overview"), ("GET", "/api/devices"), ("GET", "/design/x/board.html"),
                             ("POST", "/api/operator-name"), ("POST", "/api/devices/code")):
            with self.subTest(path=path):
                status, reply, _ = self.request(method, path, {} if method == "POST" else None)
                self.assertEqual((status, reply), (401, {"error": "Pair this device to use Altitude.", "pair": True}))
        # On the computer itself, over plain HTTP, pairing needs no certificate check.
        trust = {"local": True, "https": False, "check": False, "command": False,
                 "certificate": None, "guide": tls.setup_guide()}
        self.assertEqual(self.request("GET", "/api/access")[:2], (200, {"paired": False, "device": None, "trust": trust}))
        self.assertEqual(self.request("GET", "/api/health")[0], 200)
        self.assertNotEqual(self.request("GET", "/api/overview", device="made-up")[0], 200)

    def test_a_code_from_alt_pair_pairs_one_browser_for_good(self):
        code = alt_pair()
        status, reply, cookie = self.pair(code.lower().replace("-", " "), standalone=True)
        self.assertEqual(status, 200)
        self.assertEqual(reply["device"]["name"], "Home Screen app on iPhone")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn(f"Max-Age={access.COOKIE_SECONDS}", cookie)
        self.assertNotIn("Secure", cookie)  # plain HTTP here; over TLS the cookie is Secure
        key = cookie.split(";")[0].split("=", 1)[1]
        self.assertNotIn(key, (access.DIR / "devices.json").read_text())  # only its hash is stored
        self.assertEqual(self.request("GET", "/api/overview", device=key)[:2], (200, {"state": "ready"}))
        self.assertEqual(self.request("GET", "/api/access", device=key)[:2],
                         (200, {"paired": True, "device": "Home Screen app on iPhone", "trust": {
                             "local": True, "https": False,
                             "check": False, "command": False, "certificate": None, "guide": tls.setup_guide()}}))
        status, reply, _ = self.pair(code)
        self.assertEqual((status, reply["error"]), (410, "This code has expired or was already used. Make a new one."))

    def test_wrong_codes_count_down_and_the_fifth_cancels_the_code(self):
        code = alt_pair()
        for left in (4, 3, 2, 1):
            status, reply, _ = self.pair("2222-2222")
            self.assertEqual(status, 403)
            self.assertEqual(reply["error"], f"That code is not right. {left} {'try' if left == 1 else 'tries'} left.")
        status, reply, _ = self.pair("2222-2222")
        self.assertEqual((status, reply["error"]), (403, "Too many wrong codes, so this one is cancelled. Make a new one."))
        self.assertEqual(self.pair(code)[0], 410)

    def test_a_code_expires_and_a_new_code_replaces_the_old(self):
        first = alt_pair()
        second = alt_pair()
        self.assertEqual(self.pair(first)[0], 403)
        record = json.loads((access.DIR / "devices.json").read_text())
        record["code"]["expires"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        (access.DIR / "devices.json").write_text(json.dumps(record))
        self.assertEqual(self.pair(second)[0], 410)

    def test_the_cli_uses_the_machine_key_and_only_the_operator_makes_codes(self):
        key = access.machine_key()
        self.assertEqual((access.DIR / "machine.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual(access.DIR.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.request("GET", "/api/overview", headers={access.KEY_HEADER: key})[0], 200)
        self.assertEqual(self.request("GET", "/api/overview", headers={access.KEY_HEADER: "not-" + key})[0], 401)
        for actor in ("l2", "l3"):
            with self.subTest(actor=actor):
                self.setenv("ALTITUDE_ACTOR", actor)
                with self.assertRaises(SystemExit) as refused, contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(ALT))["main"](["pair"])
                self.assertIn("alt pair", str(refused.exception.code))

    def test_the_restart_check_reaches_the_gated_server_with_the_machine_key(self):
        restart = load_script()
        host, port = self.httpd.server_address
        self.patch(restart, "service", new=lambda: config.network({"ALTITUDE_HOST": host, "ALTITUDE_PORT": str(port),
                                                                  "ALTITUDE_TLS": "0"}))
        self.assertEqual(json.loads(restart.fetch("/api/overview")), {"state": "ready"})
        self.patch(access, "machine_key", new=lambda: None)
        with self.assertRaises(restart.RestartError):  # refused with 401
            restart.fetch("/api/overview")

    def test_lockout_recovery_needs_only_a_shell_on_the_machine(self):
        """With no device paired and Altitude reachable only at a private address, an SSH session's `alt pair`
        writes the code straight to the private store; no browser, bind address or running page is involved."""
        self.assertEqual(access.devices(), [])
        key, device = self.paired(headers={"User-Agent": DESKTOP})
        self.assertEqual(device["name"], "Chrome on Mac")
        self.assertEqual(self.request("GET", "/api/overview", device=key)[0], 200)

    def test_revoking_a_device_ends_its_access_and_its_open_stream(self):
        phone, _ = self.paired()
        laptop, laptop_row = self.paired(headers={"User-Agent": DESKTOP})
        status, reply, _ = self.request("GET", "/api/devices", device=phone)
        self.assertEqual({row["name"] for row in reply["devices"]}, {"Safari on iPhone", "Chrome on Mac"})
        self.assertNotIn("hash", reply["devices"][0])
        with socket.create_connection(self.httpd.server_address, timeout=10) as stream:
            stream.sendall(f"GET /api/changes HTTP/1.0\r\nHost: {self.host}\r\nCookie: {access.COOKIE}={laptop}\r\n\r\n".encode())
            self.assertIn(b"200", stream.recv(65536))
            status, reply, cookie = self.request("POST", "/api/devices/revoke", {"id": laptop_row["id"]}, device=phone)
            self.assertEqual((status, [row["name"] for row in reply["devices"]], cookie), (200, ["Safari on iPhone"], None))
            deadline = time.monotonic() + 5
            while (chunk := stream.recv(65536)) and time.monotonic() < deadline:
                pass
            self.assertEqual(chunk, b"")  # the stream closed within a couple of seconds
        self.assertEqual(self.request("GET", "/api/overview", device=laptop)[0], 401)
        status, _, cookie = self.request("POST", "/api/devices/revoke", {"id": access.devices()[0]["id"]}, device=phone)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", cookie)
        self.assertEqual(self.request("GET", "/api/overview", device=phone)[0], 401)

    def test_revoking_a_device_ends_its_open_chat_answer_while_the_turn_goes_on(self):
        phone, row = self.paired()
        release, finished = threading.Event(), threading.Event()

        def turn(*_args, **_kwargs):  # a silent tool call: no text arrives while the device is removed
            release.wait(10)
            finished.set()
            return {"turn_id": "t1"}

        self.patch(config, "is_managed", new=lambda _project: True)
        self.patch(server.l3, "busy", new=lambda _project: False)
        self.patch(config, "restart_in_progress", new=lambda: False)
        self.patch(server, "server_l3_turn", new=turn)
        self.addCleanup(release.set)
        body = json.dumps({"project": "demo", "text": "hello"}).encode()
        with socket.create_connection(self.httpd.server_address, timeout=10) as stream:
            stream.sendall(f"POST /api/chat HTTP/1.1\r\nHost: {self.host}\r\nCookie: {access.COOKIE}={phone}\r\n"
                           f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            self.assertIn(b"200", stream.recv(65536))
            access.revoke(row["id"])
            deadline = time.monotonic() + 5
            while (chunk := stream.recv(65536)) and time.monotonic() < deadline:
                pass
            self.assertEqual(chunk, b"")  # the answer's connection closed within a couple of seconds
        self.assertFalse(finished.is_set())
        release.set()
        self.assertTrue(finished.wait(5))

    def test_a_used_device_renews_its_cookie_at_most_daily(self):
        key, _ = self.paired()
        self.assertIsNone(self.request("GET", "/api/overview", device=key)[2])
        record = json.loads((access.DIR / "devices.json").read_text())
        record["devices"][0]["used"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        (access.DIR / "devices.json").write_text(json.dumps(record))
        cookie = self.request("GET", "/api/overview", device=key)[2]
        self.assertIn(f"{access.COOKIE}={key};", cookie)
        self.assertIsNone(self.request("GET", "/api/overview", device=key)[2])

    def test_a_boards_read_pass_opens_only_that_projects_boards_for_its_device(self):
        """Design boards run sandboxed, so their own files arrive without the cookie; the viewer's path carries a
        read pass that a paired browser receives, bound to that browser, one project and the day, and that ends
        when the browser is removed."""
        boards = self.repo / "design" / "wireframes"
        boards.mkdir(parents=True)
        (boards / "index.html").write_text("<!doctype html><title>boards</title>")
        phone, phone_row = self.paired()
        laptop, laptop_row = self.paired(headers={"User-Agent": DESKTOP})
        self.assertEqual(server.design_viewer_url(self.project), f"/design/{self.project}")
        self.assertEqual(self.raw_status(f"/design/{self.project}")[0], 401)
        today = int(time.time() // 86400)
        viewer = f"/design/{self.project}/{access.design_pass(self.project, phone_row['id'], today)}/design/wireframes/index.html"
        status, head = self.raw_status(f"/design/{self.project}", device=phone)
        self.assertEqual(status, 302)
        self.assertIn(f"Location: {viewer}", head)
        for day, expected in ((today, 200), (today - 1, 200), (today - 2, 401)):
            with self.subTest(day=day):
                path = f"/design/{self.project}/{access.design_pass(self.project, phone_row['id'], day)}/design/wireframes/index.html"
                self.assertEqual(self.raw_status(path)[0], expected)
        self.assertIn("Referrer-Policy: no-referrer", self.raw_status(viewer)[1])
        for wrong in (access.design_pass("another-project", phone_row["id"]), f"p-{phone_row['id']}-" + "0" * 32,
                      f"p-{laptop_row['id']}-" + access.design_pass(self.project, phone_row["id"]).rpartition("-")[2],
                      "p-" + "0" * 32, "design"):
            with self.subTest(wrong=wrong):
                self.assertEqual(self.raw_status(f"/design/{self.project}/{wrong}/design/wireframes/index.html")[0], 401)
        self.request("POST", "/api/devices/revoke", {"id": phone_row["id"]}, device=laptop)
        self.assertEqual(self.raw_status(viewer)[0], 401)  # a removed browser's captured link stops working

    def raw_status(self, path, device=None):
        """Status and raw headers of a GET; without a device it carries no cookie, as a sandboxed board's own
        requests do."""
        cookie = f"Cookie: {access.COOKIE}={device}\r\n" if device else ""
        with socket.create_connection(self.httpd.server_address, timeout=10) as sock:
            sock.sendall(f"GET {path} HTTP/1.0\r\nHost: {self.host}\r\n{cookie}\r\n".encode())
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        head = b"".join(chunks).partition(b"\r\n\r\n")[0].decode()
        return int(head.split()[1]), head

    def test_error_details_reach_only_the_cli_and_paired_devices(self):
        task = T.new(self.project, "Corrupt record", "Keep failures visible to the operator.")
        S.status_path(self.project, task["slug"]).write_text("{corrupt}\n")
        path = f"/api/task/{self.project}/{task['slug']}"
        self.assertEqual(self.request("GET", path)[:2], (401, {"error": server.UNPAIRED, "pair": True}))
        self.assertEqual(self.request("POST", "/api/terminal-access", {"enabled": True})[:2],
                         (401, {"error": server.UNPAIRED, "pair": True}))
        device, _ = self.paired()
        status, reply, _ = self.request("GET", path, device=device)
        self.assertEqual(status, 500)
        self.assertIn("corrupt JSON", reply["error"])

        def broken(_handler, _path):
            raise OSError("/home/example/altitude/web/dist/index.html is unreadable")
        self.patch(server.Handler, "_static", new=broken)
        self.assertEqual(self.request("GET", "/")[:2], (500, {"error": server.INTERNAL_ERROR}))
        status, reply, _ = self.request("GET", "/", device=device)
        self.assertEqual(status, 500)
        self.assertIn("/home/example/", reply["error"])

    def test_a_paired_device_makes_a_code_for_another(self):
        phone, _ = self.paired()
        status, reply, _ = self.request("POST", "/api/devices/code", {}, device=phone)
        self.assertEqual((status, reply["minutes"]), (200, access.CODE_MINUTES))
        self.assertEqual(self.pair(reply["code"], headers={"User-Agent": DESKTOP})[0], 200)

    def test_a_new_code_comes_with_the_address_a_qr_code_and_the_certificate_check_never_a_link(self):
        from unittest import mock
        from altitude import qr, tls
        self.patch(config, "TLS", True)
        self.patch(config, "TLS_DIR", self.tmp / "private-tls")
        self.patch(config, "HOST", "127.0.0.1")
        server.tls_init()
        phone, _ = self.paired()
        address = f"https://127.0.0.1:{config.PORT}"
        authority = tls.identity(config.TLS_DIR / "ca.crt")
        certificate = {"name": authority["name"], "sha256": tls.fingerprint_rows(authority["sha256"])}
        status, reply, _ = self.request("POST", "/api/devices/code", {}, device=phone)
        # Only this computer can open a loopback address, so no phone gets a QR code for it.
        self.assertEqual((status, reply["address"], reply["qr"], reply["certificate"]), (200, address, None, certificate))
        self.assertNotIn(reply["code"], json.dumps({k: v for k, v in reply.items() if k != "code"}))
        with mock.patch.object(config, "TLS", False):
            status, reply, _ = self.request("POST", "/api/devices/code", {}, device=phone)
        self.assertEqual((reply["address"], reply["qr"], reply["certificate"]), (None, None, None))
        # The suite may bind only loopback, so loopback stands in for the phone's network address here.
        self.patch(tls, "phone_address", new=lambda _found: None)
        status, reply, _ = self.request("POST", "/api/devices/code", {}, device=phone)
        self.assertEqual(reply["qr"], ["".join("1" if dark else "0" for dark in row) for row in qr.matrix(address)])

    def test_add_a_phone_opens_one_bounded_share_window_for_the_operator_only(self):
        import urllib.error
        import urllib.request
        from unittest import mock
        from altitude import qr, terminal, tls
        self.patch(config, "TLS", True)
        self.patch(config, "TLS_DIR", self.tmp / "private-tls")
        self.patch(config, "HOST", "127.0.0.1")
        server.tls_init()
        self.addCleanup(lambda: server._SHARE and server._SHARE.close())
        self.assertEqual(self.request("POST", "/api/devices/share", {})[0], 401)
        phone, _ = self.paired()
        with mock.patch.object(terminal, "agent_connection", return_value=True):
            status, reply, _ = self.request("POST", "/api/devices/share", {}, device=phone)
        self.assertEqual((status, reply), (403, {"error": server.AGENT_REFUSED}))
        status, reply, _ = self.request("POST", "/api/devices/share", {}, device=phone)
        self.assertEqual(status, 409)
        self.assertIn("configured for 127.0.0.1, which only this computer can open", reply["error"])
        # The suite may bind only loopback, so loopback stands in for the phone's network address here.
        self.patch(tls, "phone_address", new=lambda _found: None)
        status, first, _ = self.request("POST", "/api/devices/share", {}, device=phone)
        self.assertEqual(status, 200, first)
        authority = tls.identity(config.TLS_DIR / "ca.crt")
        self.assertEqual((first["name"], first["sha256"]), (authority["name"], authority["sha256"]))
        self.assertRegex(first["link"], r"^http://127\.0\.0\.1:\d+/$")
        self.assertEqual(first["qr"], ["".join("1" if dark else "0" for dark in row) for row in qr.matrix(first["link"])])
        self.assertTrue(590 <= first["seconds"] <= 600, first["seconds"])
        with urllib.request.urlopen(first["link"] + "ca.crt", timeout=5) as response:
            self.assertEqual(response.read(), (config.TLS_DIR / "ca.crt").read_bytes())
        # Opening again replaces the window; the earlier page closing it cannot close the new one.
        second = self.request("POST", "/api/devices/share", {}, device=phone)[1]
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(first["link"], timeout=5)
        self.assertEqual(self.request("POST", "/api/devices/share-close", {"link": first["link"]}, device=phone)[:2],
                         (200, {"closed": True}))
        with urllib.request.urlopen(second["link"], timeout=5) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.request("POST", "/api/devices/share-close", {}, device=phone)[0], 400)
        self.request("POST", "/api/devices/share-close", {"link": second["link"]}, device=phone)
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(second["link"], timeout=5)
