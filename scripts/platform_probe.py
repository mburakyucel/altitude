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
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from altitude import platform  # noqa: E402
from altitude.engines import common  # noqa: E402

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
# environment, a pseudo-terminal's child (a session of its own, as a test's forkpty makes), and children that keep forking
# while Stop runs. Every process carries the marker in its command line.
TREE = r"""
import os, pty, sys, time
marker = sys.argv[1]
def daemon(clear):
    if os.fork(): return
    os.setsid()
    if os.fork(): os._exit(0)
    os.execve(sys.executable, [sys.executable, "-c", "import time; time.sleep(120)", marker], {} if clear else dict(os.environ))
if os.fork() == 0:
    os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(120)", marker])
daemon(False); daemon(True)
if pty.fork()[0] == 0:
    os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(120)", marker])
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
    return (f"{before} processes (ordinary, setsid double fork, cleared environment, pseudo-terminal, forking) stopped "
            f"in {elapsed:.1f}s")


@row
def time_limit():
    marker = f"probe-limit-{NONCE}"
    job = name("limit")
    started = time.monotonic()
    result = subprocess.run(platform.job_command(job, [sys.executable, "-c", TREE, marker], env(), runtime_max=3),
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - started
    # A job that never starts also fails at once; only one stopped at its limit has run for three seconds.
    check(result.returncode != 0 and 3 <= elapsed < 20, f"status {result.returncode} after {elapsed:.1f}s")
    check(result.stdout.strip() == "tree up", f"tree did not start: {result.stdout!r}")
    check(not platform.job_active(job, env()), "job active after its limit")
    check(until(lambda: not marked(marker), 5), f"survivors after the limit: {marked(marker)}")
    return f"the whole tree stopped at the 3s limit after {elapsed:.1f}s (status {result.returncode})"


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
def sleep_hold():
    """Idle sleep waits for a running job: its supervisor's caffeinate holds the assertion and lets it go with the job.
    A closed lid or a forced sleep still sleeps; `awake-clock` shows that the time limit then counts awake time."""
    if not DARWIN:
        return None
    marker = f"probe-sleep-{NONCE}"
    job = name("sleep")
    proc = subprocess.Popen(platform.job_command(job, [sys.executable, "-c", "import time; time.sleep(120)", marker],
                                                 env(), runtime_max=60), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        check(until(lambda: marked(marker), 10), "job did not start")
        supervisor = int((platform._jobs() / platform._label(job) / "supervisor").read_text())
        check(until(lambda: held_for(supervisor), 10), f"nothing holds off idle sleep for the supervisor {supervisor}")
        held = held_for(supervisor)
        platform.job_stop(job, env())
        check(until(lambda: not held_for(supervisor), 10), f"the assertion outlived the job: {held}")
    finally:
        proc.kill()
        proc.wait()
    return f"{held[0]} on behalf of the job's supervisor while it ran, and nothing after it ended"


def held_for(pid: int) -> list[str]:
    """The idle-sleep assertions `pmset -g assertions` lists as made on behalf of this process."""
    listed = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, check=True).stdout
    owner, found = "", []
    for line in listed.splitlines():
        if line.strip().startswith("pid "):
            owner = line.strip()
        elif line.strip().split()[-1:] == [str(pid)] and "on behalf of Process ID" in line \
                and "PreventUserIdleSystemSleep" in owner:
            found.append(owner.split(" named:")[0])
    return found


@row
def awake_clock():
    """Supporting evidence that a job's time limit counts awake time, as systemd's monotonic limit does: the
    supervisor's limit is `Popen.wait`'s timeout, which counts Python's monotonic clock, on macOS the kernel's uptime
    clock that stops while the Mac sleeps. It starts no job and crosses no sleep. On a Mac that has slept since it
    started, the clock that keeps counting through sleep is ahead by the time slept."""
    if not DARWIN:
        return None
    check(subprocess._time is time.monotonic, "Popen.wait's timeout no longer counts time.monotonic")
    clock = time.get_clock_info("monotonic").implementation
    monotonic, awake = time.monotonic(), time.clock_gettime(time.CLOCK_UPTIME_RAW)
    slept = time.clock_gettime(time.CLOCK_MONOTONIC) - awake
    check(abs(monotonic - awake) < 0.01, f"monotonic ({clock}) {monotonic:.3f}s is not awake time {awake:.3f}s")
    return (f"the limit's clock ({clock}) is awake time; this Mac has slept {slept / 3600:.1f} h since it started, "
            "which that clock left out" if slept >= 1 else
            f"the limit's clock ({clock}) is awake time; this Mac has not slept since it started")


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
def launch_input():
    """A confined job launched as Altitude launches a review: its own folder and a fictional sign-in arrive in the first
    line of its input, the operator's settings are linked into that folder, and another job's saved launch input waits
    in the launcher's job folders as it does before that job's supervisor takes it."""
    if not DARWIN:
        return None  # Linux hands the input over systemd-run's pipe, with no job folder to read
    job, marker = name("input"), f"fixture-{NONCE}"
    jobs = Path(os.path.realpath(platform._jobs()))
    pending = jobs / f"altitude-probe-pending-{NONCE}"
    settings = Path.home() / f".altitude-probe-{NONCE}-settings"
    with tempfile.TemporaryDirectory() as folder:
        own = Path(folder).resolve() / "engine"
        own.mkdir()
        try:
            pending.mkdir(mode=0o700, parents=True)
            (pending / "stdin").write_text(json.dumps({"PROBE_SIGN_IN": marker}) + "\n")
            settings.mkdir()
            (settings / "settings.json").write_text("{}")
            script = (f'read rest; [ "$rest" = own-input ] && echo input; '
                      f'[ "$PROBE_FOLDER" = \'{own}\' ] && printf %s "$PROBE_SIGN_IN" > "$PROBE_FOLDER/sign-in" '
                      '&& echo sign-in; '
                      f'ln -s \'{settings}/settings.json\' "$PROBE_FOLDER/settings.json" && echo link; '
                      'grep -q . "$PROBE_FOLDER/settings.json" && echo read-linked; '
                      'echo x >> "$PROBE_FOLDER/settings.json" 2>/dev/null && echo write-linked; '
                      f"echo x > '{settings}/new' 2>/dev/null && echo write-home; "
                      'echo x > "$PROBE_FOLDER/state" && echo write-own; '
                      f"ls '{jobs}' >/dev/null 2>&1 && echo list-jobs; "
                      f"cat '{jobs}/{platform._label(job)}/spec.json' >/dev/null 2>&1 && echo read-own-job; "
                      f"cat '{pending}/stdin' >/dev/null 2>&1 && echo read-pending; "
                      f"echo x >> '{pending}/stdin' 2>/dev/null && echo write-pending")
            launch = platform.job_command(job, common.with_launch_env(["/bin/sh", "-c", script]), env(),
                                          writable=(own,), runtime_max=60)
            result = subprocess.run(launch, input=json.dumps({"PROBE_FOLDER": str(own), "PROBE_SIGN_IN": marker})
                                    + "\nown-input\n", capture_output=True, text=True, timeout=90)
            signed_in = (own / "sign-in").read_text() == marker
            untouched = (os.listdir(settings) == ["settings.json"] and (settings / "settings.json").read_text() == "{}"
                         and (pending / "stdin").read_text() == json.dumps({"PROBE_SIGN_IN": marker}) + "\n")
        finally:
            shutil.rmtree(pending, ignore_errors=True)
            shutil.rmtree(settings, ignore_errors=True)
    check(result.stdout.split() == ["input", "sign-in", "link", "read-linked", "write-own"] and signed_in and untouched
          and marker not in json.dumps(launch), f"launch input: {result.stdout.split()} {result.stderr.strip()[-300:]!r} "
                                               f"sign-in received: {signed_in}; untouched: {untouched}; "
                                               f"sign-in on the command line: {marker in json.dumps(launch)}")
    check(not platform.job_active(job, env()), "job still active after it ended")
    return ("the job takes its folder and sign-in from its input's first line, not its command line, writes its own "
            "folder and reads but cannot write the home settings linked into it, and can neither list the launcher's "
            "job folders nor read its own job folder or another job's saved launch input there")


LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/"
              "lsregister")


def fixture_app(app: Path, marker: Path) -> None:
    """A background app that LaunchServices would start outside any sandbox; it creates `marker` when it runs. Its
    executable is a script, which names no architecture: without one LaunchServices asks for Rosetta and, on a Mac
    without it, starts nothing."""
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "fixture", "CFBundleIdentifier": f"dev.altitude.probe-{NONCE}", "LSBackgroundOnly": True,
        "LSArchitecturePriority": [os.uname().machine]}))
    (app / "Contents/MacOS/fixture").write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    (app / "Contents/MacOS/fixture").chmod(0o755)


def app_ran(app: Path, marker: Path) -> bool:
    """Whether the fixture app ran, then forget it: its marker removed, LaunchServices' record of it dropped."""
    ran = until(marker.exists, 5)
    marker.unlink(missing_ok=True)
    subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True, timeout=30)
    return ran


