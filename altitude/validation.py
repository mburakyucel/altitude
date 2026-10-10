"""The validation runner: a task owner's command against a throwaway clone of its committed HEAD, isolated from
the operator's runtime, with no grant per run.

On Linux the command runs in a disposable rootless container. altd builds the only image from its own deployed source
(`scripts/validation.Containerfile`). The owner chooses the command, whether /dev/kvm is added and one container port
published on 127.0.0.1. Everything else is fixed here: a non-root user mapped to the operator's account, no host mounts
except the run's clone and results folder, rootless networking with host loopback closed, and one service unit whose
limits bound the build, Podman and the container together. On macOS, where Podman would need a virtual machine of its
own, the command runs as a job under the platform's validation Seatbelt profile, with a home and temporary folder in
its run area and nothing of altd's environment. A macOS run can also have a disposable iOS Simulator iPhone, which
altd creates and removes and the run reaches only through a relay to its Safari and two fixed native walks
(`simulator`).
The runner's storage sits beside Altitude's home, outside every worker's writable roots, and what a run produces
reaches the task folder only through no-follow descriptors. Runs are recorded in the task's `machine.jsonl` like
machine commands.
One run uses the machine at a time: a request that finds it busy waits its turn in arrival order, and a request whose
client goes away, or whose task sends a newer request, leaves the line or has its run stopped.
The operator's switch (on unless turned off) stops running runs and refuses new ones; its state lives in the runner's
storage, where a worker cannot turn it back on.
See docs/DEVELOPMENT.md#validation-runner.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import threading
import uuid

from . import capture as C, config, engines, line, platform, simulator as sim, state as S, tasks as T

TIMEOUT = 3600                   # seconds for one run, image build included
WAIT = TIMEOUT + 600             # seconds a request waits in line for the machine: the client's budget for one run
WATCH_SECONDS = 2                # how often a waiting or admitted request checks that its client is still there
SIMULATOR_SECONDS = 900          # beside it, for a run's iPhone: creation, boot, screenshot and removal
MEMORY_MAX = "8G"                # the unit's memory, swap and process caps hold Podman and the container together
TASKS_MAX = 4096
CPU_QUOTA = "400%"
FREE_DISK = 20 << 30             # bytes free in Altitude's home before a run starts
RESULTS_LIMIT = 256 << 20        # bytes copied back into the task folder
COMMAND_LIMIT = 16384
USER = "1000:1000"               # the image's `ubuntu` user, mapped to the operator's own account (keep-id)
IMAGE_CACHE = "/home/ubuntu/.cache/altitude-installation-vm"

OFF = "alt task validate: the operator has turned validation runs off in Settings"
RESTARTING = ("alt task validate: Altitude is activating merged changes; new runs start once it has restarted "
              "(`alt repo` shows the pending activation)")

UNIT_PREFIX = "altitude-validation-"   # every unit the runner starts; its rows are recorded by reconcile()

LOG = logging.getLogger(__name__)
LINE = line.Line(1, "the validation slot")   # one run on the machine at a time, and removal of earlier runs' areas before any
_state = threading.Lock()        # orders admission against turning the runner off
_ready = threading.Event()       # no earlier run's area remains; until then each request retries their removal
_active: dict = {}               # the admitted run's area and unit, and how it was stopped, if it was
_holder: str | None = None       # the task whose admitted run holds activation, from admission through cleanup
_latest: dict[str, dict] = {}    # each task's newest accepted request, which marks the one before it replaced


def enabled() -> bool:
    return not (home() / "off").exists()


def set_enabled(on: bool) -> None:
    """The operator's switch. Turning it off records that first and then stops the admitted run, so a request
    admitted after the record is refused and one admitted before it is stopped."""
    if on:
        (home() / "off").unlink(missing_ok=True)
        return
    home().mkdir(mode=0o700, parents=True, exist_ok=True)
    (home() / "off").touch()
    stop_all()


def home() -> Path:
    """Beside Altitude's home rather than in it: workers can write Altitude's home, and a path a worker replaced
    must not become a container mount or a place the unit writes."""
    return config.ROOT.with_name(config.ROOT.name + "-validation")


def containerfile() -> Path:
    """The deployed runner's Containerfile. Read at each run: the service moves `config.SOURCE` to the source it
    activates after this module is imported."""
    return config.SOURCE / "scripts" / "validation.Containerfile"


def image_tag() -> str:
    return "localhost/altitude-validation:" + hashlib.sha256(containerfile().read_bytes()).hexdigest()[:16]


def podman() -> list[str]:
    """Podman on the runner's own storage, apart from the operator's images and containers."""
    return ["podman", "--root", str(home() / "storage"), "--runroot", platform.validation_runroot(),
            "--tmpdir", str(home() / "tmp"), "--cgroup-manager=cgroupfs", "--events-backend=file"]


def host_port() -> int:
    """A free loopback port chosen by the kernel, never Altitude's own."""
    while True:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port != config.PORT:
            return port


