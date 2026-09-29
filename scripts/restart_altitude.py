#!/usr/bin/env python3
"""Operator-only, failure-safe rebuild and restart of deployed Altitude."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from altitude import access, config, dispatch, git_policy, incidents, platform, state as S  # noqa: E402


ROOT = config.REPO
SERVICE = "altitude.service"
WEB = ROOT / "web"
DIST = WEB / "dist"


class RestartError(RuntimeError):
    """A restart boundary could not be proven safe or healthy."""


def run(args: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
        capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, env=env, text=True,
                            capture_output=capture, check=False)
    if result.returncode:
        detail = ((result.stderr or result.stdout or "") if capture else "").strip()
        raise RestartError(f"{' '.join(args)} failed" + (f": {detail}" if detail else ""))
    return result


def unit_properties() -> dict[str, str]:
    result = run([
        "systemctl", "--user", "show", SERVICE,
        "--property=WorkingDirectory", "--property=Environment",
        "--property=MainPID", "--property=ActiveState", "--property=SubState",
    ], capture=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def unit_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    for item in shlex.split(unit_properties().get("Environment", "")):
        if "=" in item:
            key, value = item.split("=", 1)
            values[key] = value
    return values


def require_deployed_checkout() -> None:
    deployed = unit_properties().get("WorkingDirectory", "")
    if not deployed or Path(deployed).expanduser().resolve() != ROOT.resolve():
        raise RestartError(
            f"run this command from the installed service checkout ({deployed or 'unknown'}), not {ROOT}"
        )
    service_home = Path(unit_environment().get("ALTITUDE_HOME", Path.home() / ".altitude"))
    if service_home.expanduser().resolve() != config.ROOT.expanduser().resolve():
        raise RestartError(
            f"ALTITUDE_HOME points to {config.ROOT}, but {SERVICE} uses {service_home}; refusing the wrong state"
        )
    try:
        git_policy.fetch_and_require_exact_base(ROOT)
    except git_policy.GitPolicyError as exc:
        raise RestartError(str(exc)) from exc


def require_idle() -> None:
    # 2026-09-07 queue starvation: detached workers survive; only launch/bind, L3 and report windows hold.
    # Ask the live daemon too: its L3 turns and verification are process-local, unlike task markers.
    if unit_properties().get("ActiveState") == "active":
        try:
            status = json.loads(fetch("/api/overview")).get("restart") or {}
        except (RestartError, ValueError) as exc:
            raise RestartError(f"cannot prove Altitude is quiet: {exc}") from exc
        if status.get("waiting_for"):
            raise RestartError("restart refused while work is active: " + ", ".join(status["waiting_for"]))
    with config.restart_lock(exclusive=True) as quiet:
        active = [f"{p}/{t['slug']}" for p in config.load_projects() for t in S.list_tasks(p)
                  if t.get("dispatching") or t.get("resume_claim")]
        if not quiet or active:
            raise RestartError("restart refused while dispatch, L3 or report verification is active: " + ", ".join(active))
        # The operator command also closes entry until the replacement daemon answers.
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        pending = S.read_json(flag, {}) or {}
        if pending.pop("failed", None):
            pending.pop("requested_at", None)
        pending.setdefault("requested_at", S.now())
        S.write_json(flag, pending)


def validate_bundle(directory: Path) -> None:
    index = directory / "index.html"
    if not index.is_file():
        raise RestartError("staged web bundle has no index.html")
    contents = index.read_text(errors="replace")
    assets = re.findall(r'(?:src|href)="/assets/([^"?#]+)', contents)
    if not assets or not any(asset.endswith(".js") for asset in assets):
        raise RestartError("staged web bundle index has no JavaScript asset")
    if any(not (directory / "assets" / asset).is_file() for asset in assets):
        raise RestartError("staged web bundle index references a missing asset")


def build_bundle() -> Path:
    staging = Path(tempfile.mkdtemp(prefix=".dist-next-", dir=WEB))
    env = config.subprocess_env()
    try:
        run(["pnpm", "install", "--frozen-lockfile"], cwd=WEB, env=env)
        run(["pnpm", "exec", "tsc", "-b"], cwd=WEB, env=env)
        run(["pnpm", "exec", "vite", "build", "--outDir", str(staging)], cwd=WEB, env=env)
        validate_bundle(staging)
        return staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def service_address() -> tuple[str, int]:
    values = unit_environment()
    return values.get("ALTITUDE_HOST", config.HOST), int(values.get("ALTITUDE_PORT", config.PORT))


def fetch(path: str, *, timeout: float = 2.0) -> bytes:
    host, port = service_address()
    schemes = ("https", "http") if config.TLS else ("http", "https")
    context = ssl._create_unverified_context()  # local CA may not be in Python's trust store
    last_error: Exception | None = None
    for scheme in schemes:
        try:
            # The address is this machine's own, so the key never leaves it.
            request = Request(f"{scheme}://{host}:{port}{path}", headers={access.KEY_HEADER: access.machine_key() or ""})
            with urlopen(request, timeout=timeout,
                         context=context if scheme == "https" else None) as response:
                if response.status != 200:
                    raise RestartError(f"{path} returned HTTP {response.status}")
                return response.read()
        except (OSError, URLError, RestartError) as exc:
            last_error = exc
    raise RestartError(f"{path} is unreachable: {last_error}")


def wait_healthy(old_pid: int, timeout: int = 45) -> None:
    deadline = time.monotonic() + timeout
    last = "service did not start"
    while time.monotonic() < deadline:
        try:
            props = unit_properties()
            pid = int(props.get("MainPID", "0") or 0)
            if props.get("ActiveState") != "active" or not pid or (old_pid and pid == old_pid):
                raise RestartError(
                    f"systemd is {props.get('ActiveState')}/{props.get('SubState')} with PID {pid}"
                )
            overview = json.loads(fetch("/api/overview"))
            if not isinstance(overview.get("projects"), list):
                raise RestartError("/api/overview returned an invalid payload")
            page = fetch("/").decode("utf-8", "replace")
            if 'id="root"' not in page:
                raise RestartError("web root did not return the SPA shell")
            return
        except (RestartError, ValueError, json.JSONDecodeError) as exc:
            last = str(exc)
            time.sleep(1)
    raise RestartError(f"Altitude did not become healthy within {timeout}s: {last}")


def diagnostics() -> None:
    subprocess.run(["systemctl", "--user", "status", SERVICE, "--no-pager"], check=False)
    subprocess.run(["journalctl", "--user", "-u", SERVICE, "-n", "30", "--no-pager"], check=False)


def publish_and_restart(staging: Path) -> None:
    backup = WEB / ".dist-previous"
    if backup.exists():
        raise RestartError(
            f"refusing to overwrite leftover rollback bundle {backup}; inspect or restore it first"
        )
    old_pid = int(unit_properties().get("MainPID", "0") or 0)
    had_previous = DIST.exists()
    if had_previous:
        DIST.rename(backup)
    try:
        staging.rename(DIST)
        run(["systemctl", "--user", "restart", SERVICE])
        wait_healthy(old_pid)
    except Exception as exc:
        if DIST.exists():
            DIST.rename(staging)
        if had_previous and backup.exists():
            backup.rename(DIST)
        try:
            run(["systemctl", "--user", "restart", SERVICE])
        except RestartError:
            pass
        diagnostics()
        raise RestartError(f"restart verification failed; restored the prior web bundle: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup)


def requested_at() -> str | None:
    return (S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {}) or {}).get("requested_at")


def record_failure(attempt: str | None, error: str) -> None:
    """File this unit's own reason at once and reopen entry (I-20260924-205802: altd learned of a 10-second
    failure only at its ten-minute grace timeout, which now covers just a unit that dies without reporting).
    A replacement daemon that failed verification has already cleared the record; the fault is still filed."""
    if not attempt:
        return
    flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
    pending = S.read_json(flag, {}) or {}
    if pending.get("requested_at") == attempt:
        if pending.get("failed"):
            return
        pending.update(failed=S.now(), error=error)
        S.write_json(flag, pending)
    incidents.system_fault("restart", f"restart failed: {error}")


def main() -> int:
    platform.require_native_application()
    staging: Path | None = None
    attempt = requested_at()
    try:
        print("Checking the deployed checkout...")
        require_deployed_checkout()
        print("Building the web app in a staging directory...")
        staging = build_bundle()
        require_deployed_checkout()
        print("Checking that Altitude is idle...")
        require_idle()
        attempt = requested_at()
        print("Restarting Altitude and waiting for API/UI health...")
        publish_and_restart(staging)
        staging = None
    except RestartError as exc:
        print(f"Altitude restart failed: {exc}", file=sys.stderr)
        record_failure(attempt, str(exc))
        return 1
    finally:
        if staging and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    print("Altitude rebuilt and restarted successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
