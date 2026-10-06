#!/usr/bin/env python3
"""Setup-installed root supervisor for one offline, disposable validation guest.

Run by the guest's root LaunchDaemon with Python isolated mode. This file and the adjacent
altitude package come from trusted deployment, never from the submitted checkout.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from altitude import platform, validation_payload as payload

INPUT = Path("/Volumes/AltitudeInput")
RESULTS = Path("/Volumes/AltitudeResults")
WORK = Path("/private/var/altitude-validation")
CONFIG = Path("/Library/Application Support/AltitudeValidation/guest.json")
LOG_LIMIT = 32 * 1024**2
EVIDENCE_LIMIT = payload.RESULT_LIMIT


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _owned_tree(path: Path, uid: int, gid: int) -> None:
    for root, dirs, files in os.walk(path, followlinks=False):
        for entry in [Path(root), *(Path(root) / name for name in dirs + files)]:
            os.chown(entry, uid, gid, follow_symlinks=False)


def export_artifacts(source: Path, destination: Path, budget: int) -> None:
    """Copy bounded regular evidence using directory descriptors; never follow candidate links."""
    count = 0
    names = set()

    def copy(directory: int, target: Path, depth: int, prefix: str = "artifacts/") -> None:
        nonlocal budget, count
        if depth > 32:
            raise RuntimeError("validation artifacts exceed the directory depth limit")
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                # Reserve entries for the artifact root, protected log and receipt.
                if count > payload.ENTRY_LIMIT - 3:
                    raise RuntimeError("validation artifacts exceed the file count limit")
                try:
                    relative = payload._path(prefix + entry.name)
                except ValueError:
                    raise RuntimeError("validation artifacts contain an unsafe transfer path") from None
                if relative.lower() in names:
                    raise RuntimeError("validation artifacts contain conflicting transfer paths")
                names.add(relative.lower())
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        folder = target / entry.name
                        folder.mkdir(mode=0o755)
                        copy(child, folder, depth + 1, relative + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    with os.fdopen(fd, "rb") as content:
                        before = os.fstat(content.fileno())
                        if not stat.S_ISREG(before.st_mode) or before.st_size > budget:
                            raise RuntimeError("validation artifacts exceed the evidence budget")
                        with (target / entry.name).open("xb") as out:
                            size = 0
                            while chunk := content.read(min(65536, budget + 1)):
                                size += len(chunk)
                                budget -= len(chunk)
                                if budget < 0:
                                    raise RuntimeError("validation artifacts exceed the evidence budget")
                                out.write(chunk)
                        after = os.fstat(content.fileno())
                        if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (after.st_mtime_ns, after.st_ctime_ns, size):
                            raise RuntimeError("validation artifacts changed during capture")
                else:
                    raise RuntimeError("validation artifacts contain a link or special file")

    directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        destination.mkdir(mode=0o755)
        created = True
        copy(directory, destination, 0)
    except (OSError, ValueError, RuntimeError):
        # An incomplete inventory must not prevent the host collecting its log/receipt.
        if created:
            shutil.rmtree(destination)
        raise
    finally:
        os.close(directory)


def execute(argv: list[str], *, cwd: Path, env: dict, account: dict, log, deadline: float) -> tuple[int | None, str]:
    """Capture through a pipe: the candidate never receives the supervisor's log descriptor."""
    process = platform.validation_guest_launch(argv, cwd=cwd, env=env, account=account, output=subprocess.PIPE)
    size = log.tell()
    ended = "exit"
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if platform.validation_deadline_clock() >= deadline:
                    ended = "timeout"
                    break
                for key, _ in selector.select(timeout=0.2):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = max(0, LOG_LIMIT - size)
                    log.write(chunk[:remaining])
                    log.flush()
                    size += len(chunk)
                    if size > LOG_LIMIT:
                        ended = "output-limit"
                        break
                if ended != "exit":
                    break
        if ended != "exit":
            return None, ended
        remaining = max(0.01, deadline - platform.validation_deadline_clock())
        try:
            return process.wait(timeout=remaining), ended
        except subprocess.TimeoutExpired:
            return None, "timeout"
    finally:
        # The outer host destroys the VM after the receipt. Killing this process group also
        # stops ordinary descendants now; detached descendants cannot outlive VM retirement.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        if process.stdout:
            process.stdout.close()


