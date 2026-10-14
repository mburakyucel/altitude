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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from altitude import access, config, dispatch, git_policy, incidents, platform, state as S, tls  # noqa: E402


ROOT = config.REPO
SERVICE = "altitude.service"
WEB = ROOT / "web"
DIST = WEB / "dist"


class RestartError(RuntimeError):
    """A restart boundary could not be proven safe or healthy."""


class Transient(RestartError):
    """A refusal a later quiet point can clear: altd requests the unit again (I-20261010-214654)."""


# I-20261010-214654, I-20261012-101803: a loaded host, then a healthy replacement, answered the overview slower than the
# old two-second probe. Both the quiet and the health probe wait this long for one answer.
PROBE_SECONDS = 10.0
QUIET_PROBES = 3
RETRIES = 3  # transient refusals altd retries before the activation parks with a fault
RETRY_SECONDS = 300


def run(args: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
        capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, env=env, text=True,
                            capture_output=capture, check=False)
    if result.returncode:
        detail = ((result.stderr or result.stdout or "") if capture else "").strip()
        raise RestartError(f"{' '.join(args)} failed" + (f": {detail}" if detail else ""))
    return result


def unit_properties() -> dict[str, str]:
    try:
        return platform.service_unit()
    except RuntimeError as exc:
        raise RestartError(str(exc)) from exc


def restart_unit() -> None:
    try:
        platform.control("restart")
    except (RuntimeError, ValueError) as exc:
        raise RestartError(f"restarting {SERVICE} failed: {exc}") from exc


def unit_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    for item in shlex.split(unit_properties().get("Environment", "")):
        if "=" in item:
            key, value = item.split("=", 1)
            values[key] = value
    return values


def require_deployed_checkout() -> str:
    """The deployed checkout's head, held to the service's own startup rule: on its branch, its files exactly that
    commit, and the commit on the branch in Altitude's copy of its repository, so the web app builds from what the
    service starts. Behind is fine: the unit activates the head it builds and the deployment tick pends the rest
    (I-20261010-214654: origin moved during the build and the refusal parked the activation for hours)."""
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
        # The service's branch, as it runs: this detached job inherits only PATH.
        return git_policy.source_head(ROOT, unit_environment().get("ALTITUDE_SOURCE_BRANCH") or "main")
    except git_policy.GitPolicyError as exc:
        raise RestartError(str(exc)) from exc


def require_idle() -> None:
    # Detached workers survive; validation holds through its bounded execution and evidence recording (#649).
    # Ask the live daemon too: its L3 turns and verification are process-local, unlike task markers.
    if unit_properties().get("ActiveState") == "active":
        for probe in range(QUIET_PROBES):
            try:
                status = json.loads(fetch("/api/overview")).get("restart") or {}
                break
            except (RestartError, ValueError) as exc:
                if probe + 1 == QUIET_PROBES:
                    raise Transient(f"cannot prove Altitude is quiet: {exc}") from exc
                time.sleep(5)
        if status.get("waiting_for"):
            raise Transient("restart refused while work is active: " + ", ".join(status["waiting_for"]))
    with config.restart_lock(exclusive=True) as quiet:
        active = [f"{p}/{t['slug']}" for p in config.load_projects() for t in S.list_tasks(p)
                  if t.get("dispatching") or t.get("resume_claim")]
        if not quiet or active:
            raise Transient("restart refused while dispatch, L3, validation or report verification is active: " + ", ".join(active))
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


def service() -> dict:
    """Where the service listens and whether it serves HTTPS, from its own environment."""
    return config.network(unit_environment())


def fetch(path: str, *, timeout: float = PROBE_SECONDS) -> bytes:
    network = service()
    scheme = "https" if network["tls"] else "http"
    # The key goes only to a service that proves it is this installation's, as `alt` requires: with its folder's CA
    # (the system trust store for an external certificate), never to whatever answers on the port while it restarts.
    ca = network["tls_dir"] / "ca.crt"
    context = (ssl.create_default_context(cafile=str(ca) if ca.exists() else None) if network["tls"] else None)
    try:
        request = Request(f"{tls.located(network)['url']}{path}", headers={access.KEY_HEADER: access.machine_key() or ""})
        with urlopen(request, timeout=timeout, context=context) as response:
            if response.status != 200:
                raise RestartError(f"{path} returned HTTP {response.status}")
            return response.read()
    except OSError as exc:
        raise RestartError(f"{path} is unreachable over {scheme}: {exc}") from exc


