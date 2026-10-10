"""The platform seam: the host's service manager and process facilities.

Two hosts are implemented. Linux uses a systemd user manager, procfs and pidfd. macOS 15 or newer uses a per-user
LaunchAgent, one launchd job per worker job (each its own kernel coalition), libproc, sysctl and Seatbelt. This module
is the only place that names them; engines, the terminal, images, installation and the server ask it for the service,
jobs, processes and connections. Each operation reads `sys.platform` when called, so tests exercise either
implementation against fixtures on any host.
"""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
from contextlib import ExitStack, contextmanager
import ipaddress
import json
import os
from pathlib import Path
import platform as host_platform
import plistlib
import pwd
import re
import select
import shlex
import shutil
import signal as signals
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import uuid
import termios
import time


SERVICE = "altitude.service"
CONTAINER_STOP_WAIT = 105  # process termination45 + stop-post45 + margin15
CONTAINER_BUILD_SECONDS = 900  # extraction, build480, inspections60, save60, load60, margin
#: The LaunchAgent that runs the account's service on macOS; service_label() names the one for the current home.
LABEL = "dev.altitude.altd"


def _darwin() -> bool:
    return sys.platform == "darwin"


_GH_PACKAGES = "https://cli.github.com/packages"
#: Each tool's own installation page, for a Linux system whose package manager install_command() does not know.
INSTALL_PAGES = {"gh": "https://github.com/cli/cli#installation", "git": "https://git-scm.com/downloads/linux"}


def package_manager() -> str | None:
    """This Linux system's own package manager, apt, dnf, pacman or zypper, or None for another."""
    return next((name for name, binary in (("apt", "apt-get"), ("dnf", "dnf"), ("pacman", "pacman"),
                                           ("zypper", "zypper")) if shutil.which(binary)), None)


def install_command(tool: str) -> str | None:
    """What First run and `alt doctor` show for a missing or outdated `gh` or `git`, run in the operator's own terminal,
    or None on a Linux system whose package manager this does not know; INSTALL_PAGES names the tool's own page then.
    Homebrew's and Arch's gh are current; other distributions package one too old for alt land, so apt, dnf and zypper
    add GitHub's own repository and install or upgrade gh from it. A repository file or signing key replaces the
    installed one only once its download succeeds."""
    if _darwin():
        return {"gh": "brew install gh", "git": "xcode-select --install"}[tool]
    manager = package_manager()
    if tool == "git":
        return {"apt": "sudo apt install git", "dnf": "sudo dnf install git", "pacman": "sudo pacman -S git",
                "zypper": "sudo zypper install git"}.get(manager)
    if manager == "apt":
        keyring = "/etc/apt/keyrings/githubcli-archive-keyring.gpg"
        return (f'key=$(mktemp) && curl -fsSL {_GH_PACKAGES}/githubcli-archive-keyring.gpg -o "$key" && sudo install -D '
                f'-m 644 "$key" {keyring} && rm "$key" && echo "deb [signed-by={keyring}] {_GH_PACKAGES} stable main" | '
                "sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null && sudo apt update && sudo apt install gh")
    if manager in ("dnf", "zypper"):
        folder, install = (("/etc/yum.repos.d", "sudo dnf install gh && sudo dnf upgrade gh") if manager == "dnf"
                           else ("/etc/zypp/repos.d", "sudo zypper install gh"))
        return (f'repo=$(mktemp) && curl -fsSL {_GH_PACKAGES}/rpm/gh-cli.repo -o "$repo" && sudo install -D -m 644 '
                f'"$repo" {folder}/gh-cli.repo && rm "$repo" && {install}')
    return "sudo pacman -S github-cli" if manager == "pacman" else None

# Image-owned identity, outside every persistent/writable application volume. Neither an environment
# variable nor a forwarded connection can select the privileged native deployment paths (issue #543).
CONTAINER_MARKER = Path("/etc/altitude/container")
CONTAINER_INSTANCE = Path("/etc/altitude/instance")
CONTAINER_PROJECTS = Path("/home/altitude/Projects")
CONTAINER_USER_PATH = "/home/altitude/.local/bin:/usr/local/bin:/usr/bin:/bin"
CONTAINER_CGROUP_ROOT = Path("/sys/fs/cgroup")
IMAGE_MANAGED = "This container is image-managed. Replace or restart it from the host with Podman."


