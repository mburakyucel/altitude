"""The platform seam: the host's service manager and process facilities.

Linux x86_64 with a systemd user manager is the implemented host. This module is the only place that names
systemd or procfs; engines, the terminal and the server ask it for jobs, processes and connections.
The macOS runtime is not implemented yet (issue #225); `require_supported` refuses it.
"""
from __future__ import annotations

import fcntl
import ipaddress
import os
from pathlib import Path
import platform as host_platform
import re
import shlex
import shutil
import struct
import subprocess
import sys


SERVICE = "altitude.service"
#: What First run shows for a missing command-line tool, run in the operator's own terminal.
INSTALL = {"gh": "sudo apt install gh", "git": "sudo apt install git"}


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


#: The settings that say where the service listens and which HTTPS identity it serves.
SERVICE_SETTINGS = ("ALTITUDE_HOST", "ALTITUDE_PORT", "ALTITUDE_TLS", "ALTITUDE_TLS_DIR")


def service_settings() -> tuple[int, dict[str, str]]:
    """The running service's main process and the settings it started with, whichever unit, drop-in or
    environment file supplied them. A shell's own environment does not describe the service."""
    values = status()
    pid = int(values.get("MainPID") or 0)
    if values["LoadState"] != "loaded":
        raise RuntimeError("No Altitude service is installed for this user.")
    if values.get("ActiveState") != "active" or not pid:
        raise RuntimeError("The Altitude service is not running. Start it, then retry.")
    try:
        entries = (PROC / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError as exc:
        raise RuntimeError(f"Cannot read the Altitude service's settings: {exc}.") from exc
    settings = {}
    for entry in entries:
        key, _, value = entry.decode(errors="replace").partition("=")
        if key in SERVICE_SETTINGS:
            settings[key] = value
    return pid, settings


def control(action: str) -> str:
    require_supported()
    if action == "reload":
        return run("systemctl", "--user", "daemon-reload")
    if action not in ("start", "stop", "restart", "enable", "disable"):
        raise ValueError("Unknown application service operation")
    return run("systemctl", "--user", action, SERVICE)


def detach(name: str, argv: list[str], environment: dict[str, str]) -> str:
    """Run one command as its own short-lived user unit, so it outlives a restart of the Altitude service."""
    require_supported()
    return run("systemd-run", "--user", "--collect", "--quiet", "--expand-environment=no", f"--unit={name}",
               *(f"--setenv={key}={value}" for key, value in environment.items()), *argv)


def logs() -> str:
    return run("journalctl", "--user", "-u", SERVICE, "--no-pager", "-n", "100")


def definition(prefix: Path, python: Path, settings: Path, environment: dict[str, str]) -> str:
    def literal(value: str | Path) -> str:
        # systemd expands specifiers in every one of these settings, even inside quotes.
        return str(value).replace("%", "%%")

    def quote(value: str | Path) -> str:
        # ExecStart= and Environment= split words and unescape; no shell interprets these arguments.
        return '"' + literal(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    for value in (prefix, python, settings, *environment.values()):
        if any(ch in str(value) for ch in ("\n", "\r", "\x00")):
            raise ValueError("Service paths and PATH must not contain control characters")
    if str(prefix) != str(prefix).rstrip() or str(prefix).endswith("\\"):
        # WorkingDirectory= drops trailing whitespace and a trailing backslash continues the line.
        raise ValueError("The installation prefix must not end in whitespace or a backslash")
    return ("[Unit]\nDescription=Altitude private application\n\n[Service]\nType=simple\n"
            f"WorkingDirectory={literal(prefix)}\n"  # one verbatim path: quotes would be part of it
            f"ExecStart=:{quote(python)} -B {quote(prefix / 'current/bin/alt')} serve\n"
            f"Environment={quote('ALTITUDE_CONFIG=' + str(settings))}\n"
            + "".join(f"Environment={quote(key + '=' + value)}\n" for key, value in environment.items())
            +
            "Environment=ALTITUDE_SERVICE=1\nEnvironment=ALTITUDE_TLS=1\nRestart=on-failure\nRestartSec=5\n"
            "KillMode=control-group\nNoNewPrivileges=yes\nUMask=0077\n\n[Install]\nWantedBy=default.target\n")


# --- Jobs: one command in its own transient user unit ------------------------------------------------------------

SYSTEMD_RUN = shutil.which("systemd-run") or "systemd-run"
SYSTEMCTL = shutil.which("systemctl") or "systemctl"
ENV_BIN = shutil.which("env") or "/usr/bin/env"


def manager_env(env: dict) -> dict:
    """The environment of a process that asks the user manager for a job. A system service does not necessarily
    inherit the interactive session's bus variables, so their canonical per-user values are synthesized."""
    env["TMPDIR"] = "/tmp"
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env["DBUS_SESSION_BUS_ADDRESS"] = env.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime_dir}/bus"
    return env


def job_env(env: dict) -> dict:
    """The environment inside a job: no user-manager bus, and the shared temporary directory."""
    env["TMPDIR"] = "/tmp"
    env.pop("XDG_RUNTIME_DIR", None)
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    return env


def job_control_paths() -> tuple[Path, ...]:
    """Local user-manager endpoints a confined worker must not use to launch an unconfined job."""
    runtime = Path(f"/run/user/{os.getuid()}")
    return runtime / "bus", runtime / "systemd/private"


def _scrub(env: dict[str, str]) -> list[str]:
    # A transient service inherits the user manager's environment, not the launching client's. Clear it completely
    # and reconstruct only the already-sanitized child environment so task identity survives without ambient manager
    # credentials or control sockets crossing the boundary.
    return [ENV_BIN, "-i", *(f"{key}={env[key]}" for key in sorted(env))]


def job_command(name: str, command: list[str], env: dict[str, str], *, runtime_max: int | None = None) -> list[str]:
    """Run a command synchronously, piped to the caller, in a job of its own that holds every descendant.

    ``--wait --pipe`` keeps the launch synchronous while the user manager, rather than the hardened Altitude parent,
    creates the child. This lets nested bwrap initialize without weakening altd's ``NoNewPrivileges=yes`` boundary.
    Unlike a process group, the service cgroup retains descendants that call ``setsid`` or double-fork. An engine's inner
    sandbox supplies the PID namespace; keeping syscall filters off the outer service preserves nested bwrap.
    """
    return [SYSTEMD_RUN, "--user", "--wait", "--pipe", f"--unit={name}", "--quiet", "--collect",
            "--same-dir", "--expand-environment=no", "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes", "--property=NoNewPrivileges=no",
            *([f"--property=RuntimeMaxSec={runtime_max}", "--property=TimeoutStopSec=5"] if runtime_max else []),
            "--", *_scrub(env), *command]


def logged_job_command(name: str, command: str, *, log: Path, status: Path, env: dict[str, str],
                       timeout: int) -> list[str]:
    """One shell command as a job that appends its own output to `log` and writes its exit status to `status`,
    so a command that restarts Altitude still leaves a durable record. ``RuntimeMaxSec`` bounds it."""
    runner = 'bash -lc "$1"; status=$?; printf %s "$status" > "$2"; exit "$status"'
    return [SYSTEMD_RUN, "--user", "--wait", "--collect", "--quiet", f"--unit={name}", "--same-dir",
            "--expand-environment=no", "--property=KillMode=control-group", "--property=SendSIGKILL=yes",
            f"--property=RuntimeMaxSec={timeout}", "--property=TimeoutStopSec=5",
            f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
            "--", *_scrub(env), "/bin/bash", "-c", runner, "altitude-machine", command, str(status)]


def terminal_job(name: str, tty: str, command: list[str], env: dict[str, str], *, grace: int) -> list[str]:
    """A login shell as a job of its own on the pseudo-terminal `tty`, started with the caller's working directory.

    The user manager, not the hardened Altitude parent, creates the shell, so it does not inherit altd's
    ``NoNewPrivileges=yes`` and ``sudo`` can ask for the operator's password on that terminal (issue #543). The job's
    control group holds everything the shell starts, including processes that leave its session; stopping the job
    sends each of them SIGHUP, then SIGKILL after `grace` seconds. ``--wait`` keeps the launcher running until the
    shell has ended and returns its exit status. ``PartOf`` stops the job with Altitude's service. The shell starts
    from the user manager's environment, as a desktop session's would, plus `env`, whose values appear on the
    launcher's command line: only settings, never credentials."""
    return [SYSTEMD_RUN, "--user", "--wait", "--collect", "--quiet", f"--unit={name}", "--same-dir",
            "--expand-environment=no", "--property=NoNewPrivileges=no", f"--property=TTYPath={tty}",
            "--property=StandardInput=tty", "--property=StandardOutput=tty", "--property=StandardError=tty",
            "--property=KillMode=control-group", "--property=KillSignal=SIGHUP", "--property=SendSIGKILL=yes",
            f"--property=TimeoutStopSec={grace}", f"--property=PartOf={SERVICE}",
            *(f"--setenv={key}={value}" for key, value in sorted(env.items())), "--", *command]


def terminal_session(fd: int) -> int:
    """The session on the pseudo-terminal whose controlling side is `fd`: the shell's process id."""
    return struct.unpack("i", fcntl.ioctl(fd, TIOCGSID, b"\0" * 4))[0]


#: Linux's request for a terminal's session, which Python's termios module does not name.
TIOCGSID = 0x5429


def detached_job_command(name: str, command: list[str], *, path: str) -> list[str]:
    """Start a command as a job outside the caller's own, so it survives the caller's restart."""
    return [SYSTEMD_RUN, "--user", "--collect", "--quiet", f"--unit={name}", "--same-dir",
            f"--setenv=PATH={path}", "--", *command]


def job_logs_hint(name: str) -> str:
    return f"journalctl --user -u {name}"


def job_active(name: str, env: dict) -> bool:
    """Whether the job still runs; an unknown state raises rather than reading as stopped."""
    if not name:
        raise RuntimeError("Worker unit identity is unavailable")
    p = subprocess.run([SYSTEMCTL, "--user", "is-active", name], capture_output=True, text=True, timeout=30, env=env)
    state = (p.stdout or "").strip()
    if p.returncode == 4 and state == "inactive":  # A collected transient unit is no longer running.
        return False
    if p.returncode in (0, 3):
        if state in ("active", "activating", "deactivating", "reloading", "refreshing", "maintenance"):
            return True
        if state in ("inactive", "failed"):
            return False
    raise RuntimeError("Worker unit status is unavailable")


def job_stop(name: str, env: dict | None = None, *, timeout: int = 120) -> None:
    """Stop the job; `KillMode=control-group` takes every descendant with it. Callers confirm with `job_active`."""
    subprocess.run([SYSTEMCTL, "--user", "stop", name], capture_output=True, text=True, timeout=timeout, env=env)


# --- Service evidence --------------------------------------------------------------------------------------------

def service_status(unit: str, env: dict) -> dict:
    """Read a user service's state once; inspection failure stays in the record."""
    record = {"unit": unit, "state": None, "substate": None, "pid": None,
              "last_restart": None, "error": None}
    evidence = {
        "load_state": ("LoadState", r"loaded|error|not-found|bad-setting|masked|merged|stub"),
        "invocation_id": ("InvocationID", r"[0-9a-f]{32}"),
        "started_monotonic": ("ExecMainStartTimestampMonotonic", r"[1-9][0-9]*"),
        "exited_monotonic": ("ExecMainExitTimestampMonotonic", r"[1-9][0-9]{0,19}"),
        "result": ("Result", r"success|resources|protocol|timeout|exit-code|signal|core-dump|watchdog|"
                              r"start-limit-hit|oom-kill|exec-condition|skip-condition"),
        "exec_main_code": ("ExecMainCode", r"[123]"),
        "exec_main_status": ("ExecMainStatus", r"[0-9]{1,3}"),
        "memory_current": ("MemoryCurrent", r"[0-9]{1,20}"),
        "memory_peak": ("MemoryPeak", r"[0-9]{1,20}"),
        "memory_high": ("MemoryHigh", r"[0-9]{1,20}|infinity"),
        "memory_max": ("MemoryMax", r"[0-9]{1,20}|infinity"),
    }
    record.update(dict.fromkeys(evidence))
    properties = ["ActiveState", "SubState", "MainPID", "ActiveEnterTimestamp",
                  *(native for native, _ in evidence.values())]
    source_service = unit in {"altitude", "altitude.service"}
    if source_service:
        properties += ["Environment",
                       "EnvironmentFiles", "PassEnvironment", "UnsetEnvironment", "DropInPaths", "NeedDaemonReload"]
        record.update(dict.fromkeys(("need_daemon_reload",
                                     "owned_tls_drop_in_loaded", "owned_tls_drop_in_present",
                                     "loaded_tls_environment", "indirect_environment")))
    try:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "show", unit, *[f"--property={key}" for key in properties]],
            capture_output=True, text=True, timeout=15, env=env)
        if result.returncode:
            raise RuntimeError
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        record.update({"state": values.get("ActiveState"), "substate": values.get("SubState"),
                       "pid": int(values.get("MainPID") or 0) or None,
                       "last_restart": values.get("ActiveEnterTimestamp") or None})
        for key, (native, pattern) in evidence.items():
            value = values.get(native, "")
            if (key == "load_state" or values.get("LoadState") == "loaded") and re.fullmatch(pattern, value):
                record[key] = value
        if not record["exec_main_code"]:
            record["exec_main_status"] = None  # Native defaults are not an observed clean exit (#384).
        if record["load_state"] != "loaded":
            record["error"] = "Unit not loaded or load state unavailable; termination/resource evidence is unknown."
        if source_service:
            record["need_daemon_reload"] = {"yes": True, "no": False}.get(values.get("NeedDaemonReload"))
            owned = Path.home() / ".config/systemd/user/altitude.service.d/90-altitude-source-tls.conf"
            try:
                owned.lstat()
                record["owned_tls_drop_in_present"] = True
            except FileNotFoundError:
                record["owned_tls_drop_in_present"] = False
            if values.get("LoadState") != "loaded":
                raise ValueError
            # Escaped native strings are unknown rather than interpreted with shell escape semantics.
            drop_ins = values["DropInPaths"]
            if "\\" in drop_ins:
                raise ValueError
            record["owned_tls_drop_in_loaded"] = str(owned) in shlex.split(drop_ins)
            # Native show emits no EnvironmentFiles line for an empty array, even with --all.
            record["indirect_environment"] = any([values.get("EnvironmentFiles", ""),
                                                  values["PassEnvironment"], values["UnsetEnvironment"]])
            environment = values["Environment"]
            if "\\" in environment:
                raise ValueError
            selected = {}
            for entry in shlex.split(environment):
                key, separator, value = entry.partition("=")
                if not separator or any(ord(c) < 32 or ord(c) == 127 for c in entry):
                    raise ValueError
                if key in {"ALTITUDE_TLS", "ALTITUDE_TLS_DIR"}:
                    if key in selected or len(value) > 4096:
                        raise ValueError
                    selected[key] = value
            record["loaded_tls_environment"] = selected
            if record["need_daemon_reload"] is None:
                raise ValueError
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError):
        record["error"] = "Service inspection incomplete; unavailable fields remain null."
    return record


