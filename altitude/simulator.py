"""A disposable iOS Simulator iPhone for one macOS validation run, and the relay through which the run reaches only
that phone's Safari.

altd drives Apple's Simulator service; a validation run is refused it (`platform.SERVICE_ESCAPES`), since a booted
device runs any program its clients ask for as the operator's account. Each run's phone lives in a private device set
in the runner's part of the run area, never in the operator's own devices. The set is the record of what to remove, so
removal after the run, or at the next start after an interruption, needs no device names.
The run reaches the phone's Web Inspector only through a Unix socket in its own temporary folder. The relay passes
Safari's web pages and nothing else on the phone: other inspectable processes are hidden, any other request closes the
connection, and a page whose address is on Altitude's port is hidden and closes a connection attached to it. Its
own requests open an http(s) loopback address in Safari, the way a run puts its first page on the phone, and walk one
of two fixed native walks at such an address: Add to Home Screen, or a device setup page's profile through Settings.
altd builds the walks (`walks/`, Apple's UI testing) from its own code into the run area, outside the candidate's
folders, and returns each walk's steps and screenshots to the run.
The phone trusts one CA of the run's own, made by Altitude's certificate generator, so the run can serve it HTTPS.
See docs/DEVELOPMENT.md#ios-simulator-runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import re
import selectors
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit

from . import platform, tls

XCRUN = "/usr/bin/xcrun"
SAFARI = "com.apple.mobilesafari"
APPS = (SAFARI, "com.apple.webapp", "com.apple.Preferences")   # what a walk opens, closed when it ends
BOOT_SECONDS = 300
RECORD_STOP = 10                 # seconds for a screen recording to finish its file once asked to stop
FRAME_LIMIT = 64 << 20            # bytes in one inspector message; a page snapshot is a few MiB
FRAME_SECONDS = 30               # for the rest of a message once its first byte has arrived
SESSIONS = 4                     # run connections at once; a walkthrough uses one
OPEN = "_rpc_altitudeOpenURL:"    # the relay's own request: {"url": ...}, answered by OPENED with {"error": ...}
OPENED = "_rpc_altitudeOpenedURL:"
WALK = "_rpc_altitudeWalk:"       # {"walk": a name in WALKS, "url": ..., "code": ...}, answered by WALKED with
WALKED = "_rpc_altitudeWalked:"   # {"error": ..., "walk": its record as JSON, "files": {name: PNG}}
WALKS = {"home-screen": "testHomeScreen", "profile": "testProfile"}
WALK_SOURCE = Path(__file__).resolve().parent / "walks"
BUILD_SECONDS = 600              # for building the walks once per run
WALK_SECONDS = 420               # for one walk; its own waits end well within it
XCODEBUILD_STOP = 60             # seconds for xcodebuild to end its test runner once interrupted
FILE_LIMIT = 16 << 20            # bytes in one screenshot or icon returned to the run
PAIRING = re.compile(r"[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}")   # a pairing code, as access.issue_code makes it
UNAVAILABLE = "--simulator needs a Mac with Xcode and an installed iOS Simulator runtime"

#: What the run may ask of the phone. `_rpc_forward*` requests name a Safari application, and a page or sender the
#: relay has passed; any other request, such as an automation session, closes the connection.
FROM_RUN = {"_rpc_reportIdentifier:", "_rpc_getConnectedApplications:", "_rpc_forwardGetListing:",
            "_rpc_forwardSocketSetup:", "_rpc_forwardSocketData:", "_rpc_forwardDidClose:"}


def _simctl(devices: Path | None, *args: str, timeout: float = 60) -> str:
    command = [XCRUN, "simctl", *(["--set", str(devices)] if devices else []), *args]
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError(f"simctl {args[0]} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def plan() -> dict:
    """The newest available iOS runtime, its newest iPhone and the developer tools' version, chosen by detection;
    raises ValueError naming what is missing."""
    if platform.validation_in_container():
        raise ValueError(UNAVAILABLE)
    try:
        listing = json.loads(_simctl(None, "list", "--json", "runtimes", "devicetypes"))
        xcode = subprocess.run([XCRUN, "xcodebuild", "-version"], capture_output=True, text=True, timeout=30,
                               check=True).stdout
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise ValueError(f"{UNAVAILABLE}: {exc}") from exc
    runtimes = [r for r in listing.get("runtimes", []) if r.get("platform") == "iOS" and r.get("isAvailable")]
    if not runtimes:
        raise ValueError(f"{UNAVAILABLE}: no iOS runtime is installed")
    runtime = max(runtimes, key=lambda r: _version(r.get("version", "")))
    types = {t.get("identifier"): t for t in listing.get("devicetypes", [])}
    phones = [types.get(t.get("identifier"), t) for t in runtime.get("supportedDeviceTypes", [])
              if t.get("productFamily") == "iPhone"]
    if not phones:
        raise ValueError(f"{UNAVAILABLE}: {runtime.get('name')} supports no iPhone")
    # The newest generation, and in it the shortest-named model.
    phone = max(phones, key=lambda t: (t.get("minRuntimeVersion", 0), -len(t.get("name", ""))))
    return {"xcode": " ".join(xcode.split()), "runtime": f"{runtime.get('name')} ({runtime.get('buildversion')})",
            "runtime_id": runtime["identifier"], "device": phone.get("name"), "device_id": phone["identifier"]}


class Phone:
    """The run's iPhone in the private device set `devices`, with its walks built and run in the private folder
    `walks`."""

    def __init__(self, devices: Path, chosen: dict, walks: Path):
        self.devices, self.chosen, self.walks, self.udid = devices, chosen, walks, None
        self.walking, self.tests, self.count = threading.Lock(), None, 0

    def boot(self) -> str:
        """Create and boot the phone headless; return its Web Inspector socket. The steps share BOOT_SECONDS, so one
        that a busy host slows can use the time the others left (#724)."""
        self.devices.mkdir(mode=0o700)
        deadline = time.monotonic() + BOOT_SECONDS

        def left() -> float:
            return max(1.0, deadline - time.monotonic())
        self.udid = _simctl(self.devices, "create", "altitude-validation", self.chosen["device_id"],
                            self.chosen["runtime_id"], timeout=left()).strip()
        _simctl(self.devices, "bootstatus", self.udid, "-b", timeout=left())
        return _simctl(self.devices, "getenv", self.udid, "RWI_LISTEN_SOCKET", timeout=left()).strip()

    def trust(self, folder: Path) -> dict:
        """Make the run's HTTPS identity in `folder` with Altitude's own generator and trust its CA as a root in the
        phone, as the operator's iPhone trusts theirs. The CA's key is gone before the run starts. Returns the
        certificates' details."""
        tested = tls.fixture(folder)
        _simctl(self.devices, "keychain", self.udid, "add-root-cert", str(folder / "ca.crt"))
        return tested

    def safari(self) -> str:
        """Safari's version on the phone, from its bundle."""
        try:
            info = _simctl(self.devices, "appinfo", self.udid, SAFARI)
            bundle = re.search(r'^\s*Path = "?([^";\n]+)"?;', info, re.M)
            data = plistlib.loads((Path(bundle.group(1)) / "Info.plist").read_bytes()) if bundle else {}
            return " ".join(filter(None, (data.get("CFBundleShortVersionString"),
                                          data.get("CFBundleVersion") and f"({data['CFBundleVersion']})"))) or "unknown"
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            return f"unknown: {exc}"

    def open(self, url: str) -> None:
        _simctl(self.devices, "openurl", self.udid, url, timeout=30)

    def walk(self, name: str, url: str, code: str = "") -> tuple[dict, dict]:
        """Walk `name` (WALKS) at `url` with Apple's UI testing, typing the pairing `code` into the Home Screen app when
        one is given. Returns the walk's record, its steps and what it saw, with an `error` when the runner failed or
        did not finish, and its screenshots by name; the Home Screen walk adds the web clip it made. The apps the walk
        used are closed after it, so none keeps a connection to the run's pages."""
        with self.walking:
            tests = self._build()
            self.count += 1
            folder = self.walks / f"{self.count}-{name}"
            (folder / "files").mkdir(parents=True)
            clips = self.devices / self.udid / "data" / "Library" / "WebClips"
            before = set(os.listdir(clips)) if clips.is_dir() else set()
            env = {**os.environ, "TEST_RUNNER_WALK_URL": url, "TEST_RUNNER_WALK_CODE": code,
                   "TEST_RUNNER_WALK_OUT": str(folder / "files")}
            failed = _xcodebuild(["test-without-building", "-xctestrun", str(tests), "-destination",
                                  f"platform=iOS Simulator,id={self.udid}", f"-only-testing:Walks/Walks/{WALKS[name]}",
                                  "-parallel-testing-enabled", "NO", "-resultBundlePath", str(folder / "result.xcresult"),
                                  f"-DVTSimulatorSetLocation={self.devices}"], folder / "xcodebuild.log", WALK_SECONDS,
                                 env)
            for app in APPS:
                try:
                    _simctl(self.devices, "terminate", self.udid, app, timeout=30)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    pass   # not running
            try:
                record = json.loads((folder / "files" / "walk.json").read_text())
            except (OSError, ValueError):
                record = {"steps": [], "seen": {}}
            if failed or not (record.get("seen", {}).get("finished") or record.get("seen", {}).get("stopped")):
                record["error"] = failed or "the walk's runner ended before its walk did"
            files = {path.name: path.read_bytes() for path in sorted((folder / "files").glob("*.png"))
                     if path.stat().st_size <= FILE_LIMIT}
            made = sorted(set(os.listdir(clips)) - before) if clips.is_dir() else []
            if made:
                clip = clips / made[-1]
                info = plistlib.loads((clip / "Info.plist").read_bytes()) if (clip / "Info.plist").is_file() else {}
                record["clip"] = {key: info[key] for key in ("Title", "URL", "FullScreen")
                                  if isinstance(info.get(key), (str, bool))}
                if (clip / "icon.png").is_file() and (clip / "icon.png").stat().st_size <= FILE_LIMIT:
                    files["web-clip-icon.png"] = (clip / "icon.png").read_bytes()
            return record, files

    def _build(self) -> Path:
        """The walks' test run file, built at the first walk from altd's own copy of `walks/` in the private folder,
        since xcodebuild writes beside the project it builds."""
        if not self.tests:
            source = self.walks / "source"
            shutil.copytree(WALK_SOURCE, source)
            failed = _xcodebuild(["build-for-testing", "-project", str(source / "Walks.xcodeproj"), "-scheme", "Walks",
                                  "-destination", "generic/platform=iOS Simulator", "-derivedDataPath",
                                  str(self.walks / "build")], self.walks / "build.log", BUILD_SECONDS)
            found = sorted((self.walks / "build" / "Build" / "Products").glob("*.xctestrun"))
            if failed or not found:
                raise RuntimeError(f"the walks did not build: {failed or 'xcodebuild made no test run file'}")
            self.tests = found[0]
        return self.tests

    def record(self, path: Path) -> Recording:
        """Start recording the whole screen as the video `path`."""
        return Recording([XCRUN, "simctl", "--set", str(self.devices), "io", self.udid, "recordVideo",
                          "--codec=h264", "--force", str(path)], path.with_suffix(".log"))

    def screenshot(self, path: Path) -> str | None:
        """The whole screen as `path`; returns why there is none, or None."""
        try:
            _simctl(self.devices, "io", self.udid, "screenshot", str(path), timeout=60)
            return None
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return str(exc)


class Recording:
    """A screen recording until `stop()`, with simctl's own messages in `log`."""

    def __init__(self, command: list[str], log: Path):
        self.log = log
        with open(log, "wb") as out:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)

    def stop(self) -> str | None:
        """End the recording, forcefully after `RECORD_STOP` seconds; returns why there is no video, or None. It never
        raises, so a recording cannot change its run's outcome."""
        try:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
            try:
                code = self.process.wait(RECORD_STOP)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                return f"the screen recording did not stop within {RECORD_STOP} s"
            if code:
                tail = self.log.read_text(errors="replace").strip()[-400:] if self.log.is_file() else ""
                return f"simctl recordVideo failed: {tail or f'exit {code}'}"
        except OSError as exc:
            return f"the screen recording did not stop: {exc}"
        return None