def _container_identity(path: Path) -> bytes:
    """Read fixed image metadata across the worker's single-user namespace (#660)."""
    owner = Path('/').lstat().st_uid
    if owner != 0:
        if _darwin():
            raise ValueError('Container identity requires root ownership')
        # The kernel collapses unmapped owners to overflowuid. It cannot attest their original
        # IDs: trust stays in the fixed image path and its protected ancestors, including /.
        try:
            mapping = [list(map(int, row.split())) for row in (PROC / 'self/uid_map').read_text().splitlines()]
            overflow = int((PROC / 'sys/kernel/overflowuid').read_text())
        except (OSError, ValueError) as exc:
            raise ValueError('Container identity namespace is unavailable') from exc
        uid = os.geteuid()
        if (uid == 0 or owner != overflow or owner == uid or len(mapping) != 1
                or len(mapping[0]) != 3 or mapping[0][0] != uid or mapping[0][1] < 0 or mapping[0][2] != 1):
            raise ValueError('Container identity requires root ownership or a protected single-user namespace')
    for entry in (path, *path.parents):
        info = entry.lstat()
        expected_type = stat.S_ISREG if entry == path else stat.S_ISDIR
        if (not expected_type(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022
                or owner != 0 and os.access(entry, os.W_OK, effective_ids=True)):
            raise ValueError('Container identity and parents must be image-owned and non-writable')
    with path.open('rb') as source:
        identity = source.read(65)
    if len(identity) > 64:
        raise ValueError('Container identity is oversized')
    return identity


def containerized() -> bool:
    """Recognize the image contract; an invalid existing marker fails closed, never as native mode."""
    try:
        CONTAINER_MARKER.lstat()
    except FileNotFoundError:
        return False
    try:
        identity = _container_identity(CONTAINER_MARKER)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if identity != b"altitude-container-v1\n":
        raise RuntimeError("Unsupported container identity; use a matching Altitude image")
    return True


def container_unavailable(subject: str) -> str | None:
    if not containerized():
        return None
    if subject == "Terminal":
        return "Browser terminal is unavailable in this container. Use podman exec from a host terminal."
    if subject == "Voice":
        return "Host voice is unavailable in this container. Use browser recognition where supported"
    if subject == "Set up a device":
        return "Export the public CA with the host container command's certificate action; this image publishes no certificate-sharing port."
    if subject == "Validation":
        return validation_unavailable()
    return IMAGE_MANAGED


def coordinator_socket_directory() -> Path:
    """Runtime sockets must not become persistent backup data (#543)."""
    from . import config
    return Path('/run/user/1000/altitude-l3') if containerized() else config.ROOT / 'l3-verbs'


def require_native_application() -> None:
    if containerized():
        raise RuntimeError(IMAGE_MANAGED)


def container_setting_error(setting: str, value) -> str | None:
    if setting == "terminal":
        return container_unavailable("Terminal")
    if setting in ("update_check", "update_automatic"):
        return container_unavailable("Update")
    if setting == "voice" and value == "host":
        return container_unavailable("Voice")
    return None


def container_shell_command() -> str | None:
    if not containerized():
        return None
    return ("podman exec -it --user 1000 --env HOME=/home/altitude "
            f"--env PATH={CONTAINER_USER_PATH} "
            "--env XDG_RUNTIME_DIR=/run/user/1000 --workdir /home/altitude "
            f"{shlex.quote(socket.gethostname())} bash --noprofile --norc")


def container_git_guards() -> tuple[Path, Path, str] | None:
    """Image hooks and persistent consent receipts outside task-writable project/state roots."""
    if not containerized():
        return None
    from . import config
    return config.SOURCE / "hooks", config.HOME / ".config/altitude/git-guards", "/usr/bin/python3"


def require_container_project(path: Path, *, folder: bool = False) -> None:
    if containerized():
        root = CONTAINER_PROJECTS.resolve()
        target = path.resolve()
        if not target.is_relative_to(root) or not folder and target == root:
            raise ValueError("Choose a project folder inside the container's /home/altitude/Projects volume")


def _container_instance() -> str:
    value = _container_identity(CONTAINER_INSTANCE).decode('ascii').strip()
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Container instance identity is invalid")
    return value


def _lifecycle_directory() -> Path:
    from . import config
    return config.HOME / ".config/altitude"


@contextmanager
def _lifecycle_lock(name: str, operation: int):
    directory = _lifecycle_directory()
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Container lifecycle lock must be a regular file")
        fcntl.flock(fd, operation)
        yield
    finally:
        os.close(fd)


def _lifecycle_write(instance: str, paused: bool) -> None:
    from . import state as S
    S.write_json(_lifecycle_directory() / "lifecycle.json", {"instance": instance, "paused": paused})


def _lifecycle_state() -> dict:
    # #543: missing/invalid state must not let replacement or restore replay provider work.
    instance = None
    reason = "Container recovery is paused. Run the host launcher Continue after checking saved work."
    try:
        instance = _container_instance()
        record = json.loads((_lifecycle_directory() / "lifecycle.json").read_text())
        if not isinstance(record, dict) or type(record.get("paused")) is not bool:
            raise ValueError("Invalid lifecycle receipt")
        if record.get("instance") == instance:
            if not record["paused"]:
                return {"instance": instance, "ready": True, "reason": None}
            reason = "New AI work is paused. Run the host launcher Continue to release queued work."
    except (OSError, ValueError):
        pass
    if instance is None:
        reason = "Container identity is unavailable. Repair image startup before continuing."
    return {"instance": instance, "ready": False, "reason": reason}


def container_lifecycle() -> dict | None:
    if not containerized():
        return None
    try:
        with _lifecycle_lock("lifecycle.lock", fcntl.LOCK_EX):
            state = _lifecycle_state()
            if state["instance"]:
                state["continue_command"] = ("python3 scripts/container.py continue --name " +
                    shlex.quote(socket.gethostname()) + " --instance " + state["instance"])
            try:
                with _lifecycle_lock("lifecycle-launches.lock", fcntl.LOCK_EX | fcntl.LOCK_NB):
                    state["admitted_calls_active"] = False
            except BlockingIOError:
                state["admitted_calls_active"] = True
            # Detached workers outlive admission calls; this is never a backup/drained certificate.
            state["work_notice"] = "Previously started workers may still run. Stop the container before a consistent backup."
            return state
    except (OSError, ValueError) as exc:
        return {"instance": None, "ready": False, "reason": f"Container lifecycle state is unavailable: {exc}"}


@contextmanager
def container_admission():
    """A lease covers pre-claim work through launch; pause rejects only later admissions.

    Receipt locking serializes the check with pause. The shared lease is held independently until
    the admitted call returns, including synchronous provider turns, and is released on process exit.
    """
    if not containerized():
        yield None
        return
    with ExitStack() as active:
        try:
            with _lifecycle_lock("lifecycle.lock", fcntl.LOCK_EX):
                why = _lifecycle_state()["reason"]
                if not why:
                    active.enter_context(_lifecycle_lock("lifecycle-launches.lock", fcntl.LOCK_SH))
        except (OSError, ValueError) as exc:
            why = f"Container lifecycle state is unavailable: {exc}"
        yield why


def change_container_lifecycle(action: str, expected: str) -> dict:
    """Host-exec administration only: no CLI agent verb or HTTP mutation route."""
    from . import config
    actor = os.environ.get("ALTITUDE_ACTOR", config.OPERATOR_ACTOR)
    if actor != config.OPERATOR_ACTOR or any(os.environ.get(key) for key in
            ("ALTITUDE_TASK", "ALTITUDE_SESSION_KEY", "ALTITUDE_L3_TOKEN")):
        raise PermissionError("Container continuation requires the operator's host terminal")
    if not containerized() or action not in ("pause", "continue"):
        raise ValueError("Select pause or continue for an Altitude container")
    if action == "continue":
        container_ready()
    with _lifecycle_lock("lifecycle.lock", fcntl.LOCK_EX):
        instance = _container_instance()
        if expected != instance:
            raise ValueError("The container changed; inspect its status before continuing")
        _lifecycle_write(instance, action == "pause")
    return container_lifecycle()


def container_ready() -> None:
    """A running unit alone does not establish readiness (#543); prove its local HTTPS process."""
    from . import tls
    found = tls.service()
    if not found["tls"]:
        raise RuntimeError("The container application must serve HTTPS before continuing")
    # Published DNS may resolve only on the host. The image certificate also covers localhost.
    tls._proven({**found, "url": f"https://localhost:{found['port']}"})


def _initialize_container_lifecycle(home: Path, projects: Path) -> None:
    """Before the user manager: keep identity in this container layer, outside persistent volumes."""
    if not CONTAINER_INSTANCE.exists():
        temporary = CONTAINER_INSTANCE.with_name("instance-" + uuid.uuid4().hex)
        temporary.write_text(uuid.uuid4().hex + "\n")
        temporary.chmod(0o444)
        os.replace(temporary, CONTAINER_INSTANCE)
    instance = _container_instance()
    fresh = set(p.name for p in home.iterdir()) <= {"Projects"} and not any(projects.iterdir())
    if fresh:
        # Do not traverse an application-owned home as root. No user shell, hooks or mutable code.
        subprocess.run([sys.executable, "-B", "-c",
            "from altitude.platform import _lifecycle_write; "
            f"_lifecycle_write({instance!r}, False)"], check=True, timeout=10,
            cwd=Path(__file__).resolve().parent.parent, user=1000, group=1000, extra_groups=(),
            env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})


@contextmanager
def container_volume_locks(home: Path, projects: Path):
    """Hold both controller-volume locks, including across user-manager/daemon restarts.

    Issue #543: lock the mounted directory inodes so restoring entries cannot replace a
    lock file. These coordinate supported controllers/helpers, not hostile same-account software.
    """
    with ExitStack() as stack:
        for directory in (home, projects):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            stack.callback(os.close, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(f"Another Altitude container owns this volume: {directory}") from None
        yield


def container_bootstrap() -> None:
    """The image's root bootstrap unit, ordered before the application user manager."""
    if not containerized() or os.getuid() != 0:
        raise RuntimeError("Container bootstrap requires its image and container-root account")
    home, projects = CONTAINER_PROJECTS.parent, CONTAINER_PROJECTS
    # Named volumes can share a backing device: stat-based is_mount misses their nested bind mounts.
    # These two fixed image paths contain no mountinfo escape characters.
    mounts = {line.split()[4] for line in (PROC / "self/mountinfo").read_text().splitlines()}
    for directory in (home, projects):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or str(directory) not in mounts:
            raise RuntimeError(f"Mount a dedicated local Podman volume at {directory}")
        if info.st_uid not in (0, 1000):
            raise RuntimeError(f"Volume {directory} must belong to container UID 1000 or be newly created")
    with container_volume_locks(home, projects):
        from .container_archive import MARKER
        restored = os.environ.get('ALTITUDE_RESTORE_SHA', '')
        if restored:
            lineage, pair = (os.environ.get(key, '') for key in ('ALTITUDE_VOLUME_LINEAGE','ALTITUDE_VOLUME_PAIR'))
            if not re.fullmatch('[0-9a-f]{64}', restored) or any(not re.fullmatch('[0-9a-f]{32}', v) for v in (lineage,pair)):
                raise RuntimeError('Invalid immutable restore identity')
            expected = {'format':1,'archive':restored,'lineage':lineage,'pair':pair}
            for root in (home, projects):
                fd = os.open(root / MARKER, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd) as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode) or json.loads(source.read(4096)) != expected:
                        raise RuntimeError('Restore completion does not match this volume pair')
        elif any((root / MARKER).exists() for root in (home,projects)):
            raise RuntimeError('Restored volumes require their recorded restore identity')
        # Only the two mount roots, never recursive data or host paths. Open descriptors retain locks.
        for directory in (home, projects):
            os.chown(directory, 1000, 1000)
            directory.chmod(0o700)
        _initialize_container_lifecycle(home, projects)
        notify = os.environ.get("NOTIFY_SOCKET")
        if not notify:
            raise RuntimeError("Container bootstrap must run as its notification service")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.connect("\0" + notify[1:] if notify.startswith("@") else notify)
            channel.sendall(b"READY=1")
        def stop(_signal, _frame):
            raise SystemExit(0)
        signals.signal(signals.SIGTERM, stop)
        signals.signal(signals.SIGINT, stop)
        while True:
            signals.pause()


