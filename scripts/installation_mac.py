#!/usr/bin/env python3
"""Run the installation lifecycle on this Mac under a throwaway HOME of the running account.

    python3 scripts/installation_mac.py RESULTS_DIR [--source REF]

Builds a release from one committed revision (default HEAD) and runs installation_lifecycle.py's `mac` phase in a
fresh HOME at $TMPDIR/altitude-installation-mac/home, with a clean environment, a loopback port of its own and the
LaunchAgent label platform.service_label() derives from that HOME. The phase installs through install.sh, lets the
installed daemon find newer synthetic releases, updates with alt update and the app's Update button, rolls back a
release whose startup exits and uninstalls. Afterwards, also after a failure or a stop, the runner boots out what is
left of that label and of the jobs the installation started, copies the evidence into RESULTS_DIR and deletes the
HOME; RESULTS_DIR/mac.json records the outcome. The account's own Altitude service (dev.altitude.altd), its
LaunchAgent, trust stores and ports are never addressed; its process is recorded before and after as evidence.

launchd refuses service control from a sandboxed process, so this runs outside any worker sandbox: in the operator's
terminal, or inside a task with `alt task run` under an operator grant. Each run leaves launchd's enable/disable
record for its one label, which only an administrator can clear; the fixed HOME keeps that to one label.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parent.parent
ACCOUNT_LABEL = "dev.altitude.altd"
STARTED = time.monotonic()


def note(message: str) -> None:
    print(f"[{time.monotonic() - STARTED:5.0f}s] {message}", flush=True)


def sandboxed() -> bool:
    return ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib").sandbox_check(os.getpid(), None, 0) == 1


def launchd(label: str) -> dict | None:
    """The job's state and process in this account's launchd domain, or None when it has no such job."""
    found = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True, timeout=30)
    if found.returncode:
        return None
    fields = dict(line.strip().split(" = ", 1) for line in found.stdout.splitlines()
                  if line.startswith("\t") and not line.startswith("\t\t") and " = " in line)
    return {key: fields.get(key) for key in ("state", "pid", "path")}


def missing_prerequisites() -> list[str]:
    missing = []
    if sys.platform != "darwin" or platform.machine() != "arm64" or int(platform.mac_ver()[0].split(".")[0]) < 15:
        missing.append("macOS 15 or newer on Apple silicon")
    if sys.version_info < (3, 12):
        missing.append(f"Python 3.12 or newer (this is {platform.python_version()})")
    openssl = shutil.which("openssl")
    if not openssl or not subprocess.run([openssl, "version"], capture_output=True, text=True).stdout.startswith("OpenSSL 3"):
        missing.append("OpenSSL 3 first on PATH (brew install openssl@3)")
    missing += [tool for tool in ("git", "gh", "pnpm") if not shutil.which(tool)]
    if sandboxed():
        missing.append("a process outside the worker sandbox (alt task run under an operator grant, or a terminal)")
    return missing


def build(commit: str, output: Path, log: Path) -> None:
    with log.open("w") as stream:
        subprocess.run([sys.executable, "-B", str(REPO / "scripts/build_release.py"), "--version", "v0.0.1",
                        "--output", str(output), "--source", commit], stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=300)


def clean_environment(home: Path) -> dict[str, str]:
    """What a fresh login on this Mac would give the installer: its own HOME, and a PATH with Homebrew's Python 3.12,
    OpenSSL 3, Git and GitHub CLI ahead of the system's, and nothing of the operator's shell or Altitude."""
    folders = [str(Path(shutil.which("openssl")).parent), str(Path(sys.executable).parent),
               str(Path(shutil.which("gh")).parent), str(Path(shutil.which("git")).parent),
               "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    user = os.environ.get("USER") or os.getlogin()
    return {"HOME": str(home), "USER": user, "LOGNAME": user, "SHELL": "/bin/zsh", "LANG": "en_US.UTF-8",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"), "PATH": ":".join(dict.fromkeys(folders))}


def leftover_jobs(home: Path, label: str | None) -> list[str]:
    """The installation's own service label and every job whose record its HOME holds, never another label."""
    labels = [label] if label else []
    jobs = home / "Library/Caches/dev.altitude/jobs"
    if jobs.is_dir():
        labels += sorted(path.name for path in jobs.iterdir() if path.name.startswith("dev.altitude.job."))
    return [name for name in labels if name != ACCOUNT_LABEL]


