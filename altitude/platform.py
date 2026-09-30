"""The platform seam: the host's service manager and process facilities.

Two hosts are implemented. Linux x86_64 uses a systemd user manager, procfs and pidfd. macOS 15 or newer on Apple
silicon uses a per-user LaunchAgent, one launchd job per worker job (each its own kernel coalition), libproc, sysctl
and Seatbelt. This module is the only place that names them; engines, the terminal, images, installation and the
server ask it for the service, jobs, processes and connections. Each operation reads `sys.platform` when called, so
tests exercise either implementation against fixtures on any host.
"""
from __future__ import annotations

import ctypes
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import platform as host_platform
import plistlib
import re
import select
import shlex
import shutil
import signal as signals
import stat
import struct
import subprocess
import sys
import termios
import time


SERVICE = "altitude.service"
#: The LaunchAgent that runs the service on macOS.
LABEL = "dev.altitude.altd"
#: What First run shows for a missing command-line tool, run in the operator's own terminal.
INSTALL = ({"gh": "brew install gh", "git": "xcode-select --install"} if sys.platform == "darwin"
           else {"gh": "sudo apt install gh", "git": "sudo apt install git"})


def _darwin() -> bool:
    return sys.platform == "darwin"


def require_supported() -> None:
    machine = host_platform.machine()
    if sys.platform == "linux" and machine in ("x86_64", "AMD64"):
        return
    if _darwin() and machine == "arm64" and int(host_platform.mac_ver()[0].split(".")[0] or 0) >= 15:
        return
    raise RuntimeError("Altitude runs on Linux x86_64 with systemd, or on macOS 15 or newer on Apple silicon.")


def source_service() -> bool:
    """Whether a source-checkout service can prepare its TLS drop-in: systemd only. Source self-restart and
    installed releases' updates work the same way on both hosts."""
    return not _darwin()


def run(*args: str) -> str:
    try:
        result = subprocess.run(list(args), capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Native user service unavailable: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"Native user service failed: {(result.stderr or result.stdout).strip()[:500]}")
    return result.stdout


def service_path() -> Path:
    if _darwin():
        return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    return Path.home() / ".config/systemd/user" / SERVICE


def status() -> dict[str, str]:
    require_supported()
    if _darwin():
        return _launchd_status()
    result = run("systemctl", "--user", "show", SERVICE,
                 "--property=LoadState,ActiveState,SubState,FragmentPath,MainPID,UnitFileState")
    values = dict(line.split("=", 1) for line in result.splitlines() if "=" in line)
    if values.get("LoadState") not in ("loaded", "not-found"):
        raise RuntimeError("Cannot determine the Altitude user service; inspect systemctl --user status altitude.")
    if values["LoadState"] == "loaded" and values.get("ActiveState") not in (
            "active", "inactive", "failed", "activating", "deactivating", "reloading"):
        raise RuntimeError("Cannot determine whether the Altitude user service is running")
    return values


def service_unit() -> dict[str, str]:
    """The service's definition and state as a restart checks them, in systemd's names: WorkingDirectory,
    Environment (quoted KEY=value words), MainPID, ActiveState and SubState."""
    if _darwin():
        try:
            agent = plistlib.loads(service_path().read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            agent = {}
        environment = agent.get("EnvironmentVariables") or {}
        return {**_launchd_status(), "WorkingDirectory": agent.get("WorkingDirectory", ""),
                "Environment": " ".join(shlex.quote(f"{key}={value}") for key, value in environment.items())}
    result = run("systemctl", "--user", "show", SERVICE, "--property=WorkingDirectory", "--property=Environment",
                 "--property=MainPID", "--property=ActiveState", "--property=SubState")
    return dict(line.split("=", 1) for line in result.splitlines() if "=" in line)


def control(action: str) -> str:
    require_supported()
    if _darwin():
        return _launchd_control(action)
    if action == "reload":
        return run("systemctl", "--user", "daemon-reload")
    if action not in ("start", "stop", "restart", "enable", "disable"):
        raise ValueError("Unknown application service operation")
    return run("systemctl", "--user", action, SERVICE)


def detach(name: str, argv: list[str], environment: dict[str, str]) -> str:
    """Run one command as its own short-lived user unit, so it outlives a restart of the Altitude service."""
    require_supported()
    if _darwin():
        if _launch(_detached_spec(name, argv, {**_login_env(), **environment})):
            raise RuntimeError(f"Native user service failed: launchd did not start {name}")
        return ""
    return run("systemd-run", "--user", "--collect", "--quiet", "--expand-environment=no", f"--unit={name}",
               *(f"--setenv={key}={value}" for key, value in environment.items()), *argv)


def logs() -> str:
    if _darwin():
        try:
            return "".join((logs_dir() / "altd.log").read_text(errors="replace").splitlines(True)[-100:])
        except FileNotFoundError:
            return ""
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
    if _darwin():
        # launchd restarts the service when it fails (KeepAlive), not after a clean exit, as Restart=on-failure does.
        log = str(logs_dir() / "altd.log")
        return plistlib.dumps({
            "Label": LABEL, "ProgramArguments": [str(python), "-B", str(prefix / "current/bin/alt"), "serve"],
            "WorkingDirectory": str(prefix),
            "EnvironmentVariables": {"ALTITUDE_CONFIG": str(settings), **environment,
                                     "ALTITUDE_SERVICE": "1", "ALTITUDE_TLS": "1"},
            "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 5, "Umask": 0o077,
            "ProcessType": "Standard", "StandardOutPath": log, "StandardErrorPath": log}).decode()
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
    inherit the interactive session's bus variables, so their canonical per-user values are synthesized. macOS has
    no such bus; the user's own temporary directory stays."""
    if _darwin():
        return job_env(env)
    env["TMPDIR"] = "/tmp"
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env["DBUS_SESSION_BUS_ADDRESS"] = env.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime_dir}/bus"
    return env


