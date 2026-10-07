"""A disposable iOS Simulator iPhone for one macOS validation run, and the relay through which the run reaches only
that phone's Safari.

altd drives Apple's Simulator service; a validation run is refused it (`platform.SERVICE_ESCAPES`), since a booted
device runs any program its clients ask for as the operator's account. Each run's phone lives in a private device set
in the runner's part of the run area, never in the operator's own devices. The set is the record of what to remove, so
removal after the run, or at the next start after an interruption, needs no device names.
The run reaches the phone's Web Inspector only through a Unix socket in its own temporary folder. The relay passes
Safari's web pages and nothing else on the phone: other inspectable processes are hidden, any other request closes the
connection, and a page whose address is on Altitude's port is hidden and closes a connection attached to it. Its one
request of its own opens an http(s) loopback address in Safari, the way a run puts its first page on the phone.
See docs/DEVELOPMENT.md#ios-simulator-runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import socket
import struct
import subprocess
import threading
from urllib.parse import urlsplit

from . import platform

XCRUN = "/usr/bin/xcrun"
SAFARI = "com.apple.mobilesafari"
BOOT_SECONDS = 300
FRAME_LIMIT = 64 << 20            # bytes in one inspector message; a page snapshot is a few MiB
OPEN = "_rpc_altitudeOpenURL:"    # the relay's own request: {"url": ...}, answered by OPENED with {"error": ...}
OPENED = "_rpc_altitudeOpenedURL:"
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
    """The run's iPhone in the private device set `devices`."""

    def __init__(self, devices: Path, chosen: dict):
        self.devices, self.chosen, self.udid = devices, chosen, None

    def boot(self) -> str:
        """Create and boot the phone headless; return its Web Inspector socket."""
        self.devices.mkdir(mode=0o700)
        self.udid = _simctl(self.devices, "create", "altitude-validation", self.chosen["device_id"],
                            self.chosen["runtime_id"]).strip()
        _simctl(self.devices, "bootstatus", self.udid, "-b", timeout=BOOT_SECONDS)
        return _simctl(self.devices, "getenv", self.udid, "RWI_LISTEN_SOCKET").strip()

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

    def screenshot(self, path: Path) -> str | None:
        """The whole screen as `path`; returns why there is none, or None."""
        try:
            _simctl(self.devices, "io", self.udid, "screenshot", str(path), timeout=60)
            return None
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return str(exc)


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
    head = _exact(sock, 4)
    if head is None:
        return None
    size = struct.unpack(">I", head)[0]
    if size > FRAME_LIMIT:
        raise ValueError(f"an inspector message of {size} bytes is over the limit")
    body = _exact(sock, size)
    message = plistlib.loads(body or b"")
    if (not isinstance(message, dict) or not isinstance(message.get("__selector"), str)
            or not isinstance(message.get("__argument"), dict)):
        raise ValueError("not an inspector message")
    return message


def _exact(sock: socket.socket, size: int) -> bytes | None:
    data = b""
    while len(data) < size:
        chunk = sock.recv(min(size - len(data), 1 << 20))
        if not chunk:
            if data:
                raise ConnectionError("the connection closed inside a message")
            return None
        data += chunk
    return data


def _write(sock: socket.socket, selector: str, argument: dict) -> None:
    data = plistlib.dumps({"__selector": selector, "__argument": argument}, fmt=plistlib.FMT_BINARY)
    sock.sendall(struct.pack(">I", len(data)) + data)


class _Session:
    """One run connection and its own connection to the phone: what the phone has shown it, and its senders."""

    def __init__(self, run: socket.socket, phone: socket.socket, port: int, open_url):
        self.run, self.phone, self.port, self.open_url = run, phone, port, open_url
        self.lock = threading.Lock()
        self.apps: set = set()          # Safari's application identifiers
        self.pages: dict = {}           # application -> {page: address}, Safari's web pages only
        self.senders: dict = {}         # sender -> (application, page) set up through this connection

    def serve(self) -> None:
        upstream = threading.Thread(target=self._pump, args=(self.phone, self._from_phone), daemon=True)
        upstream.start()
        self._pump(self.run, self._from_run)
        upstream.join()

    def close(self) -> None:
        for sock in (self.run, self.phone):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _pump(self, source: socket.socket, handle) -> None:
        try:
            while (message := _read(source)) is not None and handle(message["__selector"], message["__argument"]):
                pass
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
        finally:
            self.close()

    def _from_run(self, selector: str, argument: dict) -> bool:
        if selector == OPEN:
            error = openable(argument.get("url"), self.port)
            if not error:
                try:
                    self.open_url(argument["url"])
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    error = str(exc)
            _write(self.run, OPENED, {"error": error or ""})
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
        _write(self.run, selector, argument)
        return True


class Relay:
    """Serves the run's socket at `path` while the run lasts; each connection gets its own connection to the phone's
    Web Inspector at `inspector`."""

    def __init__(self, path: Path, inspector: str, port: int, open_url):
        self.path, self.inspector, self.port, self.open_url = path, inspector, port, open_url
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
            try:
                phone = socket.socket(socket.AF_UNIX)
                phone.connect(self.inspector)
            except OSError:
                run.close()
                continue
            session = _Session(run, phone, self.port, self.open_url)
            with self.lock:
                if self.stopped.is_set():
                    session.close()
                    return
                self.sessions.append(session)
            threading.Thread(target=session.serve, daemon=True).start()

    def close(self) -> None:
        self.stopped.set()
        self.thread.join()
        self.listener.close()
        with self.lock:
            for session in self.sessions:
                session.close()