def stop_group(process: subprocess.Popen) -> bool:
    """Stop whatever is left of the lifecycle's process group; whether anything was still running."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    return True


def clean_up(work: Path) -> dict:
    """Boot out what is left of the throwaway installation's own labels and delete its HOME."""
    home = work / "home"
    try:
        label = json.loads((home / "results/service-label.json").read_text())["label"]
    except (OSError, ValueError, KeyError):
        label = None
    outcome = {"service_label": label, "bootout": []}
    for name in leftover_jobs(home, label):
        if launchd(name):
            booted = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{name}"], capture_output=True,
                                    text=True, timeout=60)
            outcome["bootout"].append({"label": name, "exit": booted.returncode})
    outcome["left_loaded"] = [name for name in leftover_jobs(home, label) if launchd(name)]
    return outcome


def run(results: Path, commit: str) -> int:
    results.mkdir(parents=True, exist_ok=True)
    git = lambda *args: subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    record = {"source_commit": commit, "harness": {"commit": git("rev-parse", "HEAD"),
              "modified": bool(git("status", "--porcelain", "--", "scripts"))},
              "host": {"macos": platform.mac_ver()[0], "machine": platform.machine(), "python": platform.python_version()},
              "account_service": {"label": ACCOUNT_LABEL, "before": launchd(ACCOUNT_LABEL)}, "passed": False}
    temp = Path(os.confstr(65537) or "/tmp")  # _CS_DARWIN_USER_TEMP_DIR
    work, home = temp / "altitude-installation-mac", temp / "altitude-installation-mac/home"
    lock = (temp / "altitude-installation-mac.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another installation lifecycle run is active on this Mac.", file=sys.stderr)
        return 2
    if work.exists():
        # A run that was killed outright left its installation; nothing else uses this folder.
        record["stale_run"] = clean_up(work)
        shutil.rmtree(work)
    work.mkdir(mode=0o700)
    lifecycle = None
    try:
        home.mkdir(mode=0o700)
        (home / "results").mkdir()
        note(f"building the release from {commit[:12]}")
        build(commit, work / "baseline", results / "build.log")
        note("running the lifecycle under the throwaway HOME")
        with (results / "lifecycle.log").open("w") as log:
            # Its own process group, so the installer processes it starts stop with it before cleanup.
            lifecycle = subprocess.Popen([sys.executable, "-I", "-B", str(REPO / "scripts/installation_lifecycle.py"),
                                          str(work / "baseline"), str(work / "baseline"), str(home / "results"), commit,
                                          "mac"], cwd=home, env=clean_environment(home), stdout=log,
                                         stderr=subprocess.STDOUT, start_new_session=True)
            record["lifecycle_exit"] = lifecycle.wait(timeout=420)
    except subprocess.TimeoutExpired:
        record["lifecycle_exit"] = "timeout"
    except subprocess.CalledProcessError as error:
        record["build_exit"] = error.returncode
    finally:
        if lifecycle is not None:
            record["stopped_processes"] = stop_group(lifecycle)
        record["cleanup"] = clean_up(work)
        if (home / "results").is_dir():
            for path in (home / "results").iterdir():
                if path.is_file():
                    shutil.copyfile(path, results / path.name)
        shutil.rmtree(work, ignore_errors=True)
        record["removed_home"] = not work.exists()
        record["account_service"]["after"] = launchd(ACCOUNT_LABEL)
        try:
            record["lifecycle"] = json.loads((results / "result.json").read_text())
        except (OSError, ValueError):
            record["lifecycle"] = None
        record["passed"] = (record.get("lifecycle_exit") == 0 and bool(record["lifecycle"]) and record["lifecycle"]["passed"]
                            and not record["cleanup"]["left_loaded"] and record["removed_home"])
        (results / "mac.json").write_text(json.dumps(record, indent=2) + "\n")
        note(("passed" if record["passed"] else "failed") + f"; evidence in {results}")
    return 0 if record["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--source", default="HEAD", help="committed revision to build and test (default: HEAD)")
    args = parser.parse_args()
    # A stop request still cleans up and writes the record.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    missing = missing_prerequisites()
    if missing:
        print("Missing: " + "; ".join(missing), file=sys.stderr)
        return 2
    resolved = subprocess.run(["git", "rev-parse", "--verify", f"{args.source}^{{commit}}"], text=True,
                              capture_output=True, cwd=REPO)
    if resolved.returncode:
        print(f"{args.source} is not a commit in this repository.", file=sys.stderr)
        return 2
    return run(args.results.resolve(), resolved.stdout.strip())


if __name__ == "__main__":
    sys.exit(main())