def job_env(env: dict) -> dict:
    """The environment inside a job: no user-manager bus, and the shared temporary directory (the user's own on
    macOS)."""
    env["TMPDIR"] = _user_temp() if _darwin() else "/tmp"
    env.pop("XDG_RUNTIME_DIR", None)
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    return env


def _scrub(env: dict[str, str]) -> list[str]:
    # A transient service inherits the user manager's environment, not the launching client's. Clear it completely
    # and reconstruct only the already-sanitized child environment so task identity survives without ambient manager
    # credentials or control sockets crossing the boundary.
    return [ENV_BIN, "-i", *(f"{key}={env[key]}" for key in sorted(env))]


def job_command(name: str, command: list[str], env: dict[str, str], *, runtime_max: int | None = None,
                writable: tuple[Path, ...] | None = None) -> list[str]:
    """Run a command synchronously, piped to the caller, in a job of its own that holds every descendant.

    `writable` confines the job with Altitude's Seatbelt profile on macOS: it may signal only its own processes and
    write only under these roots and the user's temporary directories. Linux leaves file confinement to the engine.

    ``--wait --pipe`` keeps the launch synchronous while the user manager, rather than the hardened Altitude parent,
    creates the child. This lets nested bwrap initialize without weakening altd's ``NoNewPrivileges=yes`` boundary.
    Unlike a process group, the service cgroup retains descendants that call ``setsid`` or double-fork. An engine's inner
    sandbox supplies the PID namespace; keeping syscall filters off the outer service preserves nested bwrap.
    """
    if _darwin():
        return _entry("launch", json.dumps({
            "label": _label(name), "mode": "pipe", "command": command, "env": env, "runtime_max": runtime_max,
            "writable": None if writable is None else [str(root) for root in writable]}))
    return [SYSTEMD_RUN, "--user", "--wait", "--pipe", f"--unit={name}", "--quiet", "--collect",
            "--same-dir", "--expand-environment=no", "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes", "--property=NoNewPrivileges=no",
            *([f"--property=RuntimeMaxSec={runtime_max}", "--property=TimeoutStopSec=5"] if runtime_max else []),
            "--", *_scrub(env), *command]


def logged_job_command(name: str, command: str, *, log: Path, status: Path, env: dict[str, str],
                       timeout: int, properties: tuple[str, ...] = ()) -> list[str]:
    """One shell command as a job that appends its own output to `log` and writes its exit status to `status`
    (whole, by renaming), so a command that restarts Altitude still leaves a durable record. ``RuntimeMaxSec`` bounds
    it; `properties` adds resource limits for everything the job starts."""
    runner = 'bash -lc "$1"; status=$?; printf %s "$status" > "$2.tmp" && mv "$2.tmp" "$2"; exit "$status"'
    if _darwin():
        return _entry("launch", json.dumps({
            "label": _label(name), "mode": "logged", "env": env, "runtime_max": timeout, "log": str(log),
            "command": ["/bin/bash", "-c", runner, "altitude-machine", command, str(status)]}))
    return [SYSTEMD_RUN, "--user", "--wait", "--collect", "--quiet", f"--unit={name}", "--same-dir",
            "--expand-environment=no", "--property=KillMode=control-group", "--property=SendSIGKILL=yes",
            f"--property=RuntimeMaxSec={timeout}", "--property=TimeoutStopSec=5",
            f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}", *properties,
            "--", *_scrub(env), "/bin/bash", "-c", runner, "altitude-machine", command, str(status)]


def terminal_job(name: str, tty: str, command: list[str], env: dict[str, str], *, grace: int) -> list[str]:
    """A login shell as a job of its own on the pseudo-terminal `tty`, started with the caller's working directory.

    The user manager, not the hardened Altitude parent, creates the shell, so it does not inherit altd's
    ``NoNewPrivileges=yes`` and ``sudo`` can ask for the operator's password on that terminal (issue #543). The job's
    control group holds everything the shell starts, including processes that leave its session; stopping the job
    sends each of them SIGHUP, then SIGKILL after `grace` seconds. ``--wait`` keeps the launcher running until the
    shell has ended and returns its exit status. ``PartOf`` stops the job with Altitude's service. The shell starts
    from the user manager's environment, as a desktop session's would, plus `env`, whose values appear on the
    launcher's command line: only settings, never credentials.

    On macOS the shell is its own launchd job on `tty`, whose supervisor hangs up its coalition on stop and stops
    it once the launcher (altd's child) has gone."""
    if _darwin():
        return _entry("launch", json.dumps({"label": _label(name), "mode": "terminal", "tty": tty, "command": command,
                                            "env": {**_login_env(), **env}, "grace": grace}))
    return [SYSTEMD_RUN, "--user", "--wait", "--collect", "--quiet", f"--unit={name}", "--same-dir",
            "--expand-environment=no", "--property=NoNewPrivileges=no", f"--property=TTYPath={tty}",
            "--property=StandardInput=tty", "--property=StandardOutput=tty", "--property=StandardError=tty",
            "--property=KillMode=control-group", "--property=KillSignal=SIGHUP", "--property=SendSIGKILL=yes",
            f"--property=TimeoutStopSec={grace}", f"--property=PartOf={SERVICE}",
            *(f"--setenv={key}={value}" for key, value in sorted(env.items())), "--", *command]


def terminal_session(fd: int) -> int:
    """The session on the pseudo-terminal whose controlling side is `fd`: the shell's process id."""
    if _darwin():
        session = ctypes.CDLL(None, use_errno=True).tcgetsid(fd)
        if session < 0:
            raise OSError(ctypes.get_errno(), "The terminal has no session")
        return session
    return struct.unpack("i", fcntl.ioctl(fd, TIOCGSID, b"\0" * 4))[0]


#: Linux's request for a terminal's session, which Python's termios module does not name.
TIOCGSID = 0x5429