def container_runtime() -> dict:
    """Actionable prerequisites for the tested Linux rootless-runtime family; no daemon installation."""
    require_supported()
    if _darwin():
        raise RuntimeError("The container launcher is validated on Linux first; Mac VM/ARM64 acceptance remains pending")
    if os.getuid() == 0:
        raise RuntimeError("Run container commands as your ordinary Linux account, not root")
    if not shutil.which("podman"):
        raise RuntimeError("Install Podman and crun using your Linux distribution, then retry")
    try:
        info = json.loads(container_command(["info", "--format", "json"]))
        host = info["host"]
        if not host["security"]["rootless"] or not host["security"]["seccompEnabled"]:
            raise RuntimeError("Rootless Podman with default seccomp is required")
        if host["cgroupVersion"] != "v2" or host["cgroupManager"] != "cgroupfs":
            raise RuntimeError("Rootless Podman needs delegated cgroup v2 and the cgroupfs manager")
        if host["ociRuntime"]["path"] != "/usr/bin/crun":
            raise RuntimeError("This container deployment requires distribution crun")
        if not shutil.which("slirp4netns"):
            raise RuntimeError("Install slirp4netns for the explicitly selected rootless network")
        transfers=container_transfer_records(info['store']['graphRoot'])
        if transfers:
            print('Active or unfinished private transfers retained: '+', '.join(item['id'] for item in transfers)
                  +'. Inspect with the host launcher transfers command; recover only after the service stops.',file=sys.stderr)
        return {"host": host, "version": info["version"], "store": info["store"], 'transfers':transfers}
    except (KeyError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot establish rootless container prerequisites: {exc}") from exc


def container_transfer_root(*, create=False) -> Path:
    """Persistent operator-owned recovery records, outside temporary runtime directories (#543/F3)."""
    root=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'altitude-container/transfers'
    for directory in (root.parent,root):
        if create: directory.mkdir(parents=True,mode=0o700,exist_ok=True)
        if directory.exists() or directory.is_symlink():
            info=directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                raise RuntimeError('Container transfer records need an owned private directory')
    return root


def container_transfer_records(store: str) -> list[dict]:
    result=[]
    for record in sorted(container_transfer_root().glob('*/operation.json')):
        parent,info=record.parent.lstat(),record.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.getuid() or parent.st_mode&0o077 or
                not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>65536):
            raise RuntimeError('An unsafe transfer record needs local inspection')
        value=json.loads(record.read_text())
        if value.get('store')==store:
            if value.get('id')!=record.parent.name or not re.fullmatch('[0-9a-f]{32}',value['id']):
                raise RuntimeError('Transfer record identity differs from its private directory')
            result.append({'id':value['id'],'action':value['action'],'directory':value['directory'],
                'volumes':[value['identity']['home'],value['identity']['projects']], 'unit':value['unit']})
    return result


def container_user_environment(runtime_dir: Path | None = None) -> dict[str, str]:
    """Keep Podman and its environment-stripped OCI children on the same user bus (#543)."""
    if _darwin():
        raise RuntimeError("The Linux container launcher cannot yet manage a Mac container VM")
    environment = dict(os.environ)
    canonical = Path(f"/run/user/{os.getuid()}/bus")
    runtime = runtime_dir or Path(environment.get("XDG_RUNTIME_DIR", str(canonical.parent)))
    address = f"unix:path={canonical}"
    if not runtime.is_absolute() or environment.get("DBUS_SESSION_BUS_ADDRESS", address) != address:
        raise RuntimeError("Podman requires the local user runtime bus, not a redirected bus address")
    try:
        if (runtime / "bus").resolve(strict=True) != canonical:
            raise RuntimeError("Podman runtime bus must resolve to the local user bus")
        directory, bus = runtime.lstat(), canonical.lstat()
    except OSError as exc:
        raise RuntimeError("Local user bus is unavailable; restore the login session before using containers") from exc
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or \
            directory.st_mode & 0o022 or not stat.S_ISSOCK(bus.st_mode) or bus.st_uid != os.getuid():
        raise RuntimeError("Local user bus ownership/type is invalid; refuse container operations")
    environment.update(XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS=address,
                       DBUS_SYSTEM_BUS_ADDRESS="unix:path=/dev/null/altitude-system-bus-unavailable")
    return environment


def container_unit(instance: str) -> str:
    """A stable host user unit preserves the stopped container's immutable cgroup parent."""
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", instance):
        raise ValueError("Invalid container instance name")
    return f"altitude-container-{instance}.service"


def container_parent(unit: str) -> str:
    """Only the delegated supervisor may create resource-owning children (#543)."""
    membership = [line[3:] for line in (PROC / "self/cgroup").read_text().splitlines() if line.startswith("0::")]
    if len(membership) != 1 or not membership[0].endswith("/" + unit + "/supervisor"):
        raise RuntimeError("Container creation requires its delegated user service")
    parent = membership[0].removesuffix("/supervisor")
    available = (CONTAINER_CGROUP_ROOT / parent.lstrip("/") / "cgroup.controllers").read_text().split()
    if not {"cpu", "memory", "pids"} <= set(available):
        raise RuntimeError("The user manager has not delegated CPU, memory and PID controllers")
    return parent


def container_job(unit: str, command: list[str], *, wait: bool = False,
                  after_stop: list[str] | None = None, seconds: int = CONTAINER_BUILD_SECONDS) -> str:
    """Own the complete container/build lifetime in one bounded user-manager subtree."""
    environment = container_user_environment()
    selected = {key: value for key, value in environment.items()
                if key in {"HOME", "PATH", "LANG", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                           "XDG_CACHE_HOME", "DBUS_SESSION_BUS_ADDRESS", "CONTAINERS_CONF"}}
    arguments = ["systemd-run", "--user", "--collect", "--quiet", "--expand-environment=no",
                 f"--unit={unit}", "--slice=app.slice", "--property=Type=exec", "--property=Delegate=yes",
                 "--property=DelegateSubgroup=supervisor", "--property=KillMode=mixed",
                 "--property=TimeoutStopSec=45", "--property=CPUQuota=200%",
                 "--property=MemoryMax=5G", "--property=TasksMax=1536",
                 f"--working-directory={Path(__file__).resolve().parent.parent}"]
    if wait:
        arguments += ["--wait", "--pipe", f"--property=RuntimeMaxSec={seconds}"]
    if after_stop is not None:
        # ExecStopPost is parsed by systemd, not a shell. Disable environment
        # expansion and escape its specifiers/quoting independently of argv.
        if any(any(character in value for character in "\n\r\x00") for value in after_stop):
            raise ValueError("Invalid container cleanup command")
        quoted = ['"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
                  for value in after_stop]
        arguments.append('--property=ExecStopPost=:' + " ".join(quoted))
    arguments += [f"--setenv={key}={value}" for key, value in selected.items()]
    try:
        result = subprocess.run([*arguments, "--", *command], env=environment, text=True,
                                capture_output=True, timeout=seconds + CONTAINER_STOP_WAIT + 15 if wait else 20)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timed out waiting for container user service; {job_logs_hint(unit)}") from exc
    if result.returncode:
        raise RuntimeError(f"Container user service failed: {result.stderr.strip()}; {job_logs_hint(unit)}")
    return result.stdout


