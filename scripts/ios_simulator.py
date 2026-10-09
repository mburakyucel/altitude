#!/usr/bin/env python3
"""The phone UI in iOS Safari on a validation run's Simulator iPhone.

Serves this checkout's built web app with fixture engines and fictional data on loopback and pairs the phone's Safari.
Then these steps, in this order, each selectable with --steps (`make ui-simulator STEPS=...`):

- navigation: a project's work in the phone layout, a task and back;
- dictation: voice input's restart after the X (issue #698) with diagnostics on;
- https: a page over HTTPS with the run's certificate from Altitude's generator, whose CA altd trusted in the phone,
  and an untrusted control refused;
- profile: the device setup page of a CA from the same generator, then altd's native profile walk (Download the
  profile, Allow, Settings' Profile Downloaded, Install, full trust), checking the profile's name and SHA-256 against
  the CA served, and a page with that CA's server certificate loading without a warning;
- home-screen: altd's native Add to Home Screen walk at the app's root and at a task's address: the sheet's title,
  the Home Screen icon against the approved one pixel for pixel, the web app opening on its own and pairing.

It keeps a page snapshot or screenshot at each step, Safari's console, the browser's versions and the voice diagnostic
report. A step that does not reach its state, horizontal overflow, a console error, a certificate warning, an untrusted
certificate Safari accepts or a native step that is not reached fails the walkthrough.

It runs inside `alt task validate --simulator` (`make ui-simulator`): altd's relay to that iPhone's Safari and its two
walks is the socket in $SIMULATOR_INSPECTOR, and nothing here reaches the Simulator service. Evidence goes to RESULTS,
by default $VALIDATION_RESULTS/simulator. See docs/DEVELOPMENT.md#ios-simulator-runs.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import http.server
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zlib

REPO = Path(__file__).resolve().parent.parent
FAILURES = (OSError, RuntimeError, TimeoutError, ValueError, KeyError)
sys.path.insert(0, str(REPO))
from altitude import tls
SAFARI = "com.apple.mobilesafari"
OPEN, OPENED = "_rpc_altitudeOpenURL:", "_rpc_altitudeOpenedURL:"
WALK, WALKED = "_rpc_altitudeWalk:", "_rpc_altitudeWalked:"
STEPS = ("navigation", "dictation", "https", "profile", "home-screen")
WALK_WAIT = 1200    # seconds for altd's answer to one walk, building the walks at the first included
SHARE_MINUTES = 30  # the setup page's window, longer than the profile walk
PHONE_WIDTH = 500   # widest viewport this walkthrough accepts as the phone layout
CERTIFICATE_ALERT = re.compile(r"ALERT_(BAD_CERTIFICATE|CERTIFICATE|UNKNOWN_CA)")   # a TLS client refusing the chain


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
        self.walked: list = []
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
        elif selector == WALKED:
            self.walked.append(argument)
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

    def walk(self, name: str, url: str, code: str = "") -> tuple[dict, dict]:
        """altd's native walk `name` at `url`: its record and its screenshots by name."""
        self.send(WALK, {"walk": name, "url": url, **({"code": code} if code else {})})
        answer = self.until(lambda: self.walked and [self.walked.pop()], WALK_WAIT, f"the {name} walk got no answer")[0]
        if answer.get("error"):
            raise RuntimeError(f"the {name} walk: {answer['error']}")
        return json.loads(answer["walk"]), answer.get("files", {})

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


@dataclasses.dataclass
class Run:
    """What the steps share: the relay's socket and a Safari connection through it, the fixture service and its paired
    device, the fixture project's work and task addresses, the evidence folder and the steps recorded so far."""
    inspector: str
    safari: Safari
    url: str
    device: str
    results: Path
    work: str = ""
    task: str = ""
    steps: list = dataclasses.field(default_factory=list)

    def task_link(self) -> str:
        return f"shown('section[aria-label=\"Work\"] a[href^=\"{self.work}/tasks/\"]')"


