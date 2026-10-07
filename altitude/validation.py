"""The validation runner: a task owner's command against a throwaway clone of its committed HEAD, isolated from
the operator's runtime, with no grant per run.

On Linux the command runs in a disposable rootless container. altd builds the only image from its own deployed source
(`scripts/validation.Containerfile`). The owner chooses the command, whether /dev/kvm is added and one container port
published on 127.0.0.1. Everything else is fixed here: a non-root user mapped to the operator's account, no host mounts
except the run's clone and results folder, rootless networking with host loopback closed, and one service unit whose
limits bound the build, Podman and the container together. On macOS, where Podman would need a virtual machine of its
own, the command runs as a job under the platform's validation Seatbelt profile, with a home and temporary folder in
its run area and nothing of altd's environment.
The runner's storage sits beside Altitude's home, outside every worker's writable roots, and what a run produces
reaches the task folder only through no-follow descriptors. Runs are recorded in the task's `machine.jsonl` like
machine commands.
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

from . import config, engines, platform, state as S, tasks as T

TIMEOUT = 3600                   # seconds for one run, image build included
MEMORY_MAX = "8G"                # the unit's memory, swap and process caps hold Podman and the container together
TASKS_MAX = 4096
CPU_QUOTA = "400%"
FREE_DISK = 20 << 30             # bytes free in Altitude's home before a run starts
RESULTS_LIMIT = 256 << 20        # bytes copied back into the task folder
COMMAND_LIMIT = 16384
USER = "1000:1000"               # the image's `ubuntu` user, mapped to the operator's own account (keep-id)
IMAGE_CACHE = "/home/ubuntu/.cache/altitude-installation-vm"

OFF = "alt task validate: the operator has turned validation runs off in Settings"

UNIT_PREFIX = "altitude-validation-"   # every unit the runner starts; its rows are recorded by reconcile()

LOG = logging.getLogger(__name__)
_lock = threading.Lock()         # one run on the machine at a time, and startup cleanup before any
_state = threading.Lock()        # orders admission against turning the runner off
_ready = threading.Event()       # startup cleanup has finished; no run is admitted before it
_active: dict = {}               # the admitted run's area and unit, and whether turning the runner off stopped it


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
    """The folders of a run area the candidate sees: its clone, results and, in a container, an empty mount point, or
    on macOS a home and temporary folder of its own. The area's other files (`run.json`, the delivery receipt, the
    unit's log and exit status) are the runner's, outside the candidate's reach."""
    names = ("empty",) if platform.validation_in_container() else ("home", "tmp")
    return [run / name for name in ("work", "results", *names)]


def native_script(run: Path, argv: list[str]) -> str:
    """macOS: the owner's command in the run's clone under the validation profile, which admits only the run's
    candidate folders. Its environment is only this: a home and temporary folder of its own, and altd's PATH without
    folders the profile hides."""
    hidden = os.path.realpath(Path.home())
    path = [entry for entry in os.environ.get("PATH", "/usr/bin:/bin").split(":")
            if entry and not Path(os.path.realpath(entry)).is_relative_to(hidden)]
    env = {"HOME": str(run / "home"), "TMPDIR": str(run / "tmp"), "PATH": ":".join(path), "LANG": "en_US.UTF-8",
           "ALTITUDE_VALIDATION": "1", "VALIDATION_RESULTS": str(run / "results")}
    return "\n".join(["set -u", f"[ ! -e {shlex.quote(str(run / 'stopped'))} ] || exit 125",
                      f"cd {shlex.quote(str(run / 'work'))} || exit 125",
                      "exec " + shlex.join(platform.validation_command(tuple(candidate_dirs(run)), config.PORT, argv,
                                                                        env))])


def isolation() -> str:
    """What isolated a run, for its record: the image tag, or the digest of the profile for a run area."""
    if platform.validation_in_container():
        return image_tag()
    profile = platform.validation_profile(tuple(candidate_dirs(home() / "runs" / "run")), config.PORT)
    return "seatbelt:" + hashlib.sha256(profile.encode()).hexdigest()[:16]


def run_script(name: str, run: Path, argv: list[str], *, kvm: bool, publish: tuple[int, int] | None) -> str:
    """The unit's shell script: build the image when its Containerfile changed, refresh the verified cloud image
    for a VM run with the deployed runner's own code, then run the owner's command."""
    if not platform.validation_in_container():
        return native_script(run, argv)
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
    finally:
        os.close(fd)
    target = S.task_dir(project, slug) / "validation" / str(n)
    S.write_json(area / "delivered.json", {"results": str(target), "skipped": skipped})
    return target, skipped


def cleanup(paths: list[Path], unit: str | None = None) -> str | None:
    """Remove what runs left: Podman's containers on Linux, `unit`'s processes on macOS, and `paths`. Returns why
    cleanup did not finish, or None."""
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
                shutil.rmtree(path, ignore_errors=True)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return f"cleanup failed: {exc}"
    left = [str(path) for path in paths if path.exists()]
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
                error = "altd stopped during the run; its next start stopped the run and retained its evidence"
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
    """At daemon start, before any run is admitted: stop what a run left when altd stopped, retain evidence, then
    remove its containers and run area. Runs are admitted only once every area is gone; a failed cleanup keeps the
    runner closed until the next start."""
    with _lock:
        _ready.clear()
        runs = sorted(p for p in (home() / "runs").glob("*") if p.is_dir()) if (home() / "runs").is_dir() else []
        if runs and not platform.validation_unavailable():
            platform.job_stop(f"{UNIT_PREFIX}*.service", platform.manager_env(engines.clean_env()))
            cleanup([area for area in runs if _interrupted(area)])
        left = [area for area in runs if area.exists()]
        if left:
            LOG.error(f"validation: {len(left)} run area(s) retained because evidence or cleanup could not finish; "
                      "validation runs stay refused until recovery and the next altd start")
            return
        if runs:
            LOG.warning(f"validation: removed {len(runs)} run area(s) left by an earlier altd")
        _ready.set()