# --- Processes ---------------------------------------------------------------------------------------------------

#: Where process and socket facts are read; tests point this at fixture trees.
PROC = Path("/proc")


def _stat(pid: int) -> list[str]:
    return (PROC / str(int(pid)) / "stat").read_text().rsplit(")", 1)[1].split()


def process_start(pid: int) -> str:
    """The process's start time in clock ticks, its identity against PID reuse. A missing process raises
    FileNotFoundError."""
    return _stat(pid)[19]


def process_running(pid: int, start: str) -> bool | None:
    """Whether the process with this identity still runs (a zombie has ended); None when `start` is not an
    identity. A missing process raises FileNotFoundError."""
    fields = _stat(pid)
    if not start.isdigit():
        return None
    return fields[19] == start and fields[0] != "Z"


def process_name(pid: int) -> str | None:
    """The process's short command name, or None when it cannot be read."""
    try:
        return (PROC / str(int(pid)) / "comm").read_text().strip() or None
    except OSError:
        return None


def _hex_address(address: str, port: int) -> tuple[str, str]:
    """An address as /proc/net/tcp{,6} spells it: host-order 32-bit words in hex, then the port."""
    ip = ipaddress.ip_address(address)
    words = struct.unpack(f"{len(ip.packed) // 4}I", ip.packed)
    return "".join(f"{word:08X}" for word in words) + f":{port:04X}", "6" if ip.version == 6 else ""