def detached_job_command(name: str, command: list[str], *, path: str) -> list[str]:
    """Start a command as a job outside the caller's own, so it survives the caller's restart."""
    if _darwin():
        return _entry("launch", json.dumps(_detached_spec(name, command, {**_login_env(), "PATH": path})))
    return [SYSTEMD_RUN, "--user", "--collect", "--quiet", f"--unit={name}", "--same-dir",
            f"--setenv=PATH={path}", "--", *command]


def job_logs_hint(name: str) -> str:
    if _darwin():
        return str(logs_dir() / ("altd.log" if name in ("altitude", SERVICE) else f"{name}.log"))
    return f"journalctl --user -u {name}"


def job_active(name: str, env: dict) -> bool:
    """Whether the job still runs; an unknown state raises rather than reading as stopped."""
    if not name:
        raise RuntimeError("Worker unit identity is unavailable")
    if _darwin():
        return _launchd_job_active(name)
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
    """Stop the job; `KillMode=control-group` takes every descendant with it (on macOS, every member of the job's
    coalition). Callers confirm with `job_active`."""
    if _darwin():
        return _launchd_job_stop(name, timeout)
    subprocess.run([SYSTEMCTL, "--user", "stop", name], capture_output=True, text=True, timeout=timeout, env=env)


# --- Service evidence --------------------------------------------------------------------------------------------

def service_status(unit: str, env: dict) -> dict:
    """Read a user service's state once; inspection failure stays in the record."""
    if _darwin():
        return _launchd_service_status(unit)
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
    """The process's start time (clock ticks since boot on Linux, microseconds since the epoch on macOS), its
    identity against PID reuse. A missing process raises FileNotFoundError."""
    if _darwin():
        return _started(_bsd(pid))
    return _stat(pid)[19]


def process_running(pid: int, start: str) -> bool | None:
    """Whether the process with this identity still runs (a zombie has ended); None when `start` is not an
    identity. A missing process raises FileNotFoundError."""
    if _darwin():
        info = _bsd(pid)
        return (_started(info) == start and info.status != SZOMB) if start.isdigit() else None
    fields = _stat(pid)
    if not start.isdigit():
        return None
    return fields[19] == start and fields[0] != "Z"


def process_name(pid: int) -> str | None:
    """The process's short command name, or None when it cannot be read."""
    if _darwin():
        try:
            info = _bsd(pid)
        except OSError:
            return None
        return (info.name or info.comm).decode(errors="replace") or None
    try:
        return (PROC / str(int(pid)) / "comm").read_text().strip() or None
    except OSError:
        return None


def _controlling_terminal() -> None:  # runs in the child between fork and exec
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _hex_address(address: str, port: int) -> tuple[str, str]:
    """An address as /proc/net/tcp{,6} spells it: host-order 32-bit words in hex, then the port."""
    ip = ipaddress.ip_address(address)
    words = struct.unpack(f"{len(ip.packed) // 4}I", ip.packed)
    return "".join(f"{word:08X}" for word in words) + f":{port:04X}", "6" if ip.version == 6 else ""


def client_socket(clients: list[str], client_port: int, servers: list[str], server_port: int) -> str | None:
    """The identity of the client end of a TCP connection on this host, given every spelling of each address,
    or None when that end is not in this host's network namespace (on macOS, held by none of this user's
    processes)."""
    if _darwin():
        wanted = {_tcp_handle(ipaddress.ip_address(client), client_port, ipaddress.ip_address(server), server_port)
                  for client in clients for server in servers}
        for pid in _pids():
            try:
                found = _tcp_handles(pid) & wanted
            except OSError:
                continue
            if found:
                return found.pop()
        return None
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
    that exits while being read is left out: it cannot vouch for anything. On macOS the components are the unit
    name of the Altitude job (or service) whose coalition holds the process, spelled as on Linux."""
    if _darwin():
        units = _altitude_coalitions()
        table = {}
        for pid in _pids():
            try:
                coalition = _coalition_of(pid)
                table[pid] = (_bsd(pid).ppid, [units[coalition]] if coalition in units else [])
            except OSError:
                continue
        return table
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
    if _darwin():
        try:
            return handle in _tcp_handles(pid)
        except OSError:
            return False
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


# --- Resource limits and native libraries ------------------------------------------------------------------------

# Limits are set in a fresh helper process: preexec_fn is unsafe in the threaded HTTP server.
_LIMITED_EXEC = """import os, resource, sys
memory, cpu, output = map(int, sys.argv[1:4])
resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
resource.setrlimit(resource.RLIMIT_FSIZE, (output, output))
os.execv(sys.argv[4], sys.argv[4:])
"""
# macOS rejects RLIMIT_AS. A watcher forked before exec kills the command once its physical footprint passes the
# limit; it is the command's child, so a kill of the command (a timeout) leaves nothing behind.
_LIMITED_EXEC_DARWIN = """import ctypes, os, resource, signal, sys, time
memory, cpu, output = map(int, sys.argv[1:4])
resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
resource.setrlimit(resource.RLIMIT_FSIZE, (output, output))
target = os.getpid()
if os.fork() == 0:
    usage = ctypes.create_string_buffer(512)
    rusage = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    while os.getppid() == target:
        if rusage(target, 0, usage) == 0 and int.from_bytes(usage.raw[72:80], sys.byteorder) > memory:
            os.kill(target, signal.SIGKILL)
            break
        time.sleep(0.01)
    os._exit(0)
