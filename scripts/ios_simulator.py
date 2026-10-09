#!/usr/bin/env python3
"""The phone UI in iOS Safari on a validation run's Simulator iPhone.

Serves this checkout's built web app with fixture engines and fictional data on loopback, pairs the phone's Safari,
opens a project's work in the phone layout, taps into a task and back, walks voice input's restart after the X (issue
#698) with diagnostics on, checks what Add to Home Screen would take from the app, loads a page over HTTPS with the
run's certificate from Altitude's generator, whose CA altd trusted in the phone, then opens the device setup page of
a fictional CA and taps Download the profile. It keeps a page snapshot at each step, Safari's console, the browser's
versions and the voice diagnostic report. A step that does not reach its state, horizontal overflow, a console error,
a certificate warning, an untrusted certificate Safari accepts or a profile Safari does not fetch fails the
walkthrough. The run's final screenshot shows Safari's answer to the profile.

It runs inside `alt task validate --simulator` (`make ui-simulator`): altd's relay to that iPhone's Safari is the socket
in $SIMULATOR_INSPECTOR, and nothing here reaches the Simulator service. Evidence goes to RESULTS, by default
$VALIDATION_RESULTS/simulator. See docs/DEVELOPMENT.md#ios-simulator-runs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import plistlib
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid

REPO = Path(__file__).resolve().parent.parent
FAILURES = (OSError, RuntimeError, TimeoutError, ValueError, KeyError)
sys.path.insert(0, str(REPO))
from altitude import tls
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


#: Counts the app's requests from the first step on, and once `__leaving` is set starts no more, so the walkthrough
#: leaves the app between requests. Safari logs a request cut off by leaving as a console error.
REQUESTS = """
if (!window.__requests) {
  window.__requests = new Map();
  const original = window.fetch;
  window.fetch = function (...args) {
    if (window.__leaving) return new Promise(() => {});
    const key = {};
    window.__requests.set(key, String(args[0]?.url ?? args[0]));
    return original.apply(this, args).then((response) => response.clone().arrayBuffer().then(() => response))
      .finally(() => window.__requests.delete(key));
  };
}
0
"""


def leave(safari: Safari, address: str) -> None:
    """Open `address` once the app has no request under way."""
    safari.evaluate("window.__leaving = true; 0")
    try:
        safari.wait("!window.__requests?.size", "the app's requests to end")
    except TimeoutError as exc:
        try:
            under_way = safari.evaluate("[...window.__requests.values()]", seconds=5)
        except FAILURES:
            under_way = "unknown"
        raise TimeoutError(f"{exc}; under way: {under_way}") from None
    safari.evaluate(f"location.href = {json.dumps(address)}; 0")


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

    for attempt in range(4):
        safari.open(url + "/")
        try:
            safari.attach(url, seconds=15)
            break
        except TimeoutError:
            if attempt == 3:  # a Safari launched by the first open can drop that address and show a blank page
                raise
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
    safari.evaluate(REQUESTS)
    state("02-task", "shown('section[aria-label=\"Task conversation\"]') && shown('button[aria-label=\"Back\"]') "
                     "&& !shown('section[aria-label=\"Work\"]')", "a task's conversation with Back",
          action=f"{task_link}.click(); 0")
    state("03-back", f"{task_link} && !shown('section[aria-label=\"Task conversation\"]')",
          "the project's work again", action="shown('button[aria-label=\"Back\"]').click(); 0")
    task = safari.evaluate(f"{task_link}.getAttribute('href')")
    steps += dictation(safari, url, work, task.split("?")[0], results)
    steps.append(home_screen(safari, url, ["/", work, task], results))
    return steps


#: Simulator Safari's microphone and speech recognizer stop at native permission dialogs the inspector cannot answer,
#: and granting them would record this Mac's room. A tone from Safari's own audio engine stands in for the microphone
#: and a scripted recognizer for the native one; the waveform graph, timers, focus and layout are iOS Safari's.
VOICE_FIXTURES = """
// Held, so WebKit keeps this wrapper and its stand-in instead of collecting it and offering the native request again.
window.__voice = {recognizers: [], graphs: [], streams: [], log: [], devices: navigator.mediaDevices};
window.SpeechRecognition = class {
  constructor() { this.onresult = this.onerror = this.onend = null; this.ended = false; __voice.recognizers.push(this); }
  start() { __voice.log.push(['recognizer started', Math.round(performance.now())]); this.onstart && this.onstart(); this.onaudiostart && this.onaudiostart(); }
  stop() { setTimeout(() => this.end(), 0); }
  abort() { setTimeout(() => this.end(), 0); }
  end() { if (this.ended) return; this.ended = true; this.onaudioend && this.onaudioend(); this.onend && this.onend(); }
  hear(words) { this.onresult && this.onresult({results: [{isFinal: false, 0: {transcript: words}, length: 1}]}); }
};
var NativeAudioContext = window.AudioContext;
window.AudioContext = class extends NativeAudioContext {
  constructor(...args) { super(...args); __voice.graphs.push(this); }
};
Object.defineProperty(__voice.devices, 'getUserMedia', {configurable: true, value: async () => {
  if (!__voice.tone) {
    // A tone that swells and fades twice a second, so a drawing waveform keeps changing.
    const tone = __voice.tone = new NativeAudioContext(), swell = tone.createOscillator(), depth = tone.createGain();
    __voice.oscillator = tone.createOscillator();
    __voice.level = tone.createGain();
    __voice.level.gain.value = depth.gain.value = 0.15;
    swell.frequency.value = 2;
    swell.connect(depth).connect(__voice.level.gain);
    __voice.oscillator.connect(__voice.level);
    swell.start();
    __voice.oscillator.start();
  }
  __voice.log.push(['microphone requested', __voice.tone.state, Math.round(performance.now())]);
  const destination = __voice.tone.createMediaStreamDestination();
  __voice.level.connect(destination);
  await __voice.tone.resume();
  __voice.log.push(['microphone opened', __voice.tone.state, Math.round(performance.now())]);
  __voice.streams.push(destination.stream);
  return destination.stream;
}});
0
"""

#: The waveform's drawn bars: each column's height as a share of the canvas height (the silent minimum is a few pixels).
BARS = """(() => {
  const canvas = shown('.composer-wave');
  const data = canvas && canvas.height && canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height);
  if (!data) return [];
  const bars = [];
  for (let x = 0; x < data.width; x++) {
    let column = 0;
    for (let y = 0; y < data.height; y++) if (data.data[(y * data.width + x) * 4 + 3] > 0) column++;
    bars.push(Math.round(100 * column / data.height) / 100);
  }
  return bars;
})()"""


STATE = """({phase: [...document.querySelectorAll('.composer')].map(c => c.dataset.phase),
  hint: [...document.querySelectorAll('.composer-hint, .composer-feedback, [role="status"]')].map(e => e.textContent),
  buttons: [...document.querySelectorAll('.composer button')].filter(b => b.getClientRects().length)
    .map(b => (b.getAttribute('aria-label') || b.textContent) + (b.disabled ? ' (disabled)' : '')),
  focus: document.activeElement && (document.activeElement.getAttribute('aria-label') || document.activeElement.tagName),
  log: window.__voice && __voice.log, tone: window.__voice && __voice.tone && __voice.tone.state,
  graphs: window.__voice && __voice.graphs.map(g => [g.state, g.currentTime]),
  recognizers: window.__voice && __voice.recognizers.map(r => r.ended),
  streams: window.__voice && __voice.streams.map(s => s.getTracks().map(t => t.readyState))})"""


def dictation(safari: Safari, url: str, work: str, task: str, results: Path) -> list[dict]:
    """Issue #698's journey with diagnostics on: dictate, cancel with the X and tap the microphone again, three times
    in the project's composer and three in a task's, without reloading; then the diagnostic report."""
    steps = []
    rounds = []

    def go(path: str) -> None:  # within the app, so the page's diagnostics survive
        safari.evaluate(f"history.pushState(null, '', {json.dumps(path)}); dispatchEvent(new PopStateEvent('popstate')); 0")

    def snap(name: str, what: str) -> None:
        safari.snapshot(results / f"{name}.png")
        steps.append({"step": name, "url": safari.evaluate("location.href"), "reached": what})

    def check(expression: str, what: str, seconds: float = 20):
        try:
            return safari.wait(HELPERS + f"(v => v instanceof Node || v)(fits() && ({expression}))", what, seconds)
        except TimeoutError:
            # Where it stopped: the composer, the stand-ins' log and the audio graphs, never the draft's words.
            (results / "dictation-failure.json").write_text(json.dumps(safari.evaluate(HELPERS + STATE), indent=2) + "\n")
            safari.snapshot(results / "dictation-failure.png")
            if not reporting:
                reporting.append(True)
                try:
                    (results / "dictation-failure-report.json").write_text(read_report())
                except (RuntimeError, TimeoutError) as exc:
                    print(f"ios_simulator: no diagnostic report after the failure: {exc}", file=sys.stderr)
            raise

    def read_report() -> str:
        go("/settings/voice")
        check(summary, "Voice input settings again")
        safari.evaluate(f"{summary}.parentElement.open || {summary}.click(); 0", gesture=True)
        safari.evaluate(f"{button.format('View report')}.click(); 0", gesture=True)
        return check("[...document.querySelectorAll('textarea')].find(t => t.value.includes('altitude-voice-diagnostic'))"
                     "?.value", "the diagnostic report")

    reporting: list = []
    summary = "[...document.querySelectorAll('summary')].find(s => s.textContent === 'Voice troubleshooting')"
    button = "[...document.querySelectorAll('button')].find(b => b.textContent === {!r} && b.getClientRects().length)"
    # The installation answers browser recognition, the backend whose restart #698 reports.
    status = safari.wait("window.__backend || (window.__saving ||= fetch('/api/voice').then(r => r.json()).then(v => "
                         "fetch('/api/voice', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
                         "body: JSON.stringify({backend: 'browser', selection: v.selection})})).then(r => "
                         "window.__backend = r.status), 0)", "the browser recognition setting")
    if status != 200:
        raise RuntimeError(f"saving browser recognition answered {status}")
    leave(safari, url + "/settings/voice")
    check(summary, "Voice input settings")
    safari.evaluate(REQUESTS)  # this page's, for leaving it later
    safari.evaluate(f"{summary}.click(); 0", gesture=True)
    safari.evaluate(f"{button.format('Start diagnostics')}.click(); 0", gesture=True)
    check(button.format("Stop diagnostics"), "diagnostics collecting")
    snap("voice-1-diagnostics-on", "voice diagnostics collecting")
    safari.evaluate(VOICE_FIXTURES)

    field = "shown('textarea.composer-field')"
    for where, path, draft in (("project", work, "Typed project draft"), ("task", task, "Typed task draft")):
        go(path)
        check(f"{field} && !{field}.readOnly && shown('button[aria-label=\"Start voice input\"]')", f"the {where} composer")
        safari.evaluate(HELPERS + f"var f = {field}; Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "
                        f"'value').set.call(f, {json.dumps(draft)}); f.dispatchEvent(new Event('input', {{bubbles: true}})); "
                        f"f.blur(); 0")
        check(f"{field}.value === {json.dumps(draft)} && document.activeElement !== {field}", f"the typed {where} draft")
        for turn in range(1, 4):
            words = f"{where} words {turn}"
            capture = safari.evaluate("__voice.recognizers.length")
            before = safari.evaluate("visualViewport.height")
            safari.evaluate(HELPERS + "shown('button[aria-label=\"Start voice input\"]').click(); 0", gesture=True)
            check(f"shown('.composer[data-phase=\"listening\"]') && shown('.composer-wave') && "
                  f"__voice.recognizers.length === {capture + 1}", f"{where} listening, round {turn}")
            safari.evaluate(f"__voice.recognizers[{capture}].hear({json.dumps(words)}); 0")
            check(f"{field}.value === {json.dumps(f'{draft} {words}')}", f"{where} words, round {turn}")
            # A moving waveform: a loud bar, then a different frame, while the trace samples the graph each second.
            first = check(f"Math.max(...{BARS}) > 0.5 && {BARS}", f"{where} waveform, round {turn}", seconds=10)
            time.sleep(2.2)
            later = safari.evaluate(HELPERS + BARS)
            if later == first:
                raise RuntimeError(f"{where} waveform, round {turn}: the bars did not change in two seconds")
            loudest = max(first)
            snap(f"voice-{2 if where == 'project' else 3}-{where}-{turn}-listening", f"{where} words and waveform, round {turn}")
            safari.evaluate(HELPERS + "shown('button[aria-label=\"Cancel voice input\"]').click(); 0", gesture=True)
            check(f"shown('button[aria-label=\"Start voice input\"]') && !shown('button[aria-label=\"Cancel voice input\"]') "
                  f"&& !shown('.composer-wave') && {field}.value === {json.dumps(draft)} && !{field}.readOnly",
                  f"{where} X keeps the draft, round {turn}")
            ended = check(f"__voice.recognizers[{capture}].ended && __voice.streams[{capture}].getTracks().every(t => "
                          f"t.readyState === 'ended') && __voice.graphs[{capture}].state === 'closed' && "
                          f"__voice.graphs[{capture}].state", f"{where} capture released, round {turn}")
            focus = safari.evaluate("document.activeElement && (document.activeElement.getAttribute('aria-label') || "
                                    "document.activeElement.tagName)")
            if focus != "Start voice input":
                raise RuntimeError(f"{where} X, round {turn}: focus went to {focus}, not the microphone")
            after = safari.evaluate("visualViewport.height")
            if after < before:
                raise RuntimeError(f"{where} X, round {turn}: the viewport shrank from {before} to {after}")
            snap(f"voice-{2 if where == 'project' else 3}-{where}-{turn}-cancelled", f"{where} X, round {turn}")
            rounds.append({"composer": where, "round": turn, "waveform": round(loudest, 2), "graph": ended,
                           "focus": focus, "viewport": [before, after]})
            safari.evaluate("document.activeElement && document.activeElement.blur(); 0")

    # The viewport checks above see a keyboard: focusing the field as a tap does opens it and the viewport shrinks.
    resting = safari.evaluate("visualViewport.height")
    safari.evaluate(HELPERS + f"{field}.focus(); 0", gesture=True)
    keyboard = {"resting": resting, "field focused": check(f"visualViewport.height < {resting} - 100 && "
                                                           "visualViewport.height", "the keyboard for the field", 10)}
    safari.evaluate("document.activeElement.blur(); 0")

    report = read_report()
    snap("voice-4-diagnostic-report", "the voice diagnostic report")
    (results / "voice-report.json").write_text(report)
    if "Typed" in report or "words" in report:
        raise RuntimeError("the diagnostic report contains draft or dictated text")
    events = json.loads(report)["events"]
    listening = sum(e["event"] == "capture.listening" for e in events)
    samples = [e.get("source") for e in events if e["event"] == "waveform.sample" and e.get("signal")]
    heard = {source for source in samples if samples.count(source) >= 2}
    (results / "dictation.json").write_text(json.dumps({"rounds": rounds, "keyboard": keyboard,
                                                        "captures": listening, "graphs with signal": len(heard)},
                                                       indent=2) + "\n")
    if listening != 6 or len(heard) != 6:
        raise RuntimeError(f"the report shows {listening} captures and {len(heard)} waveform graphs with signal in two "
                           "samples, not 6")
    return steps


#: What Add to Home Screen takes from each address, read as Safari parses the page, and the files it names.
HOME_SCREEN = """
window.__home || (window.__home = (async () => {
  const pages = {};
  const read = async (path) => {
    const response = await fetch(path);
    if (!response.ok || response.redirected) throw new Error(`${path} answered ${response.status} at ${response.url}`);
    return response;
  };
  for (const path of %s) {
    const page = new DOMParser().parseFromString(await (await read(path)).text(), 'text/html');
    pages[path] = {title: page.title, icon: page.querySelector('link[rel="apple-touch-icon"]')?.getAttribute('href'),
                   manifest: page.querySelector('link[rel="manifest"]')?.getAttribute('href')};
  }
  const icon = await read(pages['/'].icon), bytes = new Uint8Array(await icon.arrayBuffer());
  const image = new Image();
  image.src = pages['/'].icon;
  await image.decode();
  const manifest = await (await read(pages['/'].manifest)).json();
  return {pages, manifest, icon: {type: icon.headers.get('content-type'), size: [image.naturalWidth, image.naturalHeight],
                                  bytes: btoa(String.fromCharCode(...bytes))}};
})().then((value) => window.__homeResult = value, (error) => window.__homeResult = {error: String(error)}));
window.__homeResult
"""


def home_screen(safari: Safari, url: str, paths: list[str], results: Path) -> dict:
    """At the app's root, project and task addresses Safari finds the approved Climb icon, the manifest's name and
    standalone display: what Add to Home Screen uses. Adding the app is Safari's own menu, out of the relay's reach."""
    found = safari.wait(HOME_SCREEN % json.dumps(paths), "the Home Screen icon and manifest")
    if "error" in found:
        raise RuntimeError(f"Safari could not read the Home Screen files: {found['error']}")
    icon, approved = found["icon"], (REPO / "web" / "public" / "apple-touch-icon.png").read_bytes()
    wrong = [path for path, page in found["pages"].items()
             if page != {"title": "Altitude", "icon": "/apple-touch-icon.png", "manifest": "/manifest.webmanifest"}]
    manifest = {key: found["manifest"].get(key) for key in ("name", "short_name", "display", "start_url")}
    if wrong or icon["type"] != "image/png" or icon["size"] != [180, 180] or base64.b64decode(icon["bytes"]) != approved \
            or manifest != {"name": "Altitude", "short_name": "Altitude", "display": "standalone", "start_url": "/"}:
        raise RuntimeError(f"Safari found other Home Screen files: pages {found['pages']}, manifest {manifest}, "
                           f"icon {icon['type']} {icon['size']} sha256 "
                           f"{hashlib.sha256(base64.b64decode(icon['bytes'])).hexdigest()}")
    # The icon as Safari draws it, which also leaves the app so its change stream ends.
    leave(safari, url + "/apple-touch-icon.png")
    safari.wait("document.images[0] && document.images[0].complete && document.images[0].naturalWidth === 180",
                "the icon on its own")
    safari.snapshot(results / "04-icon.png")
    return {"step": "04-icon", "url": safari.evaluate("location.href"), "pages": found["pages"], "manifest": manifest,
            "icon": {"type": icon["type"], "size": icon["size"], "sha256": hashlib.sha256(approved).hexdigest()},
            "reached": "the approved Climb icon, name and standalone display at the root, project and task addresses"}


class Secure:
    """A page on an HTTPS loopback port served with `context`, recording the paths it answers and the handshakes it
    refuses."""

    def __init__(self, context: ssl.SSLContext, page: bytes):
        self.requests, self.refused = [], []
        secure = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                secure.requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *args) -> None:
                pass

        class Server(http.server.ThreadingHTTPServer):
            block_on_close = False

            def get_request(self):
                connection, address = self.socket.accept()
                connection.settimeout(10)
                try:
                    return context.wrap_socket(connection, server_side=True), address
                except OSError as exc:
                    secure.refused.append(str(exc))
                    connection.close()
                    raise

        self.server = Server(("127.0.0.1", 0), Handler)
        self.origin = f"https://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def https(safari: Safari, results: Path) -> dict:
    """A page served over HTTPS with the identity altd made with Altitude's certificate generator and trusted in the
    phone ($SIMULATOR_HTTPS), through the serving context Altitude's server loads: Safari fetches from it and loads it
    as a secure context, with no certificate warning. As a control, a fetch from a second identity of the same
    generator, which the phone does not trust, must be refused, so a pass shows that Safari checked the chain. Safari's
    own words about a refusal are recorded."""
    folder, nonce = Path(os.environ["SIMULATOR_HTTPS"]), uuid.uuid4().hex
    page = (f'<!doctype html><meta name="viewport" content="width=device-width"><title>{nonce}</title>'
            "<h1>HTTPS without a warning</h1><p>Served with this run's Altitude certificate.</p>").encode()
    with tempfile.TemporaryDirectory() as other:
        tls.fixture(Path(other) / "untrusted")
        untrusted = Secure(tls._load(Path(other) / "untrusted"), page)
    trusted = Secure(tls._load(folder), page)

    def fetched(origin: str) -> tuple[str, list[str]]:
        """How a fetch from `origin` by the current page ended, and what Safari's console said about `origin`."""
        safari.evaluate(f"window.__fetched = 'pending'; fetch({json.dumps(origin + '/fetch')}, {{mode: 'no-cors'}})"
                        ".then(() => window.__fetched = 'loaded', (e) => window.__fetched = String(e)); 0")
        outcome = safari.wait("window.__fetched !== 'pending' && window.__fetched", f"the fetch from {origin}")
        end = time.monotonic() + 1   # Safari's console messages about it arrive beside the answer
        while time.monotonic() < end:
            safari.pump()
        return outcome, [m.get("text", "") for m in safari.console if origin in f"{m.get('url', '')} {m.get('text', '')}"]

    try:
        outcome, said = fetched(trusted.origin)
        if outcome != "loaded":
            raise RuntimeError(f"Safari refused the run's trusted certificate: {outcome}; Safari: {said}; "
                               f"the server: {trusted.refused}")
        safari.evaluate(f"location.href = {json.dumps(trusted.origin + '/')}; 0")
        safari.wait(f"location.href === {json.dumps(trusted.origin + '/')} && document.title === {json.dumps(nonce)}"
                    " && isSecureContext", "the HTTPS page, without a certificate warning")
        safari.snapshot(results / "05-https.png")
        control, said = fetched(untrusted.origin)
        if control == "loaded" or untrusted.requests or not untrusted.refused:
            raise RuntimeError(f"Safari did not refuse an untrusted certificate: {control}; the server answered "
                               f"{untrusted.requests} and refused {untrusted.refused}")
    finally:
        trusted.close()
        untrusted.close()
    return {"step": "05-https", "url": trusted.origin + "/", "served": tls.details(folder / "server.crt")["sha256"],
            "control": {"origin": untrusted.origin, "fetch": control, "safari": said, "server": untrusted.refused},
            "reached": "an HTTPS page with the run's trusted Altitude certificate, without a warning; an untrusted "
                       "one refused"}


