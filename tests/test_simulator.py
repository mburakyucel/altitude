"""A macOS validation run's disposable iOS Simulator iPhone and the relay to its Safari.

`xcrun` is a fixture that keeps private device sets as folders and records every call, and the phone's Web Inspector
is a fixture socket speaking its message format; the relay, the runner's device lifecycle, recovery, records and
evidence delivery are real. Real Simulator runs are recorded evidence from a Mac (docs/DEVELOPMENT.md#ios-simulator-runs).
"""
import json
import os
from pathlib import Path
import plistlib
import socket
import struct
import sys
import threading
import time
from unittest import TestCase, mock

from tests.support import SUITE
from tests import test_validation as base
from altitude import capture as C, config, platform, simulator as sim, state as S, tasks as T, validation
from tests.test_capture import gif

XCRUN = f"#!{sys.executable}\n" + r'''"""xcrun stand-in: xcodebuild's version, its build and test of the walks, and simctl on private device
sets, failing a step on request."""
import json, os, plistlib, shutil, sys, uuid
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_SIMCTL_LOG"], "a") as out:
    out.write(json.dumps(args) + "\n")
if args[:2] == ["xcodebuild", "build-for-testing"]:   # the walks' test run file, unless the build fails on request
    if os.environ.get("FAKE_WALK") == "unbuilt":
        print("Walks.swift:1: error: the fixture does not compile")
        sys.exit(65)
    products = Path(args[args.index("-derivedDataPath") + 1]) / "Build" / "Products"
    products.mkdir(parents=True)
    (products / "Walks_Walks_iphonesimulator27.0-arm64-x86_64.xctestrun").write_text(args[args.index("-project") + 1])
    sys.exit(0)
if args[:2] == ["xcodebuild", "test-without-building"]:   # one walk: its record and screenshot, as the runner writes them
    import signal, time
    option = lambda prefix: next(a for a in args if a.startswith(prefix))[len(prefix):]
    out, mode = Path(os.environ["TEST_RUNNER_WALK_OUT"]), os.environ.get("FAKE_WALK", "")
    (out / "first.png").write_bytes(b"\x89PNG fixture step")
    steps = [{"step": "first", "status": "completed", "screenshot": "first.png",
              "detail": f"{os.environ['TEST_RUNNER_WALK_URL']} {os.environ['TEST_RUNNER_WALK_CODE']}".strip()}]
    (out / "walk.json").write_text(json.dumps({"steps": steps, "seen": {} if mode else {"finished": "yes"}}))
    if option("-only-testing:Walks/Walks/") == "testHomeScreen":   # the web clip iOS keeps in the phone's data
        clip = Path(option("-DVTSimulatorSetLocation=")) / option("platform=iOS Simulator,id=") / "data/Library/WebClips"
        clip = clip / f"{uuid.uuid4().hex}.webclip"
        clip.mkdir(parents=True)
        (clip / "Info.plist").write_bytes(plistlib.dumps({"Title": "Altitude", "URL": "http://127.0.0.1:5555/",
                                                          "FullScreen": True, "IconIsPrecomposed": 1}))
        (clip / "icon.png").write_bytes(b"\x89PNG fixture icon")
    if mode == "crash":
        print("Walks-Runner (4242) encountered an error (Test crashed with signal trap.)")
        sys.exit(65)
    if mode == "hang":   # until interrupted, as Ctrl-C ends a test run
        signal.signal(signal.SIGINT, lambda *_: (Path(os.environ["FAKE_SIMCTL_LOG"] + ".interrupted").touch(), sys.exit(1)))
        while True:
            time.sleep(0.05)
    sys.exit(0)
if args[0] == "xcodebuild":
    print("Xcode 27.0\nBuild version 27A266a")
    sys.exit(0)
args, devices = args[1:], None
if args[0] == "--set":
    devices, args = Path(args[1]), args[2:]
step = "delete" if args[:2] == ["delete", "all"] else "recordVideo" if args[2:3] == ["recordVideo"] else args[0]
if step == os.environ.get("FAKE_SIMCTL_FAIL"):
    if step == "create":
        (devices / "PARTIAL").mkdir()   # the device exists before simctl reports failure
    print(f"{step}: the fixture failed", file=sys.stderr)
    sys.exit(2)
if args[:2] == ["list", "--json"] and "runtimes" in args:
    print(os.environ["FAKE_SIMCTL_LIST"])
elif args[:2] == ["list", "--json"]:
    print(json.dumps({"devices": {"ios": [{"udid": d.name} for d in devices.iterdir() if d.is_dir()]}}))
elif args[0] == "create":
    udid = str(uuid.uuid4()).upper()
    (devices / udid).mkdir()
    (devices / udid / "device.json").write_text(json.dumps(args[1:]))
    print(udid)
elif args[0] == "bootstatus":
    (devices / args[1] / "booted").touch()
elif args[0] == "getenv":
    print(os.environ["FAKE_INSPECTOR"])
elif args[0] == "keychain":   # add-root-cert: the phone trusts the certificate, kept beside the log
    with open(os.environ["FAKE_SIMCTL_LOG"] + ".roots", "a") as roots:
        roots.write(Path(args[3]).read_text())
elif args[0] == "appinfo":
    print('{\n    CFBundleIdentifier = "com.apple.mobilesafari";\n    Path = "%s";\n}' % os.environ["FAKE_SAFARI"])
elif step == "recordVideo":   # records until interrupted, then finishes its file, unless it hangs on request
    import signal, time
    signal.signal(signal.SIGINT, signal.SIG_IGN if os.environ.get("FAKE_RECORD_HANG") else
                  lambda *_: (Path(args[-1]).write_bytes(b"fixture video"), sys.exit(0)))
    print("Recording started", file=sys.stderr, flush=True)
    while True:
        time.sleep(0.05)
elif args[0] == "io":
    Path(args[3]).write_bytes(b"\x89PNG fixture screen")
elif args[:2] == ["shutdown", "all"]:
    for marker in devices.glob("*/booted"):
        marker.unlink()
elif step == "delete":
    for device in devices.iterdir():
        if device.is_dir():
            shutil.rmtree(device)
'''

