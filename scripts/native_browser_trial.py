#!/usr/bin/env python3
"""Operator lifecycle transaction for the reviewed native browser candidate, with offline stock restoration.

Invoke only from the protected deployed source under the recorded installation/service purpose. No browser or
provider test runs here. A current-owner grant is not bypassed: offline restore needs operator access or a command
already admitted before owner/daemon loss. SIGKILL/outer timeout/host loss can leave recovery pending.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import fnmatch
import hashlib
import json
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE))
from altitude import config, engines, platform  # noqa: E402
from altitude import state as S


class TrialError(RuntimeError):
    pass


def home() -> Path:
    return config.ROOT.with_name(config.ROOT.name + "-browser-trial")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def protected(path: Path) -> None:
    """Protection from same-UID workers comes from root exclusion, not readonly Unix modes alone."""
    for root in engines.browser_trial_write_roots():
        if path.is_relative_to(root) or any(fnmatch.fnmatch(str(p), str(root)) for p in (path, *path.parents)):
            raise TrialError("Trial inputs must be outside worker/state/checkout write roots")
    for part in (path, *path.parents):
        st = part.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_mode & 0o022:
            raise TrialError("Linked or shared-writable trial input")
    if path.stat().st_uid != os.getuid():
        raise TrialError("Trial input owner differs from the operator")


def regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise TrialError("Trial input is not a regular file")
        return stream.read()


@contextmanager
def transaction():
    """One lifetime lock prevents an offline restore racing a still-selecting trial (#625)."""
    fd = None
    try:
        with platform.native_browser_trial_deadline(60):
            lock = home().with_name(home().name + ".lock")
            protected(lock if lock.exists() else lock.parent)
            fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise TrialError("Trial lock protection is unavailable")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TrialError("A native trial/restore is active; do not race its selection or recovery") from exc
        yield
    finally:
        if fd is not None:
            os.close(fd)


def atomic(path: Path, data: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".trial-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def record(row: dict, phase: str, **evidence) -> None:
    row.update(phase=phase, **evidence)
    atomic(home() / "receipt.json", (json.dumps(row, indent=2) + "\n").encode(), 0o600)
    print(f"Native trial: {phase}; protected evidence: {home() / 'receipt.json'}", flush=True)


def package(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise TrialError("Linked or missing package directory")
    expected = engines.browser_trial_package()["files"]
    files = set()
    for item in path.rglob("*"):
        if item.is_symlink():
            raise TrialError("Linked package input")
        if item.is_file():
            if item.stat().st_mode & 0o6000:
                raise TrialError("Set-ID package input is not permitted")
            files.add(str(item.relative_to(path)))
        elif not item.is_dir():
            raise TrialError("Special package input")
    if files != set(expected):
        raise TrialError("Candidate package file set differs from the reviewed artifact")
    for name, wanted in expected.items():
        if digest(regular(path / name)) != wanted:
            raise TrialError("Candidate package digest differs from the reviewed artifact")
        if name in engines.browser_trial_package()["executables"] and not (path / name).stat().st_mode & 0o111:
            raise TrialError("Candidate executable mode is missing")


def stock_files(root: Path) -> dict:
    protected(root)
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            protected(path.parent)
            target = path.resolve(strict=True)
            if not target.is_relative_to(root):
                raise TrialError("Stock package link leaves its protected package")
            protected(target)
            files[str(path.relative_to(root))] = {"link": os.readlink(path)}
            continue
        protected(path)
        if path.is_file():
            files[str(path.relative_to(root))] = {"sha256": digest(regular(path)),
                                                 "mode": stat.S_IMODE(path.stat().st_mode)}
        elif not path.is_dir():
            raise TrialError("Special stock package input")
    return files


def owner() -> dict:
    identity = {"project": os.environ["ALTITUDE_PROJECT"], "slug": os.environ["ALTITUDE_TASK"],
                "attempt": int(os.environ["ALTITUDE_ATTEMPT"])}
    task = S.load_task(identity["project"], identity["slug"])
    if task.get("attempt") != identity["attempt"] or task.get("state") != "running":
        raise TrialError("Trial requires the admitted current owner identity")
    identity["agent_id"] = task.get("agent_id")
    return identity


def native_interval(row: dict, seconds: int) -> None:
    until = time.monotonic() + seconds
    candidate_seen = False
    while time.monotonic() < until:
        identity = row["owner"]  # Protected identity; task data can only shorten this interval.
        task = S.load_task(identity["project"], identity["slug"])
        candidate_seen |= bool(task.get("agent_id") and task["agent_id"] != identity.get("agent_id"))
        if (task.get("attempt") != identity["attempt"] or task.get("fault") or task.get("resume_failed")
                or (candidate_seen and task.get("state") == "blocked" and task.get("stop_id"))):
            raise TrialError("Native owner identity changed or verification faulted; restoring stock")
        time.sleep(min(1, max(0, until - time.monotonic())))


def require_native() -> None:
    platform.require_native_browser_trial()


def prepare(candidate: Path) -> dict:
    require_native()
    from scripts import restart_altitude as restart
    restart.require_deployed_checkout()
    parent = home()
    if parent.exists():
        protected(parent)
        raise TrialError("Prior trial area is retained; restore and archive it before another trial")
    protected(parent.parent)
    identity = owner()
    package(candidate)
    protected(platform.service_path())
    original = regular(platform.service_path())
    agent = plistlib.loads(original)
    environment = agent.get("EnvironmentVariables", {})
    stock = engines.browser_trial_stock(environment)
    protected(stock)
    stock_root = engines.browser_trial_stock_root(stock)
    stock_identity = stock_files(stock_root)
    original_mode = stat.S_IMODE(platform.service_path().stat().st_mode)
    if platform.service_path().is_symlink() or platform.service_path().stat().st_uid != os.getuid():
        raise TrialError("Linked or foreign-owned service definition")
    parent.mkdir(mode=0o700)
    try:
        target = parent / "package"
        target.mkdir(mode=0o700)
        for name in engines.browser_trial_package()["files"]:
            output = target / name
            output.parent.mkdir(parents=True, exist_ok=True)
            data = regular(candidate / name)
            atomic(output, data, 0o555 if name in engines.browser_trial_package()["executables"] else 0o444)
        package(target)
        for directory in sorted((d for d in target.rglob("*") if d.is_dir()), reverse=True):
            directory.chmod(0o555)
        target.chmod(0o555)
        entrypoint = target / engines.browser_trial_package()["entrypoint"]
        agent["EnvironmentVariables"] = engines.browser_trial_selection(environment, entrypoint)
        selected = plistlib.dumps(agent)
        atomic(parent / "original.plist", original, 0o400)
        atomic(parent / "selected.plist", selected, 0o400)
        row = {"original_sha256": digest(original), "selected_sha256": digest(selected),
               "stock": str(stock), "stock_sha256": digest(regular(stock)),
               "stock_root": str(stock_root), "stock_files": stock_identity, "owner": identity,
               "service": str(platform.service_path()), "service_mode": original_mode,
               "entrypoint": str(entrypoint), "source": str(config.SOURCE.resolve()), "terminated_jobs": []}
        record(row, "prepared")
        return row
    except BaseException:
        # No selection occurs in prepare; remove only this command's newly-created partial area.
        for directory in parent.rglob("*"):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o700)
        shutil.rmtree(parent)
        raise


def receipt() -> dict:
    require_native()
    protected(home())
    protected(home() / "receipt.json")
    row = json.loads(regular(home() / "receipt.json"))
    if row["service"] != str(platform.service_path()) or row["entrypoint"] != str(
            home() / "package" / engines.browser_trial_package()["entrypoint"]):
        raise TrialError("Recovery receipt identity differs from this installation")
    for name, key in (("original.plist", "original_sha256"), ("selected.plist", "selected_sha256")):
        protected(home() / name)
        if digest(regular(home() / name)) != row[key]:
            raise TrialError("Recovery backup digest differs")
    return row


def activate(row: dict) -> None:
    source = Path(row["source"])
    protected(source)
    unit = f"altitude-browser-activation-{uuid.uuid4().hex}.service"
    environment = engines.codex_env(retain_user_bus=True)
    command = platform.job_command(unit, [sys.executable, "-B", str(source / "scripts/restart_altitude.py")],
                                   environment, runtime_max=90)
    try:
        result = subprocess.run(command, cwd=config.REPO, env=environment, timeout=105, check=False)
        if result.returncode:
            raise TrialError("Guarded activation refused or failed; stock health is unverified")
    except subprocess.TimeoutExpired as exc:
        raise TrialError("Guarded activation timed out; stock health is unverified") from exc
    finally:
        platform.job_stop(unit, timeout=10)


def stop_jobs(row: dict) -> None:
    for unit in engines.browser_trial_jobs(Path(row["entrypoint"])):
        platform.job_stop(unit, timeout=30)
        if platform.job_active(unit, {}):
            raise TrialError("Candidate job termination remains unverified")
        row["terminated_jobs"].append(unit)
        record(row, "restoring")


def _restore() -> None:
    row = receipt()
    record(row, "restoring")
    try:
        original = regular(home() / "original.plist")
        selected = regular(home() / "selected.plist")
        if platform.service_path().is_symlink():
            raise TrialError("Linked service definition; recovery is pending")
        protected(platform.service_path())
        current = regular(platform.service_path())
        if current not in (original, selected):
            raise TrialError("Service definition drift; unrelated settings are preserved")
        protected(Path(row["stock"]))
        original_environment = plistlib.loads(original).get("EnvironmentVariables", {})
        current_stock = engines.browser_trial_stock(original_environment)
        if str(current_stock) != row["stock"]:
            raise TrialError("Stock executable selection changed")
        if digest(regular(Path(row["stock"]))) != row["stock_sha256"]:
            raise TrialError("Stock executable identity changed")
        if stock_files(Path(row["stock_root"])) != row["stock_files"]:
            raise TrialError("Complete stock package identity changed")
        if current == selected:
            atomic(platform.service_path(), original, row["service_mode"])
        stop_jobs(row)  # Failed-isolation candidate jobs end before activation.
        activate(row)  # Retains deployed-checkout and quiet-point guards, new PID and API/UI checks.
        stop_jobs(row)  # The old daemon may have admitted more candidate jobs before stock activation.
        if engines.browser_trial_jobs(Path(row["entrypoint"])):
            raise TrialError("Candidate jobs remain after stock activation")
        record(row, "restored", stock_health="verified", candidate_termination="verified")
    except BaseException as exc:
        record(row, "recovery-pending", failure=str(exc), stock_health="unverified")
        raise


def restore() -> None:
    with transaction(), platform.native_browser_trial_deadline(210):
        _restore()


def trial(candidate: Path, seconds: int) -> None:
    if not 1 <= seconds <= 90:
        raise TrialError("Native interval must be 1..90 seconds, reserving rollback time below the grant limit")
    with transaction():
        row = None
        try:
            with platform.native_browser_trial_deadline(210):
                row = prepare(candidate)
                selected = regular(home() / "selected.plist")
                if digest(regular(platform.service_path())) != row["original_sha256"]:
                    raise TrialError("Service definition changed before selection")
                record(row, "selected")  # Durable backup exists before this first external setting change.
                atomic(platform.service_path(), selected, row["service_mode"])
                activate(row)
                record(row, "ready", seconds=seconds)
                print("L3 uses supported Stop/resume for this owner. Native acceptance runs only in that fresh confined worker.", flush=True)
                native_interval(row, seconds)
        except BaseException as exc:
            if row is not None:
                record(row, "recovery-pending", trial_failure=str(exc))
            raise
        finally:
            if row is not None:
                with platform.native_browser_trial_deadline(210):
                    _restore()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("trial")
    run.add_argument("package", type=Path)
    run.add_argument("--seconds", type=int, default=90)
    commands.add_parser("restore")
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Native trial interrupted by signal {signum}")
    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        if args.action == "trial":
            trial(args.package, args.seconds)
        else:
            restore()
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, KeyboardInterrupt, platform.NativeBrowserTrialDeadline) as exc:
        print(f"Native browser trial failed: {exc}; protected evidence retained at {home()}", file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())
