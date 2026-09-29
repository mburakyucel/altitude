#!/usr/bin/env python3
"""Exercise the platform seam natively on this host: jobs, Stop, time limits, confinement, process and socket facts.

Provider-free and without Altitude state: every job runs a small fixture command under a throwaway name and is
removed afterwards. `--service` also installs, restarts and removes a throwaway user service (never Altitude's own).
Prints one JSON document of rows (pass, fail or skip with detail) and exits non-zero when a row fails.

    python3 scripts/platform_probe.py [--service] [--only NAME ...]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from altitude import platform  # noqa: E402

DARWIN = sys.platform == "darwin"
NONCE = uuid.uuid4().hex[:8]
ROWS: dict[str, callable] = {}


class Failed(AssertionError):
    pass


def row(function):
    ROWS[function.__name__.replace("_", "-")] = function
    return function


def check(condition, detail: str):
    if not condition:
        raise Failed(detail)


def name(kind: str) -> str:
    return f"altitude-probe-{kind}-{NONCE}-{uuid.uuid4().hex[:6]}.service"


def env() -> dict[str, str]:
    return platform.job_env({"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C"})


def marked(marker: str) -> list[int]:
    """Live processes of a job whose command line carries `marker` (not the launcher, whose job description names it)."""
    rows = subprocess.run(["ps", "-Ao", "pid=,stat=,command="], capture_output=True, text=True, check=True).stdout
    return [int(pid) for pid, state, command in (line.split(None, 2) for line in rows.splitlines() if len(line.split(None, 2)) == 3)
            if marker in command and "job_main" not in command and not state.startswith("Z") and int(pid) != os.getpid()]


def until(condition, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


# A tree that tries every way out: an ordinary child, a double-forked setsid daemon, one that also clears its
# environment, and children that keep forking while Stop runs. Every process carries the marker in its command line.
TREE = r"""
import os, sys, time
marker = sys.argv[1]
def daemon(clear):
    if os.fork(): return
    os.setsid()
    if os.fork(): os._exit(0)
    os.execve(sys.executable, [sys.executable, "-c", "import time; time.sleep(120)", marker], {} if clear else dict(os.environ))
if os.fork() == 0:
    os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(120)", marker])
daemon(False); daemon(True)
for _ in range(3):
    if os.fork() == 0:
        while True:
            if os.fork() == 0:
                os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(120)", marker])
            time.sleep(0.1)
