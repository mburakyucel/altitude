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
from altitude import config, platform, simulator as sim, state as S, tasks as T, validation

XCRUN = f"#!{sys.executable}\n" + r'''"""xcrun stand-in: xcodebuild's version and simctl on private device sets, failing a step on request."""
import json, os, shutil, sys, uuid
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_SIMCTL_LOG"], "a") as out:
    out.write(json.dumps(args) + "\n")
if args[0] == "xcodebuild":
    print("Xcode 27.0\nBuild version 27A266a")
    sys.exit(0)
args, devices = args[1:], None
if args[0] == "--set":
    devices, args = Path(args[1]), args[2:]
step = "delete" if args[:2] == ["delete", "all"] else args[0]
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
elif args[0] == "appinfo":
    print('{\n    CFBundleIdentifier = "com.apple.mobilesafari";\n    Path = "%s";\n}' % os.environ["FAKE_SAFARI"])
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
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.listener.close()
        for connection in self.connections:
            connection.close()

    def _accept(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            self.connections.append(connection)
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

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
        self.opened = []
        self.relay = sim.Relay(self.tmp / "run.sock", str(self.tmp / "phone.sock"), self.port, self.opened.append)
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
json.dump({"apps": sorted(apps), "opened": opened, "socket": os.environ["SIMULATOR_INSPECTOR"]},
          open(os.path.join(os.environ["VALIDATION_RESULTS"], "relay.json"), "w"))
''')

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

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
        devices = str(validation.home() / "runs" / area_name / "simulator")
        simctl = [call[1:] for call in self.calls() if call[0] == "simctl"]
        self.assertEqual(simctl[0], ["list", "--json", "runtimes", "devicetypes"])
        self.assertTrue(all(call[:2] == ["--set", devices] for call in simctl[1:]), "only the run's own device set")
        steps = [call[2] for call in simctl[1:]]
        self.assertEqual(steps, ["create", "bootstatus", "getenv", "appinfo", "openurl", "io", "shutdown", "delete",
                                 "list"])
        self.assertEqual(simctl[1][3:], ["altitude-validation", "type.iPhone-17", "runtime.iOS-27-0"])
        self.assertEqual(simctl[5][-1], "http://127.0.0.1:5555/")
        expected = {"xcode": "Xcode 27.0 Build version 27A266a", "runtime": "iOS 27.0 (24A434)", "device": "iPhone 17",
                    "safari": "27.0 (8624.1.17)",
                    "screenshot": str(S.task_dir(self.project, self.slug) / "validation" / "1.simulator.png")}
        self.assertEqual(result["simulator"], expected)
        self.assertEqual(self.rows()[0]["simulator"], expected)
        self.assertEqual(Path(expected["screenshot"]).read_bytes(), b"\x89PNG fixture screen")
        self.assertEqual(self.runs(), [], "the device set and run area are gone")
        self.assertTrue(validation._ready.is_set())
        self.assertEqual(self.profile.read_text(), platform.validation_profile(
            tuple(validation.candidate_dirs(validation.home() / "runs" / area_name)),
            validation.home() / "runs" / area_name / f"{result['unit']}.log", config.PORT),
            "the run's profile is the one every macOS run has")

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

    def test_a_phone_that_fails_to_be_created_or_boot_is_removed_and_no_run_starts(self):
        for step in ("create", "bootstatus"):
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


class TestRemove(TestCase):
    def test_an_absent_set_needs_nothing(self):
        with mock.patch.object(sim, "_simctl") as simctl:
            self.assertIsNone(sim.remove(Path(SUITE) / "no-such-set"))
        simctl.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