def controls(app: Path, marker: Path) -> tuple[bool, str]:
    """Outside any profile, the fixture app must run and the Simulator service must answer, so a refusal inside is the
    profile's. Returns whether the Simulator attempts cover anything here, and Xcode's version or why not."""
    subprocess.run(["/usr/bin/open", "-g", "-j", str(app)], capture_output=True, timeout=60)
    check(app_ran(app, marker), "the fixture app did not run outside the profile, so its attempt shows nothing")
    listed = subprocess.run(["xcrun", "simctl", "list", "runtimes"], capture_output=True, text=True, timeout=60)
    if listed.returncode:
        return False, "Simulator attempts uncovered: the Simulator service does not answer outside the profile either"
    return True, subprocess.run(["xcodebuild", "-version"], capture_output=True, text=True).stdout.split("\n")[0]


def refusal(simulator: bool, error: Path) -> str:
    """The Simulator attempt's own error under the profile, as evidence of what refused it."""
    if not simulator:
        return ""
    lines = error.read_text().strip().splitlines() if error.exists() else []
    return f"; Simulator refused with {lines[0][:200] if lines else 'no error output'!r}"


# Seatbelt's own verdict on an Apple Event from the asking process: 1 refused, 0 allowed, -1 not established. No event
# is sent, so macOS shows no Automation consent prompt and a refusal is the profile's. sandbox_check's filter is
# variadic, which Apple silicon passes on the stack, after the eight argument registers; 4 is its Apple Event
# destination filter.
APPLE_EVENT = r"""
import ctypes, os, platform
check = ctypes.CDLL(None).sandbox_check
pad = [ctypes.c_void_p] * 5 if platform.machine() == "arm64" else []
check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, *pad, ctypes.c_char_p]
def apple_event_verdict():
    return check(os.getpid(), b"appleevent-send", 4, *[None] * len(pad), b"com.apple.Terminal")
"""

