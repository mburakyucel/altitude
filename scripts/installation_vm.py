#!/usr/bin/env python3
"""Run the installation lifecycle harness in a throwaway local Ubuntu 24.04 KVM VM.

    python3 scripts/installation_vm.py RESULTS_DIR [--source REF] [--baseline-release TAG [--recovery | --public]] [--capture]

Builds two synthetic release versions from one committed revision (default HEAD) and runs the
harness from this checkout against them; RESULTS_DIR/vm.json records the outcome. With
--baseline-release, the baseline is instead the published GitHub release TAG, downloaded anonymously on this
host and checked against its SHA256SUMS and tagged commit, and only the candidate is built. The
signature-checked Ubuntu cloud image is cached; each run boots a copy-on-write overlay that is
deleted afterwards. The guest has two network cards: one is online only while cloud-init installs
the harness prerequisites and is then unplugged; the other is restricted to the loopback SSH
forward, so during the tests the guest reaches neither the internet nor this host's services.
After the lifecycle passes, another disposable account installs through the built install.sh from a
release server inside the guest. Without --baseline-release, a third installs the baseline while that server
answers for GitHub's release list and downloads, and the app's Update request must install the candidate it
offers. Then another installs the baseline, the VM restarts and the harness checks that the service came back
on its own before removing it. --recovery instead runs only the
recovery phase: the published baseline's installation must fail, and after the documented cleanup the
candidate installed over it must start and keep its settings, TLS identity and data.
--public instead builds nothing: the candidate is the release GitHub's releases/latest names, the guest keeps
the internet with this host blocked, and the public-install and public-update phases install from GitHub itself.
--capture also keeps an accelerated replay of the lane's progress and harness output as
RESULTS_DIR/captures/installation-vm.gif (docs/DEVELOPMENT.md#validation-captures).
Requires qemu-system-x86, qemu-utils and cloud-image-utils, and read/write access to /dev/kvm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.request import urlopen

IMAGES = "https://cloud-images.ubuntu.com/noble/current/"
IMAGE = "noble-server-cloudimg-amd64.img"
KEYRING = Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg")
TOOLS = ("qemu-system-x86_64", "qemu-img", "cloud-localds", "gpgv", "ssh", "ssh-keygen", "scp")
HARNESS = ("test_installation_lifecycle.sh", "installation_lifecycle.py")
# Fixed guest addresses let the network configuration name each card.
OFFLINE_MAC, ONLINE_MAC = "52:54:00:a1:70:01", "52:54:00:a1:70:02"
# What a public guest must still not reach besides this host's own addresses: private, shared and link-local networks.
LOCAL_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16")


STARTED = time.monotonic()
REPLAY: Replay | None = None


def note(message: str) -> None:
    print(f"[{time.monotonic() - STARTED:5.0f}s] {message}", flush=True)
    if REPLAY:
        REPLAY.note(message)


class Replay:
    """With --capture: this lane's progress lines and its harness logs' lines, each with when it arrived, read from
    the logs every half second so the harness runs exactly as without a capture."""

    def __init__(self, results: Path):
        self.results, self.lines, self.steps, self.read = results, [], [], {}
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.follow, name="capture", daemon=True)
        self.thread.start()

    def note(self, message: str) -> None:
        self.steps.append(message)
        self.lines.append((time.monotonic() - STARTED, message))

    def follow(self) -> None:
        while not self.done.wait(0.5):
            self.collect()

    def collect(self) -> None:
        for log in sorted(self.results.glob("harness*.log")):
            try:
                with log.open("rb") as stream:
                    stream.seek(self.read.get(log, 0))
                    data = stream.read()
            except OSError:
                continue  # read again on the next round; the replay never stops the lane
            whole = data[:data.rfind(b"\n") + 1]
            self.read[log] = self.read.get(log, 0) + len(whole)
            at = time.monotonic() - STARTED
            self.lines.extend((at, line) for line in whole.decode(errors="replace").splitlines())

    def save(self, title: str, last: str) -> str:
        """The replay as RESULTS/captures/installation-vm.gif; returns its path, or why there is none."""
        self.done.set()
        self.thread.join()
        self.collect()
        self.note(last)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        target = self.results / "captures" / "installation-vm.gif"
        try:
            from altitude import capture
            capture.terminal(self.lines, target, title, tuple(self.steps))
            return str(target)
        except Exception as error:  # the capture never changes whether the lane passed
            return f"none: {error}"


def user_data(public_key: str) -> str:
    # The harness needs Git, GitHub CLI and OpenSSL; the image already has Python 3.12.
    return "\n".join(["#cloud-config", "ssh_pwauth: false", "ssh_authorized_keys:", f"  - {public_key}",
                      "package_update: true", "packages: [git, gh, openssl]", ""])


def network_config() -> str:
    return json.dumps({"version": 2, "ethernets": {
        "offline": {"match": {"macaddress": OFFLINE_MAC}, "set-name": "offline", "dhcp4": True,
                    "dhcp4-overrides": {"route-metric": 200}},
        "online": {"match": {"macaddress": ONLINE_MAC}, "set-name": "online", "dhcp4": True,
                   "dhcp4-overrides": {"route-metric": 100}},
    }}) + "\n"


def missing_prerequisites() -> list[str]:
    missing = [tool for tool in TOOLS if not shutil.which(tool)]
    if not KEYRING.is_file():
        missing.append(str(KEYRING))
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        missing.append("read/write /dev/kvm")
    return missing


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, path: Path) -> None:
    partial = path.with_name(path.name + ".partial")
    with urlopen(url, timeout=60) as response, partial.open("wb") as stream:
        shutil.copyfileobj(response, stream, 1 << 20)
    partial.replace(path)


def base_image(cache: Path) -> dict:
    """The current signed cloud image, downloaded again only when the signed checksum changes."""
    cache.mkdir(parents=True, exist_ok=True)
    sums, signature, image = cache / "SHA256SUMS", cache / "SHA256SUMS.gpg", cache / IMAGE
    download(IMAGES + "SHA256SUMS", sums)
    download(IMAGES + "SHA256SUMS.gpg", signature)
    subprocess.run(["gpgv", "--keyring", str(KEYRING), str(signature), str(sums)],
                   check=True, capture_output=True, timeout=30)
    expected = next(line.split()[0] for line in sums.read_text().splitlines() if line.endswith("*" + IMAGE))
    if not image.is_file() or sha256(image) != expected:
        download(IMAGES + IMAGE, image)
        if sha256(image) != expected:
            image.unlink()
            raise SystemExit(f"{IMAGE} does not match its signed checksum")
    return {"url": IMAGES + IMAGE, "sha256": expected, "signature": "verified with " + KEYRING.name}


def reached(returncode: int) -> bool:
    """A probe's outcome. SSH failure (255) or a probe that could not run (126, 127) proves nothing."""
    if returncode in (126, 127, 255):
        raise SystemExit(f"A network probe could not run in the guest (exit {returncode})")
    return returncode == 0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Machine:
    def __init__(self, work: Path, image: Path):
        self.work, self.port, self.process, self.log = work, free_port(), None, None
        self.key = work / "id_ed25519"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        (work / "user-data").write_text(user_data((work / "id_ed25519.pub").read_text().strip()))
        (work / "network-config").write_text(network_config())
        subprocess.run(["cloud-localds", "-N", str(work / "network-config"), str(work / "seed.img"),
                        str(work / "user-data")], check=True)
        self.disk = work / "disk.qcow2"
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", str(image),
                        str(self.disk), "12G"], check=True)
        self.qmp, self.pidfile = work / "qmp.sock", work / "qemu.pid"

    def start(self) -> None:
        work = self.work
        self.log = open(work / "qemu.log", "w")
        self.process = subprocess.Popen([
            "qemu-system-x86_64", "-accel", "kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
            "-display", "none", "-serial", f"file:{work / 'console.log'}", "-monitor", "none",
            "-qmp", f"unix:{self.qmp},server=on,wait=off", "-pidfile", str(self.pidfile),
            "-drive", f"file={self.disk},if=virtio,format=qcow2",
            "-drive", f"file={work / 'seed.img'},if=virtio,format=raw",
            "-netdev", f"user,id=offline,net=10.0.4.0/24,restrict=on,hostfwd=tcp:127.0.0.1:{self.port}-:22",
            "-device", f"virtio-net-pci,netdev=offline,mac={OFFLINE_MAC}",
            # A separate subnet, so replies to the forwarded SSH connection leave through the offline card.
            "-netdev", "user,id=online,net=10.0.3.0/24,ipv6=off",
            "-device", f"virtio-net-pci,netdev=online,id=online-card,mac={ONLINE_MAC}",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.log)

    def ssh(self, command: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", *self.options("-p"), "ubuntu@127.0.0.1", command], check=check,
                              text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def copy(self, *paths: str, timeout: int = 300) -> None:
        subprocess.run(["scp", "-q", "-r", *self.options("-P"), *paths], check=True, timeout=timeout)

    def options(self, port_flag: str) -> list[str]:
        return ["-i", str(self.key), port_flag, str(self.port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]

    def wait_ready(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SystemExit(f"QEMU exited with {self.process.returncode}; see qemu.log")
            try:
                status = self.ssh("cloud-init status --wait --long", timeout=60, check=False)
            except subprocess.TimeoutExpired:
                continue
            # 255 is SSH not reachable yet; 2 is a recoverable warning; 1 means provisioning failed.
            if status.returncode in (0, 2):
                return
            if status.returncode != 255:
                raise SystemExit(f"cloud-init failed in the guest:\n{status.stdout}{status.stderr}")
            time.sleep(5)
        raise SystemExit("The VM was not ready in time; see console.log")

    def boot_id(self) -> str:
        return self.ssh("cat /proc/sys/kernel/random/boot_id", timeout=30).stdout.strip()

    def reboot(self, deadline: float) -> str:
        before = self.boot_id()
        self.ssh("sudo systemctl reboot", check=False)
        while time.monotonic() < deadline:
            time.sleep(5)
            try:
                current = self.ssh("cat /proc/sys/kernel/random/boot_id", timeout=30, check=False)
            except subprocess.TimeoutExpired:
                continue
            if current.returncode == 0 and current.stdout.strip() != before:
                return current.stdout.strip()
        raise SystemExit("The VM did not come back from its restart; see console.log")

    def unplug_online_card(self) -> None:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(10)
            sock.connect(str(self.qmp))
            stream = sock.makefile("rw")
            stream.readline()
            for command in ({"execute": "qmp_capabilities"},
                            {"execute": "set_link", "arguments": {"name": "online-card", "up": False}}):
                stream.write(json.dumps(command) + "\n")
                stream.flush()
                reply = json.loads(stream.readline())
                while "event" in reply:
                    reply = json.loads(stream.readline())
                if "return" not in reply:
                    raise SystemExit(f"QEMU refused {command['execute']}: {reply}")

    def stop(self) -> None:
        if self.process is None:
            # A stop request that arrived while QEMU was launching: find it by its pid file.
            if self.pidfile.is_file():
                try:
                    os.kill(int(self.pidfile.read_text()), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
        elif self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(10)
        if self.log:
            self.log.close()


def outbound_address() -> str:
    """This host's address on its network, the one its default route leaves from; no packet is sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]


def reachable(machine: Machine) -> dict:
    """Which of the internet and this host the guest reaches, each probe proven able to run."""
    address = outbound_address()
    with socket.socket() as loopback, socket.socket() as network:
        # Listeners on this host's loopback and network address: the host probes need a service there to reach.
        loopback.bind(("127.0.0.1", 0))
        network.bind((address, 0))
        for listener in (loopback, network):
            listener.listen()
        port, served = loopback.getsockname()[1], network.getsockname()[1]
        probes = {"internet": "curl -sS --max-time 10 -o /dev/null https://cloud-images.ubuntu.com/",
                  "host-through-online-card": f"timeout 10 bash -c '</dev/tcp/10.0.3.2/{port}'",
                  "host-through-network": f"timeout 10 bash -c '</dev/tcp/{address}/{served}'",
                  "host-through-offline-card": f"timeout 10 bash -c '</dev/tcp/10.0.4.2/{port}'"}
        return {name: reached(machine.ssh(command, check=False).returncode) for name, command in probes.items()}


def harness(machine: Machine, commit: str, phase: str, log: Path) -> int:
    # Output is written as it runs, so a stopped run still shows how far it got.
    with log.open("w") as stream:
        return subprocess.run(
            ["ssh", *machine.options("-p"), "ubuntu@127.0.0.1",
             f"sudo bash input/test_installation_lifecycle.sh --disposable-vm input/baseline input/candidate "
             f"results {commit} {phase}; status=$?; sudo chown -R ubuntu results; exit $status"],
            stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=1200).returncode


def lifecycle(machine: Machine, commits: str, results: Path, record: dict, published: bool) -> None:
    """The lifecycle, then install.sh's bootstrap, the offered update, and an install that must survive the VM's restart.

    A published baseline looks up releases with its own code, so the offered update runs only for same-source versions."""
    exits = record["harness_exit"]
    for phase in ("all", "bootstrap", *(() if published else ("update",)), "reboot-install"):
        note(f"running the {phase} phase")
        exits[phase] = harness(machine, commits, phase, results / ("harness.log" if phase == "all" else f"harness-{phase}.log"))
        if exits[phase]:
            return
    note("restarting the VM")
    record["boot_id_after_restart"] = machine.reboot(time.monotonic() + 300)
    # The online card stays unplugged across the guest's restart.
    record["reachable"]["after_restart"] = reachable(machine)
    if any(record["reachable"]["after_restart"].values()):
        raise SystemExit(f"The guest is not isolated after its restart: {record['reachable']}")
    note("running the reboot-verify phase")
    exits["reboot-verify"] = harness(machine, commits, "reboot-verify", results / "harness-reboot-verify.log")


def repository() -> str:
    """The checkout's GitHub repository, as https://github.com/OWNER/NAME."""
    checkout = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(checkout))
    from altitude.server import repository_url
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=checkout, capture_output=True, text=True,
                            check=True).stdout
    return repository_url(origin) or sys.exit(f"origin is not a GitHub repository: {origin.strip()}")


def release_version(tag: str) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from altitude.installation import VERSION
    if not VERSION.fullmatch(tag):
        raise SystemExit(f"{tag} is not a release version")
    return tag


def latest(repository: str) -> str:
    """The stable release GitHub's releases/latest page redirects to."""
    with urlopen(f"{repository}/releases/latest", timeout=60) as response:
        tag = release_version(response.url.rpartition("/releases/tag/")[2])
    if "-" in tag:
        raise SystemExit(f"{repository}/releases/latest names the release candidate {tag}")
    return tag


def published(repository: str, tag: str, folder: Path) -> dict:
    """The published release's files, downloaded anonymously as a user would, each matching its SHA256SUMS line,
    and the commit its tag names."""
    release_version(tag)
    owner_name = repository.removeprefix("https://github.com/")
    with urlopen(f"https://api.github.com/repos/{owner_name}/releases/tags/{tag}", timeout=60) as response:
        names = [asset["name"] for asset in json.load(response)["assets"]]
    folder.mkdir(parents=True)
    for name in names:
        if Path(name).name != name or name.startswith("."):
            raise SystemExit(f"{tag} has an asset named {name!r}")
        download(f"{repository}/releases/download/{tag}/{name}", folder / name)
    if not (folder / "SHA256SUMS").is_file():
        raise SystemExit(f"{repository} release {tag} has no SHA256SUMS")
    sums = dict(reversed(line.split()) for line in (folder / "SHA256SUMS").read_text().splitlines())
    archive = f"altitude-{tag}.tar.gz"
    if archive not in sums or {path.name for path in folder.iterdir()} != {*sums, "SHA256SUMS", archive + ".sha256"}:
        raise SystemExit(f"{tag} assets differ from its SHA256SUMS: {sorted(p.name for p in folder.iterdir())}")
    for name, digest in sums.items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"{tag}/{name} differs from the release's SHA256SUMS")
    if (folder / (archive + ".sha256")).read_text().strip() != sums[archive]:
        raise SystemExit(f"{tag}/{archive}.sha256 differs from the release's SHA256SUMS")
    refs = subprocess.run(["git", "ls-remote", "--tags", repository, tag, f"{tag}^{{}}"], capture_output=True,
                          text=True, check=True, timeout=60).stdout.splitlines()
    # An annotated tag's peeled line names the commit; a lightweight tag names it directly.
    commits = [line.split()[0] for line in refs if line.endswith("^{}")] or [line.split()[0] for line in refs]
    if not commits:
        raise SystemExit(f"{repository} has no tag {tag}")
    with tarfile.open(folder / archive) as bundle:
        release = json.load(bundle.extractfile("release.json"))
    if (release["version"], release["commit"]) != (tag, commits[0]):
        raise SystemExit(f"{archive} declares {release['version']} at {release['commit']}, not {tag} at {commits[0]}")
    return {"release": tag, "commit": commits[0], "sha256": sums}


def next_minor(version: str) -> str:
    """A synthetic candidate label newer than the published VERSION."""
    return f"v0.{int(version.split('.')[1]) + 1}.0-rc.1"


def build(commit: str, work: Path, results: Path, versions: tuple = (("baseline", "v0.0.0-rc.1"), ("candidate", "v0.0.0-rc.2"))) -> None:
    """Release versions built from the one source commit into the throwaway work directory."""
    for name, version in versions:
        with (results / f"build-{name}.log").open("w") as log:
            subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parent / "build_release.py"),
                            "--version", version, "--output", str(work / name), "--source", commit],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)