def wait_healthy(old_pid: int, timeout: int = 45) -> None:
    deadline = time.monotonic() + timeout
    last = "service did not start"

    def probe(path: str) -> bytes:  # no probe starts or waits past the bound
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RestartError(f"no time was left to probe {path}")
        return fetch(path, timeout=min(PROBE_SECONDS, remaining))

    while time.monotonic() < deadline:
        try:
            props = unit_properties()
            pid = int(props.get("MainPID", "0") or 0)
            if props.get("ActiveState") != "active" or not pid or (old_pid and pid == old_pid):
                raise RestartError(
                    f"the service is {props.get('ActiveState')}/{props.get('SubState')} with PID {pid}"
                )
            overview = json.loads(probe("/api/overview"))
            if not isinstance(overview.get("projects"), list):
                raise RestartError("/api/overview returned an invalid payload")
            page = probe("/").decode("utf-8", "replace")
            if 'id="root"' not in page:
                raise RestartError("web root did not return the SPA shell")
            return
        except (RestartError, ValueError, json.JSONDecodeError) as exc:
            last = str(exc)
            time.sleep(1)
    raise RestartError(f"Altitude did not become healthy within {timeout}s: {last}")


def diagnostics() -> None:
    try:
        print(platform.logs(), file=sys.stderr)
    except RuntimeError as exc:
        print(f"The service log is unavailable: {exc}", file=sys.stderr)


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
        restart_unit()
        wait_healthy(old_pid)
    except Exception as exc:
        if DIST.exists():
            DIST.rename(staging)
        if had_previous and backup.exists():
            backup.rename(DIST)
        recovery = "no prior web bundle was available; recovery not attempted"
        if had_previous:
            try:
                recovery_pid = int(unit_properties().get("MainPID", "0") or 0)
                restart_unit()
                wait_healthy(recovery_pid)
                recovery = "restored the prior web bundle; recovery API/UI health verified"
            except (RestartError, ValueError) as recovery_error:
                recovery = f"restored the prior web bundle; recovery failed: {recovery_error}"
        diagnostics()
        raise RestartError(f"restart verification failed: {exc}; {recovery}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup)


def requested() -> dict:
    return S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {}) or {}


def record_failure(attempt: dict, exc: RestartError) -> None:
    """File this unit's own reason at once and reopen entry (I-20260924-205802: altd learned of a 10-second
    failure only at its ten-minute grace timeout, which now covers just a unit that dies without reporting).
    A transient refusal instead withdraws the request for altd to retry at a quiet point after RETRY_SECONDS, at
    most RETRIES times, before it too is filed (I-20261010-214654). A replacement daemon that started has already
    cleared the record and runs the new backend with the prior web bundle restored, so the attempt's web files
    return as a failed pending activation (I-20261012-101803: the stale UI went unrecorded)."""
    if not attempt.get("requested_at"):
        return
    flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
    pending = S.read_json(flag, {}) or {}
    if pending.get("requested_at") == attempt["requested_at"]:
        if pending.get("failed"):
            return
        retries = pending.get("retries", 0)
        if isinstance(exc, Transient) and retries < RETRIES:
            del pending["requested_at"]
            retry_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=RETRY_SECONDS)
            pending.update(error=str(exc), retries=retries + 1, retry_at=retry_at.isoformat())
            S.write_json(flag, pending)
            return
        pending.update(failed=S.now(), error=str(exc))
        S.write_json(flag, pending)
    elif web := [path for path in attempt.get("files", []) if dispatch.activation_component(path) == "web"]:
        create_absent(flag, {**{key: attempt[key] for key in ("since", "head") if key in attempt},
                             "files": web, "failed": S.now(), "error": str(exc)})
    incidents.system_fault("restart", f"restart failed: {exc}")


def create_absent(path: Path, record: dict) -> None:
    """Write `record` only where no record exists. A merge or restart request the replacement recorded meanwhile keeps
    its own record and activates the current bundle itself; nothing here reads and rewrites another writer's record."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError:
        pass
    finally:
        os.unlink(temporary)


def main() -> int:
    platform.require_native_application()
    staging: Path | None = None
    attempt = requested()
    try:
        print("Checking the deployed checkout...")
        head = require_deployed_checkout()
        print("Building the web app in a staging directory...")
        staging = build_bundle()
        print("Checking that Altitude is idle...")
        require_idle()
        attempt = requested()
        # With the gate closed no self-deploy moves the checkout again, so the bundle and the backend the service
        # exports at startup are the same commit.
        if (moved := require_deployed_checkout()) != head:
            raise Transient(f"the checkout moved from {head[:7]} to {moved[:7]} after the build began")
        print("Restarting Altitude and waiting for API/UI health...")
        publish_and_restart(staging)
        staging = None
    except RestartError as exc:
        print(f"Altitude restart failed: {exc}", file=sys.stderr)
        record_failure(attempt, exc)
        return 1
    finally:
        if staging and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    print("Altitude rebuilt and restarted successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