# Reads of private places from inside a profile, each after Seatbelt's own verdict on it (filter 1 is a path): when
# the profile would allow one the read is skipped, since macOS could then ask the operator. Gives each read's outcome.
SAFE_READ = APPLE_EVENT + r"""
import errno
def safe_read(path):
    if check(os.getpid(), b"file-read-data", 1, *[None] * len(pad), path.encode()) != 1:
        return "allowed"
    try:
        os.listdir(path)
        return "read"
    except OSError as error:
        return errno.errorcode.get(error.errno, str(error.errno))
"""
PRIVATE_READ = SAFE_READ + r"""
import sys
print(" ".join(safe_read(path) for path in sys.argv[1:]))
"""


def private_place() -> Path:
    """The private place the probes read: the Desktop, where a Mac's consent decision for Python, if any, is kept."""
    return Path.home() / "Desktop"


def privacy_requests(since: float) -> list[str]:
    """macOS's consent requests about Desktop, any mounted volume, or shown as a prompt since `since`, from its
    privacy log: none may follow a sandboxed read, since the profile refuses it before macOS is asked."""
    listed = subprocess.run(
        ["/usr/bin/log", "show", "--start", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since - 1)),
         "--style", "ndjson", "--predicate", 'subsystem == "com.apple.TCC" AND (eventMessage CONTAINS "AUTHREQ_CTX" '
         'OR eventMessage CONTAINS "AUTHREQ_PROMPTING")'], capture_output=True, text=True, timeout=120)
    check(listed.returncode == 0, f"the privacy log is unreadable here: {listed.stderr.strip()[:200]}")
    messages = [json.loads(line).get("eventMessage", "") for line in listed.stdout.splitlines() if line.startswith("{")]
    return [message[:200] for message in messages if "AUTHREQ_PROMPTING" in message or any(
        f"service={service}," in message for service in ("kTCCServiceSystemPolicyDesktopFolder",
                                                         "kTCCServiceSystemPolicyRemovableVolumes",
                                                         "kTCCServiceSystemPolicyNetworkVolumes"))]


