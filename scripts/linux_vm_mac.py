"""A throwaway Ubuntu 24.04 arm64 guest on this Apple silicon Mac, for scripts/container_vm.py.

It offers the same calls as the KVM machine in scripts/installation_vm.py, so the container lanes run unchanged
on a Mac: the guest boots through Apple's Virtualization framework (the helper in scripts/macos_vm.swift) from
Ubuntu's published root filesystem image, kernel and initial ramdisk, each checked against Ubuntu's SHA256SUMS
over HTTPS. cloud-init provisions it from a read-only seed disk. SSH reaches the guest through the host-guest
(vsock) socket, relayed to its sshd, so `ubuntu@127.0.0.1` with any port names it; the guest's NAT network card
serves only its own downloads. Downloads live in CACHE/linux-arm64; each run boots a copy-on-write clone of the
root filesystem in its own folder, deleted with it. Downloads and the guest hold the macOS installation lane's
lock, so one virtual machine of either lane runs at a time, and a guest stops when less than 10 GiB of disk
would stay free. Virtual machines cannot start inside a worker sandbox: run this through
`alt task run` under an operator grant.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import platform
import queue
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import installation_macos_vm as macos
from scripts.installation_vm import free_port, sha256  # noqa: F401 (the lane's shared calls)

IMAGES = "https://cloud-images.ubuntu.com/noble/current/"
ROOT = "noble-server-cloudimg-arm64.tar.gz"
KERNEL, INITRD = "noble-server-cloudimg-arm64-vmlinuz-generic", "noble-server-cloudimg-arm64-initrd-generic"
IMAGE = "linux-arm64"
TOOLS = ("xcrun", "codesign", "hdiutil", "ssh", "ssh-keygen", "scp", "nc")
CPUS, MEMORY_GIB, DISK_GIB = 4, 6, 24
GIB = 1 << 30
FLOOR = 10 * GIB
# The image's fstab names an EFI partition this root filesystem image does not have.
COMMAND_LINE = "console=hvc0 root=/dev/vda rw systemd.mask=boot-efi.mount"
# Inside the guest: each host-guest connection to port 22 is joined to sshd on the guest's loopback.
RELAY = """import socket, threading
def pump(a, b):
    try:
        while data := a.recv(65536): b.sendall(data)
    except OSError: pass
    try: b.shutdown(socket.SHUT_WR)
    except OSError: pass
def serve(client):
    try: sshd = socket.create_connection(('127.0.0.1', 22), timeout=10)
    except OSError: client.close(); return
    sshd.settimeout(None)
    other = threading.Thread(target=pump, args=(client, sshd)); other.start(); pump(sshd, client); other.join()
    client.close(); sshd.close()
server = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
server.bind((socket.VMADDR_CID_ANY, 22)); server.listen(16)
while True:
    client, _ = server.accept(); threading.Thread(target=serve, args=(client,), daemon=True).start()