def container_command(name: str, run: Path, argv: list[str], *, kvm: bool, publish: tuple[int, int] | None) -> list[str]:
    """The fixed `podman run`: nothing here comes from the owner except `argv`, /dev/kvm and the published port."""
    return [*podman(), "run", "--rm", "--pull=never", f"--name={name}", "--hostname=validation",
            "--cgroups=disabled", "--userns=keep-id:uid=1000,gid=1000", f"--user={USER}",
            "--network=slirp4netns:allow_host_loopback=false", "--device=/dev/fuse", "--device=/dev/net/tun",
            *(["--device=/dev/kvm"] if kvm else []),
            *([f"--publish=127.0.0.1:{publish[1]}:{publish[0]}"] if publish else []),
            f"--volume={run / 'work'}:/work", f"--volume={run / 'results'}:/results",
            f"--volume={home() / 'cache'}:{IMAGE_CACHE}:O", "--env=ALTITUDE_VALIDATION=1",
            "--env=VALIDATION_RESULTS=/results", "--workdir=/work",
            image_tag(), *argv]


def candidate_dirs(run: Path) -> list[Path]:
    """The folders of a run the candidate sees: its clone, results and, in a container, an empty mount point, or on
    macOS a home and a short temporary folder of its own. The area's other files (`run.json`, the delivery receipt,
    the unit's log and exit status) are the runner's, outside the candidate's reach."""
    own = [run / "empty"] if platform.validation_in_container() else [run / "home", platform.validation_temp(run.name)]
    return [run / "work", run / "results", *own]


def inspector(run: Path) -> Path:
    """The socket of a macOS run's Simulator relay, in the run's own temporary folder, which its profile admits."""
    return platform.validation_temp(run.name) / "simulator.sock"


def https(run: Path) -> Path:
    """The HTTPS identity a macOS run's Simulator iPhone trusts, in the run's own temporary folder: the CA's
    certificate and the server's certificate and key."""
    return platform.validation_temp(run.name) / "https"


def native_script(run: Path, argv: list[str], output: Path, *, simulator: bool = False) -> str:
    """macOS: the owner's command in the run's clone under the validation profile, which admits only the run's
    candidate folders. Its environment is only this: a home and temporary folder of its own, altd's PATH without
    folders the profile hides, with the developer tools ahead of /usr/bin, and its Simulator relay's socket and
    HTTPS identity."""
    hidden = os.path.realpath(Path.home())
    path = platform.validation_path([entry for entry in os.environ.get("PATH", "/usr/bin:/bin").split(":")
                                     if entry and not Path(os.path.realpath(entry)).is_relative_to(hidden)])
    env = {"HOME": str(run / "home"), "TMPDIR": str(platform.validation_temp(run.name)), "PATH": ":".join(path), "LANG": "en_US.UTF-8",
           "ALTITUDE_VALIDATION": "1", "VALIDATION_RESULTS": str(run / "results"),
           **({"SIMULATOR_INSPECTOR": str(inspector(run)), "SIMULATOR_HTTPS": str(https(run))} if simulator else {})}
    return "\n".join(["set -u", f"[ ! -e {shlex.quote(str(run / 'stopped'))} ] || exit 125",
                      f"cd {shlex.quote(str(run / 'work'))} || exit 125",
                      "exec " + shlex.join(platform.validation_command(
                          tuple(candidate_dirs(run)), output, config.PORT, argv, env))])


def isolation(run: Path, unit: str) -> str:
    """What isolated a run, for its record: the image tag, or the digest of the profile for a run area."""
    if platform.validation_in_container():
        return image_tag()
    profile = platform.validation_profile(tuple(candidate_dirs(run)), engines.machine_files(run, unit)[0],
                                          config.PORT)
    return "seatbelt:" + hashlib.sha256(profile.encode()).hexdigest()[:16]