@row
def confinement():
    if not DARWIN:
        return None  # Linux leaves file confinement to the engine's own sandbox
    job = name("confined")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        outside = Path.home() / f".altitude-probe-{NONCE}"
        marker = Path.home() / f".altitude-probe-{NONCE}-app"
        fixture_app(root / "fixture.app", marker)
        simulator, xcode = controls(root / "fixture.app", marker)
        label = platform._label(job)
        started = time.time()
        script = (f"echo x > '{root}/inside' && echo write-inside; echo x > '{outside}' 2>/dev/null && echo write-home; "
                  "kill -TERM $PPID 2>/dev/null && echo signal-supervisor; "
                  f"/bin/launchctl kill KILL gui/{os.getuid()}/{label} 2>/dev/null && echo launchctl-kill; "
                  f"/bin/launchctl bootout gui/{os.getuid()}/{label} 2>/dev/null && echo launchctl-bootout; "
                  "sleep 5 & kill $! && echo signal-own-child; "
                  f"git init -q '{root}/repo' && echo git; "
                  f"/usr/bin/open -g -j '{root}/fixture.app' 2>/dev/null && echo open-app; "
                  f"{shlex.quote(sys.executable)} -I -c {shlex.quote(APPLE_EVENT + 'print(apple_event_verdict())')}"
                  " | sed 's/^/apple-event=/'; "
                  f"{shlex.quote(sys.executable)} -I -c {shlex.quote(PRIVATE_READ)} {shlex.quote(str(private_place()))}"
                  " /Volumes | sed 's/^/private=/;s/ /,/'; "
                  f"xcrun simctl list runtimes >/dev/null 2>'{root}/simctl.err' && echo simulator")
        result = subprocess.run(platform.job_command(job, ["/bin/sh", "-c", script], env(), writable=(root,), runtime_max=60),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90)
        written, ran = outside.exists(), app_ran(root / "fixture.app", marker)
        outside.unlink(missing_ok=True)
        check(result.stdout.split() == ["write-inside", "signal-own-child", "git", "apple-event=1",
                                        "private=EPERM,EPERM"]
              and not written and not ran,
              f"confinement: {result.stdout.split()} home written: {written}; fixture app ran: {ran}")
        evidence = refusal(simulator, root / "simctl.err")
        asked = privacy_requests(started)
        check(not asked, f"macOS was asked about a private place: {asked}")
    return ("writes under the roots only; the supervisor, launchd, LaunchServices, Apple Events and the Simulator service "
            "are out of reach; the Desktop and mounted volumes are refused with no consent request in the privacy log; "
            f"own children stay signalable and git works ({xcode}{evidence})")