def _xcodebuild(args: list[str], log: Path, seconds: float, env: dict | None = None) -> str | None:
    """xcodebuild with its output in `log`; returns why it failed, or None. Past `seconds` it is interrupted as Ctrl-C
    does, so it ends its test runner itself, and is killed only when it does not."""
    with open(log, "wb") as out:
        process = subprocess.Popen([XCRUN, "xcodebuild", *args], stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=subprocess.STDOUT, env=env)
    try:
        code = process.wait(seconds)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(XCODEBUILD_STOP)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return f"xcodebuild did not finish within {seconds} s"
    if code:
        said = [line.strip() for line in log.read_text(errors="replace").splitlines()
                if re.search(r"error|crash|fail", line, re.I)]
        return f"xcodebuild exit {code}: {' | '.join(said[-4:])[-600:] or 'no error lines'}"
    return None


def remove(devices: Path) -> str | None:
    """Shut down and delete every device in the set `devices`, then the set. Returns why it stays, or None."""
    if not os.path.lexists(devices):
        return None
    errors = []
    for args in (("shutdown", "all"), ("delete", "all")):
        try:
            _simctl(devices, *args, timeout=120)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
    try:
        left = [d for group in json.loads(_simctl(devices, "list", "--json", "devices"))["devices"].values()
                for d in group]
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        return f"cannot confirm that the Simulator devices in {devices} are gone: {exc}"
    if left:
        return f"{len(left)} Simulator device(s) remain in {devices}: {'; '.join(errors) or 'still listed'}"
    failures = []
    shutil.rmtree(devices, onexc=lambda _call, entry, exc: failures.append(f"{entry}: {exc}"))
    return f"could not remove {failures[0]}" if failures and os.path.lexists(devices) else None