def run_script(name: str, run: Path, argv: list[str], *, kvm: bool, publish: tuple[int, int] | None,
               simulator: bool = False) -> str:
    """The unit's shell script: build the image when its Containerfile changed, refresh the verified cloud image
    for a VM run with the deployed runner's own code, then run the owner's command."""
    if not platform.validation_in_container():
        return native_script(run, argv, engines.machine_files(run, f"{name}.service")[0], simulator=simulator)
    tag, pod = image_tag(), shlex.join(podman())
    lines = ["set -u", f"[ ! -e {shlex.quote(str(run / 'stopped'))} ] || exit 125", f"export XDG_RUNTIME_DIR={shlex.quote(str(Path(platform.validation_runroot()).parent))}",
             f"{pod} image exists {tag} || {pod} build --quiet --tag={tag} "
             f"--file={shlex.quote(str(containerfile()))} {shlex.quote(str(run / 'empty'))} >/dev/null || exit 125"]
    if kvm:
        prefetch = ("import sys; from pathlib import Path; sys.path.insert(0, '/runner'); "
                    "import installation_vm; installation_vm.base_image(Path('/cache'))")
        lines.append(f"{pod} run --rm --pull=never --cgroups=disabled --userns=keep-id:uid=1000,gid=1000 "
                     f"--user={USER} --network=slirp4netns:allow_host_loopback=false "
                     f"--volume={shlex.quote(str(config.SOURCE / 'scripts'))}:/runner:ro "
                     f"--volume={shlex.quote(str(home() / 'cache'))}:/cache {tag} python3 -c {shlex.quote(prefetch)} "
                     "|| { echo 'validation: the Ubuntu cloud image could not be refreshed'; exit 125; }")
    if publish:
        lines.append(f"echo {shlex.quote(f'validation: container port {publish[0]} is at 127.0.0.1:{publish[1]}')}")
    lines.append(f"exec {shlex.join(container_command(name, run, argv, kvm=kvm, publish=publish))}")
    return "\n".join(lines)


def cleanup_script(paths: list[Path]) -> str:
    """Remove every container on the runner's storage, the given paths (files a container wrote as another user
    are removed inside Podman's user namespace) and images of earlier Containerfiles."""
    pod, tag = shlex.join(podman()), image_tag()
    return "\n".join([
        f"export XDG_RUNTIME_DIR={shlex.quote(str(Path(platform.validation_runroot()).parent))}",
        f"{pod} rm --all --force --time=0 >/dev/null",
        *(f"{pod} unshare rm -rf {shlex.quote(str(path))}" for path in paths),
        f"{pod} images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' | grep '^localhost/altitude-validation:' "
        f"| grep -vx {shlex.quote(tag)} | xargs -r {pod} rmi --force >/dev/null",
        f"{pod} image prune --force >/dev/null", "exit 0"])


def _job(name: str, script: str, folder: Path, timeout: int, limits: bool) -> dict:
    """Run `script` as unit `name`, which writes its output and exit status in `folder`; return its outcome."""
    properties = (f"--property=MemoryMax={MEMORY_MAX}", "--property=MemorySwapMax=0",
                  f"--property=TasksMax={TASKS_MAX}", f"--property=CPUQuota={CPU_QUOTA}") if limits else ()
    started = datetime.now(timezone.utc).isoformat()
    launch_error = engines.machine_command(script, cwd=home(), folder=folder, unit=name, identity={},
                                           timeout=timeout, properties=properties)
    outcome = engines.machine_outcome(folder, name, started, watched=True, timeout=timeout, launch_error=launch_error)
    return {**outcome, "started": started, **engines.machine_output(folder, name)}


def _clone(worktree: Path, target: Path, project: str) -> tuple[str, str]:
    """The task branch's committed HEAD as a repository of its own, with the project's remote-tracking branches
    and tags, and the project's GitHub origin when it has one. Returns the commit and its tree."""
    git = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    commit, tree = subprocess.run([*git, "-C", str(worktree), "rev-parse", "HEAD^{commit}", "HEAD^{tree}"],
                                  capture_output=True, text=True, timeout=30, check=True).stdout.split()
    for step in (["clone", "--quiet", "--no-hardlinks", "--no-checkout", str(worktree), str(target)],
                 ["-C", str(target), "fetch", "--quiet", "--no-tags", str(worktree),
                  "+refs/remotes/origin/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*"],
                 ["-C", str(target), "checkout", "--quiet", "--detach", commit]):
        subprocess.run([*git, *step], capture_output=True, text=True, timeout=600, check=True)
    origin = subprocess.run(["git", "-C", str(config.project_path(project)), "remote", "get-url", "origin"],
                            capture_output=True, text=True, timeout=10).stdout.strip()
    if re.fullmatch(r"(https://github\.com/|git@github\.com:)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", origin):
        subprocess.run([*git, "-C", str(target), "remote", "set-url", "origin", origin], check=True, timeout=10)
    return commit, tree