LISTING = {
    "runtimes": [
        {"platform": "iOS", "isAvailable": True, "version": "26.0", "name": "iOS 26.0", "buildversion": "23A343",
         "identifier": "runtime.iOS-26-0", "supportedDeviceTypes": [
             {"identifier": "type.iPhone-16", "name": "iPhone 16", "productFamily": "iPhone"}]},
        {"platform": "iOS", "isAvailable": True, "version": "27.0", "name": "iOS 27.0", "buildversion": "24A434",
         "identifier": "runtime.iOS-27-0", "supportedDeviceTypes": [
             {"identifier": "type.iPhone-16", "name": "iPhone 16", "productFamily": "iPhone"},
             {"identifier": "type.iPhone-17-Pro-Max", "name": "iPhone 17 Pro Max", "productFamily": "iPhone"},
             {"identifier": "type.iPhone-17", "name": "iPhone 17", "productFamily": "iPhone"},
             {"identifier": "type.iPad", "name": "iPad Pro", "productFamily": "iPad"}]},
        {"platform": "iOS", "isAvailable": False, "version": "28.0", "name": "iOS 28.0", "buildversion": "25A1",
         "identifier": "runtime.iOS-28-0", "supportedDeviceTypes": []},
        {"platform": "watchOS", "isAvailable": True, "version": "30.0", "name": "watchOS 30.0", "buildversion": "26A1",
         "identifier": "runtime.watchOS-30-0", "supportedDeviceTypes": []}],
    "devicetypes": [
        {"identifier": "type.iPhone-16", "name": "iPhone 16", "productFamily": "iPhone", "minRuntimeVersion": 1179648},
        {"identifier": "type.iPhone-17", "name": "iPhone 17", "productFamily": "iPhone", "minRuntimeVersion": 1703936},
        {"identifier": "type.iPhone-17-Pro-Max", "name": "iPhone 17 Pro Max", "productFamily": "iPhone",
         "minRuntimeVersion": 1703936},
        {"identifier": "type.iPad", "name": "iPad Pro", "productFamily": "iPad", "minRuntimeVersion": 1769472}]}

SAFARI_APP, OTHER_APP = "PID:1", "PID:2"