def _port(url: str) -> int | None:
    parts = urlsplit(url)
    return parts.port or {"http": 80, "https": 443}.get(parts.scheme)


def _altitudes(url: object, port: int) -> bool:
    """Whether a page's address is on Altitude's port, or cannot be read."""
    try:
        return not isinstance(url, str) or _port(url) == port
    except ValueError:
        return True


def walkable(argument: dict, port: int) -> str | None:
    """Why the relay will not walk `argument`, or None: one of WALKS at an address it would open, with a pairing code
    only for the Home Screen app."""
    name, code = argument.get("walk"), argument.get("code", "")
    if not isinstance(name, str) or name not in WALKS:
        return f"the relay walks only {' and '.join(WALKS)}"
    if not isinstance(code, str) or code and (name != "home-screen" or not PAIRING.fullmatch(code)):
        return "a walk takes only a pairing code, for the Home Screen app"
    return openable(argument.get("url"), port)


def openable(url: object, port: int) -> str | None:
    """Why the relay will not open `url` in Safari, or None: only http(s) on this Mac's loopback, never Altitude's port."""
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
        if (not parts or parts.scheme not in ("http", "https") or parts.hostname not in ("127.0.0.1", "localhost", "::1")
                or parts.username or parts.password or not parts.port):
            return "the relay opens only http(s) addresses with a port on this Mac's loopback"
        return "the relay never opens Altitude's own port" if parts.port == port else None
    except ValueError:
        return "the relay cannot read that address"