def _task_dir_fd(project: str, slug: str) -> int:
    """The task folder's `validation` folder, reached from Altitude's home without following links: a worker can
    replace any folder under the home, and a link must not send what a container wrote anywhere else."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(config.ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in S.task_dir(project, slug).relative_to(config.ROOT).parts:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            os.mkdir("validation", 0o700, dir_fd=fd)
        except FileExistsError:
            pass
        child = os.open("validation", flags, dir_fd=fd)
        os.close(fd)
        return child
    except BaseException:
        os.close(fd)
        raise


def _copy_file(source: str, dst_fd: int, name: str) -> None:
    with open(source, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW)) as src_file:
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dst_fd)
        with open(out, "wb") as dst_file:
            shutil.copyfileobj(src_file, dst_file)


def copy_results(source: Path, parent_fd: int, name: str) -> list[str]:
    """Copy the run's results into a new folder `name` under `parent_fd` as regular files and folders only, opened
    without following links, up to RESULTS_LIMIT. Returns what was skipped."""
    skipped, budget = [], [RESULTS_LIMIT]
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def walk(src: Path, dst_fd: int, rel: str) -> None:
        try:
            entries = sorted(os.scandir(src), key=lambda e: e.name)
        except OSError as exc:
            skipped.append(f"{rel or '.'}: {exc.strerror}")
            return
        for entry in entries:
            path = f"{rel}{entry.name}"
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    os.mkdir(entry.name, 0o700, dir_fd=dst_fd)
                    child = os.open(entry.name, flags, dir_fd=dst_fd)
                    try:
                        walk(Path(entry.path), child, path + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(mode):
                    size = entry.stat(follow_symlinks=False).st_size
                    if size > budget[0]:
                        skipped.append(f"{path}: over the {RESULTS_LIMIT >> 20} MiB results limit")
                        continue
                    _copy_file(entry.path, dst_fd, entry.name)
                    budget[0] -= size
                else:
                    skipped.append(f"{path}: not a regular file or folder")
            except OSError as exc:
                skipped.append(f"{path}: {exc.strerror}")

    os.mkdir(name, 0o700, dir_fd=parent_fd)
    top = os.open(name, flags, dir_fd=parent_fd)
    try:
        walk(source, top, "")
    finally:
        os.close(top)
    return skipped


def _deliver(project: str, slug: str, n: int, area: Path, unit: str) -> tuple[Path, list[str]]:
    """The run's results as the task folder's `validation/<n>/` and its output as `validation/<n>.log`."""
    receipt = S.read_json(area / "delivered.json")
    if receipt:
        return Path(receipt["results"]), receipt["skipped"]
    fd = _task_dir_fd(project, slug)
    try:
        skipped = copy_results(area / "results", fd, str(n))
        log = engines.machine_files(area, unit)[0]
        if log.is_file():
            _copy_file(str(log), fd, f"{n}.log")
        for name in ("simulator.png", "simulator.gif"):
            if (area / name).is_file():
                _copy_file(str(area / name), fd, f"{n}.{name}")
    finally:
        os.close(fd)
    target = S.task_dir(project, slug) / "validation" / str(n)
    S.write_json(area / "delivered.json", {"results": str(target), "skipped": skipped})
    return target, skipped


def _capture(area: Path) -> str | None:
    """The run's Simulator recording as `simulator.gif` beside it; returns why there is none, or None. The recording
    and its frames are the runner's own files in the run area, removed with it."""
    try:
        C.video(area / "simulator.mp4", area / "simulator.gif", C.PHONE)
        return None
    except (C.CaptureError, OSError) as exc:
        return str(exc)


