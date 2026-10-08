#!/usr/bin/env python3
"""Run the one-command installation on a fresh macOS guest on this Mac, through Apple's Virtualization framework.

    python3 scripts/installation_macos_vm.py image [--step]
    python3 scripts/installation_macos_vm.py run RESULTS_DIR [--source REF] [--phase PHASE]
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
for OpenSSL 3 not being first on PATH and, for a second account with no desktop session, for the missing session.
Each refused attempt must leave the account's files and launchd jobs as they were. The clone is deleted afterwards,
also after a failure or a stop. RESULTS_DIR/macos-vm.json records the outcome.

The host reaches a guest only through the Virtualization framework's host-guest socket, relayed to the guest's sshd.
Everything lives in ~/.cache/altitude-installation-vm/macos; nothing runs as root on this Mac. One guest runs at a
time, and every step stops when less than 10 GiB of disk would stay free. Virtual machines cannot start inside a
worker sandbox: run this in a terminal, or in a task with `alt task run` under an operator grant. `clean` deletes
the images, the helper and any leftover clone.
"""
from __future__ import annotations

import argparse
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
        raise Stop("another run of this lane is using the guests") from None
    return handle


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def helper() -> Path:
    """The Swift helper, compiled with this Mac's Xcode tools and signed here for the Virtualization framework."""
    source = SCRIPTS / "macos_vm.swift"
    stamp = HELPER.with_suffix(".sha256")
    digest = hashlib.sha256(source.read_bytes() + (SCRIPTS / "macos_vm.entitlements").read_bytes()).hexdigest()
    if HELPER.is_file() and stamp.is_file() and stamp.read_text().strip() == digest:
        return HELPER
    HELPER.parent.mkdir(parents=True, exist_ok=True)
    note("compiling the guest helper")
    run("xcrun", "swiftc", "-O", "-target", "arm64-apple-macos14", "-o", HELPER, source, timeout=600)
    run("codesign", "-f", "-s", "-", "--entitlements", SCRIPTS / "macos_vm.entitlements", HELPER)
    stamp.write_text(digest + "\n")
    return HELPER


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


def shadow_hash(password: str) -> bytes:
    salt = secrets.token_bytes(32)
    entropy = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 50_000, dklen=128)
    return plistlib.dumps({"SALTED-SHA512-PBKDF2": {"entropy": entropy, "iterations": 50_000, "salt": salt}},
                          fmt=plistlib.FMT_BINARY)


def kcpassword(password: str) -> bytes:
    """loginwindow's auto-login password: NUL-padded to a multiple of 12, at least one NUL, XORed with its key."""
    key = bytes([0x7D, 0x89, 0x52, 0x23, 0xD2, 0xBC, 0xDD, 0xEA, 0xA3, 0xB9, 0x1F])
    plain = password.encode()
    plain += b"\0" * (12 - len(plain) % 12)
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(plain))


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
        found = guest.ssh("route -n get default 2>/dev/null | awk '/gateway:/ {print $2}'", check=False).stdout.strip()
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
    """A throwaway certificate authority and a github.com certificate that only the test's curl trusts."""
    (folder / "ca.cnf").write_text(
        "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=Altitude macOS VM lane test CA\n"
        "[ca]\nbasicConstraints=critical,CA:true\nkeyUsage=critical,keyCertSign,cRLSign\n"
        "[leaf]\nbasicConstraints=CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectAltName=DNS:github.com\nextendedKeyUsage=serverAuth\n")
    openssl = "/usr/bin/openssl"
    run(openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", folder / "ca.key", "-out", folder / "ca.pem",
        "-days", "2", "-config", folder / "ca.cnf", "-extensions", "ca")
    run(openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", folder / "server.key", "-out",
        folder / "server.csr", "-config", folder / "ca.cnf", "-subj", "/CN=github.com")
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
    "openssl-not-first": ("not OpenSSL 3", "brew install openssl@3",
                          'export PATH="$(brew --prefix openssl@3)/bin:$PATH", also in your shell profile'),
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


def fix_openssl(guest: Guest) -> None:
    """The fix the installer names for OpenSSL 3, written with Homebrew's resolved path so a login shell needs no lookup."""
    guest.ssh("zsh -lc 'echo \"export PATH=\\\"$(brew --prefix openssl@3)/bin:\\$PATH\\\"\" >> ~/.zprofile'")