@contextmanager
def container_lineage_lock(lineage: str):
    """Serialize this account/store's supported copy admission and stopped backups."""
    if not re.fullmatch(r"[0-9a-f]{32}", lineage):
        raise RuntimeError("Container volume lineage is missing; experimental unlabeled volumes are not adopted")
    directory = Path(container_user_environment()["XDG_RUNTIME_DIR"]) / "altitude-container-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Container admission needs its owned private runtime directory")
    fd = os.open(directory / lineage, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise RuntimeError("Invalid container admission lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another operation is using this container lineage; wait for it to finish") from None
        yield
    finally:
        os.close(fd)  # Persistent inode; removing it would let another process bypass an existing lock.


def container_copy_available(lineage: str, volumes: list[str], *, except_id: str | None = None) -> None:
    """Caller holds the lineage lock. Check ALL mount users, including unlabeled ones."""
    ids = container_command(["ps", "--all", "--quiet"]).split()
    if not ids:
        return
    environment = container_user_environment()
    for ident in ids:
        try:
            value = json.loads(container_command(['inspect',ident]))[0]
        except RuntimeError:
            # An unrelated operation can finish between inventory and inspection.
            # Only confirmed disappearance is harmless; every other refusal stays visible.
            if ident not in container_command(['ps','--all','--quiet']).split():
                continue
            raise
        if value["Id"] == except_id:
            continue
        labels = value.get("Config", {}).get("Labels") or {}
        mounts = {item.get("Name") for item in value.get("Mounts", []) if item.get("Type") == "volume"}
        if labels.get("io.altitude.lineage") != lineage and not mounts.intersection(volumes):
            continue
        unit = labels.get("io.altitude.unit")
        pending = (unit and re.fullmatch(r"altitude-container-[a-zA-Z0-9_.-]+\.service", unit)
                   and job_active(unit, environment))
        if not container_stopped(value) or pending:
            raise RuntimeError("Stop the other active container using this volume pair or backup lineage first")


def container_binary(arguments: list[str], *, source=None, target=None, seconds=1500, max_bytes=64*1024**3):
    """Binary descriptors only; never journal/capture/decode a private archive (issue543/F6)."""
    inputs=source if source is not None else subprocess.DEVNULL
    if target is None:
        code=subprocess.run(container_arguments(arguments),env=container_user_environment(),stdin=inputs,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=seconds).returncode
    else:
        # Bound only this output, not every file Podman writes in its store. A low
        # process-wide RLIMIT_FSIZE would also interrupt its database/metadata writes.
        child=subprocess.Popen(container_arguments(arguments),env=container_user_environment(),stdin=inputs,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+seconds
        total=0
        try:
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0: raise RuntimeError('Private transfer exceeded its time limit')
                if not select.select([child.stdout],[],[],min(remaining,1))[0]: continue
                value=os.read(child.stdout.fileno(),1024*1024)
                if not value: break
                total+=len(value)
                if total>max_bytes: raise RuntimeError('Private archive exceeds its byte limit; source volumes retained')
                target.write(value)
            code=child.wait(timeout=max(.1,deadline-time.monotonic()))
        finally:
            child.stdout.close()
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=5)
    if code:
        reasons={70:'Cannot read/write private volume data; check permissions and free space',
                 71:'A controller owns the volumes; stop it before backup or restore',
                 72:'Unsupported, damaged or incomplete backup data; keep the source volumes',
                 73:'Backup helper protections or resource limits are unavailable',
                 74:'Backup helper failed; keep the source volumes and inspect the operation',
                 80:'Unsupported volume ownership: files must use container UID/GID 0 or 1000',
                 81:'Set-ID files/directories are unsupported; inspect shared Git directory permissions',
                 82:'Sockets, devices and FIFOs are unsupported; remove stale socket files only after stopping their owner',
                 83:'Unsupported extended attributes: security/SELinux labels cannot be backed up by this image',
                 84:'Unsupported POSIX ACL: named users/groups must use container IDs 0 or 1000',
                 85:'Unsupported volume layout: paths need at most 128 components and home/Projects must be empty'}
        raise RuntimeError(reasons.get(code,'Private container transfer failed; no completed backup/restore is admitted'))


def container_archive_environment() -> tuple[Path, Path]:
    """Assert the Linux image helper's actual protections before reading private volumes."""
    if os.getuid()!=0 or not containerized():
        raise ValueError('Backup helper requires the explicit container-root image entrypoint')
    home, projects = Path('/backup/home'), Path('/backup/projects')
    status = dict(line.split(':',1) for line in (PROC/'self/status').read_text().splitlines() if ':' in line)
    group = CONTAINER_CGROUP_ROOT
    quota, period = (group/'cpu.max').read_text().split()
    if (status.get('NoNewPrivs','').strip()!='1' or status.get('Seccomp','').strip()!='2'
            or int(status['CapEff'].strip(),16) & ~0xb
            or (group/'memory.max').read_text().strip()!=str(1024**3)
            or (group/'pids.max').read_text().strip()!='64'
            or not quota.isdigit() or int(quota)!=int(period)
            or not os.statvfs('/').f_flag & os.ST_RDONLY):
        raise RuntimeError('Backup helper protections or effective limits are unavailable')
    mounts = {line.split()[4] for line in (PROC/'self/mountinfo').read_text().splitlines()}
    if not {str(home), str(projects)} <= mounts:
        raise ValueError('Mount the two separate named volumes for the backup helper')
    return home, projects


def container_owned(instance: str, *, timeout: int = 30) -> dict:
    unit = container_unit(instance)
    value = json.loads(container_command(["inspect", instance], timeout=timeout))[0]
    labels = value.get("Config", {}).get("Labels") or {}
    host = value.get("HostConfig") or {}
    parent = labels.get("io.altitude.cgroup-parent", "")
    if (labels.get("io.altitude.container") != "1" or labels.get("io.altitude.unit") != unit
            or host.get("CgroupManager") != "cgroupfs" or host.get("CgroupParent") != parent
            or not parent.endswith("/app.slice/" + unit)):
        raise RuntimeError("This instance does not belong to the delegated cgroupfs launcher; it is not adopted")
    return value


def container_stopped(value: dict) -> bool:
    """Running=false also describes Stopping; require a settled no-process state (#543)."""
    state=value.get('State',{})
    return (state.get('Status') in ('configured','created','exited','stopped')
            and not state.get('Running') and not state.get('Pid'))


def container_recreatable(value: dict) -> None:
    """Explicit forced-record removal needs kernel-empty ownership, not stale PID guesses."""
    unit=value['Config']['Labels']['io.altitude.unit']
    if job_active(unit,container_user_environment()):
        raise RuntimeError('Stop the container service before recreation')
    parent=CONTAINER_CGROUP_ROOT/value['HostConfig']['CgroupParent'].lstrip('/')
    try:
        events=dict(line.split() for line in (parent/'cgroup.events').read_text().splitlines())
    except FileNotFoundError:
        if parent.exists(): raise RuntimeError('Cannot prove the owned cgroup is empty')
    else:
        if events.get('populated')!='0':
            raise RuntimeError('Owned container processes survive; recreation refused')


def _container_after_stop(instance: str) -> None:
    """Reap through the runtime even if the supervisor died before Stop (#543).

    A failed create has no container. Existing records must match the exact unit,
    manager and parent before cleanup; there is no shared-store or name-pattern removal.
    """
    container_unit(instance)
    deadline = time.monotonic() + 40  # less than ExecStopPost's45-second bound
    def call(arguments, timeout=30):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Container stop cleanup exhausted its budget; retain the instance for recovery")
        return container_command(arguments, timeout=min(timeout, remaining))
    rows = json.loads(call(["ps", "--all", "--format", "json"], timeout=5))
    if not any(instance in row.get("Names", []) for row in rows):
        return
    value = container_owned(instance, timeout=5)
    if not container_stopped(value):
        call(["stop", "--time=30", value["Id"]], timeout=35)
    call(["container", "cleanup", value["Id"]], timeout=8)
    if not container_stopped(container_owned(instance,timeout=3)):
        raise RuntimeError('Runtime did not settle after forced stop; retain evidence and recreate from the stopped volume pair')


def _container_supervise(instance: str, create: list[str] | None) -> None:
    """Translate user-service termination to the image's stop signal before forced unit cleanup."""
    unit = container_unit(instance)
    parent = container_parent(unit)
    stopping = threading.Event()
    previous = {number: signals.signal(number, lambda *_: stopping.set())
                for number in (signals.SIGTERM, signals.SIGINT, signals.SIGHUP)}
    ident = None
    child = None
    stop_attempted = False
    admission = ExitStack()
    try:
        if create is not None:
            container_command(["create", "--cgroup-parent", parent,
                "--label", f"io.altitude.unit={unit}", "--label", f"io.altitude.cgroup-parent={parent}",
                *create], timeout=45)
        value = container_owned(instance)
        if value["HostConfig"]["CgroupParent"] != parent:
            raise RuntimeError("The stopped instance's delegated parent changed; do not recreate it implicitly")
        ident = value["Id"]
        labels = value.get("Config", {}).get("Labels") or {}
        lineage = labels.get("io.altitude.lineage", "")
        admission.enter_context(container_lineage_lock(lineage))
        container_copy_available(lineage, [m["Name"] for m in value.get("Mounts", []) if m.get("Type") == "volume"], except_id=ident)
        if not stopping.is_set():
            child = subprocess.Popen(container_arguments(["start", "--attach", ident]),
                                     env=container_user_environment())
            while child.poll() is None and not stopping.wait(.2):
                if admission is not None and container_owned(instance, timeout=3)["State"]["Running"]:
                    admission.close()
                    admission = None
        if stopping.is_set():
            stop_attempted = True
            # Start --attach is asynchronous. A stop while the container is
            # still Created would be a no-op followed by an unobserved launch.
            deadline = time.monotonic() + 3
            while child is not None and child.poll() is None:
                value = container_owned(instance, timeout=3)
                if value["State"]["Running"]:
                    break
                if time.monotonic() >= deadline:
                    child.terminate()
                    raise RuntimeError("Stop interrupted container startup; forced unit cleanup required")
                time.sleep(.1)
            value = container_owned(instance, timeout=3)
            if value["State"]["Running"]:
                container_command(["stop", "--time=30", ident], timeout=35)
        if child is not None and child.wait(timeout=8) and not stopping.is_set():
            raise RuntimeError("The attached container exited unsuccessfully")
    finally:
        if admission is not None:
            admission.close()
        # A failed launch retains the exact stopped instance for diagnosis/restart.
        # The unit's mixed KillMode supplies the final bounded descendant cleanup.
        if ident and not stop_attempted:
            value = container_owned(instance)
            if value["Id"] == ident and value["State"]["Running"]:
                container_command(["stop", "--time=30", ident], timeout=35)
                if child is not None:
                    child.wait(timeout=8)
        for number, handler in previous.items():
            signals.signal(number, handler)


def container_limits(value: dict) -> None:
    """A requested quota is not an enforced quota: inspect the actual running cgroup (#543)."""
    pid = int(value.get("State", {}).get("Pid") or 0)
    if pid <= 0:
        raise RuntimeError("Container PID is unavailable")
    membership = next(line[3:] for line in (PROC / str(pid) / "cgroup").read_text().splitlines()
                      if line.startswith("0::"))
    parent = value["HostConfig"]["CgroupParent"]
    container_group = parent + "/libpod-" + value["Id"]
    if membership != container_group and not membership.startswith(container_group + "/"):
        raise RuntimeError("The container is outside its owned delegated subtree")
    conmon = int(Path(value["ConmonPidFile"]).read_text().strip())
    conmon_group = next(line[3:] for line in (PROC / str(conmon) / "cgroup").read_text().splitlines()
                        if line.startswith("0::"))
    if not conmon_group.startswith(parent + "/"):
        raise RuntimeError("The container monitor is outside its owned delegated subtree")
    directory = CONTAINER_CGROUP_ROOT / container_group.lstrip("/")
    memory = (directory / "memory.max").read_text().strip()
    pids = (directory / "pids.max").read_text().strip()
    quota, period = (directory / "cpu.max").read_text().split()
    if memory != str(4 * 1024**3) or pids != "1024" or not quota.isdigit() or int(quota) != 2 * int(period):
        raise RuntimeError("The deployment CPU, memory or PID limit is not enforced")


def container_launch(instance: str, create: list[str] | None = None) -> str:
    """Prove identity, limits, inner HTTPS and the published host endpoint (#543)."""
    unit = container_unit(instance)
    if create is None:
        value = container_owned(instance)
        if value["State"]["Running"]:
            raise RuntimeError("The container is already running")
    command = [sys.executable, "-c", "import json,sys; from altitude.platform import _container_supervise; "
               "_container_supervise(sys.argv[1],json.loads(sys.argv[2]))", instance, json.dumps(create)]
    cleanup = [sys.executable, "-c", "import sys;from altitude.platform import _container_after_stop;"
               "_container_after_stop(sys.argv[1])", instance]
    container_job(unit, command, after_stop=cleanup)
    deadline = time.monotonic() + 90
    error = "Container startup did not finish"
    environment = container_user_environment()
    try:
        while time.monotonic() < deadline:
            if not job_active(unit, environment):
                raise RuntimeError("The container supervisor exited before readiness")
            try:
                value = container_owned(instance)
                if value["State"]["Running"]:
                    container_limits(value)
                    facts = json.loads(container_command(["exec", "--user", "1000:1000", "--env", "HOME=/home/altitude",
                        "--env", "XDG_RUNTIME_DIR=/run/user/1000", value["Id"], "python3", "-c",
                        "import sys;sys.path.insert(0,'/opt/altitude');"
                        "from altitude.platform import container_ready;container_ready();"
                        "from altitude import tls;import json;f=tls.service();"
                        "print(json.dumps({'pid':f['pid'],'port':f['port'],'host':f['public_host'],"
                        "'ca':(f['tls_dir']/'ca.crt').read_text()}))"], timeout=5))
                    bindings = value["HostConfig"].get("PortBindings") or {}
                    addresses = bindings.get(f"{facts['port']}/tcp", [])
                    if len(addresses) != 1 or int(addresses[0]["HostPort"]) != facts["port"]:
                        raise RuntimeError("The HTTPS service does not match its single published port")
                    expected = dict(item.split("=",1) for item in value["Config"].get("Env",[]) if "=" in item)
                    if (facts["host"] != expected.get("ALTITUDE_PUBLIC_HOST")
                            or str(facts["port"]) != expected.get("ALTITUDE_PORT")
                            or addresses[0]["HostIp"] != value["Config"]["Labels"].get("io.altitude.bind")):
                        raise RuntimeError("Published HTTPS differs from the requested host identity or bind address")
                    container_https(addresses[0]["HostIp"], facts)
                    return value["Id"]
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                error = str(exc)
            time.sleep(.25)
        raise RuntimeError(error)
    except Exception as exc:
        try:
            container_stop_unit(unit, environment)
            suffix = ""
        except RuntimeError as cleanup:
            suffix = f"; cleanup not confirmed: {cleanup}"
        raise RuntimeError(f"Container startup failed: {exc}; {job_logs_hint(unit)}{suffix}") from exc


def container_https(address: str, facts: dict) -> None:
    """Verify publication using this instance's public CA, advertised SNI and recorded PID.

    Connect directly to the chosen host address: no proxy, DNS substitution, new
    trust-store entry or private-key export is involved. Device routing/trust is
    separate acceptance; this proves only the local published endpoint.
    """
    import http.client
    import ssl

    target = ipaddress.ip_address(address)
    if target.is_unspecified or target.is_multicast:
        raise RuntimeError("The container needs one concrete published host address")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=facts["ca"])
    with socket.create_connection((str(target), facts["port"]), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=facts["host"]) as connection:
            host = facts["host"]
            authority = f"[{host}]" if ":" in host else host
            connection.sendall((f"GET /api/health HTTP/1.1\r\nHost: {authority}:{facts['port']}\r\n"
                                "Connection: close\r\n\r\n").encode("ascii"))
            response = http.client.HTTPResponse(connection)
            response.begin()
            body = response.read(65537)
            if response.status != 200 or len(body) > 65536:
                raise RuntimeError("Published HTTPS did not return a bounded successful health response")
            health = json.loads(body)
            if not isinstance(health, dict) or health.get("pid") != facts["pid"]:
                raise RuntimeError("Published HTTPS answers from another process")