def setup(run: Run) -> None:
    """Open the app in Safari, pair it as the fixture's device the way a paired phone's cookie does, and find the
    fixture project's work and a task in it."""
    safari, url = run.safari, run.url
    for attempt in range(4):
        safari.open(url + "/")
        try:
            safari.attach(url, seconds=15)
            break
        except TimeoutError:
            if attempt == 3:  # a Safari launched by the first open can drop that address and show a blank page
                raise
    safari.wait("document.readyState === 'complete'", "the first page")
    safari.evaluate(f"document.cookie = 'altitude_device={run.device}; path=/'; 0")
    project = safari.wait("window.__project || (window.__overview ||= fetch('/api/overview').then(r => r.json())"
                          ".then(o => window.__project = o.projects.find(p => p.managed).name), '')",
                          "the fixture's project")
    run.work = f"/projects/{project}"
    safari.evaluate(f"location.href = {json.dumps(url + run.work + '?tab=work')}; 0")
    run.task = safari.wait(HELPERS + f"{run.task_link()}?.getAttribute('href')", "a task in the project's work")
    run.task = run.task.split("?")[0]


def navigation(run: Run) -> None:
    """The project's work in the phone layout, a task's conversation and Back."""
    safari, task_link = run.safari, run.task_link()

    def state(name: str, check: str, what: str, action: str | None = None) -> None:
        if action:
            safari.evaluate(HELPERS + action, gesture=True)
        safari.wait(HELPERS + f"({check}) && fits()", what)
        safari.snapshot(run.results / f"{name}.png")
        run.steps.append({"step": name, "url": safari.evaluate("location.href"), "reached": what})

    state("01-work", f"{task_link} && innerWidth <= {PHONE_WIDTH}", "the project's work in the phone layout")
    safari.evaluate(REQUESTS)
    state("02-task", "shown('section[aria-label=\"Task conversation\"]') && shown('button[aria-label=\"Back\"]') "
                     "&& !shown('section[aria-label=\"Work\"]')", "a task's conversation with Back",
          action=f"{task_link}.click(); 0")
    state("03-back", f"{task_link} && !shown('section[aria-label=\"Task conversation\"]')",
          "the project's work again", action="shown('button[aria-label=\"Back\"]').click(); 0")


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


#: The native walks' steps, in order; a step the walk did not record was not reached.
HOME_WALK = ("safari", "share", "add", "home-screen", "standalone", "paired")
PROFILE_WALK = ("download", "allow", "profile-downloaded", "certificate", "install", "trust")


def walked(run: Run, name: str, url: str, prefix: str, expected: tuple, code: str = "") -> tuple[dict, list, dict]:
    """altd's walk `name` at `url`, with its screenshots kept as `<prefix>-<name>.png`: what it saw, a row for each
    expected step, completed or not reachable with the reason, a failed `runner` row for a runner that crashed, hung or
    did not build even after its last step, and its files."""
    record, files = run.safari.walk(name, url, code)
    for file, data in files.items():
        (run.results / f"{prefix}-{file}").write_bytes(data)
    found = {row.get("step"): row for row in record.get("steps", [])}
    why = record.get("error") or record.get("seen", {}).get("stopped") or "no reason recorded"
    rows = []
    for step in expected:
        row = dict(found.get(step) or {"step": step, "status": "not reachable",
                                        "reason": f"the walk ended before this step: {why}"})
        if row.get("screenshot"):
            row["screenshot"] = f"{prefix}-{row['screenshot']}"
        rows.append(row)
    if record.get("error"):
        rows.append({"step": "runner", "status": "failed", "reason": record["error"]})
    return record, rows, files