def _read(sock: socket.socket) -> dict | None:
    """One message, or None when the peer closed the connection."""
    first = _exact(sock, 1)
    if first is None:
        return None
    deadline = time.monotonic() + FRAME_SECONDS
    size = struct.unpack(">I", first + (_exact(sock, 3, deadline) or b""))[0]
    if size > FRAME_LIMIT:
        raise ValueError(f"an inspector message of {size} bytes is over the limit")
    body = _exact(sock, size, deadline)
    message = plistlib.loads(body or b"")
    if (not isinstance(message, dict) or not isinstance(message.get("__selector"), str)
            or not isinstance(message.get("__argument"), dict)):
        raise ValueError("not an inspector message")
    return message


def _exact(sock: socket.socket, size: int, deadline: float | None = None) -> bytes | None:
    data = bytearray()
    waiting = selectors.DefaultSelector() if deadline else None  # kqueue or epoll: altd can pass select()'s limit
    try:
        if waiting:
            waiting.register(sock, selectors.EVENT_READ)
        while len(data) < size:
            if waiting and not waiting.select(max(0, deadline - time.monotonic())):
                raise TimeoutError("an inspector message stalled")
            chunk = sock.recv(min(size - len(data), 1 << 20))
            if not chunk:
                if data:
                    raise ConnectionError("the connection closed inside a message")
                return None
            data += chunk
    finally:
        if waiting:
            waiting.close()
    return bytes(data)