def stop_all() -> None:
    """Stop the admitted run, if any. Called after the setting is saved off, so a request admitted later is refused.
    The marker covers a unit not created yet: its script checks the marker first, and the marker exists before the
    stop, so the unit either already exists and is stopped or starts, finds the marker and exits."""
    with _state:
        if not _active:
            return
        _active["stopped"] = True
        (_active["area"] / "stopped").touch()
        unit = _active["unit"]
    platform.job_stop(unit, platform.manager_env(engines.clean_env()))


def run(project: str, slug: str, attempt: object, argv: object, *, kvm: object = False, publish: object = None,
        owner=lambda task: False) -> dict:
    """One validation run for the running owner's current attempt; returns the command's exit status and output.
    `owner(task)` says whether the request comes from that task's own worker."""
    with config.restart_lock() as ready:
        if not ready or config.restart_in_progress():
            raise ValueError("alt task validate: Altitude is restarting; retry when it is ready")
        return _run(project, slug, attempt, argv, kvm=kvm, publish=publish, owner=owner)


def _run(project: str, slug: str, attempt: object, argv: object, *, kvm: object, publish: object, owner) -> dict:
    S.require_task_slug(slug)
    if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv)
            or sum(len(a) + 1 for a in argv) > COMMAND_LIMIT):
        raise ValueError(f"alt task validate: supply a command of at most {COMMAND_LIMIT} characters after --")
    if not isinstance(kvm, bool) or publish is not None and (type(publish) is not int or not 1 <= publish <= 65535):
        raise ValueError("alt task validate: --kvm is on or off and --publish names one container port")
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
    if not _ready.is_set():
        raise ValueError("alt task validate: the runner has not finished removing what an earlier altd left; "
                         "see altd's log")
    if not _lock.acquire(blocking=False):
        raise ValueError("alt task validate: another validation run is using this machine; try again when it ends")
    ident = uuid.uuid4().hex[:12]
    area, unit = home() / "runs" / ident, f"{UNIT_PREFIX}{ident}.service"
    row, result, failure, stopped, target, skipped = None, None, None, False, None, []
    try:
        for folder in candidate_dirs(area):
            folder.mkdir(parents=True)
        for name in ("storage", "tmp", "cache"):
            (home() / name).mkdir(exist_ok=True)
        with _state:
            if not enabled():
                raise PermissionError(OFF)
            _active.update(area=area, unit=unit, stopped=False)
        try:
            commit, tree = _clone(Path(task["worktree"]), area / "work", project)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"alt task validate: cannot copy the task branch's committed HEAD: {exc}") from exc
        ports = (publish, host_port()) if publish else None
        (area / "run.json").write_text(json.dumps({"project": project, "slug": slug, "unit": unit}))
        row = T.start_machine_run(project, slug, lambda n: {
            "purpose": "validation", "command": shlex.join(argv), "unit": unit, "commit": commit, "tree": tree,
            "host": platform.host_identity(), "isolation": isolation(), "kvm": kvm,
            "publish": {"container": ports[0], "host": ports[1]} if ports else None,
            "log": str(S.task_dir(project, slug) / "validation" / f"{n}.log")})
        with _state:
            stopped = _active["stopped"]
        if not stopped:
            result = _job(unit, run_script(f"{UNIT_PREFIX}{ident}", area, argv, kvm=kvm, publish=ports),
                          area, TIMEOUT, limits=True)
        with _state:
            stopped = _active["stopped"]
            _active.clear()
        target, skipped = _deliver(project, slug, row["n"], area, unit)
    except BaseException as exc:
        failure = str(exc) or type(exc).__name__
        raise
    finally:
        with _state:
            _active.clear()
        recorded, delivered, cleanup_error = row is None, target is not None, None
        try:
            # Cleanup of the run's processes and of everything the candidate could write comes before the record, so
            # a run whose cleanup failed is never recorded as a success. A run that never started leaves nothing to
            # keep. Evidence that was not delivered stays in place. Only the runner's own files (run.json, the receipt,
            # log and exit status) stay until the record exists, for the next start to finish an interrupted run.
            scratch = [area] if row is None else candidate_dirs(area) if delivered else []
            cleanup_error = cleanup([path for path in scratch if path.exists()], unit if row is not None else None)
            if row is not None:
                result = result or {"exit": None, "timed_out": False, "started": None, "finished": S.now(),
                                    "error": "turned off before it started" if stopped else failure,
                                    "output": "", "output_truncated": False}
                ended = ("turned off" if stopped else "failed" if failure else "cleanup failed" if cleanup_error
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
                T.finish_machine_run(project, slug, row)
                recorded = True
        finally:
            try:
                if recorded and delivered and not cleanup_error:
                    shutil.rmtree(area, ignore_errors=True)
            finally:
                if area.exists():
                    _ready.clear()
                    LOG.warning(f"validation: retained {area}; evidence or cleanup needs recovery before another run")
                _lock.release()
    return {**result, "n": row["n"], "commit": commit, "tree": tree, "host": row["host"], "isolation": row["isolation"],
            "ended": row["ended"], "cleanup": cleanup_error, "results": str(target), "results_skipped": skipped,
            "publish": row["publish"], "log": row["log"], "unit": unit}
