"""The macOS counterpart of systemd/altitude.service: run this checkout's altd as the login's LaunchAgent.

`make install-service` runs it on a Mac. The agent carries the unit's settings. It listens on ALTITUDE_HOST from this
command's environment, 127.0.0.1 by default as for `make run`; its PATH is the one this command runs with, so the
service finds the same Python, OpenSSL 3, gh and coding CLIs as the operator's shell. A checkout on a branch other
than main runs and self-deploys that branch (ALTITUDE_SOURCE_BRANCH)."""
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from altitude import platform  # noqa: E402


def main() -> int:
    platform.require_supported()
    if sys.version_info < (3, 12):
        sys.exit(f"Altitude needs Python 3.12 or newer; {sys.executable} is {sys.version.split()[0]}.")
    log = str(platform.logs_dir() / "altd.log")
    branch = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                            check=True).stdout.strip()
    environment = {"ALTITUDE_HOST": os.environ.get("ALTITUDE_HOST") or "127.0.0.1", "ALTITUDE_PORT": "8890", "ALTITUDE_SERVICE": "1",
                   # This installation's own choice: its incidents become issues on the public tracker.
                   "ALTITUDE_UPSTREAM_ISSUE_REPOSITORY": "mburakyucel/altitude", "PATH": os.environ["PATH"],
                   # launchd starts an agent in the account's home; the service keeps this one, where its label lives.
                   "HOME": str(Path.home())}
    if branch != "main":
        environment["ALTITUDE_SOURCE_BRANCH"] = branch
    agent = {
        "Label": platform.service_label(), "ProgramArguments": [sys.executable, "-B", str(ROOT / "bin" / "alt"), "serve"],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": environment,
        # Restart=on-failure with RestartSec=20: launchd restarts a failed service, not one that exited cleanly.
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 20,
        "ProcessType": "Standard", "StandardOutPath": log, "StandardErrorPath": log}
    platform.service_path().parent.mkdir(parents=True, exist_ok=True)
    platform.service_path().write_bytes(plistlib.dumps(agent))
    platform.control("restart")  # bootstraps it again, so launchd reads this definition
    time.sleep(5)  # altd refuses a checkout that is not clean and at its origin branch within its first seconds
    status = platform.status()
    print(f"{platform.service_label()} on {branch}: {status['ActiveState']} (pid {status['MainPID']}); log: {log}")
    if status["ActiveState"] != "active":
        lines = Path(log).read_text(errors="replace").splitlines() if Path(log).exists() else []
        print(lines[-1] if lines else "The log is empty.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