def _write(sock: socket.socket, selector: str, argument: dict) -> None:
    data = plistlib.dumps({"__selector": selector, "__argument": argument}, fmt=plistlib.FMT_BINARY)
    sock.sendall(struct.pack(">I", len(data)) + data)


class _Session:
    """One run connection and its own connection to the phone: what the phone has shown it, and its senders."""

    def __init__(self, run: socket.socket, phone: socket.socket, port: int, open_url, walk):
        self.run, self.phone, self.port, self.open_url, self.walk = run, phone, port, open_url, walk
        self.lock = threading.Lock()
        self.sending = threading.Lock()  # both directions' threads write to the run
        self.apps: set = set()          # Safari's application identifiers
        self.pages: dict = {}           # application -> {page: address}, Safari's web pages only
        self.senders: dict = {}         # sender -> (application, page) set up through this connection

    def serve(self) -> None:
        upstream = threading.Thread(target=self._pump, args=(self.phone, self._from_phone), daemon=True)
        upstream.start()
        self._pump(self.run, self._from_run)
        upstream.join()
        with self.lock:  # only once neither pump uses them, so no pump waits on or reaches a reused descriptor
            self.run.close()
            self.phone.close()

    def end(self) -> None:
        """Ends both directions: a pump waiting on either socket wakes, and `serve` then closes them."""
        with self.lock:
            for sock in (self.run, self.phone):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:  # already ended or closed
                    pass

    def _to_run(self, selector: str, argument: dict) -> None:
        with self.sending:
            _write(self.run, selector, argument)

    def _pump(self, source: socket.socket, handle) -> None:
        try:
            while (message := _read(source)) is not None and handle(message["__selector"], message["__argument"]):
                pass
        except Exception:  # anything malformed or failing ends the connection
            pass
        finally:
            self.end()

    def _from_run(self, selector: str, argument: dict) -> bool:
        if selector == OPEN:
            error = openable(argument.get("url"), self.port)
            if not error:
                try:
                    self.open_url(argument["url"])
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    error = str(exc)
            self._to_run(OPENED, {"error": error or ""})
            return True
        if selector == WALK:
            error, answer = walkable(argument, self.port), {}
            if not error:
                try:
                    record, files = self.walk(argument["walk"], argument["url"], argument.get("code", ""))
                    answer = {"walk": json.dumps(record), "files": files}
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                    error = str(exc)
            self._to_run(WALKED, {"error": error or "", **answer})
            return True
        if selector not in FROM_RUN:
            return False
        with self.lock:
            app, page, sender = (argument.get(key) for key in (
                "WIRApplicationIdentifierKey", "WIRPageIdentifierKey", "WIRSenderKey"))
            if selector.startswith("_rpc_forward") and app not in self.apps:
                return False
            if selector == "_rpc_forwardSocketSetup:":
                if str(page) not in {str(p) for p in self.pages.get(app, {})}:
                    return False
                self.senders[sender] = (app, str(page))
            elif selector in ("_rpc_forwardSocketData:", "_rpc_forwardDidClose:"):
                if self.senders.get(sender) != (app, str(page)):
                    return False
                if selector == "_rpc_forwardDidClose:":
                    self.senders.pop(sender)
        _write(self.phone, selector, argument)
        return True

    def _from_phone(self, selector: str, argument: dict) -> bool:
        with self.lock:
            if selector == "_rpc_reportConnectedApplicationList:":
                apps = argument.get("WIRApplicationDictionaryKey") or {}
                self.apps = {key for key, app in apps.items() if isinstance(app, dict)
                             and app.get("WIRApplicationBundleIdentifierKey") == SAFARI}
                argument = {**argument, "WIRApplicationDictionaryKey": {key: apps[key] for key in self.apps}}
            elif selector in ("_rpc_applicationConnected:", "_rpc_applicationUpdated:"):
                if argument.get("WIRApplicationBundleIdentifierKey") != SAFARI:
                    return True
                self.apps.add(argument.get("WIRApplicationIdentifierKey"))
            elif selector == "_rpc_applicationDisconnected:":
                if argument.get("WIRApplicationIdentifierKey") not in self.apps:
                    return True
                self.apps.discard(argument.get("WIRApplicationIdentifierKey"))
            elif selector == "_rpc_applicationSentListing:":
                app = argument.get("WIRApplicationIdentifierKey")
                if app not in self.apps:
                    return True
                listing = argument.get("WIRListingKey") or {}
                pages = {key: page for key, page in listing.items() if isinstance(page, dict)
                         and page.get("WIRTypeKey") == "WIRTypeWebPage"
                         and not _altitudes(page.get("WIRURLKey"), self.port)}
                if any(a == app and p not in {str(k) for k in pages} for a, p in self.senders.values()):
                    return False  # a page this connection inspects is now on Altitude's port, or gone
                self.pages[app] = {str(key): page.get("WIRURLKey") for key, page in pages.items()}
                argument = {**argument, "WIRListingKey": pages}
            elif selector == "_rpc_applicationSentData:":
                destination = self.senders.get(argument.get("WIRDestinationKey"))
                if not destination or destination[0] != argument.get("WIRApplicationIdentifierKey"):
                    return True
            elif selector != "_rpc_reportCurrentState:":
                return True
        self._to_run(selector, argument)
        return True