def _own(name: str, dir_fd: int | None = None) -> None:
    """Give folder `name` and the folders in it their owner's permissions back. Each is changed through its parent's
    descriptor and opened without following links, so a link swapped in for a folder is never followed."""
    try:
        mode = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
        if not stat.S_ISDIR(mode):
            return
        os.chmod(name, stat.S_IMODE(mode) | stat.S_IRWXU, dir_fd=dir_fd, follow_symlinks=False)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except (OSError, NotImplementedError, ValueError):
        return  # removal names what stays; Linux refuses a no-follow change of a link with ValueError
    try:
        for entry in os.scandir(fd):
            _own(entry.name, fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _remove(path: Path) -> str | None:
    """Remove `path` as the operator's account. A link in its place is removed, never followed. Its folders first get
    their owner's permissions back, since protected-package fixtures can leave one without write permission (#700).
    Returns the first entry that could not be removed and why, or None."""
    if path.is_symlink():
        path.unlink()
        return None
    _own(str(path))
    failures = []
    shutil.rmtree(path, onexc=lambda _call, entry, exc: failures.append(
        f"{entry}: {getattr(exc, 'strerror', None) or exc}"))
    return failures[0] if failures and os.path.lexists(path) else None


def cleanup(paths: list[Path], unit: str | None = None) -> str | None:
    """Remove what runs left: Podman's containers on Linux, `unit`'s processes on macOS, and `paths`, in order: on
    macOS the first path that cannot be removed stops cleanup, so a run area listed last stays as the marker the runner
    retries. Returns why cleanup did not finish, naming what stayed, or None."""
    try:
        if platform.validation_in_container():
            name = f"{UNIT_PREFIX}clean-{uuid.uuid4().hex[:12]}.service"
            try:
                outcome = _job(name, cleanup_script(paths), home(), 300, limits=False)
            finally:
                for path in engines.machine_files(home(), name):
                    path.unlink(missing_ok=True)
            if outcome["exit"] != 0:
                return f"the cleanup unit ended without success: {outcome['error'] or outcome['exit']}"
        else:
            if unit:
                env = platform.manager_env(engines.clean_env())
                platform.job_stop(unit, env)
                if platform.job_active(unit, env):
                    return f"processes of {unit} are still running"
            for path in paths:
                failure = _remove(path)
                if failure:
                    return f"could not remove {failure}"
                if os.path.lexists(path):
                    break
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return f"cleanup failed: {exc}"
    left = [str(path) for path in paths if os.path.lexists(path)]
    return f"could not remove {', '.join(left)}" if left else None


def _interrupted(area: Path) -> bool:
    """Finish the ledger row of a run altd did not see end. `run.json` names the run before its row is written and is
    removed only after the row is finished, so a row still unfinished here was interrupted."""
    try:
        record = json.loads((area / "run.json").read_text())
        runs = S.task_dir(record["project"], record["slug"]) / "machine.jsonl"
        rows = [json.loads(line) for line in runs.read_text().splitlines() if line.strip()] if runs.exists() else []
        for row in rows:
            if row.get("unit") != record["unit"]:
                continue
            if row.get("results") == str(area / "results"):
                return False  # Evidence could not be delivered; keep its original files for recovery.
            if row.get("finished") is None:
                error = "altd did not record the run's end; the runner stopped the run and retained its evidence"
                try:
                    target, skipped = _deliver(record["project"], record["slug"], row["n"], area, record["unit"])
                    log = S.task_dir(record["project"], record["slug"]) / "validation" / f"{row['n']}.log"
                except OSError as exc:
                    target, skipped = area / "results", []
                    log = engines.machine_files(area, record["unit"])[0]
                    error += f"; cannot copy evidence to the task folder: {exc}; original files remain in {area}"
                saved = T.finish_machine_run(record["project"], record["slug"], {
                    **row, "finished": S.now(), "ended": "interrupted",
                    "error": error, "results": str(target), "results_skipped": skipped, "log": str(log)})
                return saved.get("results") != str(area / "results")
        return True
    except FileNotFoundError:
        return not (area / "run.json").exists()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        LOG.warning(f"validation: cannot finish the record of the interrupted run in {area}: {exc}")
        return False


def reconcile() -> None:
    """At daemon start, before any run is admitted: recover what runs left when altd stopped."""
    with LINE.turn("the runner is removing what earlier runs left", None, command="validation reconcile", wait=WAIT):
        _ready.clear()
        _recover()


def _recover() -> str | None:
    """With the validation slot held and no run admitted: stop what earlier runs left, retain their evidence, then remove their
    containers and run areas. Runs are admitted only once every area is gone. Called at daemon start and again by
    each request while an area remains, so a leftover that becomes removable reopens the runner without a restart.
    Returns why an area stays, or None."""
    runs = sorted(p for p in (home() / "runs").glob("*") if p.is_dir()) if (home() / "runs").is_dir() else []
    reasons = []
    if runs and not platform.validation_unavailable():
        env = platform.manager_env(engines.clean_env())
        platform.job_stop(f"{UNIT_PREFIX}*.service", env)
        done = []
        for area in runs:
            unit = f"{UNIT_PREFIX}{area.name}.service"
            try:
                running = platform.job_active(unit, env) and f"processes of {unit} are still running"
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                running = f"cannot confirm that {unit} stopped: {exc}"
            if running:
                reasons.append(running)
            elif phone := sim.remove(area / "simulator"):
                reasons.append(phone)
            elif _interrupted(area):
                done.append(area)
            else:
                reasons.append(f"{area} holds evidence or a record that could not be finished; see altd's log")
        failure = cleanup([path for path in (*(d for area in done for d in candidate_dirs(area)), *done)
                           if os.path.lexists(path)])
        reasons += [failure] if failure else []
    left = [area for area in runs if area.exists()]
    if left:
        reason = "; ".join(reasons) or f"{left[0]} remains"
        LOG.error(f"validation: {len(left)} run area(s) retained; validation runs stay refused: {reason}")
        return reason
    if runs:
        LOG.warning(f"validation: removed {len(runs)} run area(s) left by earlier runs")
    _ready.set()
    return None


def stop_all() -> None:
    """Stop the admitted run, if any. Called after the setting is saved off, so a request admitted later is refused."""
    _stop("turned off")


def _stop(ended: str, unit: str | None = None, why: str | None = None) -> None:
    """Stop the admitted run (only `unit`'s, when named) and record how it `ended` and `why`. The marker covers a unit
    not created yet: its script checks the marker first, and the marker exists before the stop, so the unit either
    already exists and is stopped or starts, finds the marker and exits."""
    with _state:
        if not _active or unit and _active["unit"] != unit or _active["stopped"]:
            return
        _active.update(stopped=ended, why=why or ended)
        (_active["area"] / "stopped").touch()
        unit = _active["unit"]
    platform.job_stop(unit, platform.manager_env(engines.clean_env()))


def run(project: str, slug: str, attempt: object, argv: object, *, kvm: object = False, publish: object = None,
        simulator: object = False, capture: object = False, owner=lambda task: False, waiting=lambda text: None,
        gone=lambda: False) -> dict:
    """One validation run for the running owner's current attempt; returns the command's exit status and output.
    `owner(task)` says whether the request comes from that task's own worker. A request that finds the machine busy
    waits its turn, telling `waiting(text)` what it waits for. `gone()` says the request's client stopped: a waiting
    request leaves the line, and an admitted run is stopped and recorded as stopped. A newer request from the same task
    replaces this one the same way, recorded as replaced: an engine can cancel a command yet leave its client running
    and connected, and the owner's next request must not wait behind it (issue #796)."""
    global _holder

    def check() -> dict:
        return _check(project, slug, attempt, argv, kvm=kvm, publish=publish, simulator=simulator, capture=capture,
                      owner=owner)
    with config.restart_lock() as ready:
        if not ready:
            raise ValueError(RESTARTING)
        check()
    try:
        phone = sim.plan() if simulator else None
    except ValueError as exc:
        raise ValueError(f"alt task validate: {exc}") from exc
    key, mine = f"{project}/{slug}", {"at": datetime.now(timezone.utc), "replaced": None}
    with _state:   # the mark stays even when the newer request ends first
        if key in _latest:
            _latest[key]["replaced"] = mine["at"]
        _latest[key] = mine

    def leaving() -> str | None:
        return (f"replaced by this task's newer validation request of {mine['replaced']:%Y-%m-%d %H:%M:%S} UTC"
                if mine["replaced"] else line.CLIENT_GONE if gone() else None)
    try:
        with LINE.turn(f"the validation run of {project}/{slug} holds this machine",
                       TIMEOUT + (SIMULATOR_SECONDS if phone else 0), command="alt task validate", wait=WAIT,
                       watch=WATCH_SECONDS, waiting=waiting, gone=leaving) as place:
            with config.restart_lock() as ready:
                if not ready:
                    raise ValueError(RESTARTING)
                task = check()  # the task, the switch and the host may have changed while the request waited
                if mine["replaced"]:
                    raise ValueError(f"alt task validate: {leaving()} while it waited")
                _holder = key
                try:
                    return _run(project, slug, task, argv, kvm=kvm, publish=publish, phone=phone, capture=capture,
                                leaving=leaving, place=place)
                finally:
                    _holder = None
    finally:
        with _state:
            if _latest.get(key) is mine:
                del _latest[key]


def holder() -> str | None:
    """The task whose validation run pending activation waits for."""
    return _holder


def _check(project: str, slug: str, attempt: object, argv: object, *, kvm: object, publish: object,
           simulator: object, capture: object, owner) -> dict:
    """Refuse a request this runner cannot or may not run; returns its task."""
    if config.activation_pending():  # an admitted run finishes; a new one would keep the quiet point from opening
        raise ValueError(RESTARTING)
    S.require_task_slug(slug)
    if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv)
            or sum(len(a) + 1 for a in argv) > COMMAND_LIMIT):
        raise ValueError(f"alt task validate: supply a command of at most {COMMAND_LIMIT} characters after --")
    if (not all(isinstance(flag, bool) for flag in (kvm, simulator, capture))
            or publish is not None and (type(publish) is not int or not 1 <= publish <= 65535)):
        raise ValueError("alt task validate: --kvm, --simulator and --capture are on or off and --publish names one "
                         "container port")
    if capture and not simulator:
        raise ValueError("alt task validate: --capture records the iOS Simulator iPhone and needs --simulator; the "
                         "other lanes make their own captures with CAPTURE=1")
    unavailable = platform.validation_unavailable()
    if unavailable:
        raise ValueError(f"alt task validate: {unavailable}")
    contained = platform.validation_in_container()
    if not contained and (kvm or publish):
        raise ValueError("alt task validate: --kvm and --publish need the Linux container runner; a macOS run binds "
                         "free loopback ports itself")
    if not enabled():
        raise PermissionError(OFF)
    if kvm and not os.access(platform.KVM, os.R_OK | os.W_OK):
        raise ValueError("alt task validate: no KVM access; /dev/kvm is usable while the operator's desktop login "
                         "is active")
    task = S.load_task(project, slug)
    if task.get("state") != "running" or str(task.get("attempt")) != str(attempt) or not task.get("worktree"):
        raise PermissionError("alt task validate: only the running owner's current attempt may run validation")
    if not owner(task):
        raise PermissionError("alt task validate: only this task's owner may run its validation")
    home().mkdir(mode=0o700, parents=True, exist_ok=True)
    if shutil.disk_usage(home()).free < FREE_DISK:
        raise ValueError(f"alt task validate: less than {FREE_DISK >> 30} GiB free for the validation runner")
    return task