def finish(run: Run, step: str, rows: list, reached: str, **seen) -> None:
    """Record a walk's rows as one step; any row not completed fails the walkthrough."""
    run.steps.append({"step": step, "walk": rows, **seen, "reached": reached})
    unreached = [row for row in rows if row.get("status") != "completed"]
    if unreached:
        row = unreached[0]
        raise RuntimeError(f"{step}: {row['step']} {row.get('status')}: {row.get('reason')}")


def unreached(step: str) -> dict:
    return {"step": step, "status": "not reachable", "reason": "an earlier step was not reached"}


def check(step: str, failures: list[str], detail: str) -> dict:
    """A row for what the run checked of a walk: completed, or failed with what differed."""
    return {"step": step, "status": "failed", "reason": "; ".join(failures)} if failures else \
        {"step": step, "status": "completed", "detail": detail}


def api(run: Run, method: str, path: str) -> dict:
    """The fixture service's answer, as the paired device."""
    request = urllib.request.Request(run.url + path, method=method, data=b"{}" if method == "POST" else None,
                                     headers={"Cookie": f"altitude_device={run.device}",
                                              "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as answer:
        return json.load(answer)


def pixels(data: bytes) -> tuple[int, int, bytes]:
    """A non-interlaced 8-bit RGB or RGBA PNG's size and RGBA pixels: what it shows, whatever chunks its encoder
    added. iOS stores a Home Screen icon re-encoded, so its bytes differ from the file the app serves."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    offset, header, compressed = 8, None, b""
    while offset + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + size]
        if kind == b"IHDR" and len(body) == 13:
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            compressed += body
        offset += 12 + size
    if not header or header[2] != 8 or header[3] not in (2, 6) or header[6]:
        raise ValueError(f"not an 8-bit RGB or RGBA PNG: {header}")
    width, height, kind = header[0], header[1], header[3]
    size = 3 if kind == 2 else 4
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        raise ValueError(f"its image data cannot be read: {exc}") from None
    stride, out = width * size, bytearray()
    if len(raw) != height * (stride + 1):
        raise ValueError(f"its image data holds {len(raw)} bytes for {width}x{height}")
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        method, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for x in range(stride):
            a, b = line[x - size] if x >= size else 0, previous[x]
            c = previous[x - size] if x >= size else 0
            if method == 1:
                line[x] = (line[x] + a) & 255
            elif method == 2:
                line[x] = (line[x] + b) & 255
            elif method == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif method == 4:
                p = a + b - c
                line[x] = (line[x] + min((abs(p - a), 0, a), (abs(p - b), 1, b), (abs(p - c), 2, c))[2]) & 255
        out += line if size == 4 else b"".join(line[x:x + 3] + b"\xff" for x in range(0, stride, 3))
        previous = line
    return width, height, bytes(out)


def home_screen(run: Run) -> None:
    """Add to Home Screen at the app's root and at a task's address, through altd's native walk. The sheet offers the
    title Altitude as a web app; the Home Screen icon iOS stored shows the approved Climb icon pixel for pixel; the clip
    opens the manifest's start address full screen; and the icon opens Altitude on its own, without Safari, where it
    pairs with a code from the fixture service as a Home Screen app."""
    approved = (REPO / "web" / "public" / "apple-touch-icon.png").read_bytes()
    for label, path in (("root", "/"), ("task", run.task)):
        prefix = f"home-{label}"
        apps = [d for d in api(run, "GET", "/api/devices")["devices"] if d["name"] == "Home Screen app on iPhone"]
        code = api(run, "POST", "/api/devices/code")["code"]
        record, rows, files = walked(run, "home-screen", run.url + path, prefix, HOME_WALK, code)
        seen, clip, icon = record.get("seen", {}), record.get("clip", {}), files.get("web-clip-icon.png")
        reached = {row["step"] for row in rows if row.get("status") == "completed"}
        if "home-screen" in reached:
            failures = [f"{what} is {value!r}" for what, value, wanted in (
                ("the sheet's title", seen.get("sheet title"), "Altitude"),
                ("Open as Web App", seen.get("open as web app"), "1"), ("the clip's title", clip.get("Title"), "Altitude"),
                ("the clip's address", clip.get("URL"), run.url + "/"),
                ("the clip's full screen", clip.get("FullScreen"), True)) if value != wanted]
            try:
                if not icon or pixels(icon) != pixels(approved):
                    failures.append("the Home Screen icon's pixels differ from the approved icon" if icon else
                                    "iOS stored no Home Screen icon")
            except ValueError as exc:
                failures.append(f"the Home Screen icon cannot be read: {exc}")
            rows.append(check("web-clip", failures, "the title Altitude as a web app, the approved icon pixel for "
                                                    "pixel, and the manifest's start address"))
        else:
            rows.append(unreached("web-clip"))
        if "paired" in reached:
            added = len([d for d in api(run, "GET", "/api/devices")["devices"]
                         if d["name"] == "Home Screen app on iPhone"]) - len(apps)
            rows.append(check("standalone-pairing", [] if seen.get("safari") == "not in front" and added == 1 else
                              [f"Safari {seen.get('safari')}; {added} new Home Screen app device(s)"],
                              "the app paired as a Home Screen app on iPhone, with Safari not in front"))
        else:
            rows.append(unreached("standalone-pairing"))
        finish(run, prefix, rows, f"Add to Home Screen at {path}: the icon, title and standalone app",
               clip=clip, icon={"approved sha256": hashlib.sha256(approved).hexdigest(),
                                "stored sha256": icon and hashlib.sha256(icon).hexdigest()})


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


def secure_page(nonce: str) -> bytes:
    return (f'<!doctype html><meta name="viewport" content="width=device-width"><title>{nonce}</title>'
            "<h1>HTTPS without a warning</h1><p>Served with an Altitude certificate.</p>").encode()


def load_secure(safari: Safari, secure: Secure, nonce: str, what: str) -> None:
    """Safari shows the page `secure` serves as a secure context: no certificate warning stands in its place."""
    try:
        safari.wait(f"location.href === {json.dumps(secure.origin + '/')} && document.title === {json.dumps(nonce)}"
                    " && isSecureContext", what)
    except TimeoutError as exc:
        raise RuntimeError(f"{exc}; the server refused {secure.refused}") from None


def https(run: Run) -> None:
    """A page served over HTTPS with the identity altd made with Altitude's certificate generator and trusted in the
    phone ($SIMULATOR_HTTPS), through the serving context Altitude's server loads: Safari loads it as a secure
    context, with no certificate warning. As a control, a fetch from a second identity of the same generator, which the
    phone does not trust, must be refused for its certificate, with Safari's message about it and a certificate alert at
    the server, so a pass shows that Safari checked the chain."""
    safari, folder, nonce = run.safari, Path(os.environ["SIMULATOR_HTTPS"]), uuid.uuid4().hex
    with tempfile.TemporaryDirectory() as other:
        tls.fixture(Path(other) / "untrusted")
        untrusted = Secure(tls._load(Path(other) / "untrusted"), secure_page(nonce))
    trusted = Secure(tls._load(folder), secure_page(nonce))

    def fetched(url: str) -> tuple[str, list[str]]:
        """How the current page's fetch of `url` ended, and what Safari's console said about that request."""
        safari.evaluate(f"window.__fetched = 'pending'; fetch({json.dumps(url)}, {{mode: 'no-cors'}})"
                        ".then(() => window.__fetched = 'loaded', (e) => window.__fetched = String(e)); 0")
        outcome = safari.wait("window.__fetched !== 'pending' && window.__fetched", f"the fetch of {url}")
        end = time.monotonic() + 1   # Safari's console messages about it arrive beside the answer
        while time.monotonic() < end:
            safari.pump()
        return outcome, [m.get("text", "") for m in safari.console if m.get("url") == url]

    try:
        safari.evaluate(f"location.href = {json.dumps(trusted.origin + '/')}; 0")
        load_secure(safari, trusted, nonce, "the HTTPS page, without a certificate warning")
        safari.snapshot(run.results / "05-https.png")
        control, said = fetched(untrusted.origin + "/fetch")
        if control == "loaded" or untrusted.requests or not said \
                or not any(CERTIFICATE_ALERT.search(refusal) for refusal in untrusted.refused):
            raise RuntimeError(f"Safari did not refuse an untrusted certificate as one: {control}; Safari: {said}; "
                               f"the server answered {untrusted.requests} and refused {untrusted.refused}")
    finally:
        trusted.close()
        untrusted.close()
    run.steps.append({"step": "05-https", "url": trusted.origin + "/", "served": tls.details(folder / "server.crt")["sha256"],
                      "control": {"url": untrusted.origin + "/fetch", "fetch": control, "safari": said,
                                  "server": untrusted.refused},
                      "reached": "an HTTPS page with the run's trusted Altitude certificate, without a warning; an "
                                 "untrusted one refused"})


def profile(run: Run) -> None:
    """The device setup page, as `alt tls-share` offers it, for a new CA from Altitude's certificate generator whose key
    is gone: it names the CA and its SHA-256. Then altd's native walk takes the profile through Safari and Settings:
    Download the profile, Allow, Settings' Profile Downloaded, the profile's details, Install and full trust under
    Certificate Trust Settings. The name and SHA-256 Settings shows must be the CA's, the CA Settings trusts must be
    it, and a page served with that CA's server certificate then loads in Safari without a warning. The phone never
    trusted this CA before, as the https step's control shows for another of the generator's CAs."""
    safari, nonce, checking = run.safari, uuid.uuid4().hex, None
    with tempfile.TemporaryDirectory() as folder:
        identity = Path(folder) / "profile"
        tls.fixture(identity)
        ca, authority, context = (identity / "ca.crt").read_bytes(), tls.identity(identity / "ca.crt"), tls._load(identity)
    sent = threading.Event()
    share = tls.Share({"name": "127.0.0.1", "kind": "IP", "url": run.url}, ca, authority, SHARE_MINUTES,
                      sent=lambda message: message.startswith("Sent the profile") and sent.set())
    secure = Secure(context, secure_page(nonce))
    try:
        safari.evaluate(f"location.href = {json.dumps(share.link + '#ios')}; 0")
        rows = tls.fingerprint_rows(authority["sha256"])
        safari.wait(f"document.body && [{json.dumps(authority['name'])}, ...{json.dumps(rows)}]"
                    ".every((text) => document.body.innerText.includes(text))", "the setup page with the CA's SHA-256")
        safari.snapshot(run.results / "06-setup.png")
        run.steps.append({"step": "06-setup", "url": share.link, "ca": authority["name"], "sha256": authority["sha256"],
                          "reached": "the setup page names the CA and its SHA-256"})
        record, rows, _ = walked(run, "profile", share.link + "#ios", "profile", PROFILE_WALK)
        seen, fingerprint = record.get("seen", {}), authority["sha256"].replace(":", "").lower()
        reached = {row["step"] for row in rows if row.get("status") == "completed"}
        failures = []
        if "certificate" in reached:
            shown = seen.get("certificate", "").splitlines()
            if authority["name"] not in seen.get("profile", "").splitlines():
                failures.append(f"Install Profile does not name {authority['name']}")
            if authority["name"] not in shown:
                failures.append(f"the certificate's details do not name {authority['name']}")
            if not any(re.sub(r"[^0-9a-f]", "", line.lower()) == fingerprint for line in shown):
                failures.append("the certificate's SHA-256 is not the served CA's")
            if not sent.is_set():
                failures.append("the setup page's server sent no profile")
        rows.insert(PROFILE_WALK.index("certificate") + 1, check(
            "verify", failures, f"the profile and its certificate are {authority['name']}, SHA-256 "
                                f"{authority['sha256']}") if "certificate" in reached else unreached("verify"))
        if "trust" in reached:
            failures = [] if seen.get("trusted") == authority["name"] else [f"Settings trusted {seen.get('trusted')}"]
            if not failures:
                # The walk closed Safari; a new connection inspects the page it opens.
                checking = Safari(run.inspector)
                try:
                    checking.open(secure.origin + "/")
                    checking.attach(secure.origin)
                    load_secure(checking, secure, nonce, "the page signed by the profile's CA, without a certificate "
                                                         "warning")
                    checking.snapshot(run.results / "profile-https.png")
                except FAILURES as exc:
                    failures.append(str(exc))
            rows.append(check("https", failures, f"{secure.origin}/ loads as a secure context"))
        else:
            rows.append(unreached("https"))
        finish(run, "profile", rows, "the profile downloaded, installed and trusted in Settings, then HTTPS without a "
                                     "warning", ca=authority["name"], sha256=authority["sha256"])
    finally:
        share.close()
        secure.close()
        if checking:
            checking.sock.close()


def selection(text: str) -> list[str]:
    chosen = [name for name in re.split(r"[\s,]+", text) if name]
    unknown = sorted(set(chosen) - set(STEPS))
    if unknown or not chosen:
        raise argparse.ArgumentTypeError(f"choose steps from {', '.join(STEPS)}, not {', '.join(unknown) or 'none'}")
    return [name for name in STEPS if name in chosen]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", nargs="?", type=Path,
                        default=Path(os.environ.get("VALIDATION_RESULTS", REPO / "web" / "test-results")) / "simulator")
    parser.add_argument("--steps", type=selection, default=list(STEPS),
                        help=f"the steps to run, separated by spaces or commas; they run in this order: {' '.join(STEPS)}")
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
    record = {"selected": args.steps, "steps": [], "error": None}
    safari, left = None, False
    steps = {"navigation": navigation, "dictation": lambda run: run.steps.extend(
                 dictation(run.safari, run.url, run.work, run.task, run.results)),
             "https": https, "profile": profile, "home-screen": home_screen}
    try:
        ready = json.loads(service.stdout.readline() or "{}")
        if not ready.get("disposable"):
            raise RuntimeError("the fixture service did not start; see service.log")
        safari = Safari(inspector)
        run = Run(inspector, safari, ready["url"], ready["device"], args.results, steps=record["steps"])
        try:
            setup(run)
            record["browser"] = safari.evaluate(
                "({userAgent: navigator.userAgent, viewport: [innerWidth, innerHeight], "
                "devicePixelRatio, speechRecognition: typeof webkitSpeechRecognition})")
            for name in args.steps:
                if name in ("https", "profile", "home-screen") and not left:
                    # Leave the app, so its change stream ends before the service stops; the native walks close Safari.
                    leave(safari, "about:blank")
                    safari.wait("location.href === 'about:blank'", "a blank page")
                    left = True
                steps[name](run)
        except FAILURES as exc:
            record["error"] = str(exc)
        if not left:
            try:
                leave(safari, "about:blank")
                safari.wait("location.href === 'about:blank'", "a blank page")
            except FAILURES as exc:
                record["cleanup"] = str(exc)
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
    controls = {step["control"]["url"] for step in record["steps"] if "control" in step}
    # Safari's refusal of the HTTPS step's control request is expected; every other error fails the walkthrough.
    errors = [m for m in console if m.get("level") == "error" and m.get("url") not in controls]
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
        for row in step.get("walk", []):
            why = f" ({row['reason']})" if row.get("reason") else ""
            print(f"ios_simulator:   {row['step']}: {row.get('status')}{why}")
    print(f"ios_simulator: {record.get('browser', {}).get('userAgent', 'no browser')}")
    if record["error"]:
        print(f"ios_simulator: failed: {record['error']}", file=sys.stderr)
        return 1
    print(f"ios_simulator: walkthrough passed; evidence in {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