def run(results: Path, commit: str, cache: Path, baseline_release: str | None = None, recovery: bool = False,
        capture: bool = False, public: bool = False) -> int:
    global REPLAY
    results.mkdir(parents=True, exist_ok=True)
    checkout = Path(__file__).resolve().parent.parent
    git = lambda *args: subprocess.run(["git", *args], cwd=checkout, capture_output=True, text=True, check=True).stdout.strip()
    record = {"source_commit": commit, "harness": {"commit": git("rev-parse", "HEAD"),
              "modified": bool(git("status", "--porcelain", "--", "scripts"))}, "host": {"kernel": platform.release(), "machine": platform.machine()},
              "vm": {"cpus": 2, "memory_mib": 4096, "disk_gib": 12}, "recovery": recovery, "public": public,
              "passed": False}
    record["qemu"] = subprocess.run(["qemu-system-x86_64", "--version"], capture_output=True,
                                    text=True).stdout.splitlines()[0]
    work = Path(tempfile.mkdtemp(prefix="altitude-installation-vm."))
    machine = None
    REPLAY = Replay(results) if capture else None
    try:
        if public:
            github = repository()
            record["latest"] = latest(github)
            note(f"downloading the published {baseline_release} and {record['latest']}, which releases/latest names")
            record["baseline"] = published(github, baseline_release, work / "baseline")
            record["candidate"] = published(github, record["latest"], work / "candidate")
            commits = f"{record['baseline']['commit']}..{record['candidate']['commit']}"
        elif baseline_release:
            note(f"downloading the published {baseline_release} and building the candidate from {commit[:12]}")
            record["baseline"] = published(repository(), baseline_release, work / "baseline")
            build(commit, work, results, (("candidate", next_minor(baseline_release)),))
            commits = f"{record['baseline']['commit']}..{commit}"
        else:
            note(f"building both release versions from {commit[:12]}")
            build(commit, work, results)
            commits = commit
        note("verifying the Ubuntu cloud image")
        record["image"] = base_image(cache)
        baseline, candidate = work / "baseline", work / "candidate"
        note("booting the VM")
        machine = Machine(work, cache / IMAGE)
        machine.start()
        machine.wait_ready(time.monotonic() + 900)
        note("guest provisioned; checking its network, then " + ("blocking this host" if public else "unplugging its online card"))
        record["guest"] = machine.ssh(". /etc/os-release; echo $PRETTY_NAME $(uname -r)").stdout.strip()
        # The online card reaches both the internet and this host, which proves the probes work; the
        # restricted card reaches neither. Afterwards nothing is reachable, or with --public only the internet.
        record["reachable"] = {"online": reachable(machine)}
        if record["reachable"]["online"] != {"internet": True, "host-through-online-card": True,
                                             "host-through-network": True, "host-through-offline-card": False}:
            raise SystemExit(f"Unexpected guest network before isolation: {record['reachable']}")
        if public:
            # The public phases reach GitHub through the online card, whose gateway address is this host's loopback.
            # The card's own network stays connected, so the guest keeps its gateway and name server.
            blocked = ("10.0.3.2/32", f"{outbound_address()}/32", *LOCAL_NETWORKS)
            machine.ssh("sudo sh -ec '" + "; ".join(f"ip route add prohibit {network}" for network in blocked) + "'")
            record["blocked"] = blocked
            record["reachable"]["public"] = reachable(machine)
            if record["reachable"]["public"] != {"internet": True, "host-through-online-card": False,
                                                 "host-through-network": False, "host-through-offline-card": False}:
                raise SystemExit(f"The guest does not reach only the internet: {record['reachable']}")
        else:
            machine.unplug_online_card()
            record["reachable"]["isolated"] = reachable(machine)
            if any(record["reachable"]["isolated"].values()):
                raise SystemExit(f"The guest is not isolated: {record['reachable']}")
        note("guest " + ("online without this host" if public else "isolated") + "; copying the harness and archives")
        machine.ssh("mkdir -p input")
        scripts = Path(__file__).resolve().parent
        machine.copy(*(str(scripts / name) for name in HARNESS), "ubuntu@127.0.0.1:input/")
        machine.copy(str(baseline), "ubuntu@127.0.0.1:input/baseline")
        machine.copy(str(candidate), "ubuntu@127.0.0.1:input/candidate")
        # Each phase creates, uses and deletes its own disposable account inside the guest.
        exits = record["harness_exit"] = {}
        try:
            if recovery or public:
                for phase in ("recovery",) if recovery else ("public-install", "public-update"):
                    note(f"running the {phase} phase")
                    exits[phase] = harness(machine, commits, phase, results / f"harness-{phase}.log")
                    if exits[phase]:
                        break
            else:
                lifecycle(machine, commits, results, record, bool(baseline_release))
        finally:
            note(f"harness exits {exits}; copying its results")
            try:
                machine.copy("ubuntu@127.0.0.1:results/.", str(results))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                record["uncopied_results"] = str(error)
        phases = 1 if recovery else 2 if public else 4 if baseline_release else 5
        record["passed"] = list(exits.values()) == [0] * phases and "uncopied_results" not in record
    finally:
        try:
            if machine:
                machine.stop()
            for name in (name for name in ("console.log", "qemu.log") if (work / name).is_file()):
                try:
                    shutil.copyfile(work / name, results / f"vm-{name}")
                except OSError as error:
                    record.setdefault("uncopied_logs", []).append(f"{name}: {error}")
        finally:
            # The overlay, its private key and seed go even when stopping or copying logs failed.
            shutil.rmtree(work)
            ended = "VM deleted; " + ("passed" if record["passed"] else "failed") + f"; evidence in {results}"
            if REPLAY:  # the capture never changes whether the lane passed
                record["capture"] = REPLAY.save(f"installation-vm {commit[:12]}", ended)
            (results / "vm.json").write_text(json.dumps(record, indent=2) + "\n")
            REPLAY = None
            note(ended)
    return 0 if record["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--source", default="HEAD", help="committed revision to build and test (default: HEAD)")
    parser.add_argument("--baseline-release", metavar="TAG", help="published release to install first and update from")
    parser.add_argument("--recovery", action="store_true",
                        help="install the candidate over the published baseline's failed installation instead")
    parser.add_argument("--public", action="store_true",
                        help="install releases/latest and update from the baseline with the guest's own GitHub downloads")
    parser.add_argument("--capture", action="store_true",
                        help="keep an accelerated replay of the run as RESULTS/captures/installation-vm.gif")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/altitude-installation-vm",
                        help="where the verified base image is kept between runs")
    args = parser.parse_args()
    if (args.recovery or args.public) and not args.baseline_release:
        parser.error("--recovery and --public need --baseline-release")
    if args.recovery and args.public:
        parser.error("--recovery and --public are separate runs")
    # A stop request still deletes the VM and writes the record.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    missing = missing_prerequisites()
    if missing:
        print("Missing: " + ", ".join(missing), file=sys.stderr)
        print("Install with: sudo apt install qemu-system-x86 qemu-utils cloud-image-utils", file=sys.stderr)
        return 2
    resolved = subprocess.run(["git", "rev-parse", "--verify", f"{args.source}^{{commit}}"], text=True,
                              capture_output=True, cwd=Path(__file__).resolve().parent)
    if resolved.returncode:
        print(f"{args.source} is not a commit in this repository.", file=sys.stderr)
        return 2
    return run(args.results.resolve(), resolved.stdout.strip(), args.cache, args.baseline_release, args.recovery,
               args.capture, args.public)


if __name__ == "__main__":
    sys.exit(main())