def run(config: dict, *, input_path: Path = INPUT, results: Path = RESULTS, work: Path = WORK) -> dict:
    """Run the immutable request once; dependency mismatch is an explicit unavailable result."""
    started = timestamp()
    receipt = {"exit": None, "ended": "unavailable", "error": None, "guest": platform.validation_host_identity(),
               "started": started, "finished": None}
    if (results / "receipt.json").exists() or (results / "output.log").exists():
        raise RuntimeError("validation guest request has already run")
    # mount_virtiofs maps this share to root. Candidates write local artifacts, never this share.
    results.chmod(0o700)
    with (results / "output.log").open("xb") as log:
        os.chmod(results / "output.log", 0o644)
        try:
            request = json.loads((input_path / "request.json").read_text())
            receipt.update({key: request[key] for key in ("run_id", "commit", "tree")})
            if set(config) != {"version", "user", "path", "fingerprints", "store", "browsers"} or config["version"] != 1:
                raise RuntimeError("unsupported guest setup configuration")
            account = platform.validation_guest_account(config["user"])
            argv = request["argv"]
            if (not isinstance(argv, list) or not argv or len(argv) > 256
                    or any(not isinstance(word, str) or not word or "\0" in word for word in argv)):
                raise RuntimeError("invalid validation command")
            source = input_path / "candidate"
            fingerprints = config["fingerprints"]
            if not isinstance(fingerprints, dict) or not {"web/package.json", "web/pnpm-lock.yaml"} <= set(fingerprints):
                raise RuntimeError("guest template has no dependency fingerprints")
            for relative, expected in fingerprints.items():
                path = source / relative
                if (Path(relative).is_absolute() or ".." in Path(relative).parts or path.resolve() != path
                        or not path.is_file() or fingerprint(path) != expected):
                    raise RuntimeError("environment unavailable: template refresh needed (dependency manifest changed)")
            work.mkdir(mode=0o755)
            checkout = work / "candidate"
            shutil.copytree(source, checkout, symlinks=True)
            # Copy the prepared package store into the disposable disk; installation may create
            # local links/metadata but cannot update the root-owned template store.
            store = work / "pnpm-store"
            shutil.copytree(config["store"], store, symlinks=True)
            artifacts = work / "artifacts"
            artifacts.mkdir(mode=0o755)
            home = work / "home"
            home.mkdir(mode=0o700)
            temp = work / "tmp"
            temp.mkdir(mode=0o700)
            for path in (checkout, store, artifacts, home, temp):
                _owned_tree(path, account["uid"], account["gid"])
            env = {"PATH": config["path"], "HOME": str(home), "USER": account["name"],
                   "LOGNAME": account["name"], "TMPDIR": str(temp), "CI": "1",
                   "ALTITUDE_HOME": str(home / ".altitude"), "ALTITUDE_VALIDATION": "1",
                   "ALTITUDE_VALIDATION_RESULTS": str(artifacts), "RESULTS": str(artifacts),
                   "PLAYWRIGHT_BROWSERS_PATH": config["browsers"], "COREPACK_ENABLE_NETWORK": "0",
                   "npm_config_offline": "true", "npm_config_cache": str(home / ".npm"),
                   "PIP_NO_INDEX": "1", "PIP_CACHE_DIR": str(home / ".cache" / "pip"),
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "LANG": "en_US.UTF-8"}
            # The host budget includes boot and import; the inner budget is only a second ceiling.
            deadline = platform.validation_deadline_clock() + 3600
            install = ["pnpm", "install", "--offline", "--frozen-lockfile", "--store-dir", str(store)]
            code, ended = execute(install, cwd=checkout / "web", env=env, account=account, log=log, deadline=deadline)
            if code != 0 or ended != "exit":
                raise RuntimeError("environment unavailable: template refresh needed (offline dependency preparation failed)")
            receipt["exit"], receipt["ended"] = execute(argv, cwd=checkout, env=env, account=account, log=log, deadline=deadline)
            if receipt["ended"] != "exit":
                receipt["error"] = "validation command exceeded its time or output budget"
            export_artifacts(artifacts, results / "artifacts", EVIDENCE_LIMIT - log.tell() - 65536)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            # Native exception messages can contain private paths; stable public descriptions only.
            receipt["error"] = str(exc) if isinstance(exc, RuntimeError) else "validation guest setup or execution failed"
            receipt["exit"], receipt["ended"] = None, "unavailable"
        receipt["finished"] = timestamp()
    temporary = results / "receipt.tmp"
    with temporary.open("x") as output:
        json.dump(receipt, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(results / "receipt.json")
    return receipt


def main() -> int:
    platform.validation_trusted_file(Path(__file__).absolute())
    platform.validation_trusted_file(CONFIG)
    config = json.loads(CONFIG.read_text())
    platform.validation_guest_mounts(INPUT, RESULTS)
    # A prepared guest logs in its fictional standard user automatically. The host bounds this
    # wait too; no command starts when the GUI bootstrap/login is unavailable.
    deadline = platform.validation_deadline_clock() + 120
    while True:
        try:
            platform.validation_guest_account(config["user"])
            break
        except (RuntimeError, KeyError, subprocess.SubprocessError):
            if platform.validation_deadline_clock() >= deadline:
                break
            time.sleep(1)
    receipt = run(config)
    return 0 if receipt["ended"] == "exit" else 1


if __name__ == "__main__":
    raise SystemExit(main())