def add_github_cli(guest: Guest, record: dict) -> None:
    """GitHub CLI, which Altitude needs and `alt doctor` checks: this Mac's own Homebrew binary, copied, since the
    guest downloads nothing during a run."""
    found = shutil.which("gh")
    if not found:
        raise Stop("GitHub CLI (gh) is not installed on this Mac; the lane copies it into the guest")
    guest.copy(Path(found).resolve(), f"{SHARED}/gh")
    guest.ssh(f"sudo -n install -m 755 -o {ACCOUNT} -g admin {SHARED}/gh /opt/homebrew/bin/gh && rm {SHARED}/gh")
    record["github_cli"] = guest.ssh("zsh -lc 'gh --version'").stdout.splitlines()[0]


def phase_fresh(guest: Guest, repository: str, results: Path, record: dict, release: Path, commit: str) -> None:
    record["prerequisites"] = prerequisites(guest)
    record["cases"] = {"missing-python": judged(attempt(guest, repository, ACCOUNT, results, "missing-python"), "missing-python")}


def phase_prerequisites(guest: Guest, repository: str, results: Path, record: dict, release: Path, commit: str) -> None:
    record["prerequisites"] = prerequisites(guest)
    cases = record["cases"] = {}
    cases["openssl-not-first"] = judged(attempt(guest, repository, ACCOUNT, results, "openssl-not-first"), "openssl-not-first")
    # The documented fix, then a second account with the same shell setup and no desktop session.
    fix_openssl(guest)
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


def phase_lifecycle(guest: Guest, repository: str, results: Path, record: dict, release: Path, commit: str) -> None:
    """installation_lifecycle.py's `mac` phase, as installation_mac.py runs it on a configured Mac, in this fresh guest:
    install through the public command, health, doctor, updates to newer stable releases by `alt update` and by the
    app's Update button, a failed update rolled back, and uninstall, under a throwaway HOME of the account."""
    fix_openssl(guest)
    add_github_cli(guest, record)
    record["prerequisites"] = prerequisites(guest)
    guest.copy(release, f"{SHARED}/release")
    guest.copy(SCRIPTS / "installation_lifecycle.py", f"{SHARED}/installation_lifecycle.py")
    (results / "diagnose.sh").write_text(DIAGNOSE)
    guest.copy(results / "diagnose.sh", f"{SHARED}/diagnose.sh")
    # installation_mac.py's clean environment: the throwaway HOME, Homebrew's OpenSSL 3, Python and GitHub CLI first.
    script = (f'temp=$(getconf DARWIN_USER_TEMP_DIR); work="$temp/altitude-installation-mac"; '
              f'mkdir -m 700 "$work" "$work/home" && mkdir "$work/home/results" && cd "$work/home" && '
              f'env -i HOME="$work/home" USER={ACCOUNT} LOGNAME={ACCOUNT} SHELL=/bin/zsh LANG=en_US.UTF-8 TMPDIR="$temp" '
              f'PATH="$(/opt/homebrew/bin/brew --prefix openssl@3)/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" '
              f'/opt/homebrew/bin/python3.12 -I -B {SHARED}/installation_lifecycle.py {SHARED}/release {SHARED}/release '
              f'"$work/home/results" {commit} mac; code=$?; cp -R "$work/home/results" {SHARED}/lifecycle; '
              f'cp -R "$work/home/Library/Logs" {SHARED}/lifecycle/logs 2>/dev/null; '
              f'[ $code = 0 ] || sh {SHARED}/diagnose.sh "$work/home" {SHARED}/lifecycle; exit $code')
    result = guest.ssh(script, timeout=480, check=False)
    (results / "lifecycle.log").write_text(result.stdout + result.stderr)
    guest.fetch(f"{SHARED}/lifecycle", results / "lifecycle")
    outcome = json.loads((results / "lifecycle/result.json").read_text())
    record["cases"] = {"lifecycle": {"exit": result.returncode, "passed": result.returncode == 0 and outcome["passed"],
                                     "steps": [step["step"] for step in outcome["steps"]], "limits": outcome["limits"],
                                     **({"error": outcome["error"]} if "error" in outcome else {})}}


