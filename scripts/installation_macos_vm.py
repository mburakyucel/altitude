#!/usr/bin/env python3
"""Run the one-command installation on a fresh macOS guest on this Mac, through Apple's Virtualization framework.

    python3 scripts/installation_macos_vm.py image [--step]
    python3 scripts/installation_macos_vm.py run RESULTS_DIR [--source REF] [--phase PHASE]
    python3 scripts/installation_macos_vm.py run RESULTS_DIR --published BASELINE[..CANDIDATE] [--source REF]
    python3 scripts/installation_macos_vm.py clean

`image` builds the guests once, from Apple's restore image for the newest macOS this Mac supports: `fresh`, a new
installation whose one administrator account (newcomer) logs in automatically, and `prerequisites`, a copy to which
the guest itself added Apple's command line tools, Homebrew and Homebrew's python@3.12 and openssl@3. `--step` does
only the next step (each finishes within ten minutes) and says what comes next. The restore image is deleted once
`fresh` is built.

`run` builds a release from one committed revision (default HEAD) and, for each phase, boots a copy-on-write clone of
an image, checks that its network reaches the internet and a listener on this Mac, unplugs the network card and checks
that neither answers, then runs the release's install.sh through its public `curl … | sh` command against a server on
the guest's loopback that answers for github.com. `fresh` must stop for the missing Python; `prerequisites` must stop
for missing OpenSSL 3 and, for a second account with no desktop session, for the missing session. With Homebrew's
OpenSSL installed, installation and service startup must succeed while stock LibreSSL remains first on PATH.
Each refused attempt must leave the account's files and launchd jobs as they were. `privacy` runs the revision's own
platform_probe.py confinement rows as the logged-in account in a guest whose Python was never asked for access to a
private place; `jobs` runs its launchd job rows, Stop of every descendant and the time limits among them, which no
worker sandbox can; `read-only-home` runs the revision's suite cases that confine a child with Seatbelt or, with
this Mac's Codex CLI copied in, Codex's own sandbox, which no validation run or worker can start; `mutation`
interrupts two mutation runs, whose cleanup lists processes with `ps`, from packages downloaded on this Mac;
`mutation-complete`, run only when named, makes one whole run of the task door, which must end with its report and
leave no scratch folder.
`source` installs from a source checkout cloned from an origin in the guest: the first start must create Altitude's own copy of its
repository and run from it, and a newer main pushed to the origin must deploy through that copy. `workers`, `grants`,
`incident`, `update`, `terminal` and `session` install the release into the account's own home with a scripted fictional
engine and the tests' fake GitHub CLI, then drive the guest's own service through the revision's
tests/macos_guest_runtime.py rows; `update` and `terminal` first trust Altitude's authority on the desktop for Safari,
whose page those rows read. `online`, run only when named, is the one phase whose guest keeps its network: the guest downloads
Homebrew's python@3.13 and Google Chrome and Firefox from their publishers, installs the latest published release
through the public command from GitHub itself, whose service must run on Python 3.13, and Chrome and Firefox must
refuse Altitude's address before `alt trust` on the desktop and open it after.
`--published` downloads the published baseline, and the candidate when it names one, on this Mac, checked against
their SHA256SUMS and tags; without a published candidate it builds one from the source revision under the baseline's
next minor version. It runs only `published`: after an outside process writes caches into the installed baseline, the
baseline's own daemon must install the candidate and keep the state made through the app. The clone is deleted
afterwards, also after a failure or a stop. RESULTS_DIR/macos-vm.json records the outcome.

The host reaches a guest only through the Virtualization framework's host-guest socket, relayed to the guest's sshd.
Everything lives in ~/.cache/altitude-installation-vm/macos; nothing runs as root on this Mac. One guest runs at a
time, and every step stops when less than 10 GiB of disk would stay free. Virtual machines cannot start inside a
worker sandbox: run this in a terminal, or in a task with `alt task run` under an operator grant. `clean` deletes
the images, the helper and any leftover clone.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parent))
from scripts.installation_vm import next_minor, published, release_version, repository  # noqa: E402 (shared with the Linux lane)

CACHE = Path.home() / ".cache/altitude-installation-vm/macos"
HELPER = CACHE / "bin/macos_vm"
IMAGES, RESTORE, RUNS = CACHE / "images", CACHE / "restore", CACHE / "runs"
KEY = IMAGES / "id_ed25519"
ACCOUNT, ACCOUNT_UID = "newcomer", 501
SECOND = "second"
SERVICE_LABEL = "dev.altitude.altd"  # the LaunchAgent of an installation in the account's own home
CPUS, MEMORY_GIB, DISK_GIB = 4, 4, 64
GIB = 1 << 30
FLOOR = 10 * GIB
RELAY_LABEL = "dev.altitude.vm-relay"
SHARED = "/Users/Shared/altitude-vm"
STARTED = time.monotonic()


def note(message: str) -> None:
    print(f"[{time.monotonic() - STARTED:5.0f}s] {message}", flush=True)


class Stop(Exception):
    """The lane cannot continue; the message says why."""


def run(*command, timeout: int = 120, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(part) for part in command], check=True, capture_output=True, text=True,
                          timeout=timeout, **kwargs)


def free() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(CACHE).free


def need(size: int, what: str) -> None:
    if free() - size < FLOOR:
        raise Stop(f"{what} needs {size / GIB:.1f} GiB and at least 10 GiB must stay free; "
                   f"{free() / GIB:.1f} GiB is free")


def watch_disk() -> None:
    """Stops the lane, through its own cleanup, when the disk falls under the floor."""
    def watch():
        while True:
            time.sleep(15)
            if free() < FLOOR:
                note(f"only {free() / GIB:.1f} GiB free; stopping")
                os.kill(os.getpid(), signal.SIGTERM)
                return
    threading.Thread(target=watch, daemon=True).start()


def lock():
    CACHE.mkdir(parents=True, exist_ok=True)
    handle = open(CACHE / "lane.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise Stop("another run of this lane is using the guests") from None
    return handle


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def helper(target: Path = HELPER) -> Path:
    """The Swift helper, compiled with this Mac's Xcode tools and signed here for the Virtualization framework."""
    source = SCRIPTS / "macos_vm.swift"
    stamp = target.with_suffix(".sha256")
    digest = hashlib.sha256(source.read_bytes() + (SCRIPTS / "macos_vm.entitlements").read_bytes()).hexdigest()
    if target.is_file() and stamp.is_file() and stamp.read_text().strip() == digest:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    note("compiling the guest helper")
    run("xcrun", "swiftc", "-O", "-target", "arm64-apple-macos14", "-o", target, source, timeout=600)
    run("codesign", "-f", "-s", "-", "--entitlements", SCRIPTS / "macos_vm.entitlements", target)
    stamp.write_text(digest + "\n")
    return target


def clone(source: Path, target: Path) -> None:
    """A copy-on-write copy of a guest: disk, auxiliary storage and identity go together."""
    target.mkdir(parents=True)
    for name in ("disk.img", "aux.img", "hardware-model", "machine-id"):
        run("cp", "-c", source / name, target / name, timeout=300)


# ---- Guests -----------------------------------------------------------------------------------------------------