def _run(project: str, slug: str, task: dict, argv: list[str], *, kvm: bool, publish: int | None, phone: dict | None,
         capture: bool, leaving, place: dict) -> dict:
    """The admitted run, holding the validation slot's `place`."""
    left = None if _ready.is_set() else _recover()
    if left:
        raise ValueError(f"alt task validate: the runner has not finished removing what earlier runs left: {left}")
    ident = uuid.uuid4().hex[:12]
    area, unit = home() / "runs" / ident, f"{UNIT_PREFIX}{ident}.service"
    row, result, failure, stopped, why, target, skipped = None, None, None, None, None, None, []
    device, relay, recording, unrecorded = None, None, None, None
    watched, watcher = threading.Event(), None

    def watch() -> None:  # the job's launcher waits for the job, so the client is watched beside it
        while not watched.wait(WATCH_SECONDS):
            why = leaving()
            if why:
                _stop("stopped" if why == line.CLIENT_GONE else "replaced", unit, why)
                return

    try:
        for folder in candidate_dirs(area):
            folder.mkdir(mode=0o700, parents=True)
        for name in ("storage", "tmp", "cache"):
            (home() / name).mkdir(exist_ok=True)
        with _state:
            if not enabled():
                raise PermissionError(OFF)
            _active.update(area=area, unit=unit, stopped=None)
        watcher = threading.Thread(target=watch, name="validation-client", daemon=True)
        watcher.start()
        try:
            commit, tree = _clone(Path(task["worktree"]), area / "work", project)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"alt task validate: cannot copy the task branch's committed HEAD: {exc}") from exc
        with _state:
            phone = None if _active["stopped"] else phone   # a run stopped while its clone was made boots no phone
        if phone:
            device = sim.Phone(area / "simulator", phone, area / "walks")
            try:
                socket_path = device.boot()
                certificates = device.trust(https(area))
                relay = sim.Relay(inspector(area), socket_path, config.PORT, device.open, device.walk)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                raise ValueError(f"alt task validate: the iOS Simulator iPhone did not start: {exc}") from exc
            phone = {**{key: phone[key] for key in ("xcode", "runtime", "device")}, "safari": device.safari(),
                     "certificates": certificates}
        ports = (publish, host_port()) if publish else None
        (area / "run.json").write_text(json.dumps({"project": project, "slug": slug, "unit": unit}))
        row = T.start_machine_run(project, slug, lambda n: {
            "purpose": "validation", "command": shlex.join(argv), "unit": unit, "commit": commit, "tree": tree,
            "host": platform.host_identity(), "isolation": isolation(area, unit), "kvm": kvm,
            "publish": {"container": ports[0], "host": ports[1]} if ports else None,
            "simulator": phone, "log": str(S.task_dir(project, slug) / "validation" / f"{n}.log")})
        with _state:
            stopped, why = _active["stopped"], _active.get("why")
        if not stopped:
            LINE.ends(place, TIMEOUT)
            if phone and capture:
                try:
                    recording = device.record(area / "simulator.mp4")
                except (OSError, subprocess.SubprocessError) as exc:
                    unrecorded = f"the screen recording did not start: {exc}"
            result = _job(unit, run_script(f"{UNIT_PREFIX}{ident}", area, argv, kvm=kvm, publish=ports,
                                           simulator=bool(phone)), area, TIMEOUT, limits=True)
        with _state:
            stopped, why = _active["stopped"], _active.get("why")
            _active.clear()
        missing, uncaptured = None, None
        if relay:
            relay.close()
            if capture:
                uncaptured = recording.stop() if recording else unrecorded or f"the run was {stopped} before it started"
                recording = None
            missing = device.screenshot(area / "simulator.png")
            if capture:
                uncaptured = uncaptured or _capture(area)
        target, skipped = _deliver(project, slug, row["n"], area, unit)
        if relay:
            evidence = S.task_dir(project, slug) / "validation"
            row["simulator"]["screenshot"] = f"none: {missing}" if missing else str(
                evidence / f"{row['n']}.simulator.png")
            if capture:
                row["simulator"]["capture"] = f"none: {uncaptured}" if uncaptured else str(
                    evidence / f"{row['n']}.simulator.gif")
    except BaseException as exc:
        failure = str(exc) or type(exc).__name__
        raise
    finally:
        watched.set()
        if watcher:
            watcher.join()   # it reads the client's connection, which the answer is written to next
        with _state:
            _active.clear()
        recorded, delivered, cleanup_error = row is None, target is not None, None
        if relay:
            relay.close()
        if recording:
            recording.stop()
        try:
            # Cleanup of the run's processes and of everything the candidate could write comes before the record, so
            # a run whose cleanup failed is never recorded as a success. A run that never started leaves nothing to
            # keep. Evidence that was not delivered stays in place. Only the runner's own files (run.json, the receipt,
            # log and exit status) stay until the record exists, for the next start to finish an interrupted run.
            # The phone goes first: its set is the record of what to remove, so a set that stays keeps the area.
            device_error = sim.remove(area / "simulator") if device else None
            scratch = [*candidate_dirs(area), *([] if device_error else [area])] if row is None \
                else candidate_dirs(area) if delivered else []
            cleanup_error = cleanup([path for path in scratch if os.path.lexists(path)],
                                    unit if row is not None else None) or device_error
            if row is not None:
                result = result or {"exit": None, "timed_out": False, "started": None, "finished": S.now(),
                                    "error": f"{why} before it started" if stopped else failure,
                                    "output": "", "output_truncated": False}
                ended = (stopped if stopped else "failed" if failure else "cleanup failed" if cleanup_error
                         else "exit" if result["exit"] is not None
                         else "timeout" if result["timed_out"] else "no exit status")
                if target is None:
                    target = area / "results"
                    row["log"] = str(engines.machine_files(area, unit)[0])
                row.update({key: result[key] for key in ("exit", "timed_out", "started", "finished", "error")},
                           ended=ended, results=str(target) if target else None, results_skipped=skipped,
                           cleanup=cleanup_error)
                if failure and not row["error"]:
                    row["error"] = failure
                if stopped in ("stopped", "replaced") and result["started"]:
                    row["error"] = why
                T.finish_machine_run(project, slug, row)
                recorded = True
        finally:
            try:
                if recorded and delivered and not cleanup_error:
                    shutil.rmtree(area, ignore_errors=True)
            finally:
                if area.exists():
                    _ready.clear()
                    why = cleanup_error or "its record or evidence was not finished"
                    LOG.warning(f"validation: retained {area}: {why}; the next request retries its removal")
    return {**result, "n": row["n"], "commit": commit, "tree": tree, "host": row["host"], "isolation": row["isolation"],
            "ended": row["ended"], "cleanup": cleanup_error, "results": str(target), "results_skipped": skipped,
            "publish": row["publish"], "simulator": row["simulator"], "log": row["log"], "unit": unit}