# Everything a validation run tries, inside the validation profile. True means the attempt succeeded.
ATTEMPTS = r"""
import json, os, plistlib, socket, subprocess, sys
area, outside, home, port, other, label, user_temp, temp = sys.argv[1:9]
port, other, seen = int(port), int(other), {}
def attempt(name, action):
    try:
        action()
        seen[name] = True
    except Exception:
        seen[name] = False
def write(path):
    with open(path, "w") as stream:
        stream.write("x")
def bind(number):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", number))
def connect(number):
    socket.create_connection(("127.0.0.1", number), timeout=5).close()
def unix(path):
    with socket.socket(socket.AF_UNIX) as probe:
        probe.connect(path)
def own_unix(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen()
        unix(path)
def run(*argv):
    subprocess.run(argv, check=True, capture_output=True, timeout=30)
def bootstrap():
    plist = os.path.join(area, "job.plist")
    with open(plist, "wb") as stream:
        plistlib.dump({"Label": label, "ProgramArguments": ["/usr/bin/true"]}, stream)
    run("launchctl", "bootstrap", f"gui/{os.getuid()}", plist)
attempt("write-area", lambda: write(os.path.join(area, "inside")))
attempt("write-home", lambda: write(os.path.join(outside, "written")))
attempt("write-through-linked-root", lambda: write(os.path.join(outside, "linked", "written")))
attempt("link-runner-file", lambda: os.symlink("/etc/hosts", os.path.join(outside, "run.exit.tmp")))
attempt("read-runner-log", lambda: open(os.path.join(outside, "run.log")).read())
attempt("read-home", lambda: open(os.path.join(outside, "secret")).read())
attempt("list-home", lambda: os.listdir(home))
attempt("read-system", lambda: open("/etc/hosts").read())
attempt("list-shared-temp", lambda: os.listdir("/private/tmp"))
attempt("list-user-temp", lambda: os.listdir(os.path.dirname(user_temp.rstrip("/"))))
attempt("write-shared-temp", lambda: write(f"/private/tmp/{label}"))
attempt("resolve", lambda: socket.getaddrinfo("localhost", 80))
attempt("bind-reserved", lambda: bind(port))
attempt("bind-other", lambda: bind(0))
attempt("connect-reserved", lambda: connect(port))
attempt("connect-other", lambda: connect(other))
attempt("unix-home", lambda: unix(os.path.join(outside, "s.sock")))
attempt("unix-area", lambda: own_unix(os.path.join(area, "own.sock")))
attempt("unix-temp", lambda: own_unix(os.path.join(temp, "at-x", "case-y", "home", ".config", "altitude", "coordinator-0123456789ab", "a.sock")))
attempt("unix-system", lambda: unix("/private/var/run/mDNSResponder"))
attempt("keychain", lambda: run("/usr/bin/security", "default-keychain"))
attempt("git", lambda: run("git", "init", "-q", os.path.join(area, "repo")))
attempt("launchd-bootstrap", bootstrap)
attempt("open-app", lambda: run("/usr/bin/open", "-g", "-j", os.path.join(area, "fixture.app")))
try:
    apple_event = apple_event_verdict()
except Exception as error:
    apple_event = repr(error)
def simulator():
    listed = subprocess.run(["xcrun", "simctl", "list", "runtimes"], capture_output=True, text=True, timeout=30)
    with open(os.path.join(area, "simctl.err"), "w") as stream:
        stream.write(listed.stderr)
    listed.check_returncode()
attempt("simulator", simulator)
attempt("signal-supervisor", lambda: os.kill(os.getppid(), 18))
verdicts = {"apple-event": apple_event, "private": [safe_read(os.path.join(home, "Desktop")), safe_read("/Volumes")]}
with open(os.path.join(area, "seen.json"), "w") as stream:
    json.dump({**seen, **verdicts}, stream)
print(json.dumps({**seen, **verdicts}))
"""