"""


def user_data(public_key: str) -> str:
    # The same packages line as installation_vm.user_data, which scripts/container_vm.py extends.
    return "\n".join(["#cloud-config", "ssh_pwauth: false", "ssh_authorized_keys:", f"  - {public_key}",
                      # The guest has no clock it reads at boot and would start at its image's build time,
                      # which fails TLS; it starts from this Mac's time instead and NTP refines it.
                      "bootcmd:", "  - " + json.dumps(["date", "-u", "-s", f"@{int(time.time())}"]),
                      "  - " + json.dumps(["modprobe", "vmw_vsock_virtio_transport"]),
                      # bootcmd runs before sysinit.target, so the relay must not wait for it.
                      "  - " + json.dumps(["systemd-run", "--no-block", "--property=DefaultDependencies=no",
                                           "--unit=altitude-vsock-ssh", "python3", "-c", RELAY]),
                      "package_update: true", "packages: [git, gh, openssl]", ""])


def missing_prerequisites() -> list[str]:
    missing = [tool for tool in TOOLS if not shutil.which(tool)]
    if sys.platform != "darwin" or platform.machine() != "arm64":
        missing.append("a Mac with Apple silicon")
    return missing


def need(path: Path, size: int, what: str) -> None:
    free = shutil.disk_usage(path).free
    if free - size < FLOOR:
        raise RuntimeError(f"{what} needs {size / GIB:.1f} GiB and at least 10 GiB must stay free; "
                           f"{free / GIB:.1f} GiB is free")


def fetch(url: str, path: Path, *, budget=None) -> None:
    partial = path.with_name(path.name + ".partial")
    try:
        with urlopen(url, timeout=60) as response, partial.open("wb") as stream:
            while block := response.read(1 << 20):
                if budget:
                    budget(len(block))
                stream.write(block)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def expected(sums: Path, name: str) -> str:
    return next(line.split()[0] for line in sums.read_text().splitlines() if line.endswith("*" + name))


def base_image(cache: Path, *, budget=None) -> dict:
    """Ubuntu's current arm64 root filesystem, kernel and initial ramdisk, downloaded again only when their
    published checksums change. Without the Ubuntu signing keyring on a Mac, the checksums are trusted over HTTPS."""
    folder = cache / IMAGE
    folder.mkdir(parents=True, exist_ok=True)
    with macos.lock():
        return _base_image(folder, budget=budget)


def _base_image(folder: Path, *, budget=None) -> dict:
    def download(url, path):
        if budget is None:
            fetch(url, path)
        else:
            fetch(url, path, budget=budget)
    download(IMAGES + "SHA256SUMS", folder / "SHA256SUMS")
    download(IMAGES + "unpacked/SHA256SUMS", folder / "unpacked-SHA256SUMS")
    wanted = {ROOT: expected(folder / "SHA256SUMS", ROOT),
              KERNEL: expected(folder / "unpacked-SHA256SUMS", KERNEL),
              INITRD: expected(folder / "unpacked-SHA256SUMS", INITRD)}
    stamp = folder / "image.json"
    if not stamp.is_file() or json.loads(stamp.read_text()) != wanted:
        need(folder, 4 * GIB, "the Ubuntu image")
        for name, digest in wanted.items():
            url = IMAGES + ("" if name == ROOT else "unpacked/") + name
            download(url, folder / name)
            if sha256(folder / name) != digest:
                (folder / name).unlink()
                raise RuntimeError(f"{name} does not match its published checksum")
        with tarfile.open(folder / ROOT) as archive:
            member = next(item for item in archive.getmembers() if item.isfile() and item.name.endswith(".img"))
            with archive.extractfile(member) as source, (folder / "rootfs.partial").open("wb") as target:
                shutil.copyfileobj(source, target, 1 << 20)
        (folder / "rootfs.partial").replace(folder / "rootfs.img")
        # The framework boots an uncompressed arm64 kernel.
        with gzip.open(folder / KERNEL) as source, (folder / "vmlinux.partial").open("wb") as target:
            shutil.copyfileobj(source, target, 1 << 20)
        (folder / "vmlinux.partial").replace(folder / "vmlinux")
        shutil.copyfile(folder / INITRD, folder / "initrd")
        for name in (ROOT, KERNEL, INITRD):
            (folder / name).unlink()
        stamp.write_text(json.dumps(wanted) + "\n")
    return {"url": IMAGES, "sha256": wanted, "signature": "not verified on a Mac; checksums over HTTPS"}


class Machine:
    def __init__(self, work: Path, image: Path):
        self.work, self.image, self.process, self.log, self.reader = work, image, None, None, None
        self.socket = work / "ssh.sock"
        self.key = work / "id_ed25519"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        seed = work / "seed"
        seed.mkdir()
        (seed / "user-data").write_text(user_data((work / "id_ed25519.pub").read_text().strip()))
        (seed / "meta-data").write_text(f"instance-id: {work.name}\nlocal-hostname: altitude-guest\n")
        subprocess.run(["hdiutil", "makehybrid", "-quiet", "-iso", "-joliet", "-default-volume-name", "cidata",
                        "-o", str(work / "seed.iso"), str(seed)], check=True, timeout=60)
        self.lock = macos.lock()
        # A copy-on-write clone in the run's folder, which the lane deletes whatever happens.
        self.disk = work / "disk.img"
        try:
            need(work, DISK_GIB * GIB - (image / "rootfs.img").stat().st_size, "the guest's disk")
            subprocess.run(["cp", "-c", str(image / "rootfs.img"), str(self.disk)], check=True, timeout=300)
            os.truncate(self.disk, DISK_GIB * GIB)  # sparse: only what the guest writes takes space
        except BaseException:
            self.lock.close()
            raise

    def start(self, *, on_spawn=None) -> None:
        self.log = (self.work / "helper.log").open("w")
        self.events = queue.Queue()
        self.process = subprocess.Popen(
            [str(macos.helper(self.image / "bin/macos_vm")), "linux", str(self.image / "vmlinux"),
             str(self.image / "initrd"), COMMAND_LINE, str(self.disk), str(self.work / "seed.iso"),
             str(self.work / "console.log"), str(self.socket), str(CPUS), str(MEMORY_GIB)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            pass_fds=(self.lock.fileno(),))
        if on_spawn:
            on_spawn(self.process.pid)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        threading.Thread(target=self._watch_disk, daemon=True).start()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if event.startswith("started"):
                return
            if event.startswith(("failed", "stopped", "exited")):
                raise RuntimeError(f"The guest helper said '{event}'; see helper.log")
        raise RuntimeError("The guest helper did not start the guest; see helper.log")

    def _read(self) -> None:
        for line in self.process.stdout:
            self.log.write(line)
            self.log.flush()
            self.events.put(line.strip())
        self.events.put("exited")

    def _watch_disk(self) -> None:
        """Stops the guest, and with it the lane's next step, when the disk falls under the floor."""
        while self.process.poll() is None:
            if shutil.disk_usage(self.work).free < FLOOR:
                self.log.write("less than 10 GiB free; stopping the guest\n")
                self.halt()
                return
            time.sleep(15)

    def ssh(self, command: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", *self.options("-p"), "ubuntu@127.0.0.1", command], check=check,
                              text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def copy(self, *paths: str, timeout: int = 300) -> None:
        subprocess.run(["scp", "-q", "-r", *self.options("-P"), *paths], check=True, timeout=timeout)

    def options(self, port_flag: str) -> list[str]:
        return ["-i", str(self.key), port_flag, "22", "-o", f"ProxyCommand=/usr/bin/nc -U {shlex.quote(str(self.socket))}",
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]

    def wait_ready(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"The guest helper exited with {self.process.returncode}; see helper.log")
            try:
                status = self.ssh("cloud-init status --wait --long", timeout=60, check=False)
            except subprocess.TimeoutExpired:
                continue
            # 255 is SSH not reachable yet; 2 is a recoverable warning; 1 means provisioning failed.
            if status.returncode in (0, 2):
                return
            if status.returncode != 255:
                raise RuntimeError(f"cloud-init failed in the guest:\n{status.stdout}{status.stderr}")
            time.sleep(5)
        raise RuntimeError("The guest was not ready in time; see console.log")

    def halt(self, *, force: bool = False) -> None:
        if self.process and self.process.poll() is None:
            if force:
                self.process.kill()
                self.process.wait(10)
                return
            try:
                self.process.stdin.write("stop\n")
                self.process.stdin.flush()
                self.process.wait(60)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(10)

    def unplug(self) -> str:
        """Physically remove NAT while retaining the host-guest SSH socket."""
        self.process.stdin.write("unplug\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            event = self.events.get(timeout=max(.1, deadline - time.monotonic()))
            if event == "unplugged":
                return event
            if event.startswith(("failed", "stopped", "exited")):
                raise RuntimeError("Guest stopped while unplugging its network")
        raise RuntimeError("Guest network unplug was not confirmed")

    def stop(self) -> None:
        try:
            self.halt()
        finally:
            if self.reader:
                self.reader.join(10)  # the helper's last lines, then its log
            if self.log:
                self.log.close()
            self.lock.close()