print("tree up", flush=True)
time.sleep(120)
"""


@row
def job_pipe():
    job = name("pipe")
    result = subprocess.run(platform.job_command(job, ["/bin/sh", "-c", 'read line; echo "in:$line"; echo err >&2; exit 7'],
                                                 env()), input="hello\n", capture_output=True, text=True, timeout=60)
    check((result.returncode, result.stdout, result.stderr) == (7, "in:hello\n", "err\n"),
          f"status/output: {result.returncode} {result.stdout!r} {result.stderr!r}")
    check(not platform.job_active(job, env()), "job still active after it ended")
    return "input, output, error and exit status pass through; the job is gone afterwards"


@row
def job_file_output():
    job = name("file")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "out.log"
        with open(out, "ab", buffering=0) as stream:
            result = subprocess.run(platform.job_command(job, ["/bin/sh", "-c", "echo one; echo two >&2"], env()),
                                    stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, timeout=60)
        check(result.returncode == 0 and sorted(out.read_text().split()) == ["one", "two"],
              f"file output: {result.returncode} {out.read_text()!r}")
    return "a worker's append-mode output file receives the job's output directly"


@row
def exit_cleanup():
    marker = f"probe-exit-{NONCE}"
    job = name("exit")
    script = f'{sys.executable} -c "import os, time; os.setsid(); time.sleep(120)" {marker} & sleep 0.5'
    result = subprocess.run(platform.job_command(job, ["/bin/sh", "-c", script], env()), stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=60)
    check(result.returncode == 0, f"status {result.returncode}")
    check(until(lambda: not marked(marker), 10), f"a setsid descendant outlived the job: {marked(marker)}")
    return "a descendant that left the session ends with the job's main process"


@row
def stop_descendants():
    marker = f"probe-stop-{NONCE}"
    job = name("stop")
    proc = subprocess.Popen(platform.job_command(job, [sys.executable, "-c", TREE, marker], env()),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    try:
        check(proc.stdout.readline().strip() == "tree up", "tree did not start")
        check(until(lambda: len(marked(marker)) >= 6, 10), f"tree incomplete: {len(marked(marker))} processes")
        check(platform.job_active(job, env()), "running job reads inactive")
        before = len(marked(marker))
        started = time.monotonic()
        platform.job_stop(job, env())
        elapsed = time.monotonic() - started
        check(not platform.job_active(job, env()), "job still active after Stop")
        check(until(lambda: not marked(marker), 5), f"survivors after Stop: {marked(marker)}")
    finally:
        proc.kill()
        proc.wait()
    return f"{before} processes (ordinary, setsid double fork, cleared environment, forking) stopped in {elapsed:.1f}s"


@row
def time_limit():
    job = name("limit")
    started = time.monotonic()
    result = subprocess.run(platform.job_command(job, ["/bin/sleep", "60"], env(), runtime_max=2),
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    elapsed = time.monotonic() - started
    check(result.returncode != 0 and elapsed < 15, f"status {result.returncode} after {elapsed:.1f}s")
    check(not platform.job_active(job, env()), "job active after its limit")
    return f"stopped at the 2s limit after {elapsed:.1f}s (status {result.returncode})"


@row
def limit_after_owner_exit():
    marker = f"probe-owner-{NONCE}"
    job = name("owner")
    proc = subprocess.Popen(platform.job_command(job, [sys.executable, "-c", "import time; time.sleep(60)", marker],
                                                 env(), runtime_max=4), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    check(until(lambda: marked(marker), 10), "job did not start")
    proc.kill()  # the owner (altd, here the launcher) goes away
    proc.wait()
    check(marked(marker), "the job ended with its owner instead of outliving it")
    check(until(lambda: not marked(marker), 15), "the limit was not enforced after the owner exited")
    return "the job outlived its owner and still ended at its limit"


@row
def logged_job():
    job = name("logged")
    with tempfile.TemporaryDirectory() as folder:
        log, status = Path(folder) / "machine.log", Path(folder) / "machine.exit"
        result = subprocess.run(platform.logged_job_command(job, "echo logged; exit 3", log=log, status=status, env=env(),
                                                            timeout=30), capture_output=True, text=True, timeout=60)
        check(log.read_text() == "logged\n" and status.read_text() == "3", f"log {log.read_text()!r} status {status.read_text()!r}")
    return f"output appended to the log, status recorded (launcher exit {result.returncode})"


@row
def detached_job():
    job = name("detached")
    with tempfile.TemporaryDirectory() as folder:
        done = Path(folder) / "done"
        result = subprocess.run(platform.detached_job_command(job, ["/bin/sh", "-c", f"sleep 1; echo ok > '{done}'"],
                                                              path="/usr/bin:/bin"), capture_output=True, text=True, timeout=60)
        check(result.returncode == 0, f"launch failed: {result.stderr}")
        check(until(done.exists, 15), "the detached command did not run")
        check(until(lambda: not platform.job_active(job, env()), 10), "detached job did not end")
    if DARWIN:
        Path(platform.job_logs_hint(job)).unlink(missing_ok=True)
    return "the command ran outside the caller and its job ended"


@row
def confinement():
    if not DARWIN:
        return None  # Linux leaves file confinement to the engine's own sandbox
    job = name("confined")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        outside = Path.home() / f".altitude-probe-{NONCE}"
        label = platform._label(job)
        script = (f"echo x > '{root}/inside' && echo write-inside; echo x > '{outside}' 2>/dev/null && echo write-home; "
                  "kill -TERM $PPID 2>/dev/null && echo signal-supervisor; "
                  f"/bin/launchctl kill KILL gui/{os.getuid()}/{label} 2>/dev/null && echo launchctl-kill; "
                  f"/bin/launchctl bootout gui/{os.getuid()}/{label} 2>/dev/null && echo launchctl-bootout; "
                  "sleep 5 & kill $! && echo signal-own-child")
        result = subprocess.run(platform.job_command(job, ["/bin/sh", "-c", script], env(), writable=(root,), runtime_max=30),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        written = outside.exists()
        outside.unlink(missing_ok=True)
        check(result.stdout.split() == ["write-inside", "signal-own-child"] and not written,
              f"confinement: {result.stdout.split()} home written: {written}")
    return "writes under the roots only; the supervisor and launchd are out of reach; own children stay signalable"


@row
def foreign_coalition():
    """Stop's member scan skips only exited processes, so an ordinary user must read every other user's coalition."""
    if not DARWIN or os.getuid() == 0:
        return None  # coalitions are macOS only; root reads every process, which proves nothing

    def owner(pid: int) -> int | None:  # None when this user may not read the process's details
        try:
            return platform._bsd(pid).uid
        except PermissionError:
            return None

    foreign, denied, unreadable, launchd = 0, 0, 0, False
    for pid in platform._pids():
        try:
            uid = owner(pid)
            if uid == os.getuid():
                continue
            platform._coalition_of(pid)
        except FileNotFoundError:  # exited meanwhile
            continue
        except OSError:
            unreadable += 1
            continue
        foreign, denied, launchd = foreign + 1, denied + (uid is None), launchd or (pid == 1 and uid in (None, 0))
    check(not unreadable and launchd, f"{unreadable} of {foreign + unreadable} other users' processes unreadable; "
                                      f"root launchd read: {launchd}")
    return f"all {foreign} other users' coalitions read ({denied} with details denied), root launchd among them"


@row
def process_facts():
    me = os.getpid()
    start = platform.process_start(me)
    check(platform.process_running(me, start) is True and platform.process_name(me), "own identity unreadable")
    child = subprocess.Popen(["/bin/sleep", "0.1"])
    child.wait()
    try:
        platform.process_start(child.pid)
        check(False, "an exited process reads as present")
    except FileNotFoundError:
        pass
    listener = socket.create_server(("127.0.0.1", 0))
    client = socket.create_connection(listener.getsockname())
    accepted, peer = listener.accept()
    try:
        handle = platform.client_socket([peer[0]], peer[1], ["127.0.0.1"], accepted.getsockname()[1])
        check(handle and platform.holds(me, handle), f"own client socket not found: {handle}")
        table = platform.process_table()
        check(me in table and table[me][0] == os.getppid(), "process table misses this process")
    finally:
        for sock in (client, accepted, listener):
            sock.close()
    return "identity, liveness, names, the process table and TCP socket holders are readable"


@row
def memory_limit():
    hog = [sys.executable, "-c", "import time; block = bytearray(700 << 20); time.sleep(5)"]
    result = subprocess.run(platform.limited_command(hog, memory=256 << 20, cpu=10, output=1 << 20), capture_output=True,
                            timeout=30)
    check(result.returncode != 0, "a command past the memory limit completed")
    fine = subprocess.run(platform.limited_command([sys.executable, "-c", "print('ok')"], memory=256 << 20, cpu=10,
                                                   output=1 << 20), capture_output=True, text=True, timeout=30)
    check(fine.stdout == "ok\n", f"a command within the limits failed: {fine.returncode}")
    return f"stopped past 256 MiB (status {result.returncode}); within limits it runs"


@row
def service():
    """A throwaway user service through the seam: start, status, restart, stop, removal."""
    if "--service" not in sys.argv:
        return None
    label, unit = f"dev.altitude.probe-{NONCE}", f"altitude-probe-{NONCE}.service"
    home = Path(tempfile.mkdtemp(prefix="altitude-probe-home-"))
    real_home = Path.home
    platform.LABEL, platform.SERVICE = label, unit
    if DARWIN:  # the definition and logs land in the throwaway home; systemd reads only the real unit directory
        Path.home = classmethod(lambda cls: home)
    try:
        program = home / "current/bin/alt"
        program.parent.mkdir(parents=True)
        program.write_text("import time\nwhile True: time.sleep(1)\n")
        path = platform.service_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(platform.definition(home, Path(sys.executable), home / "install.json", {"PATH": "/usr/bin:/bin"}))
        if not DARWIN:
            platform.control("reload")
        platform.control("start")
        check(until(lambda: platform.status().get("ActiveState") == "active", 15), f"not active: {platform.status()}")
        first = platform.status()["MainPID"]
        platform.control("restart")
        check(until(lambda: platform.status().get("MainPID") not in ("0", first), 15), "restart kept the old process")
        platform.control("stop")
        check(platform.status().get("ActiveState") in ("inactive", "failed"), f"stop unconfirmed: {platform.status()}")
        return f"started, restarted ({first} -> new process), stopped: {platform.status()['LoadState']}"
    finally:
        try:
            platform.control("stop")
        except RuntimeError:
            pass
        platform.service_path().unlink(missing_ok=True)
        if not DARWIN:
            platform.control("reload")
        Path.home = real_home
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service", action="store_true", help="also install, restart and remove a throwaway service")
    parser.add_argument("--only", nargs="*", choices=sorted(ROWS), help="run only these rows")
    args = parser.parse_args()
    platform.require_supported()
    results = []
    for row_name, function in ROWS.items():
        if args.only and row_name not in args.only:
            continue
        try:
            detail = function()
            results.append({"row": row_name, "state": "skip" if detail is None else "pass", "detail": detail or "not applicable"})
        except Exception as exc:  # noqa: BLE001 - every row reports, whatever fails
            results.append({"row": row_name, "state": "fail", "detail": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"host": sys.platform, "rows": results}, indent=1))
    return 1 if any(result["state"] == "fail" for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