os.execv(sys.argv[4], sys.argv[4:])
"""


def limited_command(command: list[str], *, memory: int, cpu: int, output: int) -> list[str]:
    """`command` held to `memory` bytes, `cpu` seconds of processor time and files of at most `output` bytes."""
    helper = _LIMITED_EXEC_DARWIN if _darwin() else _LIMITED_EXEC
    return [sys.executable, "-c", helper, str(memory), str(cpu), str(output), *command]


#: Where Homebrew installs libraries on Apple silicon and on Intel Macs; the loader does not search either.
HOMEBREW = (Path("/opt/homebrew"), Path("/usr/local"))


def find_library(name: str) -> str | None:
    import ctypes.util
    found = ctypes.util.find_library(name)
    if found or not _darwin():
        return found
    return next((str(path) for prefix in HOMEBREW if (path := prefix / "lib" / f"lib{name}.dylib").exists()), None)


# --- macOS: the LaunchAgent -----------------------------------------------------------------------------------------

LAUNCHCTL = shutil.which("launchctl") or "/bin/launchctl"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
CAFFEINATE = "/usr/bin/caffeinate"
#: launchctl's status when the domain has no such job.
NOT_FOUND = 113


def logs_dir() -> Path:
    return Path.home() / "Library/Logs/altitude"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _print(label: str) -> dict | None:
    """launchd's view of a job in the user's domain: its top-level fields and its resource coalition, or None when
    the domain has no such job. Anything else unreadable raises RuntimeError."""
    try:
        p = subprocess.run([LAUNCHCTL, "print", f"{_domain()}/{label}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Native user service unavailable: {exc}") from exc
    if p.returncode == NOT_FOUND:
        return None
    if p.returncode:
        raise RuntimeError(f"Native user service failed: {(p.stderr or p.stdout).strip()[:500]}")
    fields = {}
    for line in p.stdout.splitlines():
        if line.startswith("\t") and not line.startswith("\t\t") and " = " in line:
            key, value = line.strip().split(" = ", 1)
            fields.setdefault(key, value)
    coalition = re.search(r"resource coalition = \{\s*ID = (\d+)", p.stdout)
    fields["coalition"] = int(coalition.group(1)) if coalition else None
    return fields


def _launchd_status() -> dict[str, str]:
    """The service in the vocabulary the installation reads: loaded (defined or bootstrapped), active (running),
    its definition's path and main PID, and whether it is enabled."""
    path = service_path()
    job = _print(LABEL)
    disabled = re.findall(r'"([^"]+)" => (?:disabled|true)', run(LAUNCHCTL, "print-disabled", _domain()))
    running = bool(job and job.get("state") == "running" and job.get("pid"))
    exited = (job or {}).get("last exit code", "")
    fragment = (job or {}).get("path") or (str(path) if path.exists() else "")
    if fragment and Path(fragment).resolve() == path.resolve():
        fragment = str(path)
    return {"LoadState": "loaded" if job or path.exists() else "not-found",
            "ActiveState": "active" if running else "failed" if job and exited not in ("", "0", "(never exited)")
            else "inactive",
            "SubState": job.get("state", "") if job else "", "FragmentPath": fragment,
            "MainPID": job["pid"] if running else "0",
            "UnitFileState": "disabled" if LABEL in disabled else "enabled" if path.exists() else ""}


def _launchd_control(action: str) -> str:
    """start, stop and restart the service. launchd reads the definition when the service is bootstrapped, so
    reload has nothing to do and restart bootstraps it again."""
    target = f"{_domain()}/{LABEL}"
    if action == "reload":
        return ""
    if action in ("enable", "disable"):
        return run(LAUNCHCTL, action, target)
    if action not in ("start", "stop", "restart"):
        raise ValueError("Unknown application service operation")
    job = _print(LABEL)
    if job and action == "start":
        return run(LAUNCHCTL, "kickstart", target)
    if job:
        # bootout ends the main process group; like KillMode=control-group, nothing else the service started stays.
        run(LAUNCHCTL, "bootout", target)
        if job["coalition"] and not _confirm_stopped(job["coalition"]):
            raise RuntimeError("Native user service failed: processes the service started are still running")
    if action == "stop":
        return ""
    logs_dir().mkdir(parents=True, exist_ok=True)
    return run(LAUNCHCTL, "bootstrap", _domain(), str(service_path()))


def _launchd_service_status(unit: str) -> dict:
    """The service's or a job's state and memory evidence, with the fields launchd has no equivalent for null."""
    record = {"unit": unit, "state": None, "substate": None, "pid": None, "last_restart": None, "error": None,
              **dict.fromkeys(("load_state", "invocation_id", "started_monotonic", "exited_monotonic", "result",
                               "exec_main_code", "exec_main_status", "memory_current", "memory_peak", "memory_high",
                               "memory_max"))}
    try:
        job = _print(LABEL if unit in ("altitude", SERVICE) else _label(unit))
        if job is None:
            record.update(state="inactive", load_state="not-found",
                          error="Unit not loaded or load state unavailable; termination/resource evidence is unknown.")
            return record
        running = job.get("state") == "running" and job.get("pid")
        record.update(state="active" if running else "inactive", substate=job.get("state"),
                      pid=int(job["pid"]) if running else None, load_state="loaded")
        if job.get("last exit code", "").isdigit():
            record.update(exec_main_code="1", exec_main_status=job["last exit code"])
        if job["coalition"]:
            record["memory_current"] = str(sum(_footprint(pid) for pid, _ in _members(job["coalition"])))
    except (OSError, RuntimeError, ValueError):
        record["error"] = "Service inspection incomplete; unavailable fields remain null."
    return record


# --- macOS: jobs --------------------------------------------------------------------------------------------------
#
# A job is its own launchd job, so its processes share a kernel coalition that no setsid, double fork or cleared
# environment leaves. Its program is a supervisor, outside any sandbox, that runs the command (under Altitude's
# Seatbelt profile when the caller confines it), enforces the time limit, and when the command exits stops whatever is
# left in the coalition, records the status and removes its own launchd job. The caller runs a launcher that hands the
# supervisor its input and output: a regular file or device by path, as systemd-run --pipe passes the descriptor, and a
# pipe through a FIFO the launcher relays.