def container_stop_unit(unit: str, environment: dict) -> None:
    if not job_active(unit, environment):
        return
    try:
        response = subprocess.run(["systemctl", "--user", "stop", unit], env=environment,
                                  text=True, capture_output=True, timeout=CONTAINER_STOP_WAIT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Container stop timed out; cleanup unconfirmed; {job_logs_hint(unit)}") from exc
    if response.returncode and job_active(unit, environment):
        raise RuntimeError(f"Container stop failed: {response.stderr.strip()}; {job_logs_hint(unit)}")


def container_stop(instance: str) -> None:
    value = container_owned(instance)
    unit = container_unit(instance)
    environment = container_user_environment()
    if job_active(unit, environment):
        container_stop_unit(unit, environment)
    elif not container_stopped(value):
        raise RuntimeError("Container has no active supervisor; retain it for recovery, do not report stopped")
    if not container_stopped(container_owned(instance)) or job_active(unit, environment):
        raise RuntimeError("Container or supervisor remains active after Stop")


def container_arguments(arguments: list[str]) -> list[str]:
    return ["podman", "--remote=false", "--cgroup-manager=cgroupfs", "--runtime=/usr/bin/crun", *arguments]


def container_command(arguments: list[str], *, timeout: int = 30, interactive: bool = False,
                      runtime_dir: Path | None = None, storage_conf: Path | None = None) -> str:
    """Only the local rootless controller; callers select exact task/image/volume resources."""
    if os.getuid() == 0:
        raise RuntimeError("Run Podman as your ordinary Linux account")
    if os.environ.get("CONTAINER_HOST") or os.environ.get("CONTAINER_CONNECTION"):
        raise RuntimeError("Remote Podman endpoints are not supported by this Linux launcher")
    environment = container_user_environment(runtime_dir) if runtime_dir else container_user_environment()
    if storage_conf is not None:
        environment['CONTAINERS_STORAGE_CONF'] = str(storage_conf)
    result = subprocess.run(container_arguments(arguments), text=True,
                            capture_output=not interactive, timeout=timeout, env=environment)
    if result.returncode:
        error = RuntimeError(f"Podman {arguments[0]} failed ({result.returncode}): "
                             f"{(result.stderr or '').strip()[-2000:]}")
        error.result = result
        raise error
    return result.stdout or ""


def require_supported() -> None:
    """Refuse a system the service cannot run on: neither Linux nor macOS 15 or newer. Any architecture passes; the
    features that need one say so when used, and install.sh names a host releases are not yet validated on."""
    if sys.platform == "linux" or _darwin() and int(host_platform.mac_ver()[0].split(".")[0] or 0) >= 15:
        return
    raise RuntimeError("Altitude runs on Linux with a systemd user manager or on macOS 15 or newer.")


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


def service_label() -> str:
    """The LaunchAgent label: LABEL for the account's own home. launchd's labels are per account, not per home, so an
    installation under any other HOME (the macOS installation lane's throwaway one) gets a label of its own and can
    never stop or replace the account's service."""
    home = Path.home()
    if home.resolve() == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve():
        return LABEL
    return f"{LABEL}.{hashlib.sha256(str(home).encode()).hexdigest()[:12]}"


def service_path() -> Path:
    if _darwin():
        return Path.home() / "Library/LaunchAgents" / f"{service_label()}.plist"
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
        # launchd starts an agent in the account's home; the service keeps the home it was installed from, where its
        # definition, logs, launcher and label live.
        log = str(logs_dir() / "altd.log")
        return plistlib.dumps({
            "Label": service_label(), "ProgramArguments": [str(python), "-B", str(prefix / "current/bin/alt"), "serve"],
            "WorkingDirectory": str(prefix),
            "EnvironmentVariables": {"ALTITUDE_CONFIG": str(settings), **environment, "HOME": str(Path.home()),
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


def job_control_paths() -> tuple[Path, ...]:
    """Deny the session bus and manager runtime, including its direct private socket.

    Issue #543: native file masks reuse a descriptor consumed by the first bind-data operation.
    Masking the manager directory uses a directory mount and keeps both control sockets denied.
    """
    runtime = Path(f"/run/user/{os.getuid()}")
    return runtime / "bus", runtime / "systemd"


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
    coalition). A name with `*` stops every job it matches. Callers confirm with `job_active`."""
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


def _process_boot() -> str:
    if _darwin():
        # Public read-only kernel identity; reading root-owned launchd via libproc
        # can require privileges the ordinary application account does not have.
        value = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                               capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        return "darwin:" + str(uuid.UUID(value))
    return (PROC / "sys/kernel/random/boot_id").read_text().strip()


def process_identity(pid: int) -> dict:
    """A process lifetime within its boot and PID namespace, including container recreation."""
    start = process_start(pid)
    identity = {"pid": pid, "start": start,
                "boot": _process_boot(),
                "namespace": "darwin" if _darwin() else os.readlink(PROC / str(pid) / "ns/pid")}
    if process_start(pid) != start:
        raise ProcessLookupError("Process changed while recording its identity")
    return identity


def process_identity_live(identity: object) -> bool:
    """Unidentified/ended owners are stale; inaccessible process evidence remains an error."""
    if not isinstance(identity, dict) or type(identity.get("pid")) is not int or identity["pid"] <= 0:
        return False
    if any(not isinstance(identity.get(key), str) or not identity[key] for key in ("start", "boot", "namespace")):
        return False
    try:
        # Issue #543: a recycled PID can belong to another UID whose namespace is unreadable.
        # Disprove ownership from public lifetime facts before asking for that protected evidence.
        if (_process_boot() != identity["boot"]
                or process_start(identity["pid"]) != identity["start"]):
            return False
        return process_identity(identity["pid"]) == identity and process_running(identity["pid"], identity["start"]) is True
    except (FileNotFoundError, ProcessLookupError):
        return False


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


def session_processes(leader: int) -> list[int]:
    """Live members of a POSIX session, without executing a privileged process-table tool.

    Session membership does not cover descendants that leave it; job cleanup uses the service manager.
    """
    found = []
    pids = _pids() if _darwin() else [int(entry.name) for entry in PROC.iterdir() if entry.name.isdigit()]
    for pid in pids:
        try:
            if _darwin():
                try:
                    session = os.getsid(pid)
                except PermissionError:
                    continue  # No evidence this inaccessible process belongs to the session.
                member = session == leader and _bsd(pid).status != SZOMB
            else:
                fields = _stat(pid)
                member = int(fields[3]) == leader and fields[0] != "Z"
            if member:
                found.append(pid)
        except ProcessLookupError:
            continue
        except FileNotFoundError:
            continue  # Exited between enumeration and inspection.
    return found


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
XCODE_SELECT = "/usr/bin/xcode-select"
#: Seatbelt rules shared by every Altitude profile. Two per-user services start programs as the operator's account,
#: outside the caller's sandbox: Apple's Simulator service, whose devices run what any client asks (its helpers and
#: each booted device share the prefix), and LaunchServices, which opens any app.
SERVICE_ESCAPES = ('(deny mach-lookup (global-name-prefix "com.apple.CoreSimulator.")'
                   ' (xpc-service-name-prefix "com.apple.CoreSimulator."))(deny lsopen)')
CAFFEINATE = "/usr/bin/caffeinate"
#: launchctl's status when the domain has no such job.
NOT_FOUND = 113


def logs_dir() -> Path:
    return Path.home() / "Library/Logs/altitude"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _print(label: str, *, timeout: float = 30) -> dict | None:
    """launchd's view of a job in the user's domain: its top-level fields and its resource coalition, or None when
    the domain has no such job. Anything else unreadable raises RuntimeError."""
    try:
        p = subprocess.run([LAUNCHCTL, "print", f"{_domain()}/{label}"], capture_output=True, text=True, timeout=timeout)
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
    path, label = service_path(), service_label()
    job = _print(label)
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
            "UnitFileState": "disabled" if label in disabled else "enabled" if path.exists() else ""}


def _launchd_control(action: str) -> str:
    """start, stop and restart the service. launchd reads the definition when the service is bootstrapped, so
    reload has nothing to do and restart bootstraps it again."""
    label = service_label()
    target = f"{_domain()}/{label}"
    if action == "reload":
        return ""
    if action in ("enable", "disable"):
        return run(LAUNCHCTL, action, target)
    if action not in ("start", "stop", "restart"):
        raise ValueError("Unknown application service operation")
    job = _print(label)
    if job and action == "start":
        return run(LAUNCHCTL, "kickstart", target)
    if job:
        # bootout ends the main process group; like KillMode=control-group, nothing else the service started stays.
        run(LAUNCHCTL, "bootout", target)
        if job["coalition"] and not _confirm_stopped(job["coalition"]):
            raise RuntimeError("Native user service failed: processes the service started are still running")
        # I-20260929-182608: ended processes do not establish that launchd removed the label for bootstrap.
        deadline = time.monotonic() + 45
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            removed = _print(label, timeout=min(30, remaining)) is None
            remaining = deadline - time.monotonic()
            if removed or remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
        if remaining <= 0:
            raise RuntimeError("Native user service failed: service label was not removed within 45s")
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
        job = _print(service_label() if unit in ("altitude", SERVICE) else _label(unit))
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
# supervisor its input and output: a regular file or device by path, as systemd-run --pipe passes the descriptor, output
# pipes through FIFOs the launcher relays, and piped input as a private copy the supervisor removes once it has read
# it and pipes to the command.

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
    every sandboxed process, and the profile refuses the services that would start a program outside it."""
    user = str(Path(os.path.realpath(_user_temp())).parent)
    rules = []
    for root in dict.fromkeys((*writable, user, "/private/tmp", "/private/var/tmp", "/dev")):
        if root.endswith("*"):
            prefix = os.path.join(os.path.realpath(os.path.dirname(root)), os.path.basename(root)[:-1])
            rules.append('(regex #"^' + re.escape(prefix).replace('"', '\\"') + '")')
        else:
            rules.append('(subpath "' + os.path.realpath(root).replace("\\", "\\\\").replace('"', '\\"') + '")')
    return ("(version 1)(allow default)(deny signal)(allow signal (target same-sandbox))"
            f"(deny file-write*)(allow file-write* {' '.join(rules)}){SERVICE_ESCAPES}")


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
                spec.update(stdin=str(job / "stdin"), piped=True)
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
        if spec.get("piped"):
            streams = [_piped(Path(spec["stdin"]))]
        else:  # a terminal's shell holds its pseudo-terminal read-write on all three descriptors, as it would anywhere
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


def _piped(saved: Path) -> int:
    """The read end of a pipe that carries the launcher's saved copy of its piped input, which is removed once read.

    The command reads a pipe, as under systemd-run --pipe: a line its first reader takes is gone, whereas a reader of
    a file can start again from its beginning (a worker's GitHub token reached the engine as prompt text that way)."""
    data = saved.read_bytes()
    saved.unlink()
    read_end, write_end = os.pipe()

    def feed() -> None:
        try:
            with os.fdopen(write_end, "wb") as stream:
                stream.write(data)
        except OSError:  # the command ended, or never started, without reading all of it
            pass

    threading.Thread(target=feed, daemon=True).start()
    return read_end


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
    if "*" in name:  # every job whose record matches, as systemctl stops every unit a pattern matches
        for path in _jobs().glob(_label(name)):
            _launchd_job_stop(path.name.removeprefix("dev.altitude.job.") + ".service", timeout)
        return
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
    """Why this host cannot run validation, or None when it can: rootless Podman containers on Linux x86_64, the
    validation Seatbelt profile on a supported Mac."""
    if containerized():
        return "validation runs are unavailable inside this container image"
    if _darwin():
        return None if host_platform.machine() == "arm64" else "validation runs need Apple silicon on macOS"
    if sys.platform != "linux" or host_platform.machine() not in ("x86_64", "AMD64"):
        return "validation runs need Linux x86_64 or macOS on Apple silicon"
    missing = [tool for tool in ("podman", "slirp4netns") if not shutil.which(tool)]
    return f"validation runs need {' and '.join(missing)} on this computer" if missing else None


def validation_temp(run: str) -> Path:
    """A macOS validation run's own temporary folder: short, so Unix sockets under it stay within the 104-byte
    limit, and the only place in the shared temporary folders the run's profile admits."""
    return Path("/private/tmp") / f"av-{run[:8]}"


def validation_path(path: list[str]) -> list[str]:
    """A macOS validation run's PATH: `path` with the active developer directory's tools just ahead of /usr/bin.
    /usr/bin's git and python3 are xcrun shims that try to write xcrun's lookup cache, which the run's profile keeps
    read-only, and report that as an `error:` line on the tool's own stderr; earlier entries, such as a Homebrew
    python3, still come first."""
    try:
        developer = subprocess.run([XCODE_SELECT, "-p"], capture_output=True, text=True, timeout=5,
                                   check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return path
    tools = Path(developer) / "usr" / "bin"
    if (not tools.is_absolute() or not tools.is_dir() or str(tools) in path
            or Path(os.path.realpath(tools)).is_relative_to(os.path.realpath(Path.home()))):  # the profile hides it
        return path
    at = path.index("/usr/bin") if "/usr/bin" in path else len(path)
    return [*path[:at], str(tools), *path[at:]]


def validation_in_container() -> bool:
    """Whether a validation run is a rootless Podman container (Linux) rather than a process under the validation
    Seatbelt profile (macOS, where Podman would need a virtual machine of its own)."""
    return not _darwin()


def directory_search_access() -> int:
    """Look up names without reading hidden ancestors on Mac (#625); keep Linux directory access."""
    return os.O_SEARCH if _darwin() else os.O_RDONLY


def validation_browser_environment(temp: Path) -> dict[str, str]:
    """Chromium's supported hermetic temp override on Mac (#625), inside the run's existing write root."""
    return {"MAC_CHROMIUM_TMPDIR": str(temp)} if _darwin() else {}


def validation_browser_probe(work: Path) -> dict:
    """Finite in-profile controls for #625's fictional browser lane; never apply a nested profile.

    Directory opens read no private contents, scratch creation never replaces an existing file,
    and signal zero never changes another process. Unavailable controls remain missing evidence.
    """
    import pwd
    checks = []
    def denied(name, operation):
        try:
            value = operation()
        except PermissionError as exc:
            checks.append({"name": name, "passed": True, "errno": exc.errno})
        except OSError as exc:
            checks.append({"name": name, "passed": False, "unavailable_errno": exc.errno})
        else:
            checks.append({"name": name, "passed": False, "unexpected_allow": True})
            return value

    def directory(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        os.close(fd)

    denied("operator-home-directory-open", lambda: directory(pwd.getpwuid(os.getuid()).pw_dir))
    denied("shared-temp-directory-open", lambda: directory("/private/tmp"))
    scratch = work.parent / ("browser-probe-" + uuid.uuid4().hex)
    fd = denied("outside-candidate-root-create", lambda: os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    if fd is not None:
        try:
            os.close(fd)
            scratch.unlink()
        except OSError as exc:
            # Creation already succeeded: failed cleanup cannot retroactively prove a write denial.
            checks[-1]["cleanup_error"] = str(exc)
    with tempfile.TemporaryDirectory(prefix="browser-probe-", dir=os.environ["TMPDIR"]) as own:
        path = Path(own) / "owned"
        with path.open("wb") as stream:
            stream.write(b"fictional control"); stream.flush(); os.fsync(stream.fileno())
        checks.append({"name": "owned-read-write", "passed": path.read_bytes() == b"fictional control"})
        path.unlink()
        address = str(Path(own) / "socket")
        with socket.socket(socket.AF_UNIX) as server, socket.socket(socket.AF_UNIX) as client:
            server.settimeout(2); client.settimeout(2)
            server.bind(address); server.listen(1); client.connect(address)
            with server.accept()[0] as peer:
                peer.settimeout(2); client.sendall(b"fictional"); data = peer.recv(16)
            checks.append({"name": "owned-unix-socket", "passed": data == b"fictional"})
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        os.kill(child.pid, 0)
        child.terminate(); child.wait(timeout=3)
        checks.append({"name": "owned-child-signal-termination", "passed": child.returncode is not None})
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=3)
    identity = _bsd(os.getpid())
    parent = identity.ppid
    ancestor = None
    for _ in range(16):
        if parent <= 1:
            break
        info = _bsd(parent)
        if info.uid == os.getuid():
            try:
                os.kill(parent, 0)
            except PermissionError as exc:
                same = _started(_bsd(parent)) == _started(info)
                ancestor = {"pid": parent, "start": _started(info), "coalition": _coalition_of(parent)}
                checks.append({"name": "same-uid-ancestor-signal-zero", "passed": same, "errno": exc.errno})
                break
        parent = info.ppid
    if ancestor is None:
        checks.append({"name": "same-uid-ancestor-signal-zero", "passed": False, "unavailable": True})
    return {"passed": all(row["passed"] for row in checks), "checks": checks,
            "process": {"pid": os.getpid(), "start": _started(identity), "coalition": _coalition_of(os.getpid())},
            "denied_ancestor": ancestor, "roots": {"work": str(work), "home": os.environ["HOME"],
                                                 "temp": os.environ["TMPDIR"], "results": os.environ["VALIDATION_RESULTS"]},
            "missing": ["unrelated owned Unix-socket fixture", "keychain/service denial controls",
                        "protected supervisor identity/denial corroboration", "Mach registration/lookup and shared-peer exposure",
                        "native worker metadata enforcement"]}


def validation_profile(roots: tuple[Path, ...], output: Path, port: int) -> str:
    """The Seatbelt profile of a macOS validation run, stricter than a worker's. The run may write and read only its
    own `roots` (clone, results, home and temporary folder) and devices, and append to its runner's `output` log
    through the descriptor it inherits (Seatbelt checks that descriptor's writes and status by path too); read nothing else in the operator's home or
    the shared temporary folders, where Altitude's records, credentials, checkouts, caches and other processes' files
    and sockets live; reach Unix sockets only in its roots and the system's DNS and log services, and nothing on
    Altitude's `port`, on any address; ask nothing of the keychain, the Simulator service or LaunchServices; and signal
    only its own processes. launchd refuses service control to every sandboxed process, so the run cannot start, stop
    or change a service. It reads, never writes, xcrun's lookup cache: without it every call to a /usr/bin xcrun shim
    takes over a second."""
    def filters(kind: str, values) -> list[str]:
        return [f'({kind} "' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '")' for v in values]

    def paths(kind: str, values) -> str:
        return " ".join(filters(kind, values))
    home = os.path.realpath(Path.home())
    # A root's own name is never resolved: a worker may replace a folder in /private/tmp with a link, and Seatbelt
    # matches the link's target, which no root then admits.
    own = list(dict.fromkeys(os.path.join(os.path.realpath(Path(root).parent), Path(root).name) for root in roots))
    shared = list(dict.fromkeys(os.path.realpath(path) for path in (
        Path(_user_temp()).parent, "/private/tmp", "/private/var/tmp")))
    hidden = [home, *shared]
    temp = os.path.realpath(_user_temp())
    ancestors = list(dict.fromkeys([*shared, *(str(parent) for root in own for parent in Path(root).parents
                                               if any(parent.is_relative_to(path) for path in hidden))]))
    return "".join([
        "(version 1)(allow default)(deny signal)(allow signal (target same-sandbox))",
        f'(deny file-write*)(allow file-write* {paths("subpath", own)} (subpath "/dev"))',
        f"(deny file-read* {paths('subpath', hidden)})(allow file-read* {paths('subpath', own)})",
        f"(allow file-read-metadata {paths('literal', [*ancestors, temp])})",
        f"(allow file-read* {paths('literal', [os.path.join(temp, 'xcrun_db')])})",
        f"(allow file-write-data file-read-metadata {paths('literal', [os.path.realpath(output)])})",
        f'(deny network-bind (local ip "*:{port}"))(deny network-outbound (remote ip "*:{port}"))',
        # Seatbelt honors only the first path filter of a (remote unix-socket ...), so each path has its own (#695).
        "(deny network-outbound (remote unix-socket))(allow network-outbound " + " ".join(
            f"(remote unix-socket {path})" for path in [*filters("subpath", own), *filters(
                "path-literal", ("/private/var/run/mDNSResponder", "/private/var/run/syslog"))]) + ")",
        '(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd.xpc"))',
        SERVICE_ESCAPES])


def validation_command(roots: tuple[Path, ...], output: Path, port: int, argv: list[str],
                       env: dict[str, str]) -> list[str]:
    """`argv` under the validation profile with exactly `env`: nothing of altd's own environment crosses."""
    return [SANDBOX_EXEC, "-p", validation_profile(roots, output, port), ENV_BIN, "-i",
            *(f"{key}={env[key]}" for key in sorted(env)), *argv]


def _os_name() -> str:
    if _darwin():
        return f"macOS {host_platform.mac_ver()[0]}"
    try:
        return host_platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except OSError:
        return ""


def host_identity() -> str:
    """The OS, its version and the architecture, as validation evidence names the host."""
    if _darwin():
        return f"{_os_name()} {host_platform.machine()}"
    return " ".join(filter(None, (_os_name(), f"Linux {host_platform.release()}", host_platform.machine())))


DMI = Path("/sys/class/dmi/id")
_DMI_PLACEHOLDER = re.compile(r"default string|to be filled|o\.e\.m\.|system product name|not specified|^none$", re.I)


def _dmi(name: str) -> str:
    try:
        value = (DMI / name).read_text(errors="replace").strip()
    except OSError:
        return ""
    return "" if _DMI_PLACEHOLDER.search(value) else value


def _machine_model() -> str:
    """The Mac model identifier, or the DMI vendor with its product family (product name when the family is
    absent or a placeholder); never a serial number or hardware UUID."""
    if _darwin():
        try:
            return subprocess.run(["/usr/sbin/sysctl", "-n", "hw.model"], capture_output=True, text=True,
                                  check=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    return " ".join(filter(None, (_dmi("sys_vendor"), _dmi("product_family") or _dmi("product_name"))))


def host_facts() -> dict[str, str]:
    """What an incident says about this machine. Only these facts are read: host names, user names, home
    paths, addresses, serial numbers and hardware UUIDs are never collected."""
    facts = {"platform": "macOS" if _darwin() else "Linux", "os": _os_name(),
             "kernel": f"{'Darwin' if _darwin() else 'Linux'} {host_platform.release()}",
             "architecture": host_platform.machine(), "model": _machine_model()}
    return {key: " ".join(value.replace(";", ",").split())[:80] or "unknown" for key, value in facts.items()}


def local_names() -> dict[str, set[str]]:
    """This machine's host names and account name, which public text never carries. A container's host and
    account names are the image's and launcher's (`altitude`), not the machine's, so it has none; a name that is a
    word of the OS name (a cloud image's `ubuntu`) is that image's default and names no machine either."""
    if containerized():
        return {"host": set(), "user": set()}
    host = socket.gethostname()
    try:
        user = {pwd.getpwuid(os.getuid()).pw_name}
    except KeyError:
        user = set()
    generic = {"", "localhost", *_os_name().lower().split()}
    return {"host": {name for name in (host, host.split(".")[0]) if name.lower() not in generic},
            "user": {name for name in user if name.lower() not in generic}}


def job_confinement(*, profile: bool) -> str:
    """The job a worker runs in; `profile` is whether Altitude's own Seatbelt profile wraps it on macOS."""
    if _darwin():
        return "launchd job with Altitude's Seatbelt profile" if profile else "launchd job"
    return "systemd user unit in the Altitude container" if containerized() else "systemd user unit"


def validation_runroot() -> str:
    """Podman's runtime folder for the runner, in the user's runtime directory: Podman limits its length."""
    return f"/run/user/{os.getuid()}/altitude-validation"


# --- Host speech -------------------------------------------------------------------------------------------------

def speech_runtime() -> tuple[str | None, str]:
    """The pinned speech runtime, or an explicit deployment/platform limitation."""
    unavailable = container_unavailable("Voice")
    if unavailable:
        return None, unavailable
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