def client_socket(clients: list[str], client_port: int, servers: list[str], server_port: int) -> str | None:
    """The identity of the client end of a TCP connection on this host, given every spelling of each address,
    or None when that end is not in this host's network namespace."""
    for client in clients:
        want, family = _hex_address(client, client_port)
        for server in servers:
            ours, server_family = _hex_address(server, server_port)
            if server_family != family:
                continue
            for line in (PROC / "net" / f"tcp{family}").read_text().splitlines()[1:]:
                cols = line.split()
                if len(cols) > 9 and cols[1] == want and cols[2] == ours:
                    return f"socket:[{int(cols[9])}]"
    return None


def process_table() -> dict[int, tuple[int, list[str]]]:
    """pid -> (parent pid, the process's control-group path components) for every readable process. A process
    that exits while being read is left out: it cannot vouch for anything."""
    table = {}
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            groups = (entry / "cgroup").read_text()
        except OSError:
            continue
        table[int(entry.name)] = (int(stat.rsplit(")", 1)[1].split()[1]),
                                  [part for line in groups.splitlines() for part in line.split("/")])
    return table


def holds(pid: int, handle: str) -> bool:
    """Whether the process visibly holds the socket. Unreadable descriptors (another user's process, or
    one made undumpable) prove nothing either way."""
    try:
        for fd in (PROC / str(pid) / "fd").iterdir():
            try:
                if os.readlink(fd) == handle:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


# --- Host speech -------------------------------------------------------------------------------------------------

def speech_runtime() -> tuple[str | None, str]:
    """The pinned speech runtime this host and interpreter can run (`linux-x86_64-cp312`), or None and why not.
    The runtime's wheels need glibc 2.28. macOS is not verified (issue #225)."""
    if sys.platform != "linux" or host_platform.machine() not in ("x86_64", "AMD64"):
        return None, "voice runs on Linux x86_64 only for now"
    libc = (os.confstr("CS_GNU_LIBC_VERSION") or "") if hasattr(os, "confstr") else ""
    match = re.fullmatch(r"glibc (\d+)\.(\d+).*", libc)
    if not match or (int(match[1]), int(match[2])) < (2, 28):
        return None, "voice needs glibc 2.28 or newer"
    return f"linux-x86_64-cp{sys.version_info.major}{sys.version_info.minor}", ""


def available_memory() -> int | None:
    """Bytes of memory available without swapping, or None when the host does not say."""
    try:
        for line in (PROC / "meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None