@row
def validation_confinement():
    """The validation profile against a run area in the home folder, with a stand-in for Altitude's port."""
    if not DARWIN:
        return None  # Linux validation runs are containers; scripts/container_vm.py covers them
    outside = Path.home() / f".altitude-probe-{NONCE}"
    area, label = outside / "run", f"dev.altitude.probe-validation-{NONCE}"
    temp = platform.validation_temp(NONCE)  # a later root, as a run's own temporary folder is
    listeners = [socket.socket() for _ in range(2)]
    started = time.time()
    try:
        area.mkdir(parents=True)
        temp.mkdir()
        (outside / "secret").write_text("fictional credential\n")
        fixture_app(area / "fixture.app", outside / "launched")
        simulator, xcode = controls(area / "fixture.app", outside / "launched")
        (outside / "linked").symlink_to(outside)  # a root a worker replaced with a link into the home
        (outside / "s.sock").unlink(missing_ok=True)
        home_socket = socket.socket(socket.AF_UNIX)
        listeners.append(home_socket)
        home_socket.bind(str(outside / "s.sock"))
        for listener in listeners:
            if listener.family != socket.AF_UNIX:
                listener.bind(("127.0.0.1", 0))
            listener.listen()
        port, other = (listener.getsockname()[1] for listener in listeners[:2])
        log, status = outside / "run.log", outside / "run.exit"
        command = platform.validation_command((area, outside / "linked", temp), log, port, [sys.executable, "-I", "-c", SAFE_READ + ATTEMPTS, str(area),
                                                              str(outside), str(Path.home()), str(port), str(other),
                                                              label, platform._user_temp(), str(temp)],
                                              {"HOME": str(area), "PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"})
        # As a validation run: a logged job whose output and status are the runner's, beside the candidate's folders.
        subprocess.run(platform.logged_job_command(name("validation"), f"cd {shlex.quote(str(area))} && exec "
                                                   + shlex.join(command), log=log, status=status, env=env(), timeout=60),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        output = log.read_text() if log.exists() else ""
        lines = [line for line in output.splitlines() if line.startswith("{")]
        seen = json.loads(lines[-1]) if lines else {}
        saved = json.loads((area / "seen.json").read_text()) if (area / "seen.json").exists() else None
        check(saved == seen, f"output reached the runner's log: {bool(lines)}; attempts saved in the area {saved}")
        allowed = {"write-area", "read-system", "resolve", "bind-other", "connect-other", "unix-area", "unix-temp",
                   "unix-system", "git"}
        check(seen.get("apple-event") == 1 and seen.get("private") == ["EPERM", "EPERM"]
              and all(seen[key] == (key in allowed) for key in seen if key not in ("apple-event", "private")),
              f"attempts {seen}; status {status.read_text() if status.exists() else None}; output {output[-500:]!r}")
        check(not (outside / "written").exists(), "the home folder was written")
        check(not app_ran(area / "fixture.app", outside / "launched"), "the fixture app ran")
        evidence = refusal(simulator, area / "simctl.err")
        asked = privacy_requests(started)
        check(not asked, f"macOS was asked about a private place: {asked}")
    finally:
        subprocess.run([platform.LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True, timeout=60)
        for listener in listeners:
            listener.close()
        shutil.rmtree(outside, ignore_errors=True)
        shutil.rmtree(temp, ignore_errors=True)
        Path(f"/private/tmp/{label}").unlink(missing_ok=True)  # written only if the profile failed
    return ("only the area is written and read in the home and shared temporary folders; the Desktop and mounted volumes "
            "are refused with no consent request in the privacy log; the reserved port, other "
            "sockets, keychain, launchd, LaunchServices, Apple Events, the Simulator service and the supervisor are out of "
            f"reach; system files, name resolution, other loopback ports, own sockets in every root and git work ({xcode}{evidence})")


# What a process tries against a Simulator iPhone booted outside its profile, as the operator boots one: the phone's
# Web Inspector socket, a program spawned in the phone writing a marker in the home folder, and Xcode's device
# services. Prints which succeeded.
DEVICE_ATTEMPTS = r"""
import json, socket, subprocess, sys
inspector, devices, marker = sys.argv[1:4]
seen = {}
def attempt(name, action):
    try:
        action()
        seen[name] = True
    except Exception:
        seen[name] = False
def inspect():
    with socket.socket(socket.AF_UNIX) as probe:
        probe.settimeout(5)
        probe.connect(inspector)
def run(*argv):
    subprocess.run(argv, check=True, capture_output=True, timeout=60)
attempt("inspector", inspect)
attempt("spawn", lambda: run("xcrun", "simctl", "--set", devices, "spawn", "booted", "/bin/sh", "-c", f"echo x > {marker}"))
attempt("device-services", lambda: run("xcrun", "devicectl", "list", "devices", "--timeout", "10"))
print(json.dumps(seen))
"""


@row
def simulator_escapes():
    """#705's residual paths: an iPhone booted outside any profile, in a private device set removed afterwards. Outside,
    its Web Inspector socket answers and a program spawned in it writes in the home folder; from the worker profile and
    the validation profile both must be out of reach. Xcode's device services are recorded as listed or not; launching
    through them needs a paired device, which this probe does not have."""
    if not DARWIN or "simulator-escapes" not in sys.argv:
        return None  # Simulator runs on macOS only, and boots only when named
    from altitude import simulator
    try:
        chosen = simulator.plan()
    except ValueError:
        return None  # no Xcode or iOS runtime: nothing to escape through
    marker = Path.home() / f".altitude-probe-{NONCE}-spawned"
    with tempfile.TemporaryDirectory() as folder, socket.socket() as reserved:
        root = Path(folder).resolve()
        devices = root / "devices"
        reserved.bind(("127.0.0.1", 0))  # a stand-in for Altitude's port, which the validation profile names
        seen = {}
        try:
            inspector = simulator.Phone(devices, chosen, root / "walks").boot()
            argv = [sys.executable, "-I", "-c", DEVICE_ATTEMPTS, inspector, str(devices), str(marker)]
            for where, command in (
                    ("outside", argv),
                    ("worker", platform.job_command(name("simulator"), argv, env(), writable=(root,), runtime_max=150)),
                    ("validation", platform.validation_command(
                        (root,), root / "validation.log", reserved.getsockname()[1], argv,
                        {"HOME": str(root), "PATH": "/usr/bin:/bin", "LANG": "C"}))):
                result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=200)
                lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
                seen[where] = {**(json.loads(lines[-1]) if lines else {"error": result.stderr.strip()[-300:]}),
                               "written": marker.exists()}
                marker.unlink(missing_ok=True)
        finally:
            left = simulator.remove(devices)
            marker.unlink(missing_ok=True)
        check(left is None, f"the probe's Simulator device set stays: {left}")
        outside = seen.get("outside", {})
        check(outside.get("inspector") and outside.get("spawn") and outside.get("written"),
              f"outside any profile {outside}, so the attempts inside show nothing")
        for where in ("worker", "validation"):
            found = seen.get(where, {})
            check(found.get("inspector") is False and found.get("spawn") is False and found.get("written") is False,
                  f"{where} profile: {found}")
    services = ", ".join(f"{where} {'listed' if seen[where].get('device-services') else 'refused'}"
                         for where in ("outside", "worker", "validation"))
    return (f"an iPhone booted outside any profile ({chosen['device']}, {chosen['runtime']}): its Web Inspector socket and "
            f"a program spawned in it are out of reach from the worker and validation profiles; Xcode's device services: "
            f"{services}; launching through them needs a paired device, uncovered here ({chosen['xcode']})")


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
        started = time.monotonic()
        found = platform.client_groups([peer[0]], peer[1], ["127.0.0.1"], accepted.getsockname()[1])
        cost = (time.monotonic() - started) * 1000
        check(found and all(ours for _, ours in found), f"own client socket not attributed to this process: {found}")
        table = platform.process_table()
        check(me in table and table[me][0] == os.getppid(), "process table misses this process")
    finally:
        for sock in (client, accepted, listener):
            sock.close()
    return f"identity, liveness, names, the process table and TCP client attribution ({cost:.0f} ms) are readable"


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
