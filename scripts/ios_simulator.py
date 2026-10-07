#!/usr/bin/env python3
"""The phone UI in iOS Safari on a validation run's Simulator iPhone.

Serves this checkout's built web app with fixture engines and fictional data on loopback, pairs the phone's Safari,
opens a project's work in the phone layout, taps into a task and back, and keeps a page snapshot at each step,
Safari's console and the browser's versions. A step that does not reach its state, horizontal overflow or a console
error fails the walkthrough.

It runs inside `alt task validate --simulator` (`make ui-simulator`): altd's relay to that iPhone's Safari is the socket
in $SIMULATOR_INSPECTOR, and nothing here reaches the Simulator service. Evidence goes to RESULTS, by default
$VALIDATION_RESULTS/simulator. See docs/DEVELOPMENT.md#ios-simulator-runs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import plistlib
import socket
import struct
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parent.parent
SAFARI = "com.apple.mobilesafari"
OPEN, OPENED = "_rpc_altitudeOpenURL:", "_rpc_altitudeOpenedURL:"
PHONE_WIDTH = 500   # widest viewport this walkthrough accepts as the phone layout


class Safari:
    """A Web Inspector client for one Safari page, through the relay's socket."""

    def __init__(self, path: str):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(path)
        self.sock.settimeout(0.2)
        self.connection = str(uuid.uuid4()).upper()
        self.buffer = b""
        self.apps: dict = {}
        self.listing: dict = {}
        self.inbox: list = []
        self.console: list = []
        self.opened: list = []
        self.target = self.app = self.page = self.sender = None
        self.console_on = False
        self.next_id = 1
        self.send("_rpc_reportIdentifier:", {})

    def send(self, selector: str, argument: dict) -> None:
        data = plistlib.dumps({"__selector": selector, "__argument": {"WIRConnectionIdentifierKey": self.connection,
                                                                      **argument}}, fmt=plistlib.FMT_BINARY)
        self.sock.sendall(struct.pack(">I", len(data)) + data)

    def pump(self) -> None:
        try:
            chunk = self.sock.recv(1 << 20)
        except TimeoutError:
            return
        if not chunk:
            raise ConnectionError("the relay closed the connection")
        self.buffer += chunk
        while len(self.buffer) >= 4 and len(self.buffer) >= 4 + struct.unpack(">I", self.buffer[:4])[0]:
            size = struct.unpack(">I", self.buffer[:4])[0]
            message = plistlib.loads(self.buffer[4:4 + size])
            self.buffer = self.buffer[4 + size:]
            self.receive(message["__selector"], message["__argument"])

    def receive(self, selector: str, argument: dict) -> None:
        if selector == "_rpc_reportConnectedApplicationList:":
            self.apps = dict(argument.get("WIRApplicationDictionaryKey") or {})
        elif selector in ("_rpc_applicationConnected:", "_rpc_applicationUpdated:"):
            self.apps[argument["WIRApplicationIdentifierKey"]] = argument
        elif selector == "_rpc_applicationDisconnected:":
            self.apps.pop(argument["WIRApplicationIdentifierKey"], None)
        elif selector == "_rpc_applicationSentListing:":
            self.listing[argument["WIRApplicationIdentifierKey"]] = argument["WIRListingKey"]
        elif selector == OPENED:
            self.opened.append(argument.get("error", ""))
        elif selector == "_rpc_applicationSentData:":
            message = json.loads(argument["WIRMessageDataKey"])
            method, params = message.get("method"), message.get("params", {})
            if method == "Target.targetCreated" and self.target is None:
                self.target = params["targetInfo"]["targetId"]
            elif method == "Target.didCommitProvisionalTarget":
                self.target = params["newTargetId"]
                self.console_on = False
            elif method == "Target.dispatchMessageFromTarget":
                inner = json.loads(params["message"])
                if inner.get("method") == "Console.messageAdded":
                    self.console.append(inner["params"]["message"])
                elif "id" in inner:
                    self.inbox.append(inner)

    def until(self, check, seconds: float, what: str):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = check()
            if value:
                return value
            self.pump()
        raise TimeoutError(what)

    def open(self, url: str) -> None:
        self.send(OPEN, {"url": url})
        error = self.until(lambda: self.opened and [self.opened.pop()], 60, "the relay did not answer")[0]
        if error:
            raise RuntimeError(f"Safari did not open {url}: {error}")

    def attach(self, prefix: str, seconds: float = 60) -> None:
        """Inspect Safari's page whose address starts with `prefix`."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            # Asked each time: a Safari launched just now registers with the inspector after the first answer.
            self.send("_rpc_getConnectedApplications:", {})
            for app, row in list(self.apps.items()):
                if row.get("WIRApplicationBundleIdentifierKey") == SAFARI:
                    self.send("_rpc_forwardGetListing:", {"WIRApplicationIdentifierKey": app})
            for _ in range(5):
                self.pump()
            page = next(((app, key) for app, pages in self.listing.items() for key, row in pages.items()
                         if str(row.get("WIRURLKey", "")).startswith(prefix)), None)
            if page:
                break
        else:
            seen = sorted(str(row.get("WIRURLKey")) for pages in self.listing.values() for row in pages.values())
            raise TimeoutError(f"Safari listed no page at {prefix} (applications "
                               f"{sorted(str(a.get('WIRApplicationBundleIdentifierKey')) for a in self.apps.values())}, "
                               f"pages {seen})")
        self.app, key = page
        self.page = int(key) if str(key).isdigit() else key
        self.sender = str(uuid.uuid4()).upper()
        self.send("_rpc_forwardSocketSetup:", {"WIRApplicationIdentifierKey": self.app, "WIRPageIdentifierKey": self.page,
                                               "WIRSenderKey": self.sender, "WIRAutomaticallyPause": False})
        self.until(lambda: self.target, seconds, "Safari gave no page target")

    def call(self, method: str, params: dict, seconds: float = 30) -> dict:
        ident, self.next_id = self.next_id, self.next_id + 1
        if not self.console_on and method != "Console.enable":
            self.console_on = True
            self.call("Console.enable", {}, seconds)
        outer = {"id": ident, "method": "Target.sendMessageToTarget",
                 "params": {"targetId": self.target, "message": json.dumps({"id": ident, "method": method,
                                                                             "params": params})}}
        self.send("_rpc_forwardSocketData:", {"WIRApplicationIdentifierKey": self.app, "WIRPageIdentifierKey": self.page,
                                              "WIRSenderKey": self.sender, "WIRSocketDataKey": json.dumps(outer).encode()})
        body = self.until(lambda: next((m for m in self.inbox if m.get("id") == ident), None), seconds,
                          f"{method} got no answer")
        self.inbox.remove(body)
        if "error" in body:
            raise RuntimeError(f"{method}: {body['error']}")
        return body["result"]

    def evaluate(self, expression: str, *, gesture: bool = False, seconds: float = 30):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                "emulateUserGesture": gesture}, seconds)
        if result.get("wasThrown"):
            raise RuntimeError(f"{expression[:80]}: {result['result'].get('description')}")
        return result["result"].get("value")

    def wait(self, expression: str, what: str, seconds: float = 20):
        """Poll `expression` until it is truthy; the page may navigate between polls."""
        end, error = time.monotonic() + seconds, None
        while time.monotonic() < end:
            try:
                value = self.evaluate(expression, seconds=5)
                if value:
                    return value
            except (RuntimeError, TimeoutError) as exc:
                error = exc
            time.sleep(0.3)
        raise TimeoutError(f"{what} did not appear{f': {error}' if error else ''}")

    def snapshot(self, path: Path) -> None:
        width, height = self.evaluate("[innerWidth, innerHeight]")
        data = self.call("Page.snapshotRect", {"x": 0, "y": 0, "width": width, "height": height,
                                               "coordinateSystem": "Viewport"})["dataURL"]
        path.write_bytes(base64.b64decode(data.split(",", 1)[1]))


#: Page helpers, evaluated before each check: a labelled element that is laid out, and whether the page overflows.
HELPERS = """
var shown = (selector) => [...document.querySelectorAll(selector)].find((e) => e.getClientRects().length > 0);
var fits = () => document.documentElement.scrollWidth <= innerWidth;
"""


def walkthrough(safari: Safari, url: str, device: str, results: Path) -> list[dict]:
    steps = []

    def state(name: str, check: str, what: str, action: str | None = None) -> None:
        if action:
            safari.evaluate(HELPERS + action, gesture=True)
        safari.wait(HELPERS + f"({check}) && fits()", what)
        safari.snapshot(results / f"{name}.png")
        steps.append({"step": name, "url": safari.evaluate("location.href"), "reached": what})

    safari.open(url + "/")
    safari.attach(url)
    safari.wait("document.readyState === 'complete'", "the first page")
    # Pair as the fixture's device, the way a paired phone's cookie does, then read the fixture's project.
    safari.evaluate(f"document.cookie = 'altitude_device={device}; path=/'; 0")
    project = safari.wait("window.__project || (window.__overview ||= fetch('/api/overview').then(r => r.json())"
                          ".then(o => window.__project = o.projects.find(p => p.managed).name), '')",
                          "the fixture's project")
    work = f"/projects/{project}"
    safari.evaluate(f"location.href = {json.dumps(url + work + '?tab=work')}; 0")
    task_link = f"shown('section[aria-label=\"Work\"] a[href^=\"{work}/tasks/\"]')"
    state("01-work", f"{task_link} && innerWidth <= {PHONE_WIDTH}", "the project's work in the phone layout")
    state("02-task", "shown('section[aria-label=\"Task conversation\"]') && shown('button[aria-label=\"Back\"]') "
                     "&& !shown('section[aria-label=\"Work\"]')", "a task's conversation with Back",
          action=f"{task_link}.click(); 0")
    state("03-back", f"{task_link} && !shown('section[aria-label=\"Task conversation\"]')",
          "the project's work again", action="shown('button[aria-label=\"Back\"]').click(); 0")
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", nargs="?", type=Path,
                        default=Path(os.environ.get("VALIDATION_RESULTS", REPO / "web" / "test-results")) / "simulator")
    args = parser.parse_args()
    inspector = os.environ.get("SIMULATOR_INSPECTOR")
    if not inspector:
        sys.exit("ios_simulator: run through `alt task validate --simulator -- make ui-simulator` on a Mac")
    if not (REPO / "web" / "dist" / "index.html").is_file():
        sys.exit("ios_simulator: build the web app first (make web)")
    args.results.mkdir(parents=True, exist_ok=True)
    service_log = (args.results / "service.log").open("w")
    service = subprocess.Popen([sys.executable, "e2e/acceptance-service.py"], cwd=REPO / "web",
                               stdout=subprocess.PIPE, stderr=service_log, text=True)
    record = {"steps": [], "error": None}
    safari = None
    try:
        ready = json.loads(service.stdout.readline() or "{}")
        if not ready.get("disposable"):
            raise RuntimeError("the fixture service did not start; see service.log")
        safari = Safari(inspector)
        try:
            record["steps"] = walkthrough(safari, ready["url"], ready["device"], args.results)
        finally:
            if safari.target:
                record["browser"] = safari.evaluate(
                    "({userAgent: navigator.userAgent, viewport: [innerWidth, innerHeight], "
                    "devicePixelRatio, speechRecognition: typeof webkitSpeechRecognition})")
                # Leave the app, so its change stream ends before the service stops.
                safari.evaluate("location.href = 'about:blank'; 0")
                safari.wait("location.href === 'about:blank'", "a blank page")
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        record["error"] = str(exc)
    finally:
        service.terminate()
        try:
            service.wait(10)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait()
        service_log.close()
    console = safari.console if safari else []
    errors = [m for m in console if m.get("level") == "error"]
    (args.results / "console.log").write_text("".join(
        f"{m.get('level')}: {m.get('text')} ({m.get('url', '')}:{m.get('line', '')})\n" for m in console))
    if errors and not record["error"]:
        record["error"] = f"Safari logged {len(errors)} console error(s); see console.log"
    # As in the Playwright fixture: the service ends cleanly on SIGTERM and logs nothing unexpected.
    if (service.returncode or (args.results / "service.log").stat().st_size) and not record["error"]:
        record["error"] = f"the fixture service exited with {service.returncode} or logged errors; see service.log"
    (args.results / "walkthrough.json").write_text(json.dumps(record, indent=2) + "\n")
    for step in record["steps"]:
        print(f"ios_simulator: {step['step']}: {step['reached']}")
    print(f"ios_simulator: {record.get('browser', {}).get('userAgent', 'no browser')}")
    if record["error"]:
        print(f"ios_simulator: failed: {record['error']}", file=sys.stderr)
        return 1
    print(f"ios_simulator: walkthrough passed; evidence in {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