_BOOT = "import sys; sys.path.insert(0, sys.argv[1]); from altitude import platform; platform.job_main(sys.argv[2:])"


def _entry(*args: str) -> list[str]:
    return [sys.executable, "-I", "-B", "-c", _BOOT, str(Path(__file__).resolve().parent.parent), *args]


def _label(name: str) -> str:
    return "dev.altitude.job." + name.removesuffix(".service")


def _jobs() -> Path:
    return Path.home() / "Library/Caches/dev.altitude/jobs"


def _user_temp() -> str:
    """The user's private temporary directory, whatever TMPDIR says (launchd jobs get none)."""
    try:
        return os.confstr(65537) or "/tmp"  # _CS_DARWIN_USER_TEMP_DIR
    except (OSError, ValueError):  # not a Mac
        return "/tmp"


def _login_env() -> dict[str, str]:
    """What a login session gives a command; a launchd job starts with little more than PATH."""
    return {**{key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "SHELL", "LANG") if key in os.environ},
            "TMPDIR": _user_temp()}


def _detached_spec(name: str, command: list[str], env: dict[str, str]) -> dict:
    return {"label": _label(name), "mode": "detached", "command": command, "env": env,
            "log": str(logs_dir() / f"{name}.log")}


def _write_private(path: Path, data: str | bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data.encode() if isinstance(data, str) else data)


def _fd_path(fd: int) -> str | None:
    """The path of a regular file or device this process holds, which the job opens itself; None for a pipe or
    socket; the null device when the descriptor is closed."""
    try:
        mode = os.fstat(fd).st_mode
    except OSError:
        return os.devnull
    if not (stat.S_ISREG(mode) or stat.S_ISCHR(mode)):
        return None
    return fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), bytes(1024)).split(b"\0", 1)[0].decode()


def seatbelt_profile(writable: list[str]) -> str:
    """Altitude's Seatbelt profile. The job may signal only processes in its own sandbox, never its supervisor, and
    write only under `writable` (a root ending in * admits every path that starts with it), the user's temporary and
    cache directories, /private/tmp and devices. Seatbelt matches resolved paths. launchd refuses service control to
    every sandboxed process."""
    user = str(Path(os.path.realpath(_user_temp())).parent)
    rules = []
    for root in dict.fromkeys((*writable, user, "/private/tmp", "/private/var/tmp", "/dev")):
        if root.endswith("*"):
            prefix = os.path.join(os.path.realpath(os.path.dirname(root)), os.path.basename(root)[:-1])
            rules.append('(regex #"^' + re.escape(prefix).replace('"', '\\"') + '")')
        else:
            rules.append('(subpath "' + os.path.realpath(root).replace("\\", "\\\\").replace('"', '\\"') + '")')
    return ("(version 1)(allow default)(deny signal)(allow signal (target same-sandbox))"
            f"(deny file-write*)(allow file-write* {' '.join(rules)})")


def confined(command: list[str], writable: tuple[Path, ...]) -> list[str]:
    """`command` under Altitude's Seatbelt profile on macOS, for a process that runs as the caller's own child
    rather than as a job; unchanged on Linux. sandbox-exec replaces itself with the command, so its PID is the
    command's."""
    if not _darwin():
        return command
    return [SANDBOX_EXEC, "-p", seatbelt_profile([str(root) for root in writable]), *command]


def job_main(argv: list[str]) -> None:
    """The launcher's and the supervisor's entry point (see `_entry`)."""
    role, argument = argv
    sys.exit(_launch(json.loads(argument)) if role == "launch" else _supervise(Path(argument)))


def _launch(spec: dict) -> int:
    """The caller's end of a job: bootstrap its supervisor and, unless detached, relay output and exit with the
    command's status."""
    label, mode = spec["label"], spec["mode"]
    job = _jobs() / label
    job.parent.mkdir(parents=True, exist_ok=True)
    try:
        job.mkdir(mode=0o700)
    except FileExistsError:
        if not _collect(label):
            print(f"Job {label} already exists.", file=sys.stderr)
            return 1
        job.mkdir(mode=0o700)
    relays: dict[int, int] = {}
    try:
        spec.update(cwd=os.getcwd(), launcher=None if mode == "detached" else [os.getpid(), _started(_bsd(os.getpid()))])
        if mode == "terminal":
            spec["stdin"] = spec["tty"]
            supervisor_log = job / "supervisor.log"
        elif mode == "pipe":
            spec["stdin"] = _fd_path(0)
            if spec["stdin"] is None:  # a pipe: callers write a whole prompt, then close it
                _write_private(job / "stdin", sys.stdin.buffer.read())
                spec["stdin"] = str(job / "stdin")
            for fd in (1, 2):
                path = _fd_path(fd)
                if path is None:
                    os.mkfifo(job / str(fd), 0o600)
                    relays[os.open(job / str(fd), os.O_RDONLY | os.O_NONBLOCK)] = fd
                    spec[str(fd)] = {"fifo": str(job / str(fd))}
                else:
                    spec[str(fd)] = {"file": path}
            supervisor_log = job / "supervisor.log"
        else:
            Path(spec["log"]).parent.mkdir(parents=True, exist_ok=True)
            spec.update(stdin=os.devnull, **{"1": {"file": spec["log"]}, "2": {"file": spec["log"]}})
            supervisor_log = Path(spec["log"])
        _write_private(job / "spec.json", json.dumps(spec))
        _write_private(job / "job.plist", plistlib.dumps({
            "Label": label, "ProgramArguments": _entry("supervise", str(job)), "WorkingDirectory": spec["cwd"],
            "RunAtLoad": True, "AbandonProcessGroup": True, "ProcessType": "Standard",
            "StandardOutPath": str(supervisor_log), "StandardErrorPath": str(supervisor_log)}))
        started = subprocess.run([LAUNCHCTL, "bootstrap", _domain(), str(job / "job.plist")],
                                 capture_output=True, text=True, timeout=60)
        if started.returncode:
            print(f"launchd did not start {label}: {(started.stderr or started.stdout).strip()[:300]}", file=sys.stderr)
            shutil.rmtree(job, ignore_errors=True)
            return 1
        if mode == "detached":
            return 0
        if not _await(job, "started", label):
            print(f"Job {label} ended before its command started.", file=sys.stderr)
            subprocess.run([LAUNCHCTL, "bootout", f"{_domain()}/{label}"], capture_output=True, timeout=60)
            return 1
        _relay(relays)
        if not _await(job, "status", label):
            print(f"Job {label} ended without an exit status.", file=sys.stderr)
            subprocess.run([LAUNCHCTL, "bootout", f"{_domain()}/{label}"], capture_output=True, timeout=60)
            return 1
        _await_removal(label)  # as systemd-run --wait returns once the unit has gone
        return int((job / "status").read_text())
    finally:
        for source in relays:
            os.close(source)
        if mode != "detached" and not (job / "survivors").exists():
            shutil.rmtree(job, ignore_errors=True)