def certificate_setup(safari: Safari, app: str, results: Path) -> dict:
    """The device setup page for a fictional CA, as `alt tls-share` offers it: it names the CA and its SHA-256, and
    Download the profile makes Safari fetch the profile, which the share sends in full. Whether Safari accepts it shows
    only in its own prompt in the run's final screenshot; allowing, installing and trusting it are Safari's and
    Settings' controls, out of the relay's reach. tests/test_tls.py checks the profile's contents."""
    with tempfile.TemporaryDirectory() as folder:
        ca = Path(folder) / "ca.crt"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                        "-keyout", str(Path(folder) / "ca.key"), "-out", str(ca), "-days", "1",
                        "-subj", "/CN=Fixture Altitude CA", "-addext", "basicConstraints=critical,CA:TRUE"],
                       check=True, capture_output=True)
        authority, sent = tls.identity(ca), threading.Event()
        share = tls.Share({"name": "127.0.0.1", "kind": "IP", "url": app}, ca.read_bytes(), authority, 5,
                          sent=lambda message: message.startswith("Sent the profile") and sent.set())
    try:
        safari.evaluate(f"location.href = {json.dumps(share.link + '#ios')}; 0")
        rows = tls.fingerprint_rows(authority["sha256"])
        safari.wait(f"document.body && [{json.dumps(authority['name'])}, ...{json.dumps(rows)}]"
                    ".every((text) => document.body.innerText.includes(text))", "the setup page with the CA's SHA-256")
        safari.snapshot(results / "06-setup.png")
        safari.evaluate("[...document.links].find((a) => a.textContent === 'Download the profile').click(); 0",
                        gesture=True)
        if not sent.wait(30):
            raise TimeoutError("Safari did not fetch the profile")
        time.sleep(3)  # for Safari's prompt to appear in the final screenshot
    finally:
        share.close()
    return {"step": "06-profile", "url": share.link, "ca": authority["name"], "sha256": authority["sha256"],
            "reached": "the setup page's CA and SHA-256, and the profile sent to Safari in full"}


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
        except FAILURES as exc:
            record["error"] = str(exc)
        # Recorded beside a failure, never in its place.
        try:
            if safari.target:
                record["browser"] = safari.evaluate(
                    "({userAgent: navigator.userAgent, viewport: [innerWidth, innerHeight], "
                    "devicePixelRatio, speechRecognition: typeof webkitSpeechRecognition})")
                if record["error"]:  # a finished walkthrough has left the app
                    # Leave the app, so its change stream ends before the service stops.
                    leave(safari, "about:blank")
                    safari.wait("location.href === 'about:blank'", "a blank page")
        except FAILURES as exc:
            record["cleanup"] = str(exc)
        if not record["error"]:
            record["steps"].append(https(safari, args.results))
            record["steps"].append(certificate_setup(safari, ready["url"], args.results))
    except FAILURES as exc:
        record["error"] = str(exc)
    finally:
        service.terminate()
        try:
            service.wait(10)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait()
        service_log.close()
    if record.get("cleanup") and not record["error"]:
        record["error"] = record["cleanup"]
    console = safari.console if safari else []
    controls = [step["control"]["origin"] for step in record["steps"] if "control" in step]
    errors = [m for m in console if m.get("level") == "error"   # bar the control's expected refusal
              and not any(origin in f"{m.get('url', '')} {m.get('text', '')}" for origin in controls)]
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
