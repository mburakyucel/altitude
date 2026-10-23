#!/usr/bin/env python3
"""Run the sandbox's socket cases (tests/test_session_sockets.py) and a routine run's own attempts to change what only
Altitude may (tests/test_routine_acting.py) with real bubblewrap in a throwaway Ubuntu 24.04 KVM VM.

    python3 scripts/sandbox_vm.py RESULTS_DIR [--source REF]

Real bubblewrap starts neither inside an Altitude worker, nor in the validation container, which cannot mount a fresh
/proc, nor on the hosted runner, whose AppArmor refuses it user namespaces. The guest boots as the installation lane's
does (scripts/installation_vm.py), from the signature-checked cached image on a copy-on-write overlay. While online it
installs bubblewrap, Ubuntu's own AppArmor profile that admits bubblewrap to user namespaces as on an Ubuntu desktop,
the hardware-enablement kernel, which offers Landlock's abstract-socket scope, and Git and the GitHub CLI, which a
worker reaches GitHub with. It then restarts on that kernel, loses its online card and runs the cases from the
committed source. RESULTS_DIR/sandbox-vm.json records the guest, its kernel, bubblewrap and Landlock versions, the
cases' output and whether every case ran and passed: a case skipped for any reason but being macOS-only, as
Seatbelt's are, fails the run. The VM is deleted afterwards. Requires what the installation lane requires.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

CHECKOUT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CHECKOUT))
from scripts import installation_vm as vm  # noqa: E402 (the lane's machine)
PACKAGES = ("bubblewrap", "apparmor-profiles", "linux-generic-hwe-24.04", "git", "gh")
COMMANDS = ("cp /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/",
            "apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict")
#: The sandbox's socket cases and a routine run's own attempts to change what only Altitude may (routines that act).
MODULE = "tests.test_session_sockets tests.test_routine_acting.TestTheRunsBoxForReal"
LANDLOCK = ("python3 -c 'import ctypes; libc = ctypes.CDLL(None); libc.syscall.restype = ctypes.c_long; "
            "print(libc.syscall(444, None, ctypes.c_size_t(0), ctypes.c_uint32(1)))'")
#: How a case the Linux guest skips by design says so (tests/test_session_sockets.py's `macos_only`).
MACOS_ONLY = "macOS only: "


def judge(returncode: int, output: str) -> dict:
    """The cases' verbose unittest output summarized: it passes when every case passed and every skip is macOS-only,
    with at least one case run."""
    ran, summary = re.search(r"^Ran (\d+) tests?", output, re.M), re.findall(r"^(?:OK|FAILED)\b.*$", output, re.M)
    ran = int(ran.group(1)) if ran else 0
    skipped = [ast.literal_eval(reason) for reason in re.findall(r"\.\.\. skipped (.*)$", output, re.M)]
    return {"returncode": returncode, "ran": ran, "summary": summary[-1] if summary else None, "skipped": skipped,
            "passed": returncode == 0 and ran > len(skipped) and all(r.startswith(MACOS_ONLY) for r in skipped)}


def run(results: Path, commit: str, cache: Path) -> int:
    results.mkdir(parents=True, exist_ok=True)
    record = {"source_commit": commit, "module": MODULE, "vm": {"cpus": 2, "memory_mib": 4096, "disk_gib": 12},
              "passed": False}
    work = Path(tempfile.mkdtemp(prefix="altitude-sandbox-vm."))
    machine = None
    try:
        vm.note("verifying the Ubuntu cloud image")
        record["image"] = vm.base_image(cache)
        source = work / "source.tar"
        subprocess.run(["git", "archive", "--format=tar", "-o", str(source), commit], cwd=CHECKOUT, check=True)
        vm.note("booting the VM; it installs bubblewrap, its AppArmor profile, the hardware-enablement kernel, Git and "
                "the GitHub CLI")
        machine = vm.Machine(work, cache / vm.IMAGE, packages=PACKAGES, commands=COMMANDS)
        machine.start()
        machine.wait_ready(time.monotonic() + 1800)
        vm.note("restarting the guest on its newest kernel, then unplugging its online card")
        machine.reboot(time.monotonic() + 600)
        machine.unplug_online_card()
        record["guest"] = machine.ssh(". /etc/os-release; echo $PRETTY_NAME $(uname -r)").stdout.strip()
        record["bubblewrap"] = machine.ssh("bwrap --version").stdout.strip()
        record["landlock_abi"] = int(machine.ssh(LANDLOCK).stdout)
        machine.copy(str(source), "ubuntu@127.0.0.1:source.tar")
        vm.note(f"running {MODULE} in {record['guest']}")
        done = machine.ssh(f"mkdir source && tar -xf source.tar -C source && cd source && "
                           f"python3 -m unittest -v {MODULE}", timeout=600, check=False)
        output = done.stdout + done.stderr
        (results / "sandbox-vm.log").write_text(output)
        record.update(judge(done.returncode, output))
        vm.note(("passed: " if record["passed"] else "failed: ") + output.strip().splitlines()[-1])
        return 0 if record["passed"] else 1
    except (Exception, SystemExit) as error:
        record["error"] = repr(error)
        vm.note(f"failed: {error!r}")
        return 1
    finally:
        if machine:
            machine.stop()
        shutil.rmtree(work, ignore_errors=True)
        record["vm_removed"] = not work.exists()
        (results / "sandbox-vm.json").write_text(json.dumps(record, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--source", default="HEAD", help="committed revision to test (default: HEAD)")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/altitude-installation-vm",
                        help="where the verified base image is kept between runs")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # a stop request still deletes the VM
    missing = vm.missing_prerequisites()
    if missing:
        print("Missing: " + ", ".join(missing), file=sys.stderr)
        return 2
    resolved = subprocess.run(["git", "rev-parse", "--verify", f"{args.source}^{{commit}}"], text=True,
                              capture_output=True, cwd=CHECKOUT)
    if resolved.returncode:
        print(f"{args.source} is not a commit in this repository.", file=sys.stderr)
        return 2
    return run(args.results.resolve(), resolved.stdout.strip(), args.cache)


if __name__ == "__main__":
    sys.exit(main())