def _await(job: Path, name: str, label: str) -> bool:
    """Wait for the supervisor to write `name`; False once its launchd job has gone without it."""
    checked = time.monotonic()
    while not (job / name).exists() and not (job / "status").exists():
        if time.monotonic() - checked >= 1:
            checked = time.monotonic()
            try:
                job_gone = _print(label) is None
            except RuntimeError:
                job_gone = False
            if job_gone and not (job / name).exists() and not (job / "status").exists():
                return False
        time.sleep(0.02)
    return True


def _await_removal(label: str, timeout: float = 10) -> None:
    """Wait while the supervisor removes its launchd job after recording the status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _print(label) is None:
                return
        except RuntimeError:
            return
        time.sleep(0.02)


def _relay(relays: dict[int, int]) -> None:
    """Copy each FIFO to this process's own output until every writer has closed it."""
    open_sources = dict(relays)
    for source in open_sources:
        os.set_blocking(source, True)
    while open_sources:
        ready, _, _ = select.select(list(open_sources), [], [])
        for source in ready:
            data = os.read(source, 65536)
            if not data:
                del open_sources[source]
                continue
            try:
                while data:
                    data = data[os.write(open_sources[source], data):]
            except OSError:  # the caller stopped reading; keep draining so the command is not blocked
                pass