def phase_login(guest: Guest, repository: str, results: Path, record: dict, release: Path, commit: str) -> None:
    """The account's own installation through the public command, its LaunchAgent started again by the automatic login
    after the guest restarts, and its uninstall."""
    fix_openssl(guest)
    add_github_cli(guest, record)
    record["prerequisites"] = prerequisites(guest)
    steps = record["steps"] = []
    version = re.search(r"^VERSION='([^']+)'", (release / "install.sh").read_text(), re.M).group(1)
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

    installed = step("install", public_command(repository), timeout=300)
    check(installed.returncode == 0 and f"Altitude {version} is installed" in installed.stdout, "the installer failed")
    port = int(re.search(r"Address: https://\S+:(\d+)", installed.stdout).group(1))
    before = running()
    doctor = step("doctor", f"{alt} doctor")
    report = json.loads(doctor.stdout)
    checks = {item["name"]: item["state"] for item in report["checks"]}
    record["doctor"] = checks
    check(doctor.returncode == 0 and report["version"] == version and checks["Python"] == checks["user service"] == "tested"
          and all(checks[name] == "configured" for name in ("git", "gh", "openssl")), f"doctor reports {checks}")
    booted = guest.ssh("sysctl -n kern.boottime").stdout.strip()
    note("login: restarting the guest")
    guest.ssh("sudo -n /sbin/shutdown -r now", timeout=30, check=False)
    time.sleep(20)
    guest.wait_ssh(300)
    restarted = step("restarted", "sysctl -n kern.boottime")
    check(restarted.returncode == 0 and restarted.stdout.strip() != booted, "the guest did not restart")
    after = running()
    check(after["pid"] != before["pid"], "the service was not started again after the restart")
    removed = step("uninstall", f"{alt} uninstall")
    check(removed.returncode == 0 and json.loads(removed.stdout)["uninstalled"], "uninstall failed")
    gone = step("removed", f"test ! -e {agent} && ! launchctl print gui/{ACCOUNT_UID}/{SERVICE_LABEL} >/dev/null 2>&1 && "
                           f"! curl -sk --max-time 5 https://127.0.0.1:{port}/api/health >/dev/null")
    check(gone.returncode == 0, "the LaunchAgent or the service is still there")
    record["cases"] = {"login": {"passed": True, "version": version, "service_label": SERVICE_LABEL,
                                 "pids": [before["pid"], after["pid"]]}}


PHASES = {"fresh": phase_fresh, "prerequisites": phase_prerequisites, "lifecycle": phase_lifecycle, "login": phase_login}


def build(commit: str, work: Path, log: Path) -> Path:
    """The release, built in `work` with packages already in this Mac's pnpm store; the build downloads nothing."""
    temp = work / "tmp"
    temp.mkdir()
    environment = {**os.environ, "TMPDIR": str(temp), "XDG_CACHE_HOME": str(temp), "npm_config_cache": str(temp),
                   "npm_config_offline": "true"}
    with log.open("w") as stream:
        subprocess.run([sys.executable, "-B", str(SCRIPTS / "build_release.py"), "--version", "v0.0.1",
                        "--output", str(work / "release"), "--source", commit], stdout=stream, stderr=subprocess.STDOUT,
                       env=environment, check=True, timeout=300)
    return work / "release"


def run_phase(name: str, release: Path, results: Path, record: dict) -> None:
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
        note(f"{name}: checking the guest's network, then unplugging it")
        isolate(guest, phase)
        serve = work / "serve"
        serve.mkdir()
        certificates(serve)
        repository = release_tree(release, serve)
        guest.ssh(f"sudo -n mkdir -p {SHARED} && sudo -n chown {ACCOUNT} {SHARED}")
        for name_ in ("root", "ca.pem", "server.pem", "server.key"):
            guest.copy(serve / name_, f"{SHARED}/{name_}")
        guest.ssh(f"chmod -R a+rX {SHARED}")
        guest.ssh(SERVE)
        note(f"{name}: running the installer")
        PHASES[name](guest, repository, folder, phase, release, record["source_commit"])
        phase["guest_load"] = guest.ssh("sysctl -n vm.loadavg").stdout.split()[1:4]
        phase["reachable"]["after"] = reachable(guest)
        if any(phase["reachable"]["after"].values()):
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


def lane(results: Path, commit: str, phases: list[str]) -> int:
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
        note(f"building the release from {commit[:12]}")
        release = build(commit, work, results / "build.log")
        for name in phases:
            run_phase(name, release, results, record)
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
    run_parser.add_argument("--phase", choices=list(PHASES), action="append", help="run only this phase (repeatable)")
    commands.add_parser("clean", help="delete the images, the helper and any leftover clone")
    args = parser.parse_args()
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
            resolved = subprocess.run(["git", "rev-parse", "--verify", f"{args.source}^{{commit}}"], text=True,
                                      capture_output=True, cwd=SCRIPTS)
            if resolved.returncode:
                print(f"{args.source} is not a commit in this repository.", file=sys.stderr)
                return 2
            return lane(args.results.resolve(), resolved.stdout.strip(), args.phase or list(PHASES))
    except Stop as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
