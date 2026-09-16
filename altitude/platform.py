"""Native per-user application service operations. Worker execution stays in engines."""
from __future__ import annotations

import os
from pathlib import Path
import platform as host_platform
import subprocess
import sys


SERVICE = "altitude.service"


def require_supported() -> None:
    if sys.platform != "linux" or host_platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("Packaged runtime currently targets Linux x86_64; native macOS validation is pending.")


def run(*args: str) -> str:
    try:
        result = subprocess.run(list(args), capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Native user service unavailable: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"Native user service failed: {(result.stderr or result.stdout).strip()[:500]}")
    return result.stdout


def service_path() -> Path:
    return Path.home() / ".config/systemd/user" / SERVICE


def status() -> dict[str, str]:
    require_supported()
    result = run("systemctl", "--user", "show", SERVICE,
                 "--property=LoadState,ActiveState,SubState,FragmentPath,MainPID,UnitFileState")
    values = dict(line.split("=", 1) for line in result.splitlines() if "=" in line)
    if values.get("LoadState") not in ("loaded", "not-found"):
        raise RuntimeError("Cannot determine the Altitude user service; inspect systemctl --user status altitude.")
    if values["LoadState"] == "loaded" and values.get("ActiveState") not in (
            "active", "inactive", "failed", "activating", "deactivating", "reloading"):
        raise RuntimeError("Cannot determine whether the Altitude user service is running")
    return values


def control(action: str) -> str:
    require_supported()
    if action == "reload":
        return run("systemctl", "--user", "daemon-reload")
    if action not in ("start", "stop", "restart", "enable", "disable"):
        raise ValueError("Unknown application service operation")
    return run("systemctl", "--user", action, SERVICE)


def logs() -> str:
    return run("journalctl", "--user", "-u", SERVICE, "--no-pager", "-n", "100")


def definition(prefix: Path, python: Path, settings: Path, environment: dict[str, str]) -> str:
    def quote(value: str | Path) -> str:
        # systemd expands specifiers even inside quotes; no shell interprets these arguments.
        return '"' + str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'

    for value in (prefix, python, settings, *environment.values()):
        if any(ch in str(value) for ch in ("\n", "\r", "\x00")):
            raise ValueError("Service paths and PATH must not contain control characters")
    return ("[Unit]\nDescription=Altitude private application\n\n[Service]\nType=simple\n"
            f"WorkingDirectory={quote(prefix)}\n"
            f"ExecStart=:{quote(python)} -B {quote(prefix / 'current/bin/alt')} serve\n"
            f"Environment={quote('ALTITUDE_CONFIG=' + str(settings))}\n"
            + "".join(f"Environment={quote(key + '=' + value)}\n" for key, value in environment.items())
            +
            "Environment=ALTITUDE_SERVICE=1\nEnvironment=ALTITUDE_TLS=1\nRestart=on-failure\nRestartSec=5\n"
            "KillMode=control-group\nNoNewPrivileges=yes\nUMask=0077\n\n[Install]\nWantedBy=default.target\n")