class Relay:
    """Serves the run's socket at `path` while the run lasts; each connection gets its own connection to the phone's
    Web Inspector at `inspector`. `open_url(url)` and `walk(name, url, code)` answer the run's own requests."""

    def __init__(self, path: Path, inspector: str, port: int, open_url, walk):
        self.path, self.inspector, self.port, self.open_url, self.walk = path, inspector, port, open_url, walk
        self.sessions: list[_Session] = []
        self.lock = threading.Lock()
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(path))
        self.listener.listen()
        self.listener.settimeout(0.5)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        while not self.stopped.is_set():
            try:
                run, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            run.settimeout(None)
            with self.lock:
                full = len(self.sessions) >= SESSIONS
            if full:
                run.close()
                continue
            try:
                phone = socket.socket(socket.AF_UNIX)
                phone.connect(self.inspector)
            except OSError:
                run.close()
                continue
            session = _Session(run, phone, self.port, self.open_url, self.walk)
            with self.lock:
                if self.stopped.is_set():
                    run.close()
                    phone.close()
                    return
                self.sessions.append(session)
            threading.Thread(target=self._serve, args=(session,), daemon=True).start()

    def _serve(self, session: _Session) -> None:
        try:
            session.serve()
        finally:
            with self.lock:
                self.sessions.remove(session)

    def close(self) -> None:
        self.stopped.set()
        self.thread.join()
        self.listener.close()
        with self.lock:
            for session in self.sessions:
                session.end()
