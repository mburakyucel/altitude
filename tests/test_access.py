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

from tests.support import ALT, AltitudeCase
from tests.test_restart_command import load_script
from altitude import access, config, server, state as S, tasks as T

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
DESKTOP = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def alt_pair() -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runpy.run_path(str(ALT))["main"](["pair"])
    return output.getvalue().split("Pairing code: ")[1].split()[0]


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

    def test_an_unpaired_browser_sees_only_the_page_and_the_way_to_pair(self):
        for method, path in (("GET", "/api/overview"), ("GET", "/api/devices"), ("GET", "/design/x/board.html"),
                             ("POST", "/api/operator-name"), ("POST", "/api/devices/code")):
            with self.subTest(path=path):
                status, reply, _ = self.request(method, path, {} if method == "POST" else None)
                self.assertEqual((status, reply), (401, {"error": "Pair this device to use Altitude.", "pair": True}))
        self.assertEqual(self.request("GET", "/api/access")[:2], (200, {"paired": False, "device": None}))
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
                         (200, {"paired": True, "device": "Home Screen app on iPhone"}))
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
        self.patch(restart, "service_address", new=lambda: self.httpd.server_address)
        self.patch(restart.config, "TLS", False)
        self.assertEqual(json.loads(restart.fetch("/api/overview")), {"state": "ready"})
        self.patch(access, "machine_key", new=lambda: None)
        with self.assertRaises(restart.RestartError):  # refused with 401 over HTTP; HTTPS is not served here
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