def _supervise(job: Path) -> int:
    """The launchd end of a job: run the command, hold it to its time limit, stop whatever it leaves in the
    coalition, record its status and remove this launchd job."""
    spec = json.loads((job / "spec.json").read_text())
    own = os.getpid()
    coalition = _coalition_of(own)
    _write_private(job / "coalition", str(coalition))
    _write_private(job / "supervisor", str(own))
    terminal = spec.get("mode") == "terminal"
    status = 1
    try:
        # A terminal's shell holds its pseudo-terminal read-write on all three descriptors, as it would anywhere.
        streams = [os.open(spec["stdin"], os.O_RDWR if terminal else os.O_RDONLY)]
        for fd in ("1", "2"):
            if terminal:
                streams.append(os.dup(streams[0]))
            elif "file" in spec[fd]:
                streams.append(os.open(spec[fd]["file"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600))
            else:  # ENXIO: the launcher has gone, so nothing reads the output
                streams.append(os.open(spec[fd]["fifo"], os.O_WRONLY | os.O_NONBLOCK))
                os.set_blocking(streams[-1], True)
        command = spec["command"]
        if spec.get("writable") is not None:
            command = confined(command, spec["writable"])
        try:
            child = subprocess.Popen(command, stdin=streams[0], stdout=streams[1], stderr=streams[2],
                                     env=spec["env"], cwd=spec["cwd"], start_new_session=terminal,
                                     preexec_fn=_controlling_terminal if terminal else None)
            _write_private(job / "leader", str(child.pid))
        except OSError as exc:
            os.write(streams[2], f"{command[0]}: {exc}\n".encode())
            child = None
        for stream in streams:
            os.close(stream)
        if child is not None and not terminal:
            # Idle sleep waits for the job (a closed lid still sleeps); the assertion ends with this supervisor.
            try:
                subprocess.Popen([CAFFEINATE, "-i", "-w", str(own)], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
        (job / "started").touch()
        if child is None:
            status = 127
        else:
            try:
                code = child.wait(timeout=spec.get("runtime_max")) if not terminal else _hold(child, spec["launcher"])
            except subprocess.TimeoutExpired:
                _confirm_stopped(coalition, spare=own, **_stop_signal(spec))
                code = child.wait()
            status = code if code >= 0 else 128 - code
    except OSError as exc:
        print(f"{spec['label']}: {exc}", file=sys.stderr)
    finally:
        stopped = _confirm_stopped(coalition, spare=own, **_stop_signal(spec))
        _write_private(job / "status", str(status))
        launcher = spec.get("launcher")
        if not stopped:
            _keep(job, coalition)
        elif launcher is None or not _running(*launcher):
            shutil.rmtree(job, ignore_errors=True)
        os.execv(LAUNCHCTL, [LAUNCHCTL, "remove", spec["label"]])
    return status


def _hold(child: subprocess.Popen, launcher: list) -> int:
    """Wait for a terminal's shell; stop it (the caller stops the rest) once its launcher has gone."""
    while True:
        try:
            return child.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            if not _running(*launcher):
                child.kill()


def _stop_signal(spec: dict) -> dict:
    """How a job's members are stopped: a terminal's hang up first, as KillSignal=SIGHUP, with its own grace."""
    return {"first": signals.SIGHUP, "grace": spec["grace"]} if spec.get("mode") == "terminal" else {}


def _keep(job: Path, coalition: int) -> None:
    """Keep a stopped job's record while processes it started may still run, so `job_active` still reports them."""
    job.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (job / "coalition").exists():
        _write_private(job / "coalition", str(coalition))
    (job / "survivors").touch()


def _collect(label: str) -> bool:
    """Remove a record kept for survivors once they have all gone, as systemd collects a unit whose processes have
    ended; False while it is anything else."""
    job = _jobs() / label
    try:
        if not (job / "survivors").exists() or _print(label) or _members(_recorded_coalition(label) or 0):
            return False
    except (OSError, RuntimeError):
        return False
    shutil.rmtree(job, ignore_errors=True)
    return True


def _recorded_coalition(label: str) -> int | None:
    try:
        return int((_jobs() / label / "coalition").read_text())
    except (OSError, ValueError):
        return None


def _launchd_job_active(name: str) -> bool:
    label = _label(name)
    job = _print(label)
    if job and (job.get("state") == "running" or job.get("last exit code") == "(never exited)"):
        return True  # running, or bootstrapped and about to start
    coalition = _recorded_coalition(label) or (job or {}).get("coalition")
    try:
        return bool(coalition and _members(coalition))
    except OSError as exc:
        raise RuntimeError("Worker unit status is unavailable") from exc


def _launchd_job_stop(name: str, timeout: int) -> None:
    label = _label(name)
    try:
        job = _print(label)
    except RuntimeError:
        job = None
    coalition = _recorded_coalition(label) or (job or {}).get("coalition")
    try:
        spec = json.loads((_jobs() / label / "spec.json").read_text())
    except (OSError, ValueError):
        spec = {}
    stopped = not coalition or _confirm_stopped(coalition, limit=timeout, **_stop_signal(spec))
    subprocess.run([LAUNCHCTL, "bootout", f"{_domain()}/{label}"], capture_output=True, timeout=timeout)
    if stopped:
        shutil.rmtree(_jobs() / label, ignore_errors=True)
    else:
        _keep(_jobs() / label, coalition)


def _altitude_coalitions() -> dict[int, str]:
    """Coalition -> unit name for every job whose supervisor has recorded one and, when this process is the
    service, its own."""
    units = {_recorded_coalition(path.name): path.name.removeprefix("dev.altitude.job.") + ".service"
             for path in _jobs().glob("*")}
    if os.environ.get("ALTITUDE_SERVICE") == "1":
        units[_coalition_of(os.getpid())] = SERVICE
    return {coalition: unit for coalition, unit in units.items() if coalition}


# --- macOS: processes and sockets ---------------------------------------------------------------------------------

_LIBPROC = None
SZOMB = 5  # proc_bsdinfo.pbi_status of a process that has exited but not been reaped


class _BSDInfo(ctypes.Structure):
    _fields_ = ([(name, ctypes.c_uint32) for name in ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid",
                                                      "rgid", "svuid", "svgid", "rfu")]
                + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
                + [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "pjobc", "tdev", "tpgid")]
                + [("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)])


class _FileInfo(ctypes.Structure):
    _fields_ = [("openflags", ctypes.c_uint32), ("status", ctypes.c_uint32), ("offset", ctypes.c_int64),
                ("type", ctypes.c_int32), ("guardflags", ctypes.c_uint32)]


class _VInfoStat(ctypes.Structure):
    _fields_ = [("dev", ctypes.c_uint32), ("mode", ctypes.c_uint16), ("nlink", ctypes.c_uint16),
                ("ino", ctypes.c_uint64), ("uid", ctypes.c_uint32), ("gid", ctypes.c_uint32),
                ("times", ctypes.c_int64 * 8), ("size", ctypes.c_int64), ("blocks", ctypes.c_int64),
                ("blksize", ctypes.c_int32), ("flags", ctypes.c_uint32), ("gen", ctypes.c_uint32),
                ("rdev", ctypes.c_uint32), ("qspare", ctypes.c_int64 * 2)]


class _SockbufInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ("cc", "hiwat", "mbcnt", "mbmax", "lowat")] + [
        ("flags", ctypes.c_short), ("timeo", ctypes.c_short)]


class _InSockInfo(ctypes.Structure):
    class _V6(ctypes.Structure):
        _fields_ = [("hlim", ctypes.c_uint8), ("cksum", ctypes.c_int), ("ifindex", ctypes.c_ushort),
                    ("hops", ctypes.c_short)]
    _fields_ = [("fport", ctypes.c_int), ("lport", ctypes.c_int), ("gencnt", ctypes.c_uint64),
                ("flags", ctypes.c_uint32), ("flow", ctypes.c_uint32), ("vflag", ctypes.c_uint8),
                ("ip_ttl", ctypes.c_uint8), ("rfu", ctypes.c_uint32), ("faddr", ctypes.c_uint32 * 4),
                ("laddr", ctypes.c_uint32 * 4), ("v4", ctypes.c_uint8), ("v6", _V6)]


class _SocketFdInfo(ctypes.Structure):
    class _SocketInfo(ctypes.Structure):
        _fields_ = [("stat", _VInfoStat), ("so", ctypes.c_uint64), ("pcb", ctypes.c_uint64),
                    ("type", ctypes.c_int), ("protocol", ctypes.c_int), ("family", ctypes.c_int)] + [
            (name, ctypes.c_short) for name in ("options", "linger", "state", "qlen", "incqlen", "qlimit", "timeo")] + [
            ("error", ctypes.c_ushort), ("oobmark", ctypes.c_uint32), ("rcv", _SockbufInfo), ("snd", _SockbufInfo),
            ("kind", ctypes.c_int), ("rfu", ctypes.c_uint32), ("proto", ctypes.c_uint64 * 66)]
    _fields_ = [("pfi", _FileInfo), ("psi", _SocketInfo)]


def _libproc():
    global _LIBPROC
    if _LIBPROC is None:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidfdinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        _LIBPROC = lib
    return _LIBPROC


def _pidinfo(pid: int, flavor: int, buffer, arg: int = 0) -> int:
    """proc_pidinfo into `buffer`: bytes filled. A process that does not exist raises FileNotFoundError."""
    filled = _libproc().proc_pidinfo(int(pid), flavor, arg, ctypes.byref(buffer), ctypes.sizeof(buffer))
    if filled <= 0:
        error = ctypes.get_errno()
        raise (FileNotFoundError if error == 3 else OSError)(error, f"process {pid} is unreadable")  # 3: ESRCH
    return filled


def _pids() -> list[int]:
    lib = _libproc()
    buffer = (ctypes.c_int * (lib.proc_listallpids(None, 0) + 256))()
    count = lib.proc_listallpids(buffer, ctypes.sizeof(buffer))
    if count <= 0:
        raise OSError(ctypes.get_errno(), "The process table is unreadable")
    return [pid for pid in buffer[:count] if pid > 0]


def _bsd(pid: int) -> _BSDInfo:
    info = _BSDInfo()
    if _pidinfo(pid, 3, info) != ctypes.sizeof(info):  # PROC_PIDTBSDINFO
        raise OSError(f"process {pid} is unreadable")
    return info


def _started(info: _BSDInfo) -> str:
    return f"{info.start_sec}{info.start_usec:06d}"


def _running(pid: int, start: str) -> bool:
    try:
        return process_running(pid, start) is True
    except OSError:
        return False


def _coalition_of(pid: int) -> int:
    """The process's resource coalition, which every descendant inherits whatever it does."""
    ids = (ctypes.c_uint64 * 5)()
    _pidinfo(pid, 20, ids)  # PROC_PIDCOALITIONINFO
    return int(ids[0])


def _members(coalition: int) -> list[tuple[int, str]]:
    """(pid, start) of each process in the coalition. Any user's process has a readable coalition, so only one that
    has exited is skipped; any other unreadable process raises OSError rather than reading as absent."""
    members = []
    for pid in _pids():
        try:
            if _coalition_of(pid) == coalition:
                members.append((pid, _started(_bsd(pid))))
        except FileNotFoundError:
            continue
    return members


def _confirm_stopped(coalition: int, **stop) -> bool:
    """Stop the coalition's members; False unless every one is confirmed gone."""
    try:
        return _stop_members(coalition, **stop)
    except OSError:
        return False


def _stop_members(coalition: int, *, spare: int | None = None, first: int = signals.SIGTERM, grace: float = 5,
                  limit: float = 60) -> bool:
    """Stop every process in the coalition but `spare`: `first`, then SIGKILL for whatever outlasts `grace`,
    repeated for members that fork meanwhile. Each signal is preceded by a fresh identity check. False when members
    remain after `limit` seconds."""
    began = time.monotonic()
    termed: set[tuple[int, str]] = set()
    while True:
        members = [member for member in _members(coalition) if member[0] != spare]
        if not members:
            return True
        elapsed = time.monotonic() - began
        if elapsed >= limit:
            return False
        for pid, start in members:
            sig = signals.SIGKILL if elapsed >= grace else first
            if sig != signals.SIGKILL and (pid, start) in termed:
                continue
            try:
                if _coalition_of(pid) == coalition and _started(_bsd(pid)) == start:
                    os.kill(pid, sig)
                    termed.add((pid, start))
            except OSError:
                pass
        time.sleep(0.05)


def _footprint(pid: int) -> int:
    usage = ctypes.create_string_buffer(512)
    if _libproc().proc_pid_rusage(int(pid), 0, usage):  # RUSAGE_INFO_V0
        return 0
    return int.from_bytes(usage.raw[72:80], sys.byteorder)  # ri_phys_footprint


def _tcp_handle(local, local_port: int, remote, remote_port: int) -> str:
    return f"tcp:{local}:{local_port}>{remote}:{remote_port}"


def _tcp_handles(pid: int) -> set[str]:
    """The TCP connections the process holds, each named by its local and remote endpoints."""
    fds = (ctypes.c_int32 * 2 * (_bsd(pid).nfiles + 64))()
    count = _pidinfo(pid, 1, fds) // 8  # PROC_PIDLISTFDS: (fd, type) pairs
    handles = set()
    for fd, kind in fds[:count]:
        if kind != 2:  # PROX_FDTYPE_SOCKET
            continue
        info = _SocketFdInfo()
        if _libproc().proc_pidfdinfo(pid, fd, 3, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            continue  # PROC_PIDFDSOCKETINFO; closed meanwhile
        if info.psi.kind != 2:  # SOCKINFO_TCP
            continue
        tcp = _InSockInfo.from_buffer(info.psi.proto)

        def address(words):
            raw = bytes(words)
            return ipaddress.ip_address(raw[12:] if tcp.vflag & 1 else raw)  # INI_IPV4

        handles.add(_tcp_handle(address(tcp.laddr), _socket_port(tcp.lport), address(tcp.faddr), _socket_port(tcp.fport)))
    return handles


def _socket_port(value: int) -> int:
    return int.from_bytes((value & 0xFFFF).to_bytes(2, sys.byteorder), "big")


# --- Validation runner ---------------------------------------------------------------------------------------------

KVM = Path("/dev/kvm")


def validation_unavailable() -> str | None:
    """Why this host cannot run the validation runner's rootless Podman containers, or None when it can. macOS
    runs Podman inside a virtual machine of its own and has no KVM, so the runner is not implemented there."""
    if sys.platform != "linux" or host_platform.machine() not in ("x86_64", "AMD64"):
        return "validation runs need Linux x86_64 for now"
    missing = [tool for tool in ("podman", "slirp4netns") if not shutil.which(tool)]
    return f"validation runs need {' and '.join(missing)} on this computer" if missing else None


def validation_runroot() -> str:
    """Podman's runtime folder for the runner, in the user's runtime directory: Podman limits its length."""
    return f"/run/user/{os.getuid()}/altitude-validation"


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