class Guest:
    """A running guest. `log` receives the helper's events; SSH goes through the host-guest socket."""

    def __init__(self, bundle: Path, work: Path, log: Path, password: str | None = None):
        self.work, self.password = work, password
        self.socket = work / "ssh.sock"
        self.log = log.open("a")
        for attempt in range(6):
            self.events = queue.Queue()
            self.process = subprocess.Popen([str(helper()), "run", str(bundle), str(self.socket), str(CPUS),
                                             str(MEMORY_GIB)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True)
            self.reader = threading.Thread(target=self._read, args=(self.process, self.events), daemon=True)
            self.reader.start()
            try:
                self.mac = self.expect("started", 120).split()[1]
                break
            except BaseException as error:
                # The framework's service can hold the guest's storage for a moment after the previous guest ended.
                if not isinstance(error, Stop) or "Failed to lock" not in str(error) or attempt == 5:
                    self.process.kill()
                    self.process.wait(30)
                    self.reader.join(10)
                    self.log.close()
                    raise
                self.process.wait(30)
                time.sleep(10)
        self.gateway: str | None = None
        self.listener: socket.socket | None = None
        self.address: str | None = None

    def _read(self, process: subprocess.Popen, events: queue.Queue) -> None:
        for line in process.stdout:
            self.log.write(f"[{time.monotonic() - STARTED:5.0f}s] {line}")
            self.log.flush()
            events.put(line.strip())
        events.put("exited")

    def expect(self, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if event.startswith(prefix):
                return event
            if event.startswith(("failed", "exited", "stopped")):
                raise Stop(f"the guest helper said '{event}' while waiting for '{prefix}'")
        raise Stop(f"the guest helper did not say '{prefix}' within {timeout:.0f}s")

    def command(self, line: str) -> None:
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def options(self) -> list[str]:
        common = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                  "-o", "ConnectTimeout=10"]
        if self.password:
            return common + ["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive"]
        return common + ["-o", f"ProxyCommand=/usr/bin/nc -U {self.socket}", "-o", "BatchMode=yes",
                         "-o", "IdentitiesOnly=yes", "-i", str(KEY)]

    def destination(self) -> str:
        """The relay's socket, or before the relay is installed, the address the guest's network card got."""
        if not self.password:
            return f"{ACCOUNT}@guest"
        if self.address is None:
            self.address = lease(self.mac)
        return f"{ACCOUNT}@{self.address}"

    def environment(self) -> dict:
        if not self.password:
            return os.environ.copy()
        askpass = self.work / "askpass"
        if not askpass.exists():
            askpass.write_text("#!/bin/sh\nprintf '%s\\n' \"$GUEST_PASSWORD\"\n")
            askpass.chmod(0o700)
        return {**os.environ, "SSH_ASKPASS": str(askpass), "SSH_ASKPASS_REQUIRE": "force",
                "GUEST_PASSWORD": self.password, "DISPLAY": ":0"}

    def ssh(self, command: str, timeout: int = 120, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run(["ssh", *self.options(), self.destination(), command], input=stdin, text=True,
                                capture_output=True, timeout=timeout, env=self.environment(),
                                stdin=None if stdin is not None else subprocess.DEVNULL)
        if check and result.returncode:
            raise Stop(f"guest command failed ({result.returncode}): {command[:200]}\n{result.stdout[-2000:]}{result.stderr[-2000:]}")
        return result

    def copy(self, source: Path, target: str, timeout: int = 300) -> None:
        subprocess.run(["scp", "-q", "-r", *self.options(), str(source), f"{self.destination()}:{target}"], check=True,
                       capture_output=True, timeout=timeout, env=self.environment())

    def fetch(self, source: str, target: Path, timeout: int = 120) -> None:
        subprocess.run(["scp", "-q", "-r", *self.options(), f"{self.destination()}:{source}", str(target)], check=True,
                       capture_output=True, timeout=timeout, env=self.environment())

    def wait_ssh(self, timeout: int = 300) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise Stop("the guest stopped while booting")
            if self.password and self.address is None and lease(self.mac) is None:
                time.sleep(5)
                continue
            try:
                if self.ssh("true", timeout=30, check=False).returncode == 0:
                    return
            except subprocess.TimeoutExpired:
                pass
            time.sleep(5)
        raise Stop(f"the guest's sshd did not answer within {timeout}s")

    def shutdown(self) -> None:
        """A clean power-off from inside the guest, or the helper's own when that does not finish."""
        if self.process.poll() is None:
            try:
                self.ssh("sudo -n /sbin/shutdown -h now", timeout=30, check=False)
                self.expect("stopped", 120)
            except (Stop, subprocess.TimeoutExpired):
                pass
        self.stop()

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                self.command("stop")
                self.process.wait(60)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(10)
        if self.listener:
            self.listener.close()
        self.reader.join(10)  # the helper's last lines, then its log
        self.log.close()


def lease(mac: str, leases: Path = Path("/var/db/dhcpd_leases")) -> str | None:
    """The address this Mac's DHCP server gave the guest's network card, if any."""
    try:
        text = leases.read_text()
    except OSError:
        return None
    # The leases file drops each byte's leading zero.
    wanted = [int(part, 16) for part in mac.split(":")]
    for block in text.split("}"):
        address = re.search(r"ip_address=(\S+)", block)
        hardware = re.search(r"hw_address=1,(\S+)", block)
        if address and hardware and [int(part, 16) for part in hardware.group(1).split(":")] == wanted:
            return address.group(1)
    return None


# ---- Offline account setup ----------------------------------------------------------------------------------------
# Writing the account, auto-login and SSH settings to the stopped guest's Data volume skips Setup Assistant; this
# follows the method Lume (MIT, github.com/trycua/cua) publishes. Files written here keep this Mac's user as owner, so
# launchd will not load the relay yet: the first login, over SSH to the address the guest's network card got, gives
# each file its owner as root and starts the relay (FINALIZE).

class Mounted:
    """The guest disk's Data volume, mounted on this Mac without root."""

    def __init__(self, disk: Path):
        attached = plistlib.loads(run("hdiutil", "attach", "-imagekey", "diskimage-class=CRawDiskImage", "-nomount",
                                      "-plist", disk, timeout=120).stdout.encode())
        self.whole = min(entity["dev-entry"] for entity in attached["system-entities"])
        try:
            store = Path(self.whole).name + "s"
            containers = plistlib.loads(run("diskutil", "apfs", "list", "-plist").stdout.encode())["Containers"]
            devices = [volume["DeviceIdentifier"] for container in containers
                       if container.get("DesignatedPhysicalStore", "").startswith(store)
                       for volume in container["Volumes"] if "Data" in volume.get("Roles", [])]
            if len(devices) != 1:
                raise Stop(f"expected one Data volume on {self.whole}, found {devices}")
            run("diskutil", "mount", devices[0])
            self.root = Path(plistlib.loads(run("diskutil", "info", "-plist", devices[0]).stdout.encode())["MountPoint"])
        except BaseException:
            self.detach()
            raise

    def detach(self) -> None:
        if subprocess.run(["hdiutil", "detach", self.whole], capture_output=True, timeout=120).returncode:
            run("hdiutil", "detach", "-force", self.whole)

    def path(self, relative: str) -> Path:
        return self.root / relative.lstrip("/")

    def read(self, relative: str) -> dict:
        path = self.path(relative)
        return plistlib.loads(path.read_bytes()) if path.exists() else {}

    def write(self, relative: str, data: bytes, mode: int = 0o644) -> None:
        """Existing files are rewritten in place: launchd and loginwindow can ignore a replaced file."""
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.chmod(path.stat().st_mode | 0o200)
            with path.open("r+b") as stream:
                stream.truncate(0)
                stream.write(data)
        else:
            path.write_bytes(data)
        path.chmod(mode)

    def plist(self, relative: str, value: dict, mode: int = 0o644, xml: bool = False) -> None:
        self.write(relative, plistlib.dumps(value, fmt=plistlib.FMT_XML if xml else plistlib.FMT_BINARY), mode)


# An address in the shared VPN range (RFC 6598) that the login phase gives the guest's loopback.
VPN_ADDRESS = "100.64.0.10"
KCPASSWORD_KEY = bytes([0x7D, 0x89, 0x52, 0x23, 0xD2, 0xBC, 0xDD, 0xEA, 0xA3, 0xB9, 0x1F])


def shadow_hash(password: str) -> bytes:
    salt = secrets.token_bytes(32)
    entropy = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 50_000, dklen=128)
    return plistlib.dumps({"SALTED-SHA512-PBKDF2": {"entropy": entropy, "iterations": 50_000, "salt": salt}},
                          fmt=plistlib.FMT_BINARY)


def kcpassword(password: str) -> bytes:
    """loginwindow's auto-login password: NUL-padded to a multiple of 12, at least one NUL, XORed with its key."""
    plain = password.encode()
    plain += b"\0" * (12 - len(plain) % 12)
    return bytes(byte ^ KCPASSWORD_KEY[index % len(KCPASSWORD_KEY)] for index, byte in enumerate(plain))


def from_kcpassword(data: bytes) -> str:
    plain = bytes(byte ^ KCPASSWORD_KEY[index % len(KCPASSWORD_KEY)] for index, byte in enumerate(data))
    return plain.split(b"\0", 1)[0].decode()


def write_account(volume: Mounted, password: str, restore: dict) -> None:
    nodes = "private/var/db/dslocal/nodes/Default"
    # With owners ignored these folders read as having no search permission; the owner needs it to reach the records.
    for folder in (nodes, f"{nodes}/users", f"{nodes}/groups"):
        volume.path(folder).chmod(volume.path(folder).stat().st_mode | 0o100)
    generated = str(uuid.uuid4()).upper()
    volume.plist(f"{nodes}/users/{ACCOUNT}.plist", {
        "name": [ACCOUNT], "realname": ["Newcomer"], "uid": [str(ACCOUNT_UID)], "gid": ["20"],
        "home": [f"/Users/{ACCOUNT}"], "shell": ["/bin/zsh"], "passwd": ["********"], "generateduid": [generated],
        "ShadowHashData": [shadow_hash(password)], "authentication_authority": [";ShadowHash;HASHLIST:<SALTED-SHA512-PBKDF2>"],
        "record_daemon_version": ["9040000"], "unlockOptions": ["0"],
        **{f"_writers_{name}": [ACCOUNT] for name in ("UserCertificate", "hint", "jpegphoto", "passwd", "picture", "realname")},
    }, mode=0o600)
    for group in ("admin", "staff"):
        record = volume.read(f"{nodes}/groups/{group}.plist")
        record["users"] = sorted({*record.get("users", []), ACCOUNT})
        record["groupmembers"] = sorted({*record.get("groupmembers", []), generated})
        volume.plist(f"{nodes}/groups/{group}.plist", record)
    home = f"Users/{ACCOUNT}"
    # macOS creates the protected folders (Desktop, Documents, Downloads) itself at the first login.
    volume.path(f"{home}/Library/Preferences/ByHost").mkdir(parents=True, exist_ok=True)
    volume.write(f"{home}/.CFUserTextEncoding", f"0x{ACCOUNT_UID:X}:0x0:0x0\n".encode())
    volume.plist(f"{home}/Library/Preferences/com.apple.screensaver.plist",
                 {"askForPassword": 0, "askForPasswordDelay": 0, "idleTime": 0})
    seen = {"DidSeeCloudSetup": True, "DidSeePrivacy": True, "DidSeeSiriSetup": True, "DidSeeTouchIDSetup": True,
            "DidSeeTrueToneSetup": True, "GestureMovieSeen": "none", "LastSeenBuddyBuildVersion": restore["build"],
            "LastSeenCloudProductVersion": restore["version"]}
    volume.plist(f"{home}/Library/Preferences/com.apple.SetupAssistant.plist", seen)
    volume.plist("Library/Preferences/com.apple.SetupAssistant.plist", {**volume.read("Library/Preferences/com.apple.SetupAssistant.plist"), **seen})
    volume.write("private/var/db/.AppleSetupDone", b"")
    login = volume.read("Library/Preferences/com.apple.loginwindow.plist")
    account_info = login.get("AccountInfo", {})
    # A FirstLogins entry brings Setup Assistant back at the next login.
    account_info.pop("FirstLogins", None)
    login.update({"AccountInfo": account_info, "autoLoginUser": ACCOUNT, "GuestEnabled": False, "lastUser": "loggedIn",
                  "lastUserName": ACCOUNT})
    volume.plist("Library/Preferences/com.apple.loginwindow.plist", login)
    volume.write("private/etc/kcpassword", kcpassword(password), mode=0o600)
    volume.plist("Library/Preferences/.GlobalPreferences.plist",
                 {**volume.read("Library/Preferences/.GlobalPreferences.plist"), "com.apple.autologout.AutoLogOutDelay": 0})
    disabled = "private/var/db/com.apple.xpc.launchd/disabled.plist"
    volume.plist(disabled, {**volume.read(disabled), "com.openssh.sshd": False}, xml=True)
    volume.write("private/var/db/com.apple.xpc.launchd/disabled.migrated", b"")
    # The relay joins the host-guest socket to sshd; FINALIZE gives it root's ownership.
    volume.path("usr/local/altitude-vm").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(helper(), volume.path("usr/local/altitude-vm/macos_vm"))
    volume.path("usr/local/altitude-vm/macos_vm").chmod(0o755)
    volume.plist(f"Library/LaunchDaemons/{RELAY_LABEL}.plist", {
        "Label": RELAY_LABEL, "ProgramArguments": ["/usr/local/altitude-vm/macos_vm", "relay", "22"],
        "RunAtLoad": True, "KeepAlive": True}, xml=True)


FINALIZE = """set -ex
printf '%s ALL=(ALL) NOPASSWD: ALL\\n' {account} > /etc/sudoers.d/altitude-vm
chmod 440 /etc/sudoers.d/altitude-vm
chown root:wheel /etc/sudoers.d/altitude-vm /var/db/.AppleSetupDone /etc/kcpassword
chown -R root:wheel /usr/local/altitude-vm /Library/LaunchDaemons/{relay}.plist
chmod 644 /Library/LaunchDaemons/{relay}.plist
launchctl print system/{relay} >/dev/null 2>&1 || launchctl bootstrap system /Library/LaunchDaemons/{relay}.plist
install -d -o {account} -g staff -m 700 /Users/{account}/.ssh
printf '%s\\n' '{key}' > /Users/{account}/.ssh/authorized_keys
# Only what this Mac wrote can have another owner; macOS protects some folders the account created itself.
find /Users/{account} ! -user {account} -exec chown -h {account}:staff {{}} + 2>/dev/null || true
test -z "$(find /Users/{account} ! -user {account} -print 2>/dev/null)"
chmod 600 /Users/{account}/.ssh/authorized_keys
pmset -a sleep 0 displaysleep 0 disksleep 0
softwareupdate --schedule off >/dev/null 2>&1 || true
diskutil apfs updatePreboot / >/dev/null
launchctl enable system/com.openssh.sshd
"""


# ---- Image steps --------------------------------------------------------------------------------------------------

def restore_image() -> dict:
    """Apple's newest restore image for this Mac, downloaded and checked once."""
    found = sorted(RESTORE.glob("*.json"))
    if found:
        record = json.loads(found[-1].read_text())
        if (RESTORE / f"{record['build']}.ipsw").is_file() or (IMAGES / "fresh").is_dir():
            return record
    latest = json.loads(run(helper(), "latest", timeout=120).stdout)
    target = RESTORE / f"{latest['build']}.ipsw"
    RESTORE.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    size = int(re.search(r"(?im)^content-length:\s*(\d+)", run(
        "curl", "-fsSI", "--proto", "=https", latest["url"], timeout=60).stdout).group(1))
    if not target.is_file():
        need(size - (partial.stat().st_size if partial.exists() else 0) + 30 * GIB, "the restore image and its guest")
        note(f"downloading macOS {latest['version']} ({latest['build']}), {size / GIB:.1f} GiB, from {latest['url']}")
        subprocess.run(["curl", "-fsSL", "--proto", "=https", "--retry", "5", "-C", "-", "--max-time", "480", "-o",
                        str(partial), latest["url"]], check=False, timeout=540)
        if partial.stat().st_size != size:
            raise Stop(f"downloaded {partial.stat().st_size / GIB:.1f} of {size / GIB:.1f} GiB; run again to continue")
        partial.replace(target)
    if target.stat().st_size != size:
        raise Stop(f"{target} is not the {size}-byte restore image Apple serves")
    record = {**latest, "bytes": size, "sha256": sha256(target), "downloaded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (RESTORE / f"{latest['build']}.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def image_steps() -> list[tuple[str, callable]]:
    partial, fresh = IMAGES / "fresh.partial", IMAGES / "fresh"

    def install():
        restore = restore_image()
        shutil.rmtree(partial, ignore_errors=True)
        need(30 * GIB, "installing macOS")
        note(f"installing macOS {restore['version']} into a new guest")
        IMAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(helper()), "install", str(RESTORE / f"{restore['build']}.ipsw"), str(partial),
                        str(DISK_GIB)], check=True, timeout=540)
        (partial / "installed").write_text(json.dumps(restore) + "\n")

    def first_boot():
        # macOS writes its first-boot state, which the offline setup edits, while Setup Assistant waits.
        work = Path(tempfile.mkdtemp(prefix="first-boot.", dir=CACHE))
        try:
            guest = Guest(partial, work, partial / "boot.log")
            try:
                deadline = time.monotonic() + 300
                while lease(guest.mac) is None:
                    if time.monotonic() > deadline:
                        raise Stop("the new guest did not reach its network within 5 minutes")
                    time.sleep(5)
                time.sleep(30)
            finally:
                guest.stop()
        finally:
            shutil.rmtree(work)
        (partial / "booted").touch()

    def account():
        restore = json.loads((partial / "installed").read_text())
        password = secrets.token_urlsafe(18)
        volume = Mounted(partial / "disk.img")
        try:
            write_account(volume, password, restore)
        finally:
            volume.detach()
        # Needed only until FINALIZE has installed the SSH key and passwordless sudo.
        secret = partial / "password"
        secret.write_text(password)
        secret.chmod(0o600)

    def finalize():
        if not KEY.exists():
            run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "altitude-macos-vm", "-f", KEY)
        work = Path(tempfile.mkdtemp(prefix="finalize.", dir=CACHE))
        try:
            guest = Guest(partial, work, partial / "boot.log", password=(partial / "password").read_text())
            try:
                guest.wait_ssh(300)
                script = FINALIZE.format(account=ACCOUNT, relay=RELAY_LABEL, key=(KEY.with_suffix(".pub")).read_text().strip())
                # The first line is the password; an earlier attempt may already have made sudo passwordless.
                guest.ssh("if /usr/bin/sudo -n true 2>/dev/null; then read -r _; /usr/bin/sudo /bin/sh -s; "
                          "else /usr/bin/sudo -S -p '' /bin/sh -s; fi", stdin=guest.password + "\n" + script, timeout=180)
                guest.shutdown()
            finally:
                guest.stop()
            # A second boot proves the key, passwordless sudo and the automatic login.
            guest = Guest(partial, work, partial / "boot.log")
            try:
                guest.wait_ssh(300)
                deadline = time.monotonic() + 180
                while guest.ssh(f"launchctl print gui/{ACCOUNT_UID} >/dev/null", check=False).returncode:
                    if time.monotonic() > deadline:
                        raise Stop("the account did not log in automatically")
                    time.sleep(5)
                guest.ssh("sudo -n true")
                details = guest.ssh("sw_vers -productVersion; sw_vers -buildVersion; uname -m").stdout.split()
                guest.shutdown()
            finally:
                guest.stop()
        finally:
            shutil.rmtree(work)
        (partial / "password").unlink()
        for name in ("installed", "booted", "boot.log"):
            (partial / name).unlink(missing_ok=True)
        restore = json.loads(next(RESTORE.glob("*.json")).read_text())
        (partial / "image.json").write_text(json.dumps({
            "restore_image": {key: restore[key] for key in ("version", "build", "url", "sha256", "bytes")},
            "guest": {"version": details[0], "build": details[1], "machine": details[2]},
            "account": ACCOUNT, "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "disk_gib": round(int(run("du", "-sk", partial, timeout=300).stdout.split()[0]) / (1 << 20), 1)}, indent=2) + "\n")
        partial.replace(fresh)
        for path in RESTORE.glob("*.ipsw*"):
            path.unlink()
        note("fresh guest ready; restore image deleted")

    prerequisites_partial, prerequisites = IMAGES / "prerequisites.partial", IMAGES / "prerequisites"

    def online(script: str, timeout: int, label: str):
        work = Path(tempfile.mkdtemp(prefix="prerequisites.", dir=CACHE))
        try:
            guest = Guest(prerequisites_partial, work, prerequisites_partial / "boot.log")
            try:
                guest.wait_ssh(300)
                result = guest.ssh(f"zsh -lc {quote(script)}", timeout=timeout, check=False)
                (prerequisites_partial / f"{label}.log").write_text(result.stdout + result.stderr)
                if result.returncode:
                    raise Stop(f"{label} failed in the guest; see {prerequisites_partial / (label + '.log')}")
                guest.shutdown()
                return result.stdout
            finally:
                guest.stop()
        finally:
            shutil.rmtree(work)

    def homebrew():
        shutil.rmtree(prerequisites_partial, ignore_errors=True)
        need(15 * GIB, "the prerequisites guest")
        free_before = free()
        clone(fresh, prerequisites_partial)
        note("installing Homebrew and Apple's command line tools in the guest, online")
        # Homebrew's documented installer, which also installs Apple's command line tools, then its shell setup.
        online('NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
               ' && echo \'eval "$(/opt/homebrew/bin/brew shellenv)"\' >> ~/.zprofile', 540, "homebrew")
        (prerequisites_partial / "homebrew").write_text(str(free_before))

    def formulae():
        note("installing python@3.12 and openssl@3 in the guest, online")
        versions = online("brew install python@3.12 openssl@3 >/dev/null && xcode-select -p && brew --version | head -1"
                          " && python3.12 --version && $(brew --prefix openssl@3)/bin/openssl version", 540, "formulae")
        free_before = int((prerequisites_partial / "homebrew").read_text())
        (prerequisites_partial / "homebrew").unlink()
        image = json.loads((fresh / "image.json").read_text())
        image["prerequisites"] = versions.strip().splitlines()
        # A clone of fresh: only the blocks the guest changed take space.
        image["disk_gib"] = round((free_before - free()) / GIB, 1)
        image["built"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (prerequisites_partial / "image.json").write_text(json.dumps(image, indent=2) + "\n")
        (prerequisites_partial / "boot.log").unlink(missing_ok=True)
        prerequisites_partial.replace(prerequisites)
        note("prerequisites guest ready")

    steps = []
    if not fresh.is_dir():
        steps += [("install", install)] if not (partial / "installed").exists() else []
        steps += [("first boot", first_boot)] if not (partial / "booted").exists() else []
        steps += [("account", account)] if not (partial / "password").exists() else []
        steps.append(("finalize", finalize))
    if not prerequisites.is_dir():
        steps += [("homebrew", homebrew)] if not (prerequisites_partial / "homebrew").exists() else []
        steps.append(("formulae", formulae))
    return steps


def quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def image(step_only: bool) -> int:
    steps = image_steps()
    for index, (name, step) in enumerate(steps):
        note(f"image step: {name}")
        step()
        if step_only:
            following = image_steps()
            note(f"next: {following[0][0]}" if following else "images complete")
            return 0
    note("images complete")
    return 0


# ---- Runs ---------------------------------------------------------------------------------------------------------

def probe(guest: Guest, command: str) -> bool:
    """Whether the probe reached its destination (exit 0) or found it unreachable (exit 1). Anything else, such as
    an SSH failure, a probe that could not run or a TLS error, proves nothing."""
    code = guest.ssh(command, timeout=60, check=False).returncode
    if code not in (0, 1):
        raise Stop(f"a network probe could not run in the guest (exit {code}): {command}")
    return code == 0


# curl's exits for a name that does not resolve, a refused connection and a timeout mean unreachable. The host is
# one the lane never maps to the guest's release server.
INTERNET = ("curl -sS --max-time 10 -o /dev/null https://www.apple.com/; "
            "case $? in 0) exit 0 ;; 6|7|28) exit 1 ;; *) exit 3 ;; esac")


def reachable(guest: Guest) -> dict:
    """Which of the internet and a listener on this Mac the guest reaches."""
    if guest.listener is None:
        # On a loaded Mac the guest's address can come after sshd answers.
        deadline = time.monotonic() + 60
        while True:
            found = guest.ssh("route -n get default 2>/dev/null | awk '/gateway:/ {print $2}'", check=False).stdout.strip()
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", found) or time.monotonic() > deadline:
                break
            time.sleep(2)
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", found):
            raise Stop(f"the guest has no default route while online: {found!r}")
        # Bound while this Mac has the guests' network; it stays open after the card is unplugged.
        guest.gateway, guest.listener = found, socket.socket()
        guest.listener.bind((found, 0))
        guest.listener.listen()
    port = guest.listener.getsockname()[1]
    return {"internet": probe(guest, INTERNET),
            "host": probe(guest, f"nc -z -G 5 -w 5 {guest.gateway} {port}")}


def isolate(guest: Guest, record: dict) -> None:
    record["reachable"] = {"online": reachable(guest)}
    if record["reachable"]["online"] != {"internet": True, "host": True}:
        raise Stop(f"unexpected guest network before isolation: {record['reachable']}")
    guest.command("unplug")
    guest.expect("unplugged", 30)
    record["reachable"]["isolated"] = reachable(guest)
    if any(record["reachable"]["isolated"].values()):
        raise Stop(f"the guest is not isolated: {record['reachable']}")


def certificates(folder: Path) -> None:
    """A throwaway certificate authority and a github.com and api.github.com certificate that only the test's
    curl, and an installation made through it, trusts."""
    # Each certificate's name comes from its own configuration: LibreSSL's `req` keeps a configuration's name over
    # -subj, and a server certificate named as its authority is one OpenSSL 3, the service's Python, calls self-signed.
    for name, common in (("ca", "Altitude macOS VM lane test CA"), ("server", "github.com")):
        (folder / f"{name}.cnf").write_text(
            f"[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN={common}\n"
            "[ca]\nbasicConstraints=critical,CA:true\nkeyUsage=critical,keyCertSign,cRLSign\n"
            "[leaf]\nbasicConstraints=CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "subjectAltName=DNS:github.com,DNS:api.github.com\nextendedKeyUsage=serverAuth\n")
    openssl = "/usr/bin/openssl"
    run(openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", folder / "ca.key", "-out", folder / "ca.pem",
        "-days", "2", "-config", folder / "ca.cnf", "-extensions", "ca")
    run(openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", folder / "server.key", "-out",
        folder / "server.csr", "-config", folder / "server.cnf")
    run(openssl, "x509", "-req", "-in", folder / "server.csr", "-CA", folder / "ca.pem", "-CAkey", folder / "ca.key",
        "-CAcreateserial", "-out", folder / "server.pem", "-days", "2", "-extfile", folder / "ca.cnf", "-extensions", "leaf")
    (folder / "ca.key").unlink()


def release_tree(release: Path, folder: Path) -> str:
    """The built release laid out as GitHub serves it; returns the repository's path."""
    repository = re.search(r"^REPOSITORY='https://github\.com/([^']+)'", (release / "install.sh").read_text(), re.M).group(1)
    version = re.search(r"^VERSION='([^']+)'", (release / "install.sh").read_text(), re.M).group(1)
    latest = folder / "root" / repository / "releases/latest/download"
    tagged = folder / "root" / repository / "releases/download" / version
    latest.mkdir(parents=True)
    tagged.mkdir(parents=True)
    for path in release.iterdir():
        shutil.copyfile(path, tagged / path.name)
    shutil.copyfile(release / "install.sh", latest / "install.sh")
    return repository


SERVE = f"""set -e
echo '127.0.0.1 github.com' | sudo -n tee -a /etc/hosts >/dev/null
cd {SHARED}/root
nohup /usr/bin/openssl s_server -quiet -WWW -accept 443 -cert {SHARED}/server.pem -key {SHARED}/server.key \
  >{SHARED}/server.log 2>&1 </dev/null &
sleep 1
"""

# What a refused attempt must not change: every file's size and modification time outside Library, including shell
# profiles, and in the Library folders an installation writes, and the account's launchd jobs.
LISTING = ("{ find \"$HOME\" -path \"$HOME/Library\" -prune -o -exec stat -f '%m %z %N' {} +; "
           "find \"$HOME/Library/LaunchAgents\" \"$HOME/Library/Logs/altitude\" \"$HOME/Library/Caches/dev.altitude\" "
           "-exec stat -f '%m %z %N' {} + 2>/dev/null; } | LC_ALL=C sort; "
           "launchctl list 2>/dev/null | grep -i altitude || true")


def public_command(repository: str) -> str:
    """The documented command, with the test's certificate authority trusted by it and by what install.sh runs."""
    return (f"export SSL_CERT_FILE={SHARED}/ca.pem CURL_CA_BUNDLE={SHARED}/ca.pem; curl --proto '=https' --tlsv1.2 "
            f"-fsSL https://github.com/{repository}/releases/latest/download/install.sh | sh")


def attempt(guest: Guest, repository: str, user: str, results: Path, label: str) -> dict:
    """The public command as `user` in a login shell, with what it changed."""
    public = public_command(repository)
    wrap = (lambda command: command) if user == ACCOUNT else (lambda command: f"sudo -n -u {user} -i zsh -c {quote(command)}")
    login = (lambda command: f"zsh -lc {quote(command)}") if user == ACCOUNT else (lambda command: command)
    before = guest.ssh(wrap(login(LISTING)), timeout=120).stdout
    result = guest.ssh(wrap(login(public)), timeout=300, check=False)
    after = guest.ssh(wrap(login(LISTING)), timeout=120).stdout
    (results / f"{label}.log").write_text(f"$ {public}\n# exit {result.returncode}\n{result.stdout}{result.stderr}")
    (results / f"{label}.listing").write_text(before)
    return {"user": user, "exit": result.returncode, "output": (result.stdout + result.stderr).strip(),
            "listed": len(before.splitlines()), "unchanged": before == after}


# Each refusal names the missing prerequisite and its documented fix.
EXPECTED = {
    "missing-python": ("Python 3.12 or newer was not found", "brew install python@3.12"),
    "missing-openssl": ("OpenSSL 3 or newer was not found", "brew install openssl@3",
                        "no shell-profile change is needed"),
    "no-desktop-session": ("no logged-in desktop session", "Log in to this Mac's desktop"),
}


def judged(outcome: dict, case: str) -> dict:
    expected = EXPECTED[case]
    outcome["expected"] = list(expected)
    outcome["passed"] = (outcome["exit"] != 0 and outcome["unchanged"]
                         and all(text in outcome["output"] for text in expected))
    return outcome


def prerequisites(guest: Guest) -> dict:
    """What the account's login shell finds of the installer's prerequisites."""
    found = lambda command: guest.ssh(f"zsh -lc {quote(command)}", check=False).returncode == 0
    return {"command_line_tools": found("xcode-select -p"), "homebrew": found("test -d /opt/homebrew"),
            "python3.12": found("command -v python3.12"),
            "openssl": guest.ssh("zsh -lc 'openssl version'", check=False).stdout.strip()}


def add_github_cli(guest: Guest, record: dict) -> None:
    """GitHub CLI, which Altitude needs and `alt doctor` checks: this Mac's own Homebrew binary, copied, since the
    guest downloads nothing during a run."""
    found = shutil.which("gh")
    if not found:
        raise Stop("GitHub CLI (gh) is not installed on this Mac; the lane copies it into the guest")
    guest.copy(Path(found).resolve(), f"{SHARED}/gh")
    guest.ssh(f"sudo -n install -m 755 -o {ACCOUNT} -g admin {SHARED}/gh /opt/homebrew/bin/gh && rm {SHARED}/gh")
    record["github_cli"] = guest.ssh("zsh -lc 'gh --version'").stdout.splitlines()[0]


def phase_fresh(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    record["prerequisites"] = prerequisites(guest)
    record["cases"] = {"missing-python": judged(attempt(guest, repository, ACCOUNT, results, "missing-python"), "missing-python")}


def phase_prerequisites(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    record["prerequisites"] = prerequisites(guest)
    cases = record["cases"] = {}
    # Hide only the CLI in this disposable guest; Python's linked libraries remain available.
    binary = "/opt/homebrew/opt/openssl@3/bin/openssl"
    guest.ssh(f"mv {binary} {binary}.unavailable")
    try:
        cases["missing-openssl"] = judged(attempt(guest, repository, ACCOUNT, results, "missing-openssl"), "missing-openssl")
    finally:
        guest.ssh(f"mv {binary}.unavailable {binary}")
    # Homebrew's keg is available again, without editing either account's shell profile.
    password = secrets.token_urlsafe(18)
    guest.ssh(f"sudo -n sysadminctl -addUser {SECOND} -password {quote(password)} -home /Users/{SECOND} 2>&1 && "
              f"sudo -n createhomedir -c -u {SECOND} >/dev/null && sudo -n cp ~/.zprofile /Users/{SECOND}/.zprofile && "
              f"sudo -n chown {SECOND}:staff /Users/{SECOND}/.zprofile")
    cases["no-desktop-session"] = judged(attempt(guest, repository, SECOND, results, "no-desktop-session"), "no-desktop-session")


# What launchd, the installation's processes and the guest were doing when a lifecycle step failed: HOME, then where.
DIAGNOSE = r"""label=$(sed -n 's/.*"label": "\(.*\)".*/\1/p' "$1/results/service-label.json")
launchctl print "gui/$(id -u)/$label" > "$2/launchd.txt" 2>&1
ps -Ao pcpu,pmem,etime,comm -r | head -25 > "$2/processes.txt"
ps -axo pid,ppid,pgid,stat,etime,command | grep -F "$1" | grep -v grep > "$2/installation-processes.txt"
pid=$(sed -n 's/^\tpid = //p' "$2/launchd.txt")
if [ -n "$pid" ]; then
    sample "$pid" 3 -file "$2/sample.txt" >/dev/null 2>&1
    lsof -p "$pid" > "$2/open-files.txt" 2>&1
fi
log show --last 10m --style compact --predicate "eventMessage CONTAINS '$label'" > "$2/system-log.txt" 2>&1
"""


def phase_lifecycle(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """installation_lifecycle.py's `mac` phase, as installation_mac.py runs it on a configured Mac, in this fresh guest:
    install through the public command, health, doctor, the daemon's automatic installation of a newer stable release
    with no browser terminal open, with automatic updates off a release that waits for `alt update` and then the app's
    Update button, a failed automatic update rolled back and not retried, and uninstall, under a throwaway HOME of the
    account."""
    lifecycle(guest, results, record, inputs / "release", inputs / "release", commit, "mac", "lifecycle")


def phase_published(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commits: str) -> None:
    """installation_lifecycle.py's `mac-published` phase under a throwaway HOME of the account: the published baseline
    installed through its public command, a paired device and a machine setting made through the app, then the
    baseline's own daemon installing the published candidate, which must keep them and read them."""
    lifecycle(guest, results, record, inputs / "baseline", inputs / "candidate", commits, "mac-published", "published")


def lifecycle(guest: Guest, results: Path, record: dict, baseline: Path, candidate: Path, commits: str, phase: str,
              case: str) -> None:
    """An installation_lifecycle.py phase in installation_mac.py's clean environment; its results join the record."""
    add_github_cli(guest, record)
    record["prerequisites"] = prerequisites(guest)
    guest.copy(baseline, f"{SHARED}/baseline")
    guest.copy(candidate, f"{SHARED}/candidate")
    guest.copy(SCRIPTS / "installation_lifecycle.py", f"{SHARED}/installation_lifecycle.py")
    (results / "diagnose.sh").write_text(DIAGNOSE)
    guest.copy(results / "diagnose.sh", f"{SHARED}/diagnose.sh")
    # Stock LibreSSL remains first in both the installing shell and the saved service environment.
    path = "/usr/bin:/bin:/opt/homebrew/bin:/usr/sbin:/sbin"
    if phase == "mac-published":
        # The published baseline may predate discovery; its own installer contract still applies.
        path = "/opt/homebrew/opt/openssl@3/bin:" + path
    # The user temp prefix makes a registered project's AF_UNIX broker exceed macOS's pathname limit.
    # Keep the disposable home in the launcher's existing guest-owned shared tree, never on the host.
    script = (f'temp=$(getconf DARWIN_USER_TEMP_DIR); work="{SHARED}/altitude-installation-mac"; '
              f'mkdir -m 700 "$work" "$work/home" && mkdir "$work/home/results" && cd "$work/home" && '
              f'env -i HOME="$work/home" USER={ACCOUNT} LOGNAME={ACCOUNT} SHELL=/bin/zsh LANG=en_US.UTF-8 TMPDIR="$temp" '
              f'PATH="{path}" '
              f'/opt/homebrew/bin/python3.12 -I -B {SHARED}/installation_lifecycle.py {SHARED}/baseline {SHARED}/candidate '
              f'"$work/home/results" {commits} {phase}; code=$?; cp -R "$work/home/results" {SHARED}/lifecycle; '
              f'cp -R "$work/home/Library/Logs" {SHARED}/lifecycle/logs 2>/dev/null; '
              f'[ $code = 0 ] || sh {SHARED}/diagnose.sh "$work/home" {SHARED}/lifecycle; exit $code')
    result = guest.ssh(script, timeout=480, check=False)
    (results / "lifecycle.log").write_text(result.stdout + result.stderr)
    guest.fetch(f"{SHARED}/lifecycle", results / "lifecycle")
    outcome = json.loads((results / "lifecycle/result.json").read_text())
    record["cases"] = {case: {"exit": result.returncode, "passed": result.returncode == 0 and outcome["passed"],
                                     "steps": [step["step"] for step in outcome["steps"]], "limits": outcome["limits"],
                                     **({"error": outcome["error"]} if "error" in outcome else {})}}


# Where macOS's password window sits on the guest's display once it shows, read in the desktop session: window
# owners and bounds need no screen recording permission.
WINDOWS = """import CoreGraphics
import Foundation
for _ in 0..<60 {
    let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]] ?? []
    let bounds = windows.filter { $0[kCGWindowOwnerName as String] as? String == "SecurityAgent" }
        .compactMap { $0[kCGWindowBounds as String] as? [String: Double] }
    if !bounds.isEmpty {
        FileManager.default.createFile(atPath: CommandLine.arguments[1],
                                       contents: try! JSONSerialization.data(withJSONObject: bounds))
        break
    }
    Thread.sleep(forTimeInterval: 1)
}
"""


class Desktop:
    """The account's desktop session in GUEST, used as the person logged in there uses it: programs started there,
    Terminal windows and macOS's password window. The password is the automatic login's, which the lane wrote when it
    made the account; STEP records commands' output in RESULTS, and the password is removed from it."""

    def __init__(self, guest: Guest, results: Path, step):
        self.guest, self.results, self.step = guest, results, step
        self.password = from_kcpassword(base64.b64decode(guest.ssh("sudo -n base64 -i /etc/kcpassword").stdout))
        guest.ssh(f"cat > {SHARED}/windows.swift", stdin=WINDOWS)

    def within(self, seconds: int, check: str) -> bool:
        return any(self.guest.ssh(check, check=False).returncode == 0 or time.sleep(2) for _ in range(seconds // 2))

    def agent(self, name: str, arguments: list[str]) -> None:
        """ARGUMENTS run once in the account's desktop session, as a program the person logged in there starts."""
        definition = self.results / f"lane.{name}.plist"
        definition.write_bytes(plistlib.dumps({"Label": f"lane.{name}", "RunAtLoad": True, "ProgramArguments": arguments}))
        self.guest.copy(definition, f"{SHARED}/lane.{name}.plist")
        self.guest.ssh(f"launchctl bootstrap gui/{ACCOUNT_UID} {SHARED}/lane.{name}.plist")

    def terminal(self, name: str, command: str) -> tuple[str, str]:
        """COMMAND opened as a person opens it: Terminal comes to the front running it in a window. Its transcript
        and, once it ends, its exit status."""
        log, script = f"{SHARED}/{name}.log", f"{SHARED}/{name}.command"
        self.guest.ssh(f"cat > {script} && chmod 755 {script}",
                       stdin=f"#!/bin/zsh\n/usr/bin/script -q {log} /bin/zsh -c {quote(command)}\necho $? > {log}.exit\n")
        self.agent(name, ["/usr/bin/open", "-a", "Terminal", script])
        return log, f"{log}.exit"

    def authorize(self, name: str) -> dict | None:
        """macOS's password window, once it shows: clicked, as a person brings it to the front, then the password."""
        self.agent(name, ["/usr/bin/swift", f"{SHARED}/windows.swift", f"{SHARED}/{name}.json"])
        if not self.within(150, f"grep -q Width {SHARED}/{name}.json"):
            return None
        window = max(json.loads(self.guest.ssh(f"cat {SHARED}/{name}.json").stdout),
                     key=lambda bounds: bounds["Width"] * bounds["Height"])
        self.guest.command(f"click {window['X'] + window['Width'] / 2:.0f} {window['Y'] + window['Height'] / 2:.0f}")
        self.guest.expect("clicked", 30)
        time.sleep(1)
        self.guest.command(f"type {self.password}")
        self.guest.expect("typed", 30)
        return window

    def transcript(self, name: str, log: str, done: str) -> str:
        ended = self.within(120, f"test -s {done}")
        answered = self.step(name, f"cat {log}; echo; echo exit $(cat {done})")
        for path in self.results.glob("*.log"):
            if self.password in (text := path.read_text(errors="replace")):
                path.write_text(text.replace(self.password, "[the account's password]"))
        return answered.stdout.replace("\r", "") if ended and self.password not in answered.stdout else ""

    def trust(self, alt: str) -> tuple[dict | None, str]:
        """`alt trust` run in Terminal and answered y on the keyboard once it asks, then macOS's password window
        given the account's password: the window, if it showed, and the command's transcript."""
        log, done = self.terminal("trust-desktop", f"{alt} trust")
        window = None
        if self.within(120, f"grep -q 'Trust it?' {log}"):
            self.guest.command("type y")
            self.guest.expect("typed", 30)
            window = self.authorize("trust-window")
        return window, self.transcript("trust-desktop", log, done)

    def safari(self) -> bool:
        """Safari's own WebDriver listening on the guest's port 4444 in the desktop session, remote automation on."""
        self.guest.ssh("sudo -n safaridriver --enable")
        self.agent("safaridriver", ["/usr/bin/safaridriver", "-p", "4444"])
        return self.within(60, "curl -sf http://127.0.0.1:4444/status")


def desktop_trust(guest: Guest, results: Path, step, alt: str, listed: str, unchanged: str, printed: str) -> dict:
    """#1006 checks 1 and 2 for Safari: on the account's desktop, `alt trust` run in Terminal, as the installer runs it
    there too, and answered y on the guest's keyboard raises macOS's own password window; clicked and given the
    account's password on the keyboard, it must leave the authority trusted to identify websites only, as the one
    trust setting. Safari, driven through its own WebDriver, must refuse the address of PRINTED, the installer's
    private link, before and open Altitude's page after, and #926's pairing walk (`pairing`) and VPN-range address
    (`vpn`) run in it. The undo command `alt trust` prints, run there too, raises the window again and must remove the trust. The
    password is the automatic login's, which the lane wrote when it made the account; it is removed from the
    evidence."""
    address, used = printed.split("#pair=")[0].removesuffix("/projects"), printed.split("#pair=")[1]
    desktop = Desktop(guest, results, step)
    agent, terminal, authorize, transcript = desktop.agent, desktop.terminal, desktop.authorize, desktop.transcript

    def driver(method: str, path: str, body: dict | None = None):
        """One request to Safari's own WebDriver in the desktop session: its answer's value."""
        command = f"curl -sS --max-time 60 -X {method} -H 'Content-Type: application/json' http://127.0.0.1:4444/session{path}"
        answer = guest.ssh(command + (f" --data {quote(json.dumps(body))}" if body is not None else ""), check=False).stdout
        try:
            return json.loads(answer).get("value")
        except (ValueError, AttributeError):
            return {"error": answer[-300:]}

    def browse(steps: list[str]) -> list[dict]:
        """A new Safari automation window, with storage of its own, opening each address in turn or going Back: what
        each shows, with `ok` false when WebDriver refused the navigation or a read."""
        session = (driver("POST", "", {"capabilities": {"alwaysMatch": {"browserName": "safari"}}}) or {}).get("sessionId")
        if not session:
            return []
        seen = []
        for url in steps:
            went = driver("POST", f"/{session}/back", {}) if url == "back" else driver("POST", f"/{session}/url", {"url": url})
            time.sleep(4)
            shown = [driver("GET", f"/{session}/title"), driver("GET", f"/{session}/url"),
                     driver("POST", f"/{session}/execute/sync", {"script": "return document.body.innerText", "args": []})]
            seen.append({"ok": went is None and all(isinstance(value, str) for value in shown),
                         **dict(zip(("title", "url", "text"), map(str, shown)))})
        driver("DELETE", f"/{session}")
        return seen

    def devices(origin: str = address) -> list[str] | None:
        """The paired devices' ids, read at ORIGIN through the authority Safari was told to trust, so a service
        serving under any other authority answers None."""
        listed = guest.ssh(f"curl -sS --cacert {SHARED}/trusted-ca.crt -H \"X-Altitude-Key: $(cat "
                           f"~/.config/altitude/access/machine.key)\" {origin}/api/devices", check=False).stdout
        return sorted(device["id"] for device in json.loads(listed)["devices"]) if listed.startswith("{") else None

    def added(before: list[str] | None, after: list[str] | None) -> bool:
        """AFTER keeps every device of BEFORE and adds exactly one."""
        return before is not None and after is not None and set(before) <= set(after) and len(after) == len(before) + 1

    def link() -> str:
        """The installer's printed link with only its code replaced by a fresh one, as `alt pair` issues it."""
        return printed.replace(used, re.search(r"Pairing code: (\S+) ", guest.ssh(f"{alt} pair").stdout).group(1))

    desktop.safari()
    untrusted = (browse([f"{address}/"]) or [{}])[0]
    guest.ssh(f"cp ~/.config/altitude/tls/ca.crt {SHARED}/trusted-ca.crt")
    window, trusted = desktop.trust(alt)
    settings = step("trust-desktop-settings", listed).stdout
    # #926: the pair screen's own trust check, then a printed link pairing Safari into the app and leaving no code in
    # its address or history; a used and a malformed link recover in another window; the OS opens a link in Safari.
    fresh, start = link(), devices()
    walked = browse([f"{address}/", fresh, "back"])
    paired = devices()
    # The installer's own link, used earlier, and a malformed one, each opened as a new page loads it.
    reopened = browse([printed]) + browse([printed.replace(used, "not-a-code")])
    opened = link()
    agent("open-link", ["/usr/bin/open", opened])
    os_opened = any(added(paired, devices()) or time.sleep(2) for _ in range(30))
    safari = {"before": untrusted, "walked": walked, "devices": [start, paired, devices()], "reopened": reopened,
              "os_opened": os_opened}
    record_pairing = (len(walked) == 3 and all(seen["ok"] for seen in walked + reopened) and "Pair this device" in walked[0]["text"] and "Trusted" in walked[0]["text"]
                      and "Pair this device" not in walked[1]["text"] and "#pair=" not in walked[1]["url"]
                      and walked[2]["url"] == walked[0]["url"] and added(start, paired) and len(reopened) == 2
                      and "expired or was already used" in reopened[0]["text"] and "#pair=" not in reopened[0]["url"]
                      and "This pairing link is not valid" in reopened[1]["text"] and os_opened)
    # #926 item 7: the address moved to a VPN-range one and back. The guest has no VPN; an address in the shared VPN
    # range on its loopback stands in for the one a VPN interface assigns, since Altitude asks only that the address
    # be private and assigned to this computer. Safari must trust the reissued certificate there, within the
    # authority's name constraints, pair with a fresh code and show the address in Settings › Network.
    vpn, before_vpn = address.replace("127.0.0.1", VPN_ADDRESS), devices()
    guest.ssh(f"sudo -n ifconfig lo0 alias {VPN_ADDRESS}/32")
    moved = step("address-vpn", f"{alt} service configure --host {VPN_ADDRESS} --apply")
    there = browse([link().replace(address, vpn), f"{vpn}/settings/network"])
    paired_there = devices(vpn)
    returned = step("address-local", f"{alt} service configure --host 127.0.0.1 --apply")
    guest.ssh(f"sudo -n ifconfig lo0 -alias {VPN_ADDRESS}")
    safari["vpn"] = {"there": there, "devices": [before_vpn, paired_there, devices()]}

    def configured(answer: subprocess.CompletedProcess, url: str) -> bool:
        result = json.loads(answer.stdout) if answer.returncode == 0 and answer.stdout.startswith("{") else {}
        return result.get("applied") is True and result.get("verified") is True and result.get("url") == url

    record_vpn = (configured(moved, vpn) and len(there) == 2 and all(seen["ok"] for seen in there)
                  and there[0]["url"].startswith(vpn) and "Pair this device" not in there[0]["text"]
                  and "#pair=" not in there[0]["url"] and vpn in there[1]["text"]
                  and "Selected private address" in there[1]["text"] and added(before_vpn, paired_there)
                  and configured(returned, address) and devices() == paired_there)
    undo = re.search(r"^  (security delete-certificate -Z [0-9A-F]{64} -t \"\$HOME/Library/Keychains/login\.keychain-db\")$",
                     trusted, re.M)
    undone = ""
    if undo:
        log, done = terminal("trust-desktop-undo", undo.group(1))
        authorize("undo-window")
        undone = transcript("trust-desktop-undo", log, done)
    after = step("trust-desktop-after", listed).stdout
    return {"window": window, "settings": settings, "after": after, "safari": safari, "pairing": record_pairing,
            "vpn": record_vpn,
            "passed": bool(window) and untrusted.get("title") == "This Connection Is Not Private"
            and walked and walked[0]["title"] == "Altitude" and "Trusted in macOS login keychain" in trusted and trusted.endswith("exit 0\n")
            and settings.count("Cert ") == 1 and re.findall(r"Policy OID\s*:\s*(\S+)", settings) == ["SSL"]
            and undone.endswith("exit 0\n") and after == unchanged}


def phase_login(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """The account's own installation through the public command, its LaunchAgent started again by the automatic login
    after the guest restarts, and its uninstall."""
    add_github_cli(guest, record)
    record["prerequisites"] = prerequisites(guest)
    steps = record["steps"] = []
    version = re.search(r"^VERSION='([^']+)'", (inputs / "release/install.sh").read_text(), re.M).group(1)
    agent = f"/Users/{ACCOUNT}/Library/LaunchAgents/{SERVICE_LABEL}.plist"
    alt = f"/Users/{ACCOUNT}/.local/bin/alt"

    def step(name: str, command: str, timeout: int = 120) -> subprocess.CompletedProcess:
        result = guest.ssh(f"zsh -lc {quote(command)}", timeout=timeout, check=False)
        (results / f"{len(steps) + 1:02d}-{name}.log").write_text(f"$ {command}\n# exit {result.returncode}\n"
                                                                    f"{result.stdout}{result.stderr}")
        steps.append({"step": name, "exit": result.returncode})
        return result

    def check(condition: bool, what: str) -> None:
        steps[-1]["passed"] = bool(condition)
        if not condition:
            raise Stop(f"{steps[-1]['step']}: {what}")

    def running() -> dict:
        """The account's service as launchd and its HTTPS health on the generated authority report it."""
        health = f"curl -fsS --max-time 10 --cacert ~/.config/altitude/tls/ca.crt https://127.0.0.1:{port}/api/health"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and guest.ssh(
                f"launchctl print gui/{ACCOUNT_UID}/{SERVICE_LABEL} | grep -q 'state = running' && {health} >/dev/null",
                check=False).returncode:
            time.sleep(5)
        loaded = step("service", f"launchctl print gui/{ACCOUNT_UID}/{SERVICE_LABEL}")
        health = step("health", health)
        check(loaded.returncode == 0 and f"path = {agent}" in loaded.stdout and "state = running" in loaded.stdout,
              "the LaunchAgent is not running from the account's LaunchAgents")
        answer = json.loads(health.stdout)
        check((answer["version"], answer["commit"]) == (version, commit) and f"pid = {answer['pid']}" in loaded.stdout,
              f"health reports {answer}")
        return answer

    def browser(name: str, path: str, body: dict | None = None, jar: str = f"{SHARED}/browser-cookies") -> tuple[int, dict]:
        """The page's own request at the printed origin, from a browser that trusts the generated authority."""
        command = (f"curl -sS --max-time 10 --cacert ~/.config/altitude/tls/ca.crt --connect-to ::127.0.0.1: "
                   f"-b {jar} -c {jar} -A 'Mozilla/5.0 (Macintosh) macOS VM lane' -w '\\n%{{http_code}}' ")
        if body is not None:
            command += f"-H 'Origin: {origin}' -H 'Content-Type: application/json' --data {quote(json.dumps(body))} "
        answer, _, status = step(name, command + quote(origin + path)).stdout.rpartition("\n")
        return int(status), json.loads(answer) if answer.startswith("{") else {}

    def paired() -> bool:
        return browser("access", "/api/access")[1].get("paired") is True

    def doctor(name: str) -> dict:
        report = step(name, f"{alt} doctor --json")
        check(report.returncode == 0, "doctor failed")
        return json.loads(report.stdout)

    installed = step("install", public_command(repository), timeout=300)
    check(installed.returncode == 0 and f"Altitude {version} is installed" in installed.stdout, "the installer failed")
    origin, port = re.search(r"^Address: (https://\S+:(\d+)) ", installed.stdout, re.M).groups()
    port = int(port)
    link = re.search(r"It pairs this browser and opens First run:\n +((\S+)/projects#pair=(\S+))\n +Works once, for 10 "
                     r"minutes\. Keep it private\.\n", installed.stdout)
    check(link is not None and link.group(2) == origin, "the installer printed no private pairing link for its address")
    printed_link, code = link.group(1), link.group(3)
    running()
    printed = step("version", f"{alt} --version")
    check(printed.returncode == 0 and printed.stdout.strip() == f"alt {version}", "alt --version does not print the release")
    # Doctor's one next step follows real state: the installer's private link while it still works...
    hint = doctor("doctor-link")["next"]
    check(hint[0].startswith("Next: open the private link the installer printed. It works once, until "), f"next: {hint}")
    # Opening the printed link: the page at its origin, then the page's pairing request with the fragment's code.
    check(browser("page", "/projects")[0] == 200 and not paired(), "the link's page is not served to an unpaired browser")
    status, answer = browser("pair", "/api/pair", {"code": code})
    check(status == 200 and "device" in answer and paired(), f"the printed link did not pair: {status} {answer}")
    device = answer["device"]["id"]
    # The link works once; another browser, or a well-formed unknown code, is told to make a new one.
    for name, tried in (("pair-again", code), ("pair-unknown", "2345-6789")):
        status, answer = browser(name, "/api/pair", {"code": tried}, jar=f"{SHARED}/other-browser-cookies")
        check(status == 410 and "expired or was already used" in answer.get("error", ""), f"{name}: {status} {answer}")
    def identity(name: str) -> str:
        """The digests of the certificate authority, server key and certificate, all four present."""
        digests = step(name, "shasum -a 256 ~/.config/altitude/tls/ca.crt ~/.config/altitude/tls/ca.key "
                             "~/.config/altitude/tls/server.key ~/.config/altitude/tls/server.crt")
        check(digests.returncode == 0 and len(re.findall(r"^[0-9a-f]{64}  ", digests.stdout, re.M)) == 4,
              "the HTTPS identity files are not all readable")
        return digests.stdout

    certificate = identity("identity")
    # Running the public command again keeps the paired browser and the certificate and prints no new link.
    again = step("install-again", public_command(repository), timeout=300)
    check(again.returncode == 0 and "#pair=" not in again.stdout and f"2. Open Altitude: {origin}" in again.stdout,
          "the second installation failed or printed a pairing link")
    before = running()
    check(identity("identity-again") == certificate and paired(),
          "the second installation changed the certificate or lost the paired browser")
    report = doctor("doctor")
    checks = {item["name"]: item["state"] for item in report["checks"]}
    record["doctor"] = checks
    check(report["version"] == version and checks["Python"] == checks["user service"] == "tested"
          and all(checks[name] == "configured" for name in ("git", "gh", "openssl")), f"doctor reports {checks}")
    # ...then, with a browser paired, the first missing tool: the GitHub CLI copied in is not signed in.
    check(report["next"] == ["Next: Sign in to the GitHub CLI.", "  gh auth login"], f"next: {report['next']}")
    # macOS has no agent sandbox to install; with no coding agent, one requirement names every agent's install command.
    coding = next(item for item in report["checks"] if item["name"] == "coding agent")
    check("agent sandbox" not in checks and coding["state"] == "unavailable"
          and coding["detail"].startswith("Install at least one coding agent: ")
          and all(f"; {seat['name']}: " in coding["detail"] for seat in report["engines"]), f"doctor reports {report['checks']}")
    status, answer = browser("prerequisites", "/api/prerequisites")
    items = answer.get("items", [])
    check(status == 200 and [item["key"] for item in items] == ["github", "agents", "git"]
          and all(option["command"] for option in items[1]["options"])
          and len(items[1]["options"]) == len(report["engines"]), f"prerequisites: {status} {answer}")
    booted = guest.ssh("sysctl -n kern.boottime").stdout.strip()
    note("login: restarting the guest")
    guest.ssh("sudo -n /sbin/shutdown -r now", timeout=30, check=False)
    time.sleep(20)
    guest.wait_ssh(300)
    restarted = step("restarted", "sysctl -n kern.boottime")
    check(restarted.returncode == 0 and restarted.stdout.strip() != booted, "the guest did not restart")
    after = running()
    check(after["pid"] != before["pid"], "the service was not started again after the restart")
    check(paired(), "the paired browser was lost after the restart")
    # A project added before any coding agent can run waits for one, with no incident filed.
    project = "fictional-app"
    made = step("project", f"git init -q ~/{project} && git -C ~/{project} -c user.name=Newcomer "
                           f"-c user.email=newcomer@example.invalid commit -q --allow-empty -m start")
    check(made.returncode == 0, "the fictional project could not be made")
    status, answer = browser("project-add", "/api/project/add", {"name": project, "path": f"/Users/{ACCOUNT}/{project}"})
    check(status == 200 and answer.get("ok"), f"adding the project failed: {status} {answer}")
    # Setup runs in the background after the add; each poll's log replaces the last, so the final answer is kept.
    deadline = time.monotonic() + 120
    while True:
        status, answer = browser("project-setup", f"/api/setup/{project}")
        coordinator = next((item for item in answer.get("steps", []) if item["id"] == "coordinator"), {})
        if coordinator.get("status") == "waiting" or time.monotonic() > deadline:
            break
        steps.pop()
        time.sleep(5)
    record["coordinator"] = coordinator
    check(status == 200 and coordinator.get("status") == "waiting" and coordinator.get("action") == "tools"
          and re.fullmatch(r"Waiting for a coding tool\. Install \w+ \(Coding tools\), and setup continues by itself\.",
                           coordinator.get("detail", "")), f"setup: {status} {answer}")
    status, answer = browser("project-view", f"/api/project/{project}")
    check(status == 200 and answer.get("incidents") == [], f"the waiting project filed an incident: {answer.get('incidents')}")
    # ...and, with no browser paired and the installer's link used, certificate trust and pairing come next.
    status, answer = browser("revoke", "/api/devices/revoke", {"id": device})
    check(status == 200 and not paired(), f"the browser could not be unpaired: {status} {answer}")
    hint = doctor("doctor-trust")["next"]
    check(hint == ["Next: compare the certificate below with what your browser shows, trust it, then run alt pair."],
          f"next: {hint}")
    # From this SSH session, with the account's desktop logged in, `alt trust` answered y must fail for the login
    # keychain with the remote-terminal message and change nothing (#1006 check 3). The undo command it prints on a
    # desktop names the authority by SHA-256: `security delete-certificate -Z` must remove it by that name.
    keychain = f"/Users/{ACCOUNT}/Library/Keychains/login.keychain-db"
    # The refusal must leave no trust setting; `security add-trusted-cert` keeps the certificate itself untrusted.
    listed = "security dump-trust-settings 2>&1"
    unchanged = step("trust-before", listed).stdout
    # The answer is typed once the question shows: `alt trust` discards keys typed before it asks.
    answered = step("trust-ssh", f"/usr/bin/expect -c 'set timeout 240; spawn {alt} trust; expect \"Trust it?\"; "
                                 "send \"y\\r\"; expect {{ eof {{}} timeout {{ puts STILL-WAITING; "
                                 "exec ps -axo pid,etime,command >@ stdout; exit 9 }} }}; catch wait result; "
                                 "exit [lindex $result 3]'", timeout=300)
    trust_ssh = {"passed": "This is an SSH session" in answered.stdout
                 and "a remote terminal cannot show; run alt trust there, or follow the Mac steps on the pair page"
                 in answered.stdout.replace("\r", "").replace("\n", " ")
                 and step("trust-after", listed).stdout == unchanged}
    fingerprint = "openssl x509 -in ~/.config/altitude/tls/ca.crt -noout -fingerprint -sha256 | cut -d= -f2 | tr -d :"
    found = f"security find-certificate -a -Z {keychain} | grep -qi \"SHA-256 hash: $sha\""
    undone = step("undo-sha256", f"sha=$({fingerprint}) && {{ {found} || security add-certificates -k {keychain} "
                                 f"~/.config/altitude/tls/ca.crt; }} && {found} && "
                                 f"security delete-certificate -Z $sha -t {keychain} && ! {found}")
    trust_desktop = desktop_trust(guest, results, step, alt, listed, unchanged, printed_link)
    removed = step("uninstall", f"{alt} uninstall")
    check(removed.returncode == 0 and json.loads(removed.stdout)["uninstalled"], "uninstall failed")
    gone = step("removed", f"test ! -e {agent} && ! launchctl print gui/{ACCOUNT_UID}/{SERVICE_LABEL} >/dev/null 2>&1 && "
                           f"! curl -sk --max-time 5 https://127.0.0.1:{port}/api/health >/dev/null")
    check(gone.returncode == 0, "the LaunchAgent or the service is still there")
    record["cases"] = {"login": {"passed": True, "version": version, "service_label": SERVICE_LABEL,
                                 "pids": [before["pid"], after["pid"]]},
                       "trust-ssh": trust_ssh, "undo-sha256": {"passed": undone.returncode == 0},
                       "trust-desktop": trust_desktop, "pair-safari": {"passed": trust_desktop["pairing"]},
                       "address-vpn": {"passed": trust_desktop["vpn"]}}


# The browsers the online phase downloads from their publishers, and the team each one's Developer ID signature names.
BROWSERS = {"Google Chrome": ("https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg", "EQHXZ8M8AV"),
            "Firefox": ("https://download.mozilla.org/?product=firefox-latest-ssl&os=osx&lang=en-US", "43AQ936H96")}

# A Marionette client, Firefox's own remote protocol: opens ARGV[1] in a new session and prints whether the
# navigation succeeded or the error Firefox gave, and then the page's title and text.
MARIONETTE = r"""import json, socket, sys, time
for _ in range(90):
    try:
        sock = socket.create_connection(("127.0.0.1", 2828), timeout=120)
        break
    except OSError:
        time.sleep(1)
else:
    sys.exit(json.dumps({"error": "Marionette did not answer"}))
buffer, ident = b"", 0
def read():
    global buffer
    while b":" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise SystemExit(json.dumps({"error": "Marionette closed the connection"}))
        buffer += chunk
    size, _, buffer = buffer.partition(b":")
    while len(buffer) < int(size):
        chunk = sock.recv(65536)
        if not chunk:
            raise SystemExit(json.dumps({"error": "Marionette closed the connection"}))
        buffer += chunk
    message, buffer = buffer[:int(size)], buffer[int(size):]
    return json.loads(message)
def call(name, parameters):
    global ident
    ident += 1
    body = json.dumps([0, ident, name, parameters]).encode()
    sock.sendall(str(len(body)).encode() + b":" + body)
    _, _, error, result = read()
    return (error or {}).get("error"), (result or {}).get("value")
read()
call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
error, _ = call("WebDriver:Navigate", {"url": sys.argv[1]})
seen = {"error": error}
for _ in range(20):
    seen["title"] = call("WebDriver:GetTitle", {})[1]
    seen["text"] = call("WebDriver:ExecuteScript", {"script": "return document.body ? document.body.innerText : ''",
                                                    "args": []})[1]
    if error or "Pair this device" in (seen["text"] or ""):
        break
    time.sleep(1)
print(json.dumps(seen))
"""


def phase_online(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """The checks that need the internet, in the one phase whose guest keeps its network (#1006, #225, #226):
    Homebrew's python@3.13, and Google Chrome and Firefox from their publishers, downloaded in the guest; then the
    latest published release installed through the public command from GitHub itself, whose service must run on
    Python 3.13; then, on the account's desktop, Chrome and Firefox must refuse Altitude's address before `alt trust`
    and open it after. REPOSITORY is the checkout's GitHub repository."""
    add_github_cli(guest, record)
    steps = record["steps"] = []
    alt = f"/Users/{ACCOUNT}/.local/bin/alt"
    github = repository

    def step(name: str, command: str, timeout: int = 120) -> subprocess.CompletedProcess:
        result = guest.ssh(f"zsh -lc {quote(command)}", timeout=timeout, check=False)
        (results / f"{len(steps) + 1:02d}-{name}.log").write_text(f"$ {command}\n# exit {result.returncode}\n"
                                                                    f"{result.stdout}{result.stderr}")
        steps.append({"step": name, "exit": result.returncode})
        return result

    def check(condition: bool, what: str) -> None:
        steps[-1]["passed"] = bool(condition)
        if not condition:
            raise Stop(f"{steps[-1]['step']}: {what}")

    python = step("python3.13", "brew install python@3.13 >/dev/null && python3.13 --version", timeout=540)
    check(python.returncode == 0 and python.stdout.startswith("Python 3.13."), "python@3.13 did not install")
    record["browsers"] = {}
    for app, (url, team) in BROWSERS.items():
        name, bundle = app.split()[-1].lower(), f"/Applications/{app}.app"
        image, mount = f"{SHARED}/{name}.dmg", f"{SHARED}/{name}-image"
        got = step(f"{name}-download", f"curl -fsSL --max-time 600 -o {image} {quote(url)} && stat -f %z {image} && "
                                       f"hdiutil attach -quiet -nobrowse -readonly -mountpoint {mount} {image} && "
                                       f"ditto {quote(f'{mount}/{app}.app')} {quote(bundle)} && "
                                       f"hdiutil detach -quiet {mount} && rm {image}", timeout=900)
        check(got.returncode == 0, f"{app} did not download and install")
        signed = step(f"{name}-signature", f"codesign --verify --deep {quote(bundle)} && "
                                           f"spctl --assess --type execute {quote(bundle)} && "
                                           f"codesign -dv {quote(bundle)} 2>&1 | sed -n 's/^TeamIdentifier=//p' && "
                                           f"defaults read {quote(bundle + '/Contents/Info')} CFBundleShortVersionString")
        check(signed.returncode == 0 and signed.stdout.split()[:1] == [team], f"{app} is not signed by its publisher")
        record["browsers"][app] = {"version": signed.stdout.split()[-1], "team": team,
                                   "bytes": int(got.stdout.split()[0])}
    latest = step("latest", f"curl -fsSI {github}/releases/latest | tr -d '\\r' | sed -n 's|^location: .*/releases/tag/||Ip'")
    tag = latest.stdout.strip()
    check(latest.returncode == 0 and re.fullmatch(r"v\d+\.\d+\.\d+", tag), f"no latest release: {tag!r}")
    # The documented command as a person runs it: GitHub's own servers and the guest's own certificate store.
    installed = step("install", f"curl --proto '=https' --tlsv1.2 -fsSL {github}/releases/latest/download/install.sh | sh",
                     timeout=600)
    check(installed.returncode == 0 and f"Altitude {tag} is installed" in installed.stdout, "the installer failed")
    origin = re.search(r"^Address: (https://\S+:\d+) ", installed.stdout, re.M).group(1)
    health = f"curl -fsS --max-time 10 --cacert ~/.config/altitude/tls/ca.crt {origin}/api/health"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and guest.ssh(f"zsh -lc {quote(health)} >/dev/null", check=False).returncode:
        time.sleep(5)
    # The Python the service's process runs: the framework it loaded.
    runtime = step("runtime", f"pid=$({health} | python3.13 -c 'import json, sys; print(json.load(sys.stdin)[\"pid\"])')"
                              " && lsof -a -p $pid -d txt -Fn | sed -n 's/^n//p' | grep -m1 'Python.framework/Versions/'")
    check(runtime.returncode == 0 and "/Python.framework/Versions/3.13/" in runtime.stdout,
          "the service does not run on Python 3.13")
    record["release"], record["runtime"] = tag, runtime.stdout.strip()

    desktop = Desktop(guest, results, step)
    guest.ssh(f"cat > {SHARED}/marionette.py", stdin=MARIONETTE)

    def chrome(label: str) -> dict:
        """Chrome, started on the desktop with a profile of its own, loading Altitude's address: its exit status, the
        title it shows, and whether that page's load failed for the certificate (ERR_CERT_AUTHORITY_INVALID)."""
        out = f"{SHARED}/chrome-{label}"
        desktop.agent(f"chrome-{label}", ["/bin/sh", "-c", (
            f"{quote('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')} --headless=new --no-first-run "
            f"--no-default-browser-check --user-data-dir={out}.profile --enable-logging=stderr --virtual-time-budget=10000 "
            f"--dump-dom {quote(origin + '/')} >{out}.html 2>{out}.err; echo $? >{out}.exit")])
        ended = desktop.within(180, f"test -s {out}.exit")
        page = guest.ssh(f"cat {out}.html", check=False).stdout
        errors = guest.ssh(f"cat {out}.err", check=False).stdout
        (results / f"chrome-{label}.html").write_text(page)
        (results / f"chrome-{label}.err").write_text(errors)
        title = re.search(r"<title>(.*?)</title>", page, re.S)
        status = guest.ssh(f"cat {out}.exit", check=False).stdout.strip()
        # The headless command handler's own line for the page it dumps, not a background request's handshake.
        return {"ended": ended, "exit": status or None, "title": title and title.group(1).strip(),
                "refused": "Page load failed: net::ERR_CERT_AUTHORITY_INVALID" in errors}

    def firefox(label: str) -> dict:
        """Firefox, started headless on the desktop with a new profile and Marionette, opening Altitude's address."""
        profile = f"{SHARED}/firefox-{label}"
        guest.ssh(f"mkdir -p {profile}")
        desktop.agent(f"firefox-{label}", ["/Applications/Firefox.app/Contents/MacOS/firefox", "--headless",
                                           "--marionette", "--no-remote", "--profile", profile])
        seen = step(f"firefox-{label}", f"python3.13 -I {SHARED}/marionette.py {quote(origin + '/')}", timeout=240)
        guest.ssh(f"launchctl bootout gui/{ACCOUNT_UID}/lane.firefox-{label}", check=False)
        desktop.within(30, "! pgrep -x firefox")
        try:
            return json.loads(seen.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"error": f"no answer: {seen.stderr.strip()[-300:]}"}

    before = {"chrome": chrome("before"), "firefox": firefox("before")}
    window, trusted = desktop.trust(alt)
    after = {"chrome": chrome("after"), "firefox": firefox("after")}
    record["trust"] = {"window": window, "before": before, "after": after}
    accepted = bool(window) and trusted.endswith("exit 0\n")
    record["cases"] = {
        "github-install": {"passed": True, "release": tag},
        "python-3.13": {"passed": True, "runtime": record["runtime"]},
        "chrome-trust": {"passed": accepted and before["chrome"]["ended"] and before["chrome"]["refused"]
                         and before["chrome"]["title"] != "Altitude"
                         and after["chrome"]["ended"] and after["chrome"]["title"] == "Altitude"
                         and not after["chrome"]["refused"]},
        "firefox-trust": {"passed": accepted and before["firefox"].get("error") == "insecure certificate"
                          and not after["firefox"].get("error") and after["firefox"].get("title") == "Altitude"
                          and "Pair this device" in (after["firefox"].get("text") or "")}}


# The confinement rows that must refuse private places without macOS asking, and how their details say so.
PRIVACY_ROWS, REFUSED = ("confinement", "validation-confinement"), \
    "the Desktop and mounted volumes are refused with no consent request in the privacy log"
# The rows that run fixture commands as launchd jobs: output, Stop of every descendant, cleanup, time limits and a
# confined job's launch input.
JOB_ROWS = ("job-pipe", "job-file-output", "exit-cleanup", "stop-descendants", "time-limit", "limit-after-owner-exit",
            "sleep-hold", "awake-clock", "logged-job", "detached-job", "launch-input")
# The suite's read-only-home cases, which confine a child with a second Seatbelt profile or Codex's own sandbox and so
# skip in a validation run or a confined worker (docs/DEVELOPMENT.md#macos-validation-runs).
READ_ONLY_HOME = ("tests.test_worker_writes", "tests.test_worker_roots",
                  "tests.test_land.TestLand.test_the_owner_lands_with_altitudes_home_read_only")


def in_source(guest: Guest, inputs: Path, command: str, timeout: int) -> subprocess.CompletedProcess:
    """`command` run by the logged-in account, outside any sandbox, in the revision's files unpacked in the guest."""
    guest.copy(inputs / "source.tar", f"{SHARED}/source.tar")
    return guest.ssh(f"mkdir {SHARED}/source && tar -xf {SHARED}/source.tar -C {SHARED}/source && cd {SHARED}/source && "
                     f"{command}", timeout=timeout, check=False)


def probe_rows(guest: Guest, results: Path, record: dict, inputs: Path, case: str, rows: tuple[str, ...],
               detail: str = "") -> None:
    """platform_probe.py's `rows` from the revision's own files, run by the logged-in account outside any sandbox;
    the case passes when every row passes and its detail says `detail`."""
    command = f"/opt/homebrew/bin/python3.12 -B scripts/platform_probe.py --only {' '.join(rows)}"
    result = in_source(guest, inputs, command, 300)
    (results / f"{case}.log").write_text(f"$ {command}\n# exit {result.returncode}\n{result.stdout}{result.stderr}")
    try:
        found = json.loads(result.stdout)["rows"]
    except (ValueError, KeyError):
        found = []
    record["python"] = guest.ssh("/opt/homebrew/bin/python3.12 --version").stdout.strip()
    record["cases"] = {case: {"exit": result.returncode, "rows": found, "passed": result.returncode == 0 and sorted(
        row["row"] for row in found) == sorted(rows) and all(row["state"] == "pass" and detail in row["detail"]
                                                             for row in found)}}


def phase_privacy(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """The confinement rows in a guest whose Python was never asked for access to a private place: a worker's and a
    validation run's profiles must refuse the Desktop and mounted volumes before macOS asks, so its privacy log shows
    no request."""
    probe_rows(guest, results, record, inputs, "privacy", PRIVACY_ROWS, REFUSED)


def phase_jobs(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """The job rows, which a worker sandbox cannot run because launchd refuses it service control: real launchd jobs
    in the account's own domain, whose Stop must end every descendant, however it left the session, before the job
    reads inactive."""
    probe_rows(guest, results, record, inputs, "jobs", JOB_ROWS)


def phase_read_only_home(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """The suite's read-only-home cases, which no validation run or worker can start: each must run and pass, none
    skipped, its child confined by a Seatbelt profile that keeps Altitude's home read-only, the roots case with the
    roots each engine's worker launch names."""
    environment = f"ALTITUDE_VALIDATION=1 TMPDIR={SHARED}/tmp"
    guest.ssh(f"mkdir {SHARED}/tmp")
    command = f"env {environment} /opt/homebrew/bin/python3.12 -B -m unittest -v {' '.join(READ_ONLY_HOME)}"
    result = in_source(guest, inputs, command, 900)
    (results / "read-only-home.log").write_text(f"$ {command}\n# exit {result.returncode}\n{result.stdout}{result.stderr}")
    # unittest's verbose lines: `name (id) ... outcome`, with a docstring's first line before the dots when there is one.
    found = dict(re.findall(r"^test\w+ \(([\w.]+)\)(?:\n.*)? \.\.\. (.*)$", result.stderr, re.MULTILINE))
    record["python"] = guest.ssh("/opt/homebrew/bin/python3.12 --version").stdout.strip()
    record["cases"] = {"read-only-home": {"exit": result.returncode, "tests": found, "passed": result.returncode == 0
                                          and bool(found) and all(outcome == "ok" for outcome in found.values())}}


def phase_mutation(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """The mutation run on macOS (#993), whose cleanup lists processes with `ps`, which no sandbox here allows. Its
    hash-pinned packages are downloaded on this Mac and installed in the offline guest from those files. Each of two
    runs of the task door is interrupted once mutmut runs; its scratch folder, mutmut and a planted process naming
    that folder must be gone afterwards, and the second run starts afresh. A whole run takes hours on four CPUs."""
    result, found = mutation(guest, inputs, results, "", 900)
    for n in (1, 2):
        try:
            guest.fetch(f"{SHARED}/mutation-{n}.log", results / f"mutation-{n}.log")
        except subprocess.CalledProcessError:  # the run never started
            pass
    record["cases"] = {"mutation": {"exit": result.returncode, **found}}


def phase_mutation_complete(guest: Guest, repository: str, results: Path, record: dict, inputs: Path,
                            commit: str) -> None:
    """One whole mutation run of the task door on macOS (#993), from the same packages: it must end with its report,
    which has the door's row, and leave no scratch folder. About five hours on the guest's four CPUs."""
    result, found = mutation(guest, inputs, results, " complete altitude/door.py", 10 * 3600)
    for name in ("mutation-complete.log", "mutation-complete"):
        try:
            guest.fetch(f"{SHARED}/{name}", results / name)
        except subprocess.CalledProcessError:  # the run never started
            pass
    record["cases"] = {"mutation-complete": {"exit": result.returncode, **found}}


def mutation(guest: Guest, inputs: Path, results: Path, arguments: str,
             timeout: int) -> tuple[subprocess.CompletedProcess, dict]:
    """tests/macos_guest_mutation.py with `arguments` in the revision's files made a Git checkout, with mutmut's
    hash-pinned packages downloaded on this Mac; its result and the JSON object it printed."""
    wheels = inputs / "mutation-wheels"
    run(sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--no-cache-dir",
        "--require-hashes", "--only-binary=:all:", "-d", wheels, "-r", SCRIPTS / "mutation-requirements.txt",
        timeout=300)
    guest.copy(wheels, f"{SHARED}/wheels")
    command = (f"git init -q && git add -A && git -c user.name=Lane -c user.email=lane@example.invalid commit -qm "
               f"source && PIP_NO_INDEX=1 PIP_FIND_LINKS={SHARED}/wheels {GUEST_PYTHON} -B tests/macos_guest_mutation.py"
               f"{arguments}")
    result = in_source(guest, inputs, command, timeout)
    (results / "mutation.log").write_text(f"$ {command}\n# exit {result.returncode}\n{result.stdout}{result.stderr}")
    try:
        return result, json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return result, {"passed": False}


HOMEBREW = Path("/opt/homebrew")
CHECKOUT = f"/Users/{ACCOUNT}/Projects/altitude"
SOURCE_COPY = f"/Users/{ACCOUNT}/.altitude-repository"   # config.SOURCE_COPY beside the default ALTITUDE_HOME
# A file under scripts/, which activation covers, so the newer main makes the service rebuild and restart, and a mark
# in the web app's page, which only a rebuilt bundle serves.
DEPLOYED = "scripts/vm-lane-deploy.txt"
PAGE_MARK = '<meta name="altitude-vm-lane" content="newer-main">'


def homebrew_kegs(binary: Path) -> list[Path]:
    """The Homebrew kegs `binary` and every library it loads come from, its own first."""
    kegs: list[Path] = []
    pending = [binary.resolve()]
    while pending:
        path = pending.pop(0)
        try:
            relative = path.relative_to(HOMEBREW / "Cellar")
        except ValueError:
            continue
        keg = HOMEBREW / "Cellar" / relative.parts[0] / relative.parts[1]
        if keg in kegs:
            continue
        kegs.append(keg)
        files = [item for folder in ("bin", "lib") if (keg / folder).is_dir() for item in (keg / folder).iterdir()
                 if item.is_file() and not item.is_symlink() and (folder == "bin" or item.suffix == ".dylib")]
        for item in files:
            listed = subprocess.run(["otool", "-L", str(item)], capture_output=True, text=True, timeout=30).stdout
            pending += [Path(line.split()[0]).resolve() for line in listed.splitlines()[1:]
                        if line.strip().startswith(f"{HOMEBREW}/")]
    return kegs


def source_inputs(commit: str, work: Path) -> None:
    """What the offline guest needs for a source install: the commit's history as the bundle its origin is cloned
    from, and this Mac's Node with the kegs it links, the pnpm version web/package.json pins from Corepack's cache and
    the pnpm store holding the web packages, which `make web` and activation install from. No registry setting or
    credential goes with them."""
    node = shutil.which("node")
    kegs = homebrew_kegs(Path(node)) if node else []
    if not kegs:
        raise Stop(f"the source phase copies Homebrew's Node with the kegs it links into the guest; node is {node}")
    manager = json.loads((SCRIPTS.parent / "web/package.json").read_text())["packageManager"]
    version = manager.split("@", 1)[1].split("+", 1)[0]
    corepack = Path(os.environ.get("COREPACK_HOME") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
                    / "node/corepack") / "v1/pnpm" / version
    if not corepack.is_dir():
        raise Stop(f"pnpm {version} is not in Corepack's cache on this Mac ({corepack}); the guest downloads nothing")
    store = Path(run("pnpm", "store", "path", cwd=SCRIPTS.parent / "web").stdout.strip())
    if not store.is_dir():
        raise Stop(f"this Mac's pnpm store ({store}) is missing; install the web packages once")
    run("tar", "-cf", work / "kegs.tar", "-C", HOMEBREW, *(keg.relative_to(HOMEBREW) for keg in kegs), timeout=300)
    (work / "kegs.txt").write_text("".join(f"{keg.relative_to(HOMEBREW)}\n" for keg in kegs))
    run("tar", "-cf", work / "pnpm.tar", "--exclude", ".npmrc", "-C", corepack.parent, version, timeout=120)
    (work / "pnpm-version").write_text(version)
    run("tar", "-cf", work / "store.tar", "--exclude", ".npmrc", "-C", store, ".", timeout=300)
    with tempfile.TemporaryDirectory(dir=work) as staging:
        run("git", "init", "-q", "--bare", staging)
        run("git", "-C", staging, "fetch", "-q", "--no-tags", SCRIPTS.parent, f"{commit}:refs/heads/main", timeout=300)
        run("git", "-C", staging, "bundle", "create", "-q", work / "source.bundle", "main", timeout=300)


def phase_source(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """A source install as OPERATIONS.md's "Run from a source checkout" describes, from an origin in the guest: the
    first start creates Altitude's own copy of its repository and runs that commit, exported from the copy; with the
    checkout registered as the project `altitude`, a newer main pushed to the origin is fast-forwarded through the
    copy, built, and the service restarts into it."""
    add_github_cli(guest, record)
    record["prerequisites"] = prerequisites(guest)
    steps = record["steps"] = []
    for name in ("source.bundle", "kegs.tar", "pnpm.tar", "store.tar"):
        guest.copy(inputs / name, f"{SHARED}/{name}")
    node = next(line for line in (inputs / "kegs.txt").read_text().splitlines()).split("/")[1]
    path = (f"/opt/homebrew/opt/python@3.12/libexec/bin:/opt/homebrew/opt/{node}/bin:/opt/homebrew/bin:"
            "/usr/bin:/bin:/usr/sbin:/sbin")
    origin = "curl -fsS --max-time 10 --cacert ~/.config/altitude/tls/ca.crt https://127.0.0.1:8890"
    health = f"{origin}/api/health"

    def step(name: str, command: str, timeout: int = 120) -> subprocess.CompletedProcess:
        result = guest.ssh(f"zsh -lc {quote(f'export PATH={path}; {command}')}", timeout=timeout, check=False)
        (results / f"{len(steps) + 1:02d}-{name}.log").write_text(f"$ {command}\n# exit {result.returncode}\n"
                                                                    f"{result.stdout}{result.stderr}")
        steps.append({"step": name, "exit": result.returncode})
        return result

    def check(condition: bool, what: str) -> None:
        steps[-1]["passed"] = bool(condition)
        if not condition:
            raise Stop(f"{steps[-1]['step']}: {what}")

    def running(name: str) -> int:
        """The LaunchAgent's process, which answers HTTPS health on the generated authority."""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and guest.ssh(
                f"launchctl print gui/{ACCOUNT_UID}/{SERVICE_LABEL} | grep -q 'state = running' && {health} >/dev/null",
                check=False).returncode:
            time.sleep(5)
        answer = step(name, f"launchctl print gui/{ACCOUNT_UID}/{SERVICE_LABEL} && {health}")
        lines = answer.stdout.strip().splitlines()
        served = json.loads(lines[-1]) if answer.returncode == 0 and lines else {}
        # A source install has no release, so health names no version or commit.
        check(f"path = /Users/{ACCOUNT}/Library/LaunchAgents/{SERVICE_LABEL}.plist" in answer.stdout
              and served.get("version", "") is None and served.get("commit", "") is None
              and f"pid = {served.get('pid')}" in answer.stdout,
              "the LaunchAgent is not running from the account's LaunchAgents or does not answer health as a source "
              "install")
        return served["pid"]

    def page(name: str) -> bool:
        """Whether the page the service serves carries the newer main's mark."""
        served = step(name, f"{origin}/")
        check(served.returncode == 0 and "<html" in served.stdout, "the service does not serve the web app's page")
        return PAGE_MARK in served.stdout

    def runs(name: str, expected: str) -> None:
        """The checkout, Altitude's copy and the export the service starts from all name `expected`."""
        found = step(name, f"git -C {CHECKOUT} rev-parse HEAD; git -C {CHECKOUT} status --porcelain; "
                           f"git -C {SOURCE_COPY} rev-parse --is-bare-repository main; "
                           f"git -C {SOURCE_COPY} remote get-url origin; readlink {CHECKOUT}/.altitude-source/current; "
                           f"test -d {CHECKOUT}/.altitude-source/{expected}/altitude")
        check(found.returncode == 0 and found.stdout.split() == [expected, "true", expected, f"{SHARED}/origin.git",
                                                                 expected],
              f"the checkout, Altitude's copy of its repository and the running export do not all hold {expected[:12]}")

    try:
        # This Mac's Node, its kegs the guest lacks, the pinned pnpm in Corepack's cache and the web packages.
        tools = step("toolchain", f"""set -e
cd /opt/homebrew
while read -r keg; do
  name=${{keg#Cellar/}}; name=${{name%/*}}
  if [ -e opt/$name ]; then echo "present $name"; else tar -xf {SHARED}/kegs.tar "$keg"; ln -s ../$keg opt/$name; echo "added $name"; fi
done <<'KEGS'
{(inputs / "kegs.txt").read_text()}KEGS
mkdir -p ~/.cache/node/corepack/v1/pnpm && tar -xf {SHARED}/pnpm.tar -C ~/.cache/node/corepack/v1/pnpm
corepack enable pnpm
node --version""")
        check(tools.returncode == 0, "Node does not run in the guest")
        record["toolchain"] = tools.stdout.strip().splitlines()
        # Corepack runs the pnpm web/package.json pins; outside web it would look for another version online.
        cloned = step("clone", f"git clone -q --bare {SHARED}/source.bundle {SHARED}/origin.git && "
                               f"mkdir -p ~/Projects && git clone -q {SHARED}/origin.git {CHECKOUT} && "
                               f"git -C {CHECKOUT} rev-parse HEAD && cd {CHECKOUT}/web && pnpm --version && "
                               "pnpm store path", timeout=300)
        found = cloned.stdout.split()
        check(cloned.returncode == 0 and found[:2] == [commit, (inputs / "pnpm-version").read_text()],
              "the clone is not the source commit or the pinned pnpm does not run")
        store = found[2]
        check(step("store", f"mkdir -p {store} && tar -xf {SHARED}/store.tar -C {store} && rm {SHARED}/store.tar",
                   timeout=300).returncode == 0, "the pnpm store did not unpack")
        check(step("make-web", f"cd {CHECKOUT} && make web", timeout=420).returncode == 0, "make web failed")
        check(step("tls-init", f"cd {CHECKOUT} && bin/alt tls-init").returncode == 0, "alt tls-init failed")
        check(step("no-copy-yet", f"test ! -e {SOURCE_COPY}").returncode == 0,
              "Altitude's copy of its repository exists before the first start")
        installed = step("install-service", f"cd {CHECKOUT} && make install-service", timeout=180)
        check(installed.returncode == 0 and f"{SERVICE_LABEL} on main: active" in installed.stdout,
              "make install-service did not start the service")
        first = running("first-start")
        runs("first-start-source", commit)
        check(not page("first-start-page"), "the first start already serves the newer main's page")
        check(step("register", f"cd {CHECKOUT} && bin/alt project add altitude --path {CHECKOUT}").returncode == 0,
              "the checkout did not register as the project altitude")
        pushed = step("newer-main", f"""set -e
git clone -q {SHARED}/origin.git {SHARED}/newer && cd {SHARED}/newer
echo 'A newer main the macOS VM lane deploys.' > {DEPLOYED}
sed -i '' 's#</head>#{PAGE_MARK}</head>#' web/index.html
grep -qF '{PAGE_MARK}' web/index.html
git add {DEPLOYED} web/index.html
git -c user.name='macOS VM lane' -c user.email=vm-lane@example.invalid commit -qm 'A newer main for the VM lane'
git push -q origin main && git rev-parse HEAD""")
        check(pushed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", pushed.stdout.strip() or "") is not None,
              "the newer main was not pushed to the origin")
        newer = pushed.stdout.strip()
        record["commits"] = {"first": commit, "newer": newer}
        # The tick fetches within thirty seconds; activation builds the web app and restarts at the quiet point.
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline and guest.ssh(
                f"test \"$(readlink {CHECKOUT}/.altitude-source/current)\" = {newer} && "
                f"test ! -e ~/.altitude/monitor/restart-pending.json && {health} >/dev/null", check=False).returncode:
            time.sleep(10)
        second = running("deployed")
        check(second != first, "the service did not restart into the newer main")
        runs("deployed-source", newer)
        check(page("deployed-page"), "the service still serves the web app built before the newer main")
        record["cases"] = {"source": {"passed": True, "commits": [commit, newer], "pids": [first, second]}}
    finally:
        guest.ssh(f"cp -R ~/Library/Logs/altitude {SHARED}/logs", check=False)
        try:
            guest.fetch(f"{SHARED}/logs", results / "logs")
        except subprocess.SubprocessError:
            pass


# The runtime rows each phase drives in the guest (tests/macos_guest_runtime.py), split so that a `run` of one phase
# finishes within `alt task run`'s ten minutes.
RUNTIME_ROWS = {"workers": ("coordinator-stop", "failed-turn", "status-unknown", "validation-restart"),
                "grants": ("grants", "contention", "sign-in"), "incident": ("incident", "schedule"),
                "update": ("quiet-point",), "terminal": ("operator-step", "terminal-hold"),
                "session": ("scratch", "coordinator-channel", "logout-login")}
# The phases whose rows read Altitude's page in Safari.
BROWSED = ("update", "terminal")
GUEST_PYTHON = "/opt/homebrew/bin/python3.12"
# Rows may end the account's session, so they run in the background while the host polls.
ROWS_BUDGET = 420


def browser(guest: Guest, results: Path) -> None:
    """Safari ready for the rows on the account's desktop: Altitude's authority trusted through `alt trust` and macOS's
    password window, as the installer offers it, since Safari's WebDriver cannot accept a certificate Safari does not
    trust, and its WebDriver listening for the rows' own windows."""
    def step(name: str, command: str) -> subprocess.CompletedProcess:
        result = guest.ssh(f"zsh -lc {quote(command)}", timeout=120, check=False)
        (results / f"{name}.log").write_text(f"$ {command}\n# exit {result.returncode}\n{result.stdout}{result.stderr}")
        return result

    desktop = Desktop(guest, results, step)
    window, trusted = desktop.trust(f"/Users/{ACCOUNT}/.local/bin/alt")
    if not window or "Trusted in macOS login keychain" not in trusted:
        raise Stop("Safari's trust of Altitude's authority failed; see trust-desktop.log")
    if not desktop.safari():
        raise Stop("Safari's WebDriver did not start")


def runtime(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, phase: str) -> None:
    """The release installed into the account's own home through the public command, with the fictional Codex CLI as
    its only engine, the tests' fake GitHub CLI first on its saved PATH and api.github.com answered on the guest's
    loopback, a fictional project registered, then PHASE's rows as the account."""
    record["prerequisites"] = prerequisites(guest)
    script = f"{GUEST_PYTHON} -B {SHARED}/source/tests/macos_guest_runtime.py"
    setup = in_source(guest, inputs, f"zsh -lc {quote(script + ' setup')}", 120)
    if setup.returncode:
        raise Stop(f"the guest's setup failed: {setup.stderr.strip()[-300:]}")
    record["setup"] = json.loads(setup.stdout)
    guest.ssh("echo '127.0.0.1 api.github.com' | sudo -n tee -a /etc/hosts >/dev/null")
    version = re.search(r"^VERSION='([^']+)'", (inputs / "release/install.sh").read_text(), re.M).group(1)
    fixtures = (f"export CODEX_BIN={SHARED}/fixture/codex ALTITUDE_PRIMARY_ENGINE=codex "
                f"ALTITUDE_UPSTREAM_ISSUE_REPOSITORY=fictional-owner/fictional-altitude PATH={SHARED}/fixture/bin:$PATH; ")
    for name, command, timeout, expected in (
            ("install", fixtures + public_command(repository), 300, f"Altitude {version} is installed"),
            ("project", "~/.local/bin/alt project add fictional --path ~/Projects/fictional --l2-engine codex", 120, "")):
        result = guest.ssh(f"zsh -lc {quote(command)}", timeout=timeout, check=False)
        (results / f"{name}.log").write_text(f"$ {command}\n# exit {result.returncode}\n{result.stdout}{result.stderr}")
        if result.returncode or expected not in result.stdout:
            raise Stop(f"{name} failed; see {name}.log")
    if phase in BROWSED:
        browser(guest, results)
    # The operator's shell, as it installed: the fake GitHub CLI first on PATH for `alt` commands run from it.
    rows = (f"PATH={SHARED}/fixture/bin:$PATH LANE_VERSION={version} LANE_LATEST={SHARED}/root/repos/{repository}/releases/latest "
            f"{script} rows {' '.join(RUNTIME_ROWS[phase])} >{SHARED}/rows.jsonl 2>{SHARED}/rows.err; "
            f"echo $? >{SHARED}/rows.exit")
    guest.ssh(f"cd {SHARED} && nohup zsh -lc {quote(rows)} </dev/null >/dev/null 2>&1 &")
    deadline = time.monotonic() + ROWS_BUDGET
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            if guest.ssh(f"test -f {SHARED}/rows.exit", timeout=30, check=False).returncode == 0:
                break
        except subprocess.TimeoutExpired:  # between sessions
            pass
    # Whatever the rows wrote, and the records they read: the fixture's turns, the service's log and the tasks.
    guest.ssh(f"mkdir -p {SHARED}/evidence && cp /private/tmp/altitude-fixture/log.jsonl {SHARED}/evidence/ 2>/dev/null; "
              f"cp -R ~/Library/Logs/altitude {SHARED}/evidence/logs 2>/dev/null; "
              f"cp -R ~/.altitude/fictional/tasks {SHARED}/evidence/tasks 2>/dev/null; "
              f"cp -R ~/.altitude/monitor {SHARED}/evidence/monitor 2>/dev/null; "
              f"cp {SHARED}/rows.jsonl {SHARED}/rows.err {SHARED}/evidence/ 2>/dev/null; true", timeout=120, check=False)
    guest.fetch(f"{SHARED}/evidence", results / "evidence")
    found = [json.loads(line) for line in (results / "evidence/rows.jsonl").read_text().splitlines()
             if line.startswith("{")] if (results / "evidence/rows.jsonl").exists() else []
    done = {row["row"]: row for row in found}
    record["cases"] = {name: {"passed": bool(done.get(name, {}).get("passed")),
                              **({"unavailable": done[name]["unavailable"]} if done.get(name, {}).get("unavailable") else {}),
                              **({"error": done[name]["error"]} if done.get(name, {}).get("error") else {}),
                              **({} if name in done else {"error": "the row did not finish"})}
                       for name in RUNTIME_ROWS[phase]}


def phase_workers(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """A coordinator's Stop, a worker ended at its failed turn, a worker whose job cannot be read, and a validation
    run interrupted by the service's restart (#779, #782, #678, #650)."""
    runtime(guest, repository, results, record, inputs, "workers")


def phase_grants(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """Granted commands as launchd jobs: revocation stops one, also across the service's restart, the coordinator
    reuses an answer for the identical purpose, and a second command waits for the one machine-run place (#656, #668,
    #728); Altitude's own Claude sign-in from Settings saves its token privately and leaves nothing behind (#1025)."""
    runtime(guest, repository, results, record, inputs, "grants")


def phase_incident(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """Incident issues held on a release behind a newer one, and sent to the fake GitHub CLI otherwise (#767); then
    the release lookup's 12-hour schedule and the once-a-day notice on the guest's own clock (#226, #731)."""
    runtime(guest, repository, results, record, inputs, "incident")


def phase_update(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """An automatic update offered during a validation run waits for it, holding new runs, and installs itself once the
    run ends, while the validating task's worker stays in its turn; Safari shows what it waits for, its installation
    and its result (#1003)."""
    runtime(guest, repository, results, record, inputs, "update")


def phase_terminal(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """A command the coordinator hands the operator waits in Needs you until the project terminal runs it to its end
    (#1017); an automatic update waits for the open project terminal, as Safari shows, and installs itself once it
    closes (#1003)."""
    runtime(guest, repository, results, record, inputs, "terminal")


def phase_session(guest: Guest, repository: str, results: Path, record: dict, inputs: Path, commit: str) -> None:
    """A worker's scratch folder through completion (#958), the coordinator channel refusing a worker and another
    project's coordinator (#943), then logout and login without a restart (#731, #226).
    The guest cannot sleep: macOS under Apple's Virtualization framework refuses `pmset sleepnow` as not permitted;
    the `jobs` phase's `sleep-hold` and `awake-clock` rows cover a job across sleep."""
    runtime(guest, repository, results, record, inputs, "session")


# `fresh` starts from the fresh image, every other phase from prerequisites.
PHASES = {"fresh": phase_fresh, "prerequisites": phase_prerequisites, "lifecycle": phase_lifecycle, "login": phase_login,
          "privacy": phase_privacy, "jobs": phase_jobs, "read-only-home": phase_read_only_home,
          "mutation": phase_mutation, "source": phase_source,
          "workers": phase_workers, "grants": phase_grants, "incident": phase_incident, "update": phase_update,
          "terminal": phase_terminal, "session": phase_session,
          "published": phase_published, "online": phase_online, "mutation-complete": phase_mutation_complete}
# The phases a run includes only when named: `published` with --published, `online` and `mutation-complete` with
# --phase.
NAMED = ("published", "online", "mutation-complete")
# The phases that install the release built from the source, and those that also read the revision's files.
BUILT = ("fresh", "prerequisites", "lifecycle", "login", *RUNTIME_ROWS)
SOURCED = ("privacy", "jobs", "read-only-home", "mutation", "mutation-complete", *RUNTIME_ROWS)


def build(commit: str, work: Path, log: Path, version: str = "v0.0.1", output: str = "release") -> None:
    """The release, built in `work` with packages already in this Mac's pnpm store; the build downloads nothing."""
    temp = work / "tmp"
    temp.mkdir()
    environment = {**os.environ, "TMPDIR": str(temp), "XDG_CACHE_HOME": str(temp), "npm_config_cache": str(temp),
                   "npm_config_offline": "true"}
    with log.open("w") as stream:
        subprocess.run([sys.executable, "-B", str(SCRIPTS / "build_release.py"), "--version", version,
                        "--output", str(work / output), "--source", commit], stdout=stream, stderr=subprocess.STDOUT,
                       env=environment, check=True, timeout=300)


def run_phase(name: str, inputs: Path, results: Path, record: dict, commits: str) -> None:
    source = IMAGES / ("fresh" if name == "fresh" else "prerequisites")
    if not source.is_dir():
        raise Stop(f"the {name} guest is not built; run `installation_macos_vm.py image` first")
    need(8 * GIB, f"the {name} run")
    phase = record["phases"][name] = {"image": json.loads((source / "image.json").read_text()), "passed": False}
    started = time.monotonic()
    work = RUNS / f"{time.strftime('%H%M%S')}-{name}"
    folder = results / name
    folder.mkdir(parents=True)
    guest = None
    try:
        clone(source, work / "guest")
        note(f"{name}: booting a clone")
        guest = Guest(work / "guest", work, folder / "vm.log")
        guest.wait_ssh(300)
        phase["guest"] = guest.ssh("sw_vers -productVersion; sw_vers -buildVersion").stdout.split()
        if name == "online":
            note(f"{name}: checking the guest's network, which stays plugged in")
            phase["reachable"] = {"online": reachable(guest)}
            if not phase["reachable"]["online"]["internet"]:
                raise Stop(f"the online guest does not reach the internet: {phase['reachable']}")
        else:
            note(f"{name}: checking the guest's network, then unplugging it")
            isolate(guest, phase)
        guest.ssh(f"sudo -n mkdir -p {SHARED} && sudo -n chown {ACCOUNT} {SHARED}")
        repository = record.get("github") if name == "online" else None
        if (inputs / "release").is_dir() and name != "online":
            # The built release, answered as github.com on the guest's loopback.
            serve = work / "serve"
            serve.mkdir()
            certificates(serve)
            repository = release_tree(inputs / "release", serve)
            for name_ in ("root", "ca.pem", "server.pem", "server.key"):
                guest.copy(serve / name_, f"{SHARED}/{name_}")
            guest.ssh(f"chmod -R a+rX {SHARED}")
            guest.ssh(SERVE)
        note(f"{name}: running the phase")
        PHASES[name](guest, repository, folder, phase, inputs, commits)
        phase["guest_load"] = guest.ssh("sysctl -n vm.loadavg").stdout.split()[1:4]
        phase["reachable"]["after"] = reachable(guest)
        if name != "online" and any(phase["reachable"]["after"].values()):
            raise Stop(f"the guest was not isolated at the end: {phase['reachable']}")
        phase["passed"] = all(case["passed"] for case in phase["cases"].values())
    except Exception as error:  # the phase failed; the clone is still deleted and the record written
        phase["error"] = f"{type(error).__name__}: {error}"
    finally:
        if guest:
            guest.stop()
        # The clone shares the image's blocks; deleting it frees what it wrote.
        before = free()
        shutil.rmtree(work, ignore_errors=True)
        phase["clone_gib"] = round((free() - before) / GIB, 2)
        phase["seconds"] = round(time.monotonic() - started)
        note(f"{name}: clone deleted; {'passed' if phase['passed'] else 'failed'}")


def lane(results: Path, commit: str | None, phases: list[str], releases: tuple[str, str | None] | None = None) -> int:
    """Runs PHASES against COMMIT's release and files, or with RELEASES only `published` from the published baseline
    to the published candidate or, when RELEASES names none, to COMMIT's release under the baseline's next minor."""
    results.mkdir(parents=True, exist_ok=True)
    git = lambda *args: run("git", *args, cwd=SCRIPTS.parent).stdout.strip()
    record = {"source_commit": commit, "harness": {"commit": git("rev-parse", "HEAD"),
              "modified": bool(git("status", "--porcelain", "--", "scripts"))},
              "host": {"macos": platform.mac_ver()[0], "machine": platform.machine(),
                       "free_gib_before": round(free() / GIB, 1), "load_before": os.getloadavg()},
              "vm": {"cpus": CPUS, "memory_gib": MEMORY_GIB, "disk_gib": DISK_GIB},
              "images_gib": round(sum(json.loads(path.read_text()).get("disk_gib", 0) for path in IMAGES.glob("*/image.json")), 1),
              "phases": {}, "passed": False}
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="release.", dir=CACHE))
    try:
        commits = commit
        if releases:
            github = repository()
            for name, tag in zip(("baseline", "candidate"), releases):
                if tag:
                    note(f"downloading the published {tag} as the {name}")
                    try:
                        record.setdefault("published", {})[name] = published(github, tag, work / name)
                    except SystemExit as error:  # the shared download reports a mismatch this way
                        raise Stop(str(error)) from None
            if not releases[1]:
                version = next_minor(releases[0])
                note(f"building the candidate {version} from {commit[:12]}")
                build(commit, work, results / "build.log", version, "candidate")
                record["published"]["candidate"] = {"release": version, "commit": commit, "built": True}
            commits = f"{record['published']['baseline']['commit']}..{record['published']['candidate']['commit']}"
        if set(phases) & set(BUILT):
            note(f"building the release from {commit[:12]}")
            build(commit, work, results / "build.log")
        if set(phases) & set(SOURCED):
            run("git", "archive", "-o", work / "source.tar", commit, cwd=SCRIPTS.parent)
        if "online" in phases:
            record["github"] = repository()
        if "source" in phases:
            note("bundling the source commit and this Mac's Node, pnpm and web packages for the source phase")
            source_inputs(commit, work)
        for name in phases:
            run_phase(name, work, results, record, commits)
        record["passed"] = all(record["phases"][name]["passed"] for name in phases)
    except (Stop, subprocess.SubprocessError) as error:
        record["error"] = str(error)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        record["seconds"] = round(time.monotonic() - started)
        record["host"]["free_gib_after"] = round(free() / GIB, 1)
        record["host"]["load_after"] = os.getloadavg()
        (results / "macos-vm.json").write_text(json.dumps(record, indent=2) + "\n")
        note(("passed" if record["passed"] else "failed") + f"; evidence in {results}")
    return 0 if record["passed"] else 1


def clean() -> int:
    if CACHE.exists():
        for entity in plistlib.loads(run("hdiutil", "info", "-plist").stdout.encode()).get("images", []):
            if entity.get("image-path", "").startswith(str(CACHE)):
                for item in entity.get("system-entities", []):
                    subprocess.run(["hdiutil", "detach", "-force", item["dev-entry"]], capture_output=True)
                    break
        shutil.rmtree(CACHE)
    note(f"removed {CACHE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    image_parser = commands.add_parser("image", help="build the fresh and prerequisites guests")
    image_parser.add_argument("--step", action="store_true", help="do only the next step")
    run_parser = commands.add_parser("run", help="run the installer in clones of the guests")
    run_parser.add_argument("results", type=Path)
    run_parser.add_argument("--source", default="HEAD", help="committed revision to build and test (default: HEAD)")
    run_parser.add_argument("--phase", choices=[name for name in PHASES if name != "published"], action="append",
                            help="run only this phase (repeatable); `online` and `mutation-complete` run only when named")
    run_parser.add_argument("--published", metavar="BASELINE[..CANDIDATE]",
                            help="run only the published phase, updating the published BASELINE to the published "
                                 "CANDIDATE or, without one, to the source revision built under BASELINE's next minor")
    commands.add_parser("clean", help="delete the images, the helper and any leftover clone")
    args = parser.parse_args()
    releases = None
    if getattr(args, "published", None):
        baseline, separator, candidate = args.published.partition("..")
        try:
            releases = (release_version(baseline), release_version(candidate) if separator else None)
        except SystemExit as error:
            parser.error(str(error))
        if args.phase:
            parser.error("--published runs only the published phase")
    if sys.platform != "darwin" or platform.machine() != "arm64":
        print("This lane needs a Mac with Apple silicon.", file=sys.stderr)
        return 2
    # A stop request still stops the guest, deletes the clone and writes the record.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        if args.command == "clean":
            with lock():
                return clean()
        with lock():
            watch_disk()
            if args.command == "image":
                return image(args.step)
            if releases and releases[1]:
                return lane(args.results.resolve(), None, ["published"], releases)
            resolved = subprocess.run(["git", "rev-parse", "--verify", f"{args.source}^{{commit}}"], text=True,
                                      capture_output=True, cwd=SCRIPTS)
            if resolved.returncode:
                print(f"{args.source} is not a commit in this repository.", file=sys.stderr)
                return 2
            return lane(args.results.resolve(), resolved.stdout.strip(),
                        ["published"] if releases else args.phase or [name for name in PHASES if name not in NAMED],
                        releases)
    except Stop as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