class FakeInspector:
    """The phone's Web Inspector: Safari with a page on a fixture port, one on Altitude's port and a script context,
    and another inspectable process. It records every request it receives."""

    def __init__(self, path: Path, port: int):
        self.port, self.received, self.connections = port, [], []
        self.pages = {"1": ("WIRTypeWebPage", "http://127.0.0.1:5555/"),
                      "2": ("WIRTypeWebPage", f"http://localhost:{port}/"),
                      "3": ("WIRTypeJavaScript", "http://127.0.0.1:5555/worker.js")}
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(path))
        self.listener.listen()
        self.listener.settimeout(0.05)
        self.stopped, self.threads = threading.Event(), []
        self.accepting = threading.Thread(target=self._accept, daemon=True)
        self.accepting.start()

    def close(self):
        """Every thread ends before the sockets it uses close: Linux wakes a blocked call only on shutdown, and a
        descriptor closed under a thread can be reused by a later test's sockets."""
        self.stopped.set()
        self.accepting.join()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in self.threads:
            thread.join()
        for sock in (self.listener, *self.connections):
            sock.close()

    def _accept(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            self.connections.append(connection)
            self.threads.append(threading.Thread(target=self._serve, args=(connection,), daemon=True))
            self.threads[-1].start()

    def listing(self, connection):
        sim._write(connection, "_rpc_applicationSentListing:", {"WIRApplicationIdentifierKey": SAFARI_APP, "WIRListingKey": {
            key: {"WIRPageIdentifierKey": int(key), "WIRTypeKey": kind, "WIRURLKey": url}
            for key, (kind, url) in self.pages.items()}})

    def navigate(self, page, url):
        self.pages[page] = ("WIRTypeWebPage", url)
        for connection in self.connections:
            try:
                self.listing(connection)
            except OSError:
                pass

    def _serve(self, connection):
        try:
            while (message := sim._read(connection)) is not None:
                selector, argument = message["__selector"], message["__argument"]
                self.received.append((selector, argument))
                if selector == "_rpc_getConnectedApplications:":
                    sim._write(connection, "_rpc_reportConnectedApplicationList:", {"WIRApplicationDictionaryKey": {
                        SAFARI_APP: {"WIRApplicationIdentifierKey": SAFARI_APP,
                                     "WIRApplicationBundleIdentifierKey": sim.SAFARI},
                        OTHER_APP: {"WIRApplicationIdentifierKey": OTHER_APP,
                                    "WIRApplicationBundleIdentifierKey": "com.apple.amsengagementd"}}})
                    sim._write(connection, "_rpc_applicationConnected:", {
                        "WIRApplicationIdentifierKey": "PID:3", "WIRApplicationBundleIdentifierKey": "com.apple.Preferences"})
                elif selector == "_rpc_forwardGetListing:":
                    self.listing(connection)
                elif selector == "_rpc_forwardSocketSetup:":
                    for destination in ("someone-else", argument["WIRSenderKey"]):
                        sim._write(connection, "_rpc_applicationSentData:", {
                            "WIRApplicationIdentifierKey": SAFARI_APP, "WIRDestinationKey": destination,
                            "WIRMessageDataKey": json.dumps({"method": "Target.targetCreated", "params": {
                                "targetInfo": {"targetId": f"page-{destination}"}}}).encode()})
                elif selector == "_rpc_forwardSocketData:":
                    outer = json.loads(argument["WIRSocketDataKey"])
                    inner = json.loads(outer["params"]["message"])
                    sim._write(connection, "_rpc_applicationSentData:", {
                        "WIRApplicationIdentifierKey": SAFARI_APP, "WIRDestinationKey": argument["WIRSenderKey"],
                        "WIRMessageDataKey": json.dumps({"method": "Target.dispatchMessageFromTarget", "params": {
                            "message": json.dumps({"id": inner["id"], "result": {"method": inner["method"]}})}}).encode()})
        except (OSError, ValueError):
            pass


class Client:
    """The run's side of the relay socket."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        try:
            self.sock.connect(str(path))
        except OSError:
            self.sock.close()
            raise
        self.sock.settimeout(5)

    def send(self, selector, argument=None):
        sim._write(self.sock, selector, {"WIRConnectionIdentifierKey": "run", **(argument or {})})

    def read(self):
        message = sim._read(self.sock)
        return message and (message["__selector"], message["__argument"])

    def until(self, selector):
        while (message := self.read()) is not None:
            if message[0] == selector:
                return message[1]
        raise AssertionError(f"the relay closed before {selector}")

    def closed(self):
        """Whether the relay closes this connection, reading past anything already sent."""
        try:
            while self.read() is not None:
                pass
            return True
        except TimeoutError:
            return False


class RelayCase(TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="relay-", dir=SUITE))
        self.port = 18890
        self.phone = FakeInspector(self.tmp / "phone.sock", self.port)
        self.addCleanup(self.phone.close)
        self.opened, self.walked = [], []

        def walk(name, url, code):
            self.walked.append((name, url, code))
            if url.endswith("/fails"):
                raise RuntimeError("the walks did not build: fixture")
            return {"steps": [{"step": "first", "status": "completed"}]}, {"first.png": b"\x89PNG step"}
        self.relay = sim.Relay(self.tmp / "run.sock", str(self.tmp / "phone.sock"), self.port, self.opened.append, walk)
        self.addCleanup(self.relay.close)

    def client(self):
        client = Client(self.tmp / "run.sock")
        self.addCleanup(client.sock.close)
        client.send("_rpc_reportIdentifier:")
        client.send("_rpc_getConnectedApplications:")
        apps = client.until("_rpc_reportConnectedApplicationList:")["WIRApplicationDictionaryKey"]
        self.assertEqual(set(apps), {SAFARI_APP}, "only Safari is listed")
        return client

    def received(self):
        return [selector for selector, _ in self.phone.received]

    def attach(self, client, page=1):
        client.send("_rpc_forwardGetListing:", {"WIRApplicationIdentifierKey": SAFARI_APP})
        listing = client.until("_rpc_applicationSentListing:")["WIRListingKey"]
        client.send("_rpc_forwardSocketSetup:", {"WIRApplicationIdentifierKey": SAFARI_APP, "WIRPageIdentifierKey": page,
                                                 "WIRSenderKey": "sender"})
        return listing


class TestRelay(RelayCase):
    def test_the_run_sees_only_safaris_web_pages_off_altitudes_port(self):
        client = self.client()
        listing = self.attach(client)
        self.assertEqual(set(listing), {"1"}, "Altitude's page and the script context are hidden")
        data = client.until("_rpc_applicationSentData:")
        self.assertEqual(data["WIRDestinationKey"], "sender", "data for another inspector's sender is hidden")
        client.send("_rpc_forwardSocketData:", {"WIRApplicationIdentifierKey": SAFARI_APP, "WIRPageIdentifierKey": 1,
                                                "WIRSenderKey": "sender", "WIRSocketDataKey": json.dumps({
                                                    "id": 1, "method": "Target.sendMessageToTarget", "params": {
                                                        "targetId": "page-sender", "message": json.dumps(
                                                            {"id": 7, "method": "Runtime.evaluate"})}}).encode()})
        reply = json.loads(client.until("_rpc_applicationSentData:")["WIRMessageDataKey"])
        self.assertEqual(json.loads(reply["params"]["message"]), {"id": 7, "result": {"method": "Runtime.evaluate"}})

    def test_requests_beyond_safaris_pages_close_the_connection_without_reaching_the_phone(self):
        refusals = {
            "another process": ("_rpc_forwardGetListing:", {"WIRApplicationIdentifierKey": OTHER_APP}),
            "Altitude's page": ("_rpc_forwardSocketSetup:", {"WIRApplicationIdentifierKey": SAFARI_APP,
                                                             "WIRPageIdentifierKey": 2, "WIRSenderKey": "s"}),
            "a script context": ("_rpc_forwardSocketSetup:", {"WIRApplicationIdentifierKey": SAFARI_APP,
                                                              "WIRPageIdentifierKey": 3, "WIRSenderKey": "s"}),
            "a sender never set up": ("_rpc_forwardSocketData:", {"WIRApplicationIdentifierKey": SAFARI_APP,
                                                                  "WIRPageIdentifierKey": 1, "WIRSenderKey": "s",
                                                                  "WIRSocketDataKey": b"{}"}),
            "an automation session": ("_rpc_forwardAutomationSessionRequest:", {
                "WIRApplicationIdentifierKey": SAFARI_APP, "WIRSessionIdentifierKey": "s"}),
        }
        for name, (selector, argument) in refusals.items():
            with self.subTest(name):
                client = self.client()
                client.send("_rpc_forwardGetListing:", {"WIRApplicationIdentifierKey": SAFARI_APP})
                client.until("_rpc_applicationSentListing:")
                before = len(self.phone.received)
                client.send(selector, argument)
                self.assertTrue(client.closed())
                self.assertEqual(self.phone.received[before:], [])

    def test_a_page_that_moves_to_altitudes_port_closes_the_connection_inspecting_it(self):
        client = self.client()
        self.attach(client)
        client.until("_rpc_applicationSentData:")
        self.phone.navigate("1", f"http://127.0.0.1:{self.port}/api/overview")
        self.assertTrue(client.closed())

    def test_safari_opens_only_loopback_http_addresses_off_altitudes_port(self):
        client = self.client()
        for url, error in (("http://127.0.0.1:5555/", ""), ("http://localhost:5555/a", ""),
                           (f"http://127.0.0.1:{self.port}/", "never opens Altitude's own port"),
                           ("file:///etc/hosts", "only http(s)"), ("http://192.0.2.1:5555/", "only http(s)"),
                           ("http://127.0.0.1/", "only http(s)"), ("http://user@127.0.0.1:5555/", "only http(s)"),
                           ("tel:123", "only http(s)"), (7, "only http(s)")):
            with self.subTest(url):
                client.send(sim.OPEN, {"url": url})
                self.assertIn(error, client.until(sim.OPENED)["error"])
        self.assertEqual(self.opened, ["http://127.0.0.1:5555/", "http://localhost:5555/a"])

    def test_a_run_asks_only_for_the_two_walks_at_loopback_addresses_off_altitudes_port(self):
        client = self.client()
        for argument, error in (
                ({"walk": "home-screen", "url": "http://127.0.0.1:5555/", "code": "ABCD-2345"}, ""),
                ({"walk": "profile", "url": "http://127.0.0.1:5555/#ios"}, ""),
                ({"walk": "settings", "url": "http://127.0.0.1:5555/"}, "walks only home-screen and profile"),
                ({"url": "http://127.0.0.1:5555/"}, "walks only home-screen and profile"),
                ({"walk": ["profile"], "url": "http://127.0.0.1:5555/"}, "walks only home-screen and profile"),
                ({"walk": "home-screen", "url": f"http://127.0.0.1:{self.port}/"}, "never opens Altitude's own port"),
                ({"walk": "profile", "url": "https://192.0.2.1:5555/"}, "only http(s)"),
                ({"walk": "profile", "url": "http://127.0.0.1:5555/", "code": "ABCD-2345"}, "only a pairing code"),
                ({"walk": "home-screen", "url": "http://127.0.0.1:5555/", "code": "abcd-2345"}, "only a pairing code"),
                ({"walk": "home-screen", "url": "http://127.0.0.1:5555/", "code": "ABCD-2345\n"}, "only a pairing code"),
                ({"walk": "home-screen", "url": "http://127.0.0.1:5555/", "code": 7}, "only a pairing code"),
                ({"walk": "profile", "url": "http://127.0.0.1:5555/fails"}, "the walks did not build: fixture")):
            with self.subTest(argument):
                client.send(sim.WALK, argument)
                answer = client.until(sim.WALKED)
                self.assertIn(error, answer["error"])
                if not error:
                    self.assertEqual(json.loads(answer["walk"]), {"steps": [{"step": "first", "status": "completed"}]})
                    self.assertEqual(answer["files"], {"first.png": b"\x89PNG step"})
                else:
                    self.assertNotIn("walk", answer)
        self.assertEqual(self.walked, [("home-screen", "http://127.0.0.1:5555/", "ABCD-2345"),
                                       ("profile", "http://127.0.0.1:5555/#ios", ""),
                                       ("profile", "http://127.0.0.1:5555/fails", "")])
        self.assertNotIn("_rpc_altitudeWalk:", self.received(), "the phone never sees the request")

    def test_replies_to_the_run_stay_whole_while_the_phone_sends_large_messages(self):
        client = self.client()
        self.attach(client)
        client.until("_rpc_applicationSentData:")
        blob = {"WIRApplicationIdentifierKey": SAFARI_APP, "WIRDestinationKey": "sender", "WIRMessageDataKey": b"x" * (1 << 20)}
        flood = threading.Thread(target=lambda: [sim._write(self.phone.connections[-1], "_rpc_applicationSentData:", blob)
                                                 for _ in range(20)])
        write = sim._write

        def halves(sock, selector, argument):  # a send the kernel takes in two parts, as sendall may
            data = plistlib.dumps({"__selector": selector, "__argument": argument}, fmt=plistlib.FMT_BINARY)
            frame = struct.pack(">I", len(data)) + data
            sock.sendall(frame[:len(frame) // 2])
            time.sleep(0.001)
            sock.sendall(frame[len(frame) // 2:])

        with mock.patch.object(sim, "_write", halves):
            opens = threading.Thread(target=lambda: [client.send(sim.OPEN, {"url": "http://127.0.0.1:5555/"})
                                                     for _ in range(50)])
            flood.start()
            opens.start()
            counts = {sim.OPENED: 0, "_rpc_applicationSentData:": 0}
            while counts != {sim.OPENED: 50, "_rpc_applicationSentData:": 20}:
                selector, _ = client.read()
                counts[selector] += 1
            flood.join()
            opens.join()
        self.assertIs(sim._write, write)

    def test_a_run_holds_a_bounded_number_of_connections_and_ended_ones_free_their_place(self):
        clients = [self.client() for _ in range(sim.SESSIONS)]
        self.assertTrue(Client(self.tmp / "run.sock").closed(), "a connection over the limit is closed at once")
        clients[0].sock.close()
        for _ in range(100):
            if len(self.relay.sessions) < sim.SESSIONS:
                break
            time.sleep(0.05)
        self.client()

    def test_a_message_that_stalls_partway_closes_its_connection(self):
        for name, partial in (("in its length", struct.pack(">I", 100)[:2]),
                              ("in its body", struct.pack(">I", 100) + b"partial")):
            with self.subTest(name), mock.patch.object(sim, "FRAME_SECONDS", 0.2):
                client = self.client()
                client.sock.sendall(partial)
                self.assertTrue(client.closed())

    def test_a_run_that_leaves_while_the_phone_sends_frees_its_place_at_once(self):
        waiting, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        class Held(sim.selectors.DefaultSelector):
            def select(self, timeout=None):
                waiting.set()
                release.wait()
                return super().select(timeout)

        client = self.client()
        with mock.patch.object(sim, "FRAME_SECONDS", 5), mock.patch.object(sim.selectors, "DefaultSelector", Held):
            self.phone.navigate("1", "http://127.0.0.1:5555/next")
            self.assertTrue(waiting.wait(5), "the relay waits for the rest of the phone's message")
            client.sock.close()
            for thread in self.phone.threads:
                thread.join(5)  # the phone sees the relay end its side
            release.set()
            deadline = time.monotonic() + 2  # well inside the 5 s a lost wakeup would wait
            while self.relay.sessions and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.relay.sessions, [], "the waiting side wakes as the connection ends")

    def test_a_malformed_request_closes_its_connection_and_frees_its_place(self):
        for _ in range(sim.SESSIONS + 1):
            client = self.client()
            client.send("_rpc_forwardGetListing:", {"WIRApplicationIdentifierKey": []})
            self.assertTrue(client.closed())
            for _ in range(100):
                if not self.relay.sessions:
                    break
                time.sleep(0.05)
        self.assertEqual(self.relay.sessions, [])
        self.client()

    def test_closing_the_relay_ends_its_connections_and_frees_the_socket(self):
        client = self.client()
        self.relay.close()
        self.assertTrue(client.closed())
        with self.assertRaises(OSError):
            Client(self.tmp / "run.sock")


class TestPlan(TestCase):
    def test_the_newest_runtime_and_its_newest_base_iphone_are_chosen(self):
        with mock.patch.object(sim, "_simctl", return_value=json.dumps(LISTING)), \
                mock.patch.object(sim.platform, "validation_in_container", return_value=False), \
                mock.patch.object(sim.subprocess, "run", return_value=mock.Mock(stdout="Xcode 27.0\nBuild version 27A266a\n")):
            self.assertEqual(sim.plan(), {"xcode": "Xcode 27.0 Build version 27A266a", "runtime": "iOS 27.0 (24A434)",
                                          "runtime_id": "runtime.iOS-27-0", "device": "iPhone 17",
                                          "device_id": "type.iPhone-17"})


class TestSimulatorRuns(base.MacRunnerCase):
    """`alt task validate --simulator` on the Mac runner, with the xcrun and Web Inspector fixtures."""

    def setUp(self):
        super().setUp()
        xcrun = self.bin_dir / "xcrun"
        xcrun.write_text(XCRUN)
        xcrun.chmod(0o755)
        self.patch(sim, "XCRUN", str(xcrun))
        self.log = self.tmp / "simctl.jsonl"
        self.setenv("FAKE_SIMCTL_LOG", str(self.log))
        self.setenv("FAKE_SIMCTL_LIST", json.dumps(LISTING))
        self.setenv("FAKE_SIMCTL_FAIL", None)
        self.setenv("FAKE_WALK", None)
        safari = self.tmp / "MobileSafari.app"
        safari.mkdir()
        (safari / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "27.0",
                                                            "CFBundleVersion": "8624.1.17"}))
        self.setenv("FAKE_SAFARI", str(safari))
        self.phone = FakeInspector(self.tmp / "phone.sock", config.PORT)
        self.addCleanup(self.phone.close)
        self.setenv("FAKE_INSPECTOR", str(self.tmp / "phone.sock"))
        self.client = self.tmp / "client.py"
        self.client.write_text(r'''import json, os, socket, sys
sys.path.insert(0, sys.argv[1])
from altitude import simulator as sim
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["SIMULATOR_INSPECTOR"])
sim._write(sock, "_rpc_getConnectedApplications:", {})
apps = sim._read(sock)["__argument"]["WIRApplicationDictionaryKey"]
sim._write(sock, sim.OPEN, {"url": "http://127.0.0.1:5555/"})
opened = sim._read(sock)["__argument"]
https = os.environ["SIMULATOR_HTTPS"]
json.dump({"apps": sorted(apps), "opened": opened, "socket": os.environ["SIMULATOR_INSPECTOR"], "https": https,
           "files": sorted(os.listdir(https)), "ca": open(os.path.join(https, "ca.crt")).read()},
          open(os.path.join(os.environ["VALIDATION_RESULTS"], "relay.json"), "w"))
''')

        self.walker = self.tmp / "walker.py"
        self.walker.write_text(r'''import json, os, socket, sys
sys.path.insert(0, sys.argv[1])
from altitude import simulator as sim
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["SIMULATOR_INSPECTOR"])
answers = []
for request in json.loads(sys.argv[2]):
    sim._write(sock, sim.WALK, request)
    while (message := sim._read(sock))["__selector"] != sim.WALKED:
        pass
    answer = message["__argument"]
    answers.append({"error": answer["error"], "walk": answer.get("walk") and json.loads(answer["walk"]),
                    "files": {name: data.decode("latin-1") for name, data in answer.get("files", {}).items()}})
json.dump(answers, open(os.path.join(os.environ["VALIDATION_RESULTS"], "walks.json"), "w"))
''')

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def walk(self, *requests):
        result = self.validate([sys.executable, str(self.walker), str(Path(__file__).resolve().parents[1]),
                                json.dumps(requests)], simulator=True)
        self.assertEqual((result["exit"], result["ended"], result["cleanup"]), (0, "exit", None), result["output"])
        return result, json.loads((Path(result["results"]) / "walks.json").read_text())

    def drive(self, **options):
        return self.validate([sys.executable, str(self.client), str(Path(__file__).resolve().parents[1])],
                             simulator=True, **options)

    def runs(self):
        runs = validation.home() / "runs"
        return list(runs.iterdir()) if runs.exists() else []

    def test_a_run_drives_its_own_iphones_safari_through_the_relay_and_leaves_no_device(self):
        result = self.drive()
        self.assertEqual((result["exit"], result["ended"], result["cleanup"]), (0, "exit", None), result["output"])
        relay = json.loads((Path(result["results"]) / "relay.json").read_text())
        [area_name] = {Path(relay["socket"]).parent.name[3:]}
        self.assertEqual(relay["socket"], str(self.tmp / f"av-{area_name}" / "simulator.sock"),
                         "the relay's socket is in the run's own temporary folder")
        self.assertEqual((relay["apps"], relay["opened"]), ([SAFARI_APP], {"error": ""}))
        self.assertEqual(relay["https"], str(self.tmp / f"av-{area_name}" / "https"),
                         "the HTTPS identity is in the run's own temporary folder")
        self.assertEqual(relay["files"], ["ca.crt", "server.crt", "server.key"], "the CA's key is gone before the run")
        self.assertEqual(Path(f"{self.log}.roots").read_text(), relay["ca"], "the phone trusts the run's CA")
        certificates = result["simulator"].pop("certificates")
        self.assertEqual((certificates["ca"]["subject"], certificates["server"]["issuer"]),
                         ("CN=Altitude local CA", "CN=Altitude local CA"))
        self.assertIn("IP Address:127.0.0.1", certificates["server"]["extensions"]["Subject Alternative Name"])
        devices = str(validation.home() / "runs" / area_name / "simulator")
        simctl = [call[1:] for call in self.calls() if call[0] == "simctl"]
        self.assertEqual(simctl[0], ["list", "--json", "runtimes", "devicetypes"])
        self.assertTrue(all(call[:2] == ["--set", devices] for call in simctl[1:]), "only the run's own device set")
        steps = [call[2] for call in simctl[1:]]
        self.assertEqual(steps, ["create", "bootstatus", "getenv", "keychain", "appinfo", "openurl", "io", "shutdown",
                                 "delete", "list"])
        self.assertEqual(simctl[4][4:6], ["add-root-cert", str(Path(relay["https"]) / "ca.crt")])
        self.assertEqual(simctl[1][3:], ["altitude-validation", "type.iPhone-17", "runtime.iOS-27-0"])
        self.assertEqual(simctl[6][-1], "http://127.0.0.1:5555/")
        expected = {"xcode": "Xcode 27.0 Build version 27A266a", "runtime": "iOS 27.0 (24A434)", "device": "iPhone 17",
                    "safari": "27.0 (8624.1.17)",
                    "screenshot": str(S.task_dir(self.project, self.slug) / "validation" / "1.simulator.png")}
        self.assertEqual(result["simulator"], expected)
        self.assertEqual(self.rows()[0]["simulator"], {**expected, "certificates": certificates})
        self.assertEqual(Path(expected["screenshot"]).read_bytes(), b"\x89PNG fixture screen")
        self.assertEqual(self.runs(), [], "the device set and run area are gone")
        self.assertTrue(validation._ready.is_set())
        self.assertEqual(self.profile.read_text(), platform.validation_profile(
            tuple(validation.candidate_dirs(validation.home() / "runs" / area_name)),
            validation.home() / "runs" / area_name / f"{result['unit']}.log", config.PORT),
            "the run's profile is the one every macOS run has")

    def test_a_run_walks_its_own_phone_with_the_walks_altd_builds_from_its_own_code(self):
        home = {"walk": "home-screen", "url": "http://127.0.0.1:5555/projects/atlas", "code": "ABCD-2345"}
        result, answers = self.walk(home, {"walk": "profile", "url": "http://127.0.0.1:5555/#ios"}, home)
        self.assertEqual([answer["error"] for answer in answers], ["", "", ""])
        first, profile, again = (answer["walk"] for answer in answers)
        self.assertEqual(first["steps"], [{"step": "first", "status": "completed", "screenshot": "first.png",
                                           "detail": "http://127.0.0.1:5555/projects/atlas ABCD-2345"}])
        self.assertEqual(profile["steps"][0]["detail"], "http://127.0.0.1:5555/#ios")
        self.assertEqual(first["clip"], {"Title": "Altitude", "URL": "http://127.0.0.1:5555/", "FullScreen": True})
        self.assertEqual(again["clip"], first["clip"], "each Home Screen walk reads the clip it made")
        self.assertNotIn("clip", profile)
        self.assertEqual(answers[0]["files"], {"first.png": "\x89PNG fixture step", "web-clip-icon.png": "\x89PNG fixture icon"})
        self.assertEqual(answers[1]["files"], {"first.png": "\x89PNG fixture step"})
        area = validation.home() / "runs" / result["unit"][len(validation.UNIT_PREFIX):-len(".service")]
        builds = [call for call in self.calls() if call[:2] == ["xcodebuild", "build-for-testing"]]
        self.assertEqual(len(builds), 1, "the walks are built once per run")
        self.assertEqual(builds[0][builds[0].index("-project") + 1], str(area / "walks" / "source" / "Walks.xcodeproj"),
                         "built from altd's own copy in the run's private folder, never the candidate's clone")
        self.assertEqual(builds[0][builds[0].index("-derivedDataPath") + 1], str(area / "walks" / "build"))
        tests = [call for call in self.calls() if call[:2] == ["xcodebuild", "test-without-building"]]
        [udid] = {call[call.index("-destination") + 1].split("id=")[1] for call in tests}
        for call, name in zip(tests, ("testHomeScreen", "testProfile", "testHomeScreen")):
            self.assertIn(f"-only-testing:Walks/Walks/{name}", call)
            self.assertIn(f"-DVTSimulatorSetLocation={area / 'simulator'}", call, "only the run's own device set")
            self.assertTrue(call[call.index("-xctestrun") + 1].startswith(str(area / "walks" / "build")))
        simctl = [call[3:] for call in self.calls() if call[:3] == ["simctl", "--set", str(area / "simulator")]]
        closed = [call[2] for call in simctl if call[0] == "terminate"]
        self.assertEqual(closed, list(sim.APPS) * 3, "every walk closes the apps it used")
        self.assertTrue(all(call[1] == udid for call in simctl if call[0] == "terminate"))
        self.assertEqual(self.runs(), [], "the walks' build and evidence go with the run area")

    def test_a_walk_whose_runner_crashes_hangs_or_does_not_build_is_a_recorded_failure(self):
        request = {"walk": "profile", "url": "http://127.0.0.1:5555/#ios"}
        self.patch(sim, "WALK_SECONDS", 1)
        for mode, error in (("crash", "xcodebuild exit 65: Walks-Runner (4242) encountered an error (Test crashed "
                                      "with signal trap.)"),
                            ("hang", "xcodebuild did not finish within 1 s")):
            with self.subTest(mode):
                self.setenv("FAKE_WALK", mode)
                _, [answer] = self.walk(request)
                self.assertEqual(answer["error"], "", "the walk answers with its record")
                self.assertEqual(answer["walk"]["error"], error)
                self.assertEqual([step["step"] for step in answer["walk"]["steps"]], ["first"],
                                 "the steps it reached stay")
        self.assertTrue(Path(f"{self.log}.interrupted").exists(), "a walk past its time is interrupted, as Ctrl-C does")
        self.setenv("FAKE_WALK", "unbuilt")
        self.log.unlink()
        _, [answer] = self.walk(request)
        self.assertEqual(answer, {"error": "the walks did not build: xcodebuild exit 65: Walks.swift:1: error: the "
                                           "fixture does not compile", "walk": None, "files": {}})
        self.assertNotIn("test-without-building", [step for call in self.calls() for step in call])
        self.assertEqual(self.runs(), [])

    def converting(self, failure: str | None = None):
        """The ffmpeg capability at its seam: the recording becomes a fixture GIF, or fails with `failure`."""
        made = []

        def video(source, target, width):
            made.append((source.read_bytes(), width))
            if failure:
                raise C.CaptureError(failure)
            target.write_bytes(gif())
            return C.describe(gif())
        return self.patch(validation.C, "video", side_effect=video), made

    def test_a_capture_records_the_phone_while_the_command_runs_and_is_kept_beside_its_screenshot(self):
        _, made = self.converting()
        result = self.drive(capture=True)
        self.assertEqual((result["exit"], result["ended"], result["cleanup"]), (0, "exit", None), result["output"])
        steps = [call[3] if call[3] != "io" else call[5] for call in self.calls()[1:] if call[0] == "simctl"]
        self.assertEqual(steps[:8], ["create", "bootstatus", "getenv", "keychain", "appinfo", "recordVideo", "openurl",
                                     "screenshot"], "recording starts before the command and ends before the screenshot")
        [record] = [call for call in self.calls() if call[5:6] == ["recordVideo"]]
        self.assertEqual(record[6:8], ["--codec=h264", "--force"])
        self.assertEqual(made, [(b"fixture video", C.PHONE)])
        evidence = S.task_dir(self.project, self.slug) / "validation"
        self.assertEqual(result["simulator"]["capture"], str(evidence / "1.simulator.gif"))
        self.assertEqual(self.rows()[0]["simulator"]["capture"], str(evidence / "1.simulator.gif"))
        self.assertEqual((evidence / "1.simulator.gif").read_bytes(), gif())
        self.assertEqual(sorted(p.name for p in evidence.iterdir() if p.is_file()),
                         ["1.log", "1.simulator.gif", "1.simulator.png"], "the recording itself is not kept")
        self.assertEqual(self.runs(), [])

    def test_a_capture_that_fails_never_changes_the_run_or_its_other_evidence(self):
        for name, setup, why in (
                ("recording", lambda: self.setenv("FAKE_SIMCTL_FAIL", "recordVideo"),
                 "simctl recordVideo failed: recordVideo: the fixture failed"),
                ("ffmpeg", lambda: self.converting(C.UNAVAILABLE), C.UNAVAILABLE),
                ("stop", lambda: (self.setenv("FAKE_RECORD_HANG", "1"), self.patch(sim, "RECORD_STOP", 0.3)),
                 "the screen recording did not stop within 0.3 s"),
                ("start", lambda: self.patch(sim.Phone, "record", side_effect=OSError("no recorder")),  # stays patched
                 "the screen recording did not start: no recorder")):
            with self.subTest(name):
                self.converting()
                setup()
                result = self.drive(capture=True)
                self.assertEqual((result["exit"], result["ended"], result["cleanup"]), (0, "exit", None))
                self.assertEqual(result["simulator"]["capture"], f"none: {why}")
                self.assertTrue(Path(result["simulator"]["screenshot"]).is_file())
                self.assertEqual(self.runs(), [], "a recording that would not stop is ended with the run")
                self.setenv("FAKE_SIMCTL_FAIL", None)
                self.setenv("FAKE_RECORD_HANG", None)

    def test_without_capture_nothing_records_and_capture_needs_the_simulator(self):
        result = self.drive()
        self.assertNotIn("capture", result["simulator"])
        self.assertNotIn("recordVideo", [step for call in self.calls() for step in call])
        self.assertIn("needs --simulator", self.validate(["true"], capture=True, status=400)["error"])
        self.assertIn("are on or off", self.validate(["true"], simulator=True, capture="yes", status=400)["error"])

    def test_cli_door(self):
        self.serving(self.httpd.server_address[1])
        owner = {"ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
                 "ALTITUDE_ATTEMPT": "1"}
        result = self.alt("task", "validate", "--simulator", "--", sys.executable, str(self.client),
                          str(Path(__file__).resolve().parents[1]), env=owner)
        self.assertEqual(result.returncode, 0, result.stderr)
        screenshot = S.task_dir(self.project, self.slug) / "validation" / "1.simulator.png"
        self.assertIn(f"; iPhone 17, iOS 27.0 (24A434), 27.0 (8624.1.17), Xcode 27.0 Build version 27A266a, "
                      f"{screenshot}\n", result.stdout)

    def test_hosts_without_a_simulator_refuse_before_a_run_exists(self):
        for name, change, why in (
                ("Linux", mock.patch.object(platform, "validation_in_container", return_value=True), ""),
                ("no iOS runtime", mock.patch.dict(os.environ, {"FAKE_SIMCTL_LIST": json.dumps(
                    {"runtimes": LISTING["runtimes"][2:], "devicetypes": []})}), "no iOS runtime is installed"),
                ("no Xcode", mock.patch.object(sim, "XCRUN", str(self.tmp / "absent")), "No such file")):
            with self.subTest(name), change:
                error = self.drive(status=400)["error"]
                self.assertIn(sim.UNAVAILABLE, error)
                self.assertIn(why, error)
        self.assertIn("are on or off", self.validate(["true"], simulator="yes", status=400)["error"])
        self.assertFalse((S.task_dir(self.project, self.slug) / "machine.jsonl").exists())
        self.assertEqual(self.runs(), [])

    def test_a_phone_that_fails_to_be_created_boot_or_trust_its_ca_is_removed_and_no_run_starts(self):
        for step in ("create", "bootstatus", "keychain"):
            with self.subTest(step):
                self.setenv("FAKE_SIMCTL_FAIL", step)
                error = self.drive(status=400)["error"]
                self.assertIn(f"the iOS Simulator iPhone did not start: simctl {step} failed: {step}: the fixture "
                              "failed", error)
                self.assertFalse((S.task_dir(self.project, self.slug) / "machine.jsonl").exists())
                self.assertEqual([call[3:] for call in self.calls()[-3:]],
                                 [["shutdown", "all"], ["delete", "all"], ["list", "--json", "devices"]])
                self.assertEqual(self.runs(), [], "the partly made phone and the area are gone")
                self.assertTrue(validation._ready.is_set())

    def test_a_turned_off_run_still_removes_its_phone(self):
        boot = sim.Phone.boot
        with mock.patch.object(sim.Phone, "boot", side_effect=lambda phone: (validation.stop_all(), boot(phone))[1],
                               autospec=True):
            result = self.drive()
        self.assertEqual(result["ended"], "turned off")
        self.assertIn(["shutdown", "all"], [call[3:] for call in self.calls()])
        self.assertEqual(self.runs(), [])

    def test_a_run_stopped_while_its_clone_is_made_boots_no_phone(self):
        clone = validation._clone
        with mock.patch.object(validation, "_clone", side_effect=lambda *a: (validation.stop_all(), clone(*a))[1]):
            result = self.drive()
        self.assertEqual((result["ended"], result["simulator"]), ("turned off", None))
        self.assertNotIn("boot", [call[3] for call in self.calls() if len(call) > 3])
        self.assertEqual(self.runs(), [])

    def test_a_phone_that_cannot_be_deleted_keeps_the_area_and_closes_the_runner_until_it_goes(self):
        self.setenv("FAKE_SIMCTL_FAIL", "delete")
        result = self.drive()
        self.assertEqual((result["exit"], result["ended"]), (0, "cleanup failed"))
        self.assertIn("1 Simulator device(s) remain", result["cleanup"])
        self.assertIn("delete: the fixture failed", result["cleanup"])
        [area] = self.runs()
        self.assertTrue((area / "simulator").is_dir(), "the device set stays as the record of what to remove")
        self.assertFalse(validation._ready.is_set())
        self.assertIn("Simulator device(s) remain", self.validate(["true"], status=400)["error"])
        self.setenv("FAKE_SIMCTL_FAIL", None)
        self.assertEqual(self.validate(["true"])["ended"], "exit", "a later request removes it without a restart")
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.rows()[0]["ended"], "cleanup failed", "the recorded outcome stays")

    def test_startup_removes_the_phone_of_a_run_altd_did_not_see_end(self):
        ident = "5d1c0ffee123"
        area, unit = validation.home() / "runs" / ident, f"altitude-validation-{ident}.service"
        (area / "simulator" / "PHONE").mkdir(parents=True)
        (area / "simulator" / "PHONE" / "booted").touch()
        (area / "results").mkdir()
        S.write_json(area / "run.json", {"project": self.project, "slug": self.slug, "unit": unit})
        T.start_machine_run(self.project, self.slug, lambda n: {"purpose": "validation", "command": "true",
                                                                 "unit": unit, "simulator": {"device": "iPhone 17"}})
        validation._ready.clear()
        validation.reconcile()
        self.assertEqual([call[3:5] for call in self.calls()], [["shutdown", "all"], ["delete", "all"],
                                                                ["list", "--json"]])
        self.assertEqual(self.runs(), [])
        self.assertTrue(validation._ready.is_set())
        self.assertEqual(self.rows()[0]["ended"], "interrupted")

    def test_startup_keeps_an_area_whose_phone_cannot_be_removed(self):
        ident = "5d1c0ffee124"
        area = validation.home() / "runs" / ident
        (area / "simulator" / "PHONE").mkdir(parents=True)
        self.setenv("FAKE_SIMCTL_FAIL", "delete")
        validation._ready.clear()
        validation.reconcile()
        self.assertTrue((area / "simulator" / "PHONE").is_dir())
        self.assertFalse(validation._ready.is_set())
        self.setenv("FAKE_SIMCTL_FAIL", None)
        validation.reconcile()
        self.assertFalse(area.exists())
        self.assertTrue(validation._ready.is_set())



class TestBoot(TestCase):
    def test_a_boot_slowed_by_a_busy_host_shares_one_limit_across_its_steps(self):
        clock, calls = [1000.0], []

        def simctl(devices, *args, timeout):   # each step takes as long as a host under heavy load made it
            calls.append((args[0], timeout))
            clock[0] += {"create": 20, "bootstatus": 150, "getenv": 90}[args[0]]
            return {"create": "PHONE\n", "getenv": "/tmp/inspector.sock\n"}.get(args[0], "")
        with mock.patch.object(sim, "_simctl", side_effect=simctl), \
                mock.patch.object(sim.time, "monotonic", side_effect=lambda: clock[0]):
            phone = sim.Phone(Path(SUITE) / f"boot-{os.getpid()}-{time.monotonic_ns()}", {"device_id": "d", "runtime_id": "r"},
                              Path(SUITE) / "walks")
            self.addCleanup(phone.devices.rmdir)
            self.assertEqual(phone.boot(), "/tmp/inspector.sock")
        self.assertEqual(calls, [("create", sim.BOOT_SECONDS), ("bootstatus", sim.BOOT_SECONDS - 20),
                                 ("getenv", sim.BOOT_SECONDS - 170)])

class TestRemove(TestCase):
    def test_an_absent_set_needs_nothing(self):
        with mock.patch.object(sim, "_simctl") as simctl:
            self.assertIsNone(sim.remove(Path(SUITE) / "no-such-set"))
        simctl.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
