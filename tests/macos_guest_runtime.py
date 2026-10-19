"""Runtime rows of the macOS VM lane, run inside the offline guest by its account against its own installed Altitude.

The host's harness (`scripts/installation_macos_vm.py`, the `workers`, `grants`, `incident` and `session` phases)
unpacks the revision's files in the guest, runs `setup`, installs the built release through the public command with
the fictional engine and GitHub CLI below, then runs `rows NAME...`. Only the engine CLI (`macos_guest_engine.py`) and
GitHub CLI (the tests' fake) are fixtures: altd, its LaunchAgent, launchd jobs, Seatbelt, task records, dispatch, Stop,
grants, validation runs and incident filing are the release's own. The operator's actions are `alt` commands from this
account's shell; the coordinator's and the owner's are `alt` commands the fictional engine runs in their turns.

    macos_guest_runtime.py setup               fixture engine, fake gh and a fictional project with a local origin
    macos_guest_runtime.py rows NAME...        the named rows; one JSON line per row, then a summary
    macos_guest_runtime.py grant QUESTION REVISION [MESSAGE]
                                               inside an owner's turn: resolve the operator's answer and record the grant
"""
import ast
import base64
import hashlib
import io
import json
import os
import re
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
ALT = HOME / ".local/bin/alt"
ROOT = HOME / ".altitude"
PROJECT = "fictional"
SHARED = Path("/Users/Shared/altitude-vm")
SOURCE = SHARED / "source"
FIXTURE = SHARED / "fixture"
TEMP = Path("/private/tmp/altitude-fixture")  # writable inside a worker's sandbox too
LOG = TEMP / "log.jsonl"
GH_STATE = TEMP / "gh"
PYTHON = "/opt/homebrew/bin/python3.12"
UID = os.getuid()
SERVICE = f"gui/{UID}/dev.altitude.altd"
ENGINE_PROCESS = "macos_guest_engine.py"
#: The shell's environment without any actor or task identity, so `alt` acts as the operator.
OPERATOR = {key: value for key, value in os.environ.items() if not key.startswith("ALTITUDE_")}


class Failed(Exception):
    pass


# ---- Setup ----------------------------------------------------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30).stdout


def setup() -> dict:
    """The fixture engine and fake gh as executables, the shared temporary folder and the fictional project."""
    (FIXTURE / "bin").mkdir(parents=True, exist_ok=True)
    TEMP.mkdir(exist_ok=True)
    TEMP.chmod(0o1777)
    GH_STATE.mkdir(exist_ok=True)
    engine = FIXTURE / "codex"
    engine.write_text(f"#!/bin/sh\nexec {PYTHON} -B {SOURCE}/tests/macos_guest_engine.py \"$@\"\n")
    # The tests' fake gh, as tests/support.py defines it, logging every call and answering from GH_STATE.
    support = ast.parse((SOURCE / "tests/support.py").read_text())
    shim = next(ast.literal_eval(node.value) for node in support.body if isinstance(node, ast.Assign)
                and any(getattr(target, "id", None) == "GH" for target in node.targets))
    (FIXTURE / "fake_gh.py").write_text(shim)
    gh = FIXTURE / "bin/gh"
    gh.write_text(f"#!/bin/sh\nFAKE_GH_DIR={GH_STATE} exec {PYTHON} -B {FIXTURE}/fake_gh.py \"$@\"\n")
    for path in (engine, gh):
        path.chmod(0o755)
    repo = HOME / "Projects" / PROJECT
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture Operator")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text(".claude/\n")
    (repo / "AGENTS.md").write_text("Fictional macOS guest acceptance. No external operations.\n")
    (repo / "README.md").write_text("Fictional project.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Fictional initial commit")
    origin = repo.with_name(PROJECT + "-origin.git")
    git(repo, "init", "-q", "--bare", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-qu", "origin", "main")
    return {"engine": str(engine), "gh": str(gh), "project": str(repo)}


# ---- Reading and acting -------------------------------------------------------------------------------------------

STEPS: list[dict] = []


def alt(*args: str, stdin: str | None = None, timeout: int = 120, check: bool = True, project: bool = True):
    """One `alt` command as the operator; its JSON answer, else its text."""
    command = [str(ALT), *(["-p", PROJECT] if project else []), *args]
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout, env=OPERATOR)
    STEPS.append({"alt": list(args), "exit": result.returncode, "stdout": result.stdout[-2000:],
                  "stderr": result.stderr[-2000:]})
    if check and result.returncode:
        raise Failed(f"alt {' '.join(args)} exited {result.returncode}: {result.stderr.strip()[-500:]}")
    try:
        return json.loads(result.stdout)
    except ValueError:
        return result.stdout


def shell(command: str, timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise Failed(f"{command} exited {result.returncode}: {result.stderr.strip()[-500:]}")
    return result


def folder(slug: str) -> Path:
    return ROOT / PROJECT / "tasks" / slug


def task(slug: str) -> dict:
    try:
        return json.loads((folder(slug) / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def lines(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        return []


def events(slug: str, kind: str | None = None) -> list[dict]:
    return [event for event in lines(folder(slug) / "events.log") if kind is None or event.get("kind") == kind]


def machine(slug: str) -> list[dict]:
    return lines(folder(slug) / "machine.jsonl")


def turns(slug: str) -> list[dict]:
    """What the fixture engine recorded for this task: its turns and their commands."""
    return [entry for entry in lines(LOG) if entry.get("task") == slug]


def wait(what: str, check, timeout: float = 150, every: float = 2):
    """CHECK's first truthy answer within TIMEOUT seconds."""
    deadline = time.monotonic() + timeout
    while True:
        found = check()
        if found:
            return found
        if time.monotonic() > deadline:
            raise Failed(f"timed out after {timeout:.0f} s waiting for {what}")
        time.sleep(every)


def job_running(unit: str) -> bool:
    """Whether launchd runs this job of the installation's launcher."""
    label = "dev.altitude.job." + unit.removesuffix(".service")
    found = subprocess.run(["launchctl", "print", f"gui/{UID}/{label}"], capture_output=True, text=True, timeout=30)
    return found.returncode == 0 and "state = running" in found.stdout


def processes(pattern: str) -> list[str]:
    found = subprocess.run(["pgrep", "-fl", pattern], capture_output=True, text=True, timeout=30)
    return [line for line in found.stdout.splitlines() if "pgrep" not in line]


def engines_of(slug: str) -> list[str]:
    """The fictional engine's worker processes for this task, told apart by the worktree in their sandbox roots."""
    return processes(f'{ENGINE_PROCESS} app-server.*worktrees/{slug}"')


def worker_unit(slug: str) -> str:
    current = task(slug)
    record = json.loads((folder(slug) / "l2-engine" / f"{current['agent_id']}.json").read_text())
    return record["unit"]


def service_pid() -> int | None:
    found = subprocess.run(["launchctl", "print", SERVICE], capture_output=True, text=True, timeout=30)
    pid = re.search(r"^\tpid = (\d+)$", found.stdout, re.M)
    return int(pid[1]) if found.returncode == 0 and "state = running" in found.stdout and pid else None


def healthy() -> bool:
    environment = json.loads((HOME / ".config/altitude/install.json").read_text()).get("environment", {})
    port = environment.get("ALTITUDE_PORT", "8890")
    return shell(f"curl -fsS --max-time 5 --cacert {HOME}/.config/altitude/tls/ca.crt "
                 f"https://127.0.0.1:{port}/api/health").returncode == 0


def restart() -> dict:
    """The guest's own service restarted as launchd restarts a crashed job: a new process, its records unchanged."""
    before = service_pid()
    shell(f"launchctl kickstart -k {SERVICE}", check=True)
    after = wait("the service's new process", lambda: (service_pid() or before) != before and healthy() and service_pid(),
                 timeout=120)
    return {"before": before, "after": after}


def new_task(title: str, brief: str) -> str:
    created = alt("task", "new", "--title", title, "--engine", "codex", "-", stdin=brief)
    slug = created["slug"]
    wait(f"{slug} to start its first turn", lambda: turns(slug) and task(slug).get("state") in ("running", "blocked"),
         timeout=150)
    return slug


def page(path: str, body: dict | None = None, timeout: float = 60):
    """The service's answer to one request as the page sends it, with the CLI's machine key."""
    environment = json.loads((HOME / ".config/altitude/install.json").read_text()).get("environment", {})
    request = urllib.request.Request(
        f"https://127.0.0.1:{environment.get('ALTITUDE_PORT', '8890')}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "X-Altitude-Key": (HOME / ".config/altitude/access/machine.key").read_text().strip()})
    context = ssl.create_default_context(cafile=str(HOME / ".config/altitude/tls/ca.crt"))
    return urllib.request.urlopen(request, context=context, timeout=timeout)


def coordinator(text: str) -> None:
    """One coordinator turn in the service, started as the operator's chat message from the page starts it, with the
    CLI's machine key: the project's coordinator socket serves only the service's own running turn, so `alt chat`,
    which runs its turn in the calling process, cannot stand in for it. Returns once the turn ran every command."""
    commands = [line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("FIXTURE-RUN:")]
    before = len(lines(LOG))
    with page("/api/chat", {"project": PROJECT, "text": text}, timeout=300) as response:
        answer = response.read().decode(errors="replace")
    STEPS.append({"chat": text, "answer": answer[-1000:]})
    wait("the coordinator's commands", lambda: all(any(entry.get("role") == "l3" and entry.get("command") == command
                                                       for entry in lines(LOG)[before:]) for command in commands),
         timeout=120)
    for entry in lines(LOG)[before:]:
        if entry.get("role") == "l3" and entry.get("command") in commands and entry.get("exit"):
            raise Failed(f"the coordinator's {entry['command']!r} exited {entry['exit']}: {entry.get('stderr', '')[-300:]}")


def open_question(slug: str, audience: str = "operator") -> dict | None:
    return next((question for question in reversed(task(slug).get("questions") or [])
                 if question.get("status") == "open" and question.get("audience") == audience), None)


def stopped_cleanly(slug: str) -> None:
    """The operator's Stop of a fixture task that still runs, so the next row starts with no worker."""
    if task(slug).get("state") == "running":
        alt("task", "stop", slug, "--reason", "Fixture row complete", check=False)
        wait(f"{slug} to stop", lambda: task(slug).get("state") != "running", timeout=120)


def ran(slug: str, fragment: str) -> dict | None:
    return next((entry for entry in turns(slug) if fragment in entry.get("command", "")), None)


# ---- Rows -----------------------------------------------------------------------------------------------------------

def row_coordinator_stop() -> dict:
    """#779: the coordinator's Stop ends a live worker and waits on the coordinator, publishing no operator question."""
    slug = new_task("Coordinator Stop", "Fictional task for the coordinator's Stop.\nFIXTURE-END: hold 500\n")
    unit = worker_unit(slug)
    wait("the worker's launchd job", lambda: job_running(unit), timeout=60)
    coordinator(f"FIXTURE-RUN: alt task stop {slug} --reason 'Fictional coordinator Stop'")
    stopped = wait("the Stop", lambda: task(slug).get("state") == "blocked" and task(slug), timeout=120)
    wait("the worker's job to end", lambda: not job_running(unit), timeout=60)
    conversation = lines(folder(slug) / "conversation.jsonl")
    facts = {"task": slug, "waiting_on": stopped.get("waiting_on"), "block_actor": stopped.get("block_actor"),
             "stop_id": bool(stopped.get("stop_id")), "operator_question": open_question(slug) is not None,
             "l3_note": any(row.get("role") == "l3" for row in conversation),
             "engine_processes": engines_of(slug)}
    facts["passed"] = (facts["waiting_on"] == "l3" and facts["stop_id"] and not facts["operator_question"]
                       and not facts["engine_processes"])
    return facts


def row_failed_turn() -> dict:
    """#782: an engine that stays alive after a failed turn, with a command it started still running, is ended at
    that turn: its whole launchd job stops, rather than the worker reading as working until a later timeout."""
    go = TEMP / f"fail-{os.getpid()}"  # the turn fails only once the row has seen its job and command running
    slug = new_task("Failed turn", "Fictional task whose turn fails.\nFIXTURE-RUN: nohup /bin/sleep 901 >/dev/null 2>&1 &\n"
                    f"FIXTURE-WAIT: {go}\nFIXTURE-END: failed-linger 500\n")
    unit = worker_unit(slug)
    wait("the background command", lambda: ran(slug, "sleep 901") and processes("sleep 901"), timeout=60)
    if not job_running(unit):
        raise Failed("the worker's job was not running before its turn failed")
    go.touch()
    failed = wait("the failed turn", lambda: next((entry for entry in turns(slug) if entry.get("failed")), None), timeout=60)
    wait("the task to leave running", lambda: task(slug).get("state") != "running", timeout=150)
    ended = time.time()
    wait("the job to end", lambda: not job_running(unit), timeout=60)
    current = task(slug)
    facts = {"task": slug, "state": current.get("state"), "blocked_reason": current.get("blocked_reason"),
             "seconds_after_failure": round(ended - failed["at"]), "background_command": processes("sleep 901"),
             "engine_processes": engines_of(slug)}
    facts["passed"] = not facts["background_command"] and not facts["engine_processes"] and facts["seconds_after_failure"] < 150
    return facts


def row_status_unknown() -> dict:
    """#678: a worker whose job cannot be read stays the task's owner: unknown status, no death, one fault."""
    slug = new_task("Unknown worker status", "Fictional task whose worker status becomes unreadable.\nFIXTURE-END: hold 500\n")
    before = task(slug)
    record_path = folder(slug) / "l2-engine" / f"{before['agent_id']}.json"
    original = record_path.read_text()
    record = json.loads(original)
    unit = record["unit"]
    try:
        # A worker record with no job name, which the daemon can then only read through launchd, not its own child.
        record_path.write_text(json.dumps({**record, "unit": ""}))
        restarted = restart()
        live = ROOT / "monitor" / f"live-{PROJECT}--{slug}.json"
        wait("the unknown status", lambda: (json.loads(live.read_text()) if live.exists() else {})
             .get("agent", {}).get("status") == "unknown", timeout=120)
        during = task(slug)
        faults = json.loads((ROOT / "monitor/faults.json").read_text()) if (ROOT / "monitor/faults.json").exists() else {}
        facts = {"task": slug, "restart": restarted, "state": during.get("state"),
                 "same_worker": (during.get("agent_id"), during.get("session_id")) == (before.get("agent_id"), before.get("session_id")),
                 "job_kept": job_running(unit), "fault_keys": sorted(faults)}
    finally:
        record_path.write_text(original)
    facts["recovered"] = bool(wait("the worker read again", lambda: (json.loads(live.read_text())
                                   .get("agent", {}).get("status") not in (None, "unknown")), timeout=120))
    stopped_cleanly(slug)
    facts["passed"] = (facts["state"] == "running" and facts["same_worker"] and facts["job_kept"]
                       and any("worker-status" in key for key in facts["fault_keys"]) and facts["recovered"])
    return facts


def row_validation_restart() -> dict:
    """#650: a validation run that the service's restart interrupts is recorded as interrupted, with its log and
    results kept in the task folder."""
    slug = new_task("Validation across a restart", "Fictional task that validates its candidate.\n"
                    "FIXTURE-RUN: alt task validate -- /bin/sh -c 'echo started > \"$VALIDATION_RESULTS/started.txt\"; "
                    "echo validation-started; sleep 240'\n")
    row = wait("the validation run", lambda: next((r for r in machine(slug) if r.get("purpose") == "validation"), None),
               timeout=120)
    wait("its launchd job", lambda: job_running(row["unit"]), timeout=60)
    time.sleep(5)
    restarted = restart()
    ended = wait("the interrupted record", lambda: next((r for r in machine(slug) if r.get("n") == row["n"]
                                                          and r.get("finished")), None), timeout=150)
    results = folder(slug) / "validation" / str(row["n"])
    log = folder(slug) / "validation" / f"{row['n']}.log"
    facts = {"task": slug, "restart": restarted, "row": {key: ended.get(key) for key in
                                                         ("n", "ended", "exit", "error", "isolation", "finished")},
             "results": sorted(path.name for path in results.iterdir()) if results.is_dir() else [],
             "log": log.read_text()[-400:] if log.exists() else None, "job_running": job_running(row["unit"])}
    stopped_cleanly(slug)
    facts["passed"] = (ended.get("ended") == "interrupted" and "started.txt" in facts["results"]
                       and facts["log"] is not None and "validation-started" in facts["log"] and not facts["job_running"])
    return facts


def granted(slug: str, question: dict, message: str | None = None) -> str:
    """The owner's directives that record the operator's answer as its grant."""
    return (f"FIXTURE-RUN: {PYTHON} -B {SOURCE}/tests/macos_guest_runtime.py grant {question['id']} "
            f"{question['revision']}{' ' + message if message else ''}\n")


def running_command(slug: str, n: int) -> dict:
    row = wait(f"{slug}'s command {n}", lambda: next((r for r in machine(slug) if r.get("n") == n), None), timeout=120)
    wait(f"{slug}'s command {n} to run", lambda: job_running(row["unit"]), timeout=60)
    return row


def finished_command(slug: str, n: int, timeout: float = 90) -> dict:
    return wait(f"{slug}'s command {n} to finish", lambda: next((r for r in machine(slug) if r.get("n") == n
                                                                 and r.get("finished")), None), timeout=timeout)


def row_grants() -> dict:
    """#656 and #668: a granted command runs as its own launchd job outside the sandbox; revoking the grant stops
    it, also when the service restarted while it ran; the coordinator applies the operator's answer from one task to
    another task with the identical purpose."""
    first = new_task("Granted commands", "Fictional task that asks for a purpose.\n"
                     "FIXTURE-RUN: alt task block \"$ALTITUDE_TASK\" --for-operator --reason "
                     "'May this task run fictional commands outside its sandbox for this check?'\n")
    question = wait("the operator question", lambda: task(first).get("state") == "blocked" and open_question(first))
    alt("task", "message", first, "Yes, fictional commands for this check.\n" + granted(first, question)
        + "FIXTURE-RUN: alt task run \"$ALTITUDE_TASK\" '/bin/sleep 300'\n"
        + "FIXTURE-RUN: alt task block \"$ALTITUDE_TASK\" --reason 'Fictional step done'\n")
    command = running_command(first, 1)
    grant = task(first).get("grant") or {}
    started = time.monotonic()
    alt("task", "grant", first, "--revoke", "--reason", "Fictional revocation while the command runs")
    revoked = finished_command(first, 1)
    facts = {"tasks": [first], "grant": {key: grant.get(key) for key in ("id", "question", "revision", "source")},
             "revoked": {"seconds": round(time.monotonic() - started), "exit": revoked.get("exit"),
                         "error": revoked.get("error"), "job_running": job_running(command["unit"])}}
    resolution = next(q for q in reversed(task(first)["questions"]) if q["id"] == question["id"]).get("resolution") or {}
    answer = resolution.get("message_id")
    wait(f"{first} to block again", lambda: task(first).get("state") == "blocked" and ran(first, "Fictional step done"))

    second = new_task("Granted commands, same purpose", "Fictional task that needs the same purpose.\n"
                      "FIXTURE-RUN: alt task block \"$ALTITUDE_TASK\" --reason 'This task needs the fictional command "
                      "purpose the operator approved for the first task.'\n")
    facts["tasks"].append(second)
    wait(f"{second} to block", lambda: task(second).get("state") == "blocked")
    coordinator(f"FIXTURE-RUN: alt task grant {second} --from-task {first} --approval {answer} --question {question['id']} "
                f"--revision {question['revision']} --attempt {task(second)['attempt']} --reason 'Identical purpose'")
    reused = task(second).get("grant") or {}
    facts["reused"] = {key: reused.get(key) for key in ("from_task", "approval", "question", "actor")}
    alt("task", "message", second, "Run the fictional command.\n"
        "FIXTURE-RUN: alt task run \"$ALTITUDE_TASK\" '/bin/sleep 300'\n"
        "FIXTURE-RUN: alt task block \"$ALTITUDE_TASK\" --reason 'Fictional step done'\n")
    command = running_command(second, 1)
    restarted = restart()
    survived = job_running(command["unit"])
    alt("task", "grant", second, "--revoke", "--reason", "Fictional revocation after a restart")
    after = finished_command(second, 1)
    facts["restart_revoked"] = {"restart": restarted, "survived_restart": survived, "exit": after.get("exit"),
                                "error": after.get("error"), "job_running": job_running(command["unit"])}
    wait(f"{second} to block again", lambda: task(second).get("state") == "blocked" and ran(second, "Fictional step done"))
    stopped = lambda outcome: "revoked" in str(outcome.get("error")).lower() and not outcome["job_running"]
    facts["passed"] = (stopped(facts["revoked"]) and facts["revoked"]["seconds"] < 60
                       and facts["reused"]["from_task"] == first and facts["reused"]["approval"] == answer
                       and survived and stopped(facts["restart_revoked"]))
    STATE["grants"] = {"first": first, "second": second, "question": question, "answer": answer}
    return facts


def row_contention() -> dict:
    """#728: with one machine-run place (4 CPUs), a second task's granted command waits in line while the first
    task's runs, says what holds the place, and runs once it ends."""
    if "grants" not in STATE:
        row_grants()
    first, second, question, answer = (STATE["grants"][key] for key in ("first", "second", "question", "answer"))
    places = alt("machine", "show", project=False)
    alt("task", "message", first, "Run the long fictional command again.\n" + granted(first, question, answer)
        + "FIXTURE-RUN: alt task run \"$ALTITUDE_TASK\" '/bin/sleep 40'\n"
        + "FIXTURE-RUN: alt task block \"$ALTITUDE_TASK\" --reason 'Fictional step two done'\n")
    holder = running_command(first, 2)
    coordinator(f"FIXTURE-RUN: alt task grant {second} --from-task {first} --approval {answer} --question {question['id']} "
                f"--revision {question['revision']} --attempt {task(second)['attempt']} --reason 'Identical purpose'")
    alt("task", "message", second, "Run the short fictional command.\n"
        "FIXTURE-RUN: alt task run \"$ALTITUDE_TASK\" '/bin/echo second-place'\n"
        "FIXTURE-RUN: alt task block \"$ALTITUDE_TASK\" --reason 'Fictional step two done'\n")
    held = finished_command(first, 2, timeout=120)
    waiter = finished_command(second, 2, timeout=120)
    entry = wait("the waiting command's output", lambda: ran(second, "second-place"), timeout=60)
    facts = {"tasks": [first, second], "machine_runs": places.get("machine_runs") if isinstance(places, dict) else places,
             "holder": {key: held.get(key) for key in ("started", "finished", "exit")},
             "waiter": {key: waiter.get(key) for key in ("started", "finished", "exit")},
             "waiter_said": entry.get("stderr", "")[-600:], "holder_job": holder["unit"]}
    # Only a command that waited is told what holds the place; this one must name the first task's command.
    facts["passed"] = (facts["machine_runs"] == 1 and held.get("exit") == 0 and waiter.get("exit") == 0
                       and waiter["started"] >= held["finished"] and entry.get("exit") == 0
                       and f"waiting for a machine-run place: the command of {PROJECT}/{first} " in entry.get("stderr", ""))
    for slug in (first, second):
        if task(slug).get("grant"):
            alt("task", "grant", slug, "--revoke", "--reason", "Fixture row complete", check=False)
    return facts


def lookup(version: str) -> dict:
    """Serve VERSION as GitHub's latest release, make the daemon's 12-hour check due and wait for its answer."""
    latest = Path(os.environ["LANE_LATEST"])
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps({"tag_name": version, "draft": False}))
    path = ROOT / "update.json"
    script = ("import fcntl,json,sys; p=sys.argv[1]; l=open(p.replace('update.json','update.lock'),'a');"
              "fcntl.flock(l,fcntl.LOCK_EX); r=json.load(open(p)); r['next']=0; "
              "open(p+'.lane','w').write(json.dumps(r)); __import__('os').replace(p+'.lane',p)")
    subprocess.run([PYTHON, "-c", script, str(path)], check=True, timeout=30)
    try:
        return wait(f"the check to find {version}", lambda: (json.loads(path.read_text()).get("latest") or {})
                    .get("version") == version and json.loads(path.read_text()), timeout=90)
    except Failed as error:
        fetched = shell(f"curl -sS --max-time 10 --cacert {SHARED}/ca.pem "
                        f"https://api.github.com/{latest.relative_to(SHARED / 'root')}")
        answer = subprocess.run(
            [PYTHON, "-B", "-c", "import sys, traceback; sys.path.insert(0, sys.argv[1]); "
             "from altitude import config, installation\ntry: print(installation.latest_release("
             "installation.release_repository(), config.RELEASE['version']))\n"
             "except Exception: traceback.print_exc()", str(HOME / ".local/share/altitude/current")],
            capture_output=True, text=True, timeout=90,
            env={**OPERATOR, "ALTITUDE_CONFIG": str(HOME / ".config/altitude/install.json")})
        service = [line.strip() for line in shell(f"launchctl print {SERVICE}").stdout.splitlines()
                   if "SSL_CERT_FILE" in line or "PATH =>" in line]
        raise Failed(f"{error}; update.json {path.read_text()[:400]}; fetch {fetched.returncode} "
                     f"{(fetched.stdout + fetched.stderr)[:300]}; the release's lookup "
                     f"{(answer.stdout + answer.stderr)[-1200:]}; service environment {service}")


def row_incident() -> dict:
    """#767: on an installed release behind a newer published release, an incident's issue is held and nothing is
    sent to GitHub until the operator asks; when current, the issue goes straight to GitHub's issue creation (here
    the fake gh, so nothing is published)."""
    def calls() -> list[list[str]]:
        return [json.loads(line) for line in (GH_STATE / "log.jsonl").read_text().splitlines()] \
            if (GH_STATE / "log.jsonl").exists() else []

    settings = ROOT / "settings.json"
    current = json.loads(settings.read_text()) if settings.exists() else {}
    settings.write_text(json.dumps({**current, "update_automatic": False}))  # the newer release is a lookup answer only
    version = os.environ["LANE_VERSION"]
    newer = re.sub(r"(\d+)$", lambda m: str(int(m[1]) + 1), version)
    lookup(newer)
    before = len(calls())
    held = alt("incident", "new", "--title", "Fictional incident behind a release", "--what", "A fictional fault.",
               "--evidence", "Fictional evidence from the macOS VM lane.", "--cause", "Fictional cause.")
    held_calls = calls()[before:]
    published = alt("incident", "publish", held["id"])
    publish_calls = calls()[before + len(held_calls):]
    lookup(version)
    before = len(calls())
    direct = alt("incident", "new", "--title", "Fictional incident on the current release", "--what", "Another fault.",
                 "--evidence", "Fictional evidence from the macOS VM lane.", "--cause", "Fictional cause.")
    direct_calls = calls()[before:]
    issues = json.loads((GH_STATE / "issues.json").read_text()) if (GH_STATE / "issues.json").exists() else []
    created = lambda made: any(call[:2] == ["issue", "create"] for call in made)
    facts = {"installed": version, "newer": newer, "held": {"answer": held, "gh_calls": held_calls},
             "published_on_request": {"answer": published, "gh_create": created(publish_calls)},
             "current": {"answer": direct, "gh_create": created(direct_calls)},
             "issue_bodies_name_version": all(version in issue.get("body", "") for issue in issues),
             "issues": len(issues)}
    facts["passed"] = (held.get("held") is True and not held_calls and created(publish_calls)
                       and created(direct_calls) and facts["issue_bodies_name_version"] and len(issues) == 2)
    return facts


def row_schedule() -> dict:
    """#226, #731: the daemon looks for a release again 12 hours after its last lookup and not before, and the
    terminal names a newer release once a day. The guest's own clock is moved forward; nothing in Altitude's records
    is rewritten. Runs last in its phase: the clock stays moved."""
    path = ROOT / "update.json"
    record = lambda: json.loads(path.read_text()) if path.exists() else {}  # noqa: E731
    latest = Path(os.environ["LANE_LATEST"])
    serve = lambda version: latest.write_text(json.dumps({"tag_name": version, "draft": False}))  # noqa: E731
    found = lambda version: (record().get("latest") or {}).get("version") == version  # noqa: E731

    def move_to(moment: float) -> None:
        shell(f"sudo -n /bin/date -u {time.strftime('%m%d%H%M%Y.%S', time.gmtime(moment))}", check=True)

    def notice() -> str:
        """What `alt queue` prints to a terminal before its own output: the once-a-day line, if due."""
        shown = shell(f"/usr/bin/env -u ALTITUDE_ACTOR /usr/bin/script -q /dev/null {ALT} queue", timeout=60).stdout
        return next((line[line.index("Altitude"):].strip() for line in shown.splitlines() if "is available" in line), "")

    settings = ROOT / "settings.json"
    current = json.loads(settings.read_text()) if settings.exists() else {}
    settings.write_text(json.dumps({**current, "update_automatic": False}))  # a newer release is only offered
    version = os.environ["LANE_VERSION"]
    newer = re.sub(r"(\d+)$", lambda m: str(int(m[1]) + 1), version)
    latest.parent.mkdir(parents=True, exist_ok=True)
    serve(version)
    facts: dict = {"installed": version, "newer": newer}
    start = record()
    if not start.get("checked"):  # the startup lookup failed before a release was served: its hourly retry
        facts["retry"] = {"failed_at_next": start.get("next")}
        move_to(start["next"] + 5)
        wait("the hourly retry", lambda: record().get("checked"), timeout=120)
    base = record()
    facts.update(checked=base["checked"], interval_hours=round((base["next"] - base["checked"]) / 3600, 3))
    serve(newer)
    move_to(base["next"] - 120)
    time.sleep(75)  # more than two of the daemon's 30-second ticks, two minutes before the lookup is due
    facts["early_lookup"] = record().get("checked") != base["checked"]
    move_to(base["next"] + 5)
    due = wait(f"the 12-hour lookup to find {newer}", lambda: found(newer) and record(), timeout=120)
    facts["due_lookup"] = {"checked": due["checked"], "after_due": due["checked"] >= base["next"]}
    first, second = notice(), notice()
    move_to(time.time() + 86400 + 60)
    facts["notice"] = {"first": first, "same_day": second, "next_day": notice()}
    facts["passed"] = (facts["interval_hours"] == 12 and not facts["early_lookup"] and facts["due_lookup"]["after_due"]
                       and newer in first and not second and newer in facts["notice"]["next_day"])
    return facts


def relabelled(version: str, newer: str) -> None:
    """The installed release's archive served as NEWER, changed only in the version it declares, as the lifecycle
    lane's synthetic releases are: a release the daemon installs without a second build."""
    repository = Path(os.environ["LANE_LATEST"]).parent.parent.relative_to(SHARED / "root/repos")
    downloads = SHARED / "root" / repository / "releases/download"
    (downloads / newer).mkdir(parents=True, exist_ok=True)
    archive = downloads / newer / f"altitude-{newer}.tar.gz"
    with tarfile.open(downloads / version / f"altitude-{version}.tar.gz") as source, \
            tarfile.open(archive, "w:gz") as bundle:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            if member.name == "release.json":
                data = (json.dumps({**json.loads(data), "version": newer}, indent=2) + "\n").encode()
                member.size = len(data)
            bundle.addfile(member, io.BytesIO(data) if data is not None else None)
    (downloads / newer / f"{archive.name}.sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "\n")


def row_quiet_point() -> dict:
    """#1003: an automatic update offered while a validation run holds the machine waits for that run and names it
    in `alt doctor`; a new validation request is told Altitude restarts soon and runs only after the restart; once
    the run ends the update installs itself and the service restarts into it while the validating task's worker
    stays in its turn."""
    record = lambda: json.loads((ROOT / "update.json").read_text())  # noqa: E731
    installed = lambda: json.loads((HOME / ".local/share/altitude/current/release.json").read_text())["version"]  # noqa: E731
    settings = ROOT / "settings.json"
    current = json.loads(settings.read_text()) if settings.exists() else {}
    settings.write_text(json.dumps({**current, "update_automatic": True}))
    version = os.environ["LANE_VERSION"]
    newer = re.sub(r"(\d+)$", lambda m: str(int(m[1]) + 1), version)
    relabelled(version, newer)
    running = new_task("Validation during an update", "Fictional task that validates its candidate, then stays in "
                       "its turn.\nFIXTURE-RUN: alt task validate -- /bin/sh -c 'echo validation-started; sleep 120'\n"
                       "FIXTURE-END: hold 400\n")
    run = wait("the validation run", lambda: next((r for r in machine(running) if r.get("purpose") == "validation"),
                                                  None), timeout=120)
    wait("its launchd job", lambda: job_running(run["unit"]), timeout=60)
    held = f"the validation run of {PROJECT}/{running}"
    unit, before = worker_unit(running), service_pid()  # the service cannot restart while the run holds the machine
    lookup(newer)
    waiting = wait("the offered update to name the run", lambda: (record().get("waiting") or {}).get("for") == [held]
                   and record()["waiting"], timeout=90)
    doctor = next((line for line in alt("doctor", project=False).splitlines() if line.startswith("Update:")), "")
    later = new_task("Validation while an update waits", "Fictional task that asks for a validation run while an "
                     "update waits.\nFIXTURE-RUN: alt task validate -- /bin/sh -c 'echo second-run'\n")
    ended = wait("the validation run to end", lambda: next((r for r in machine(running) if r.get("n") == run["n"]
                                                            and r.get("finished")), None), timeout=150)
    attempt = wait("the automatic update to start", lambda: (record().get("attempt") or {}).get("version") == newer
                   and record()["attempt"], timeout=120)
    try:
        wait(f"the service to restart into {newer}", lambda: installed() == newer
             and (service_pid() or before) != before and healthy(), timeout=180)
    except Failed as error:
        raise Failed(f"{error}: installed {installed()}, service {before} then {service_pid()}, healthy {healthy()}, "
                     f"attempt {record().get('attempt')}") from None
    second = wait("the second request's answer", lambda: ran(later, "alt task validate"), timeout=120)
    # The restarted service's start, to the second; a run it started begins at or after it.
    activated = datetime.strptime(shell(f"ps -o lstart= -p {service_pid()}").stdout.strip(),
                                  "%a %b %d %H:%M:%S %Y").timestamp()
    after_update = lambda row: datetime.fromisoformat(row["started"]).timestamp() >= activated  # noqa: E731
    facts = {"installed": version, "newer": newer, "waiting": waiting, "doctor": doctor,
             "second": {key: second.get(key) for key in ("exit", "stderr")},
             "second_runs": [{key: r.get(key) for key in ("n", "started", "exit")} for r in machine(later)
                             if r.get("purpose") == "validation"],
             "run": {key: ended.get(key) for key in ("n", "ended", "exit", "finished")},
             "attempt_after_run_s": round(attempt["started"] - datetime.fromisoformat(ended["finished"]).timestamp(), 1),
             "attempt": wait("the attempt's outcome", lambda: record()["attempt"].get("state") != "running"
                             and record()["attempt"], timeout=60),
             "now": installed(), "service": {"before": before, "after": service_pid(), "started": activated},
             "worker_kept": {"job": job_running(unit), "unit": worker_unit(running) == unit,
                             "state": task(running).get("state")}}
    stopped_cleanly(later)
    stopped_cleanly(running)
    facts["passed"] = (doctor == f"Update: Altitude {newer} installs automatically at the next quiet moment; "
                                 f"waiting for {held}."
                       and "restarts soon to apply an update" in second.get("stderr", "") and second.get("exit") == 0
                       and bool(facts["second_runs"]) and all(after_update(r) for r in facts["second_runs"])
                       and ended.get("exit") == 0 and 0 <= facts["attempt_after_run_s"] < 45
                       and facts["attempt"].get("state") == "succeeded" and facts["attempt"].get("automatic") is True
                       and facts["now"] == newer and facts["worker_kept"] == {"job": True, "unit": True,
                                                                               "state": "running"})
    return facts


def row_scratch() -> dict:
    """#958: a worker keeps its drafts in its own scratch folder, shows a worktree file and completes; the scratch
    folder is gone once the task is done, and a command the worker left running writes nothing afterwards, neither
    in Altitude's home nor in a recreated scratch folder."""
    late = ROOT / "late-write.txt"
    started = TEMP / "late-writer-started"
    slug = new_task("Scratch through done", "Fictional task that drafts in its scratch folder and completes.\n"
                    "FIXTURE-RUN: echo \"$ALTITUDE_SCRATCH\"; printf '# Progress\\nFictional draft.\\n' "
                    ">\"$ALTITUDE_SCRATCH/progress.md\" && alt task save progress \"$ALTITUDE_SCRATCH/progress.md\"\n"
                    "FIXTURE-RUN: printf '# Shown\\n' >shown.md && alt task reply --show shown.md 'A fictional shown "
                    "file' && rm shown.md\n"
                    f"FIXTURE-RUN: nohup /bin/sh -c 'touch {started}; sleep 20; mkdir -p \"$ALTITUDE_SCRATCH\" && echo late "
                    f">\"$ALTITUDE_SCRATCH/late.txt\"; echo late >{late}' >/dev/null 2>&1 &\n"
                    "FIXTURE-RUN: alt task done \"$ALTITUDE_TASK\" --digest 'Fictional outcome'\n")
    drafted = wait("the drafts", lambda: ran(slug, "save progress"), timeout=90)
    scratch = Path(drafted["stdout"].splitlines()[0].strip())
    shown = wait("the shown file", lambda: ran(slug, "--show shown.md"), timeout=90)
    completed = wait("the completion", lambda: ran(slug, "alt task done"), timeout=90)
    archived = wait("the task's archive", lambda: next((ROOT / PROJECT / "archive").glob(f"*{slug}*/status.json"), None),
                    timeout=120)
    facts = {"task": slug, "scratch": str(scratch), "drafted": drafted["exit"], "shown": shown["exit"],
             "done": completed["exit"], "state": json.loads(archived.read_text()).get("state"),
             "progress_kept": (archived.parent / "progress.md").exists(), "scratch_at_done": scratch.exists(),
             "writer_started": started.exists()}
    time.sleep(30)  # past the left-running command's write
    facts["late"] = {"scratch": sorted(path.name for path in scratch.iterdir()) if scratch.exists() else None,
                     "home": late.exists()}
    facts["passed"] = (facts["drafted"] == facts["shown"] == facts["done"] == 0 and facts["state"] == "done"
                       and facts["progress_kept"] and not facts["scratch_at_done"]
                       and facts["writer_started"] and facts["late"] == {"scratch": None, "home": False})
    return facts


def row_operator_step() -> dict:
    """#1017: a command the coordinator hands the operator waits in Needs you as the named task's turn; run to its end
    in the project terminal as Open in terminal runs it, its notice clears the step and wakes the coordinator, while a
    terminal closed before its command ends leaves the step waiting."""
    def call(path: str, body: dict | None = None):
        with page(path, body) as response:
            return json.loads(response.read() or b"null")

    def steps() -> list[dict]:
        return [item for item in call("/api/overview").get("queue", []) if item.get("kind") == "step"]

    def publish(title: str, command: str) -> dict:
        coordinator(f"FIXTURE-RUN: printf '{title}\\n\\n```run\\n{command}\\n```\\n' | alt project step --task {slug} -")
        return wait(f"{title!r} in Needs you", lambda: next((item for item in steps()
                                                             if item.get("question", "").startswith(title)), None), 30)

    def hand(command: str) -> str:
        """Open in terminal: the project terminal opened, the command handed to it, then typed with Enter."""
        opened = call(f"/api/terminal/{PROJECT}/open", {})
        call(f"/api/terminal/{PROJECT}/command", {"id": opened["id"], "text": command})
        call(f"/api/terminal/{PROJECT}/input", {"id": opened["id"], "data": command + "\r"})
        return opened["id"]

    def transcript(terminal: str, marker: str) -> str:
        seen = ""
        try:
            with page(f"/api/terminal/{PROJECT}/stream?id={terminal}&offset=0", timeout=10) as response:
                for raw in response:
                    line = raw.decode(errors="replace").strip()
                    if line.startswith("data:") and "data" in (event := json.loads(line[5:]) or {}):
                        seen += base64.b64decode(event["data"]).decode(errors="replace")
                    if f"\n{marker}" in seen.replace("\r", ""):
                        break
        except OSError:  # the stream's read timed out
            pass
        return seen[-2000:]

    def close(terminal: str) -> None:
        call(f"/api/terminal/{PROJECT}/close", {"id": terminal})
        wait("the terminal to close", lambda: call(f"/api/terminal/{PROJECT}").get("state") != "running", timeout=30)

    slug = new_task("Waits for the operator's step", "Fictional task that waits for a step only the operator takes.\n"
                    "FIXTURE-RUN: alt task block \"$ALTITUDE_TASK\" --reason 'Waiting for a fictional operator step'\n")
    wait("the block", lambda: task(slug).get("state") == "blocked", timeout=90)
    call("/api/terminal-access", {"enabled": True})
    facts: dict = {"task": slug, "before": alt("task", "status", slug)["waiting"]}
    finished = "sleep 3; echo fictional-step-done"
    card = publish("Run the fictional step", finished)
    facts.update(card={key: card.get(key) for key in ("id", "question", "tasks", "asked_by")},
                 during=alt("task", "status", slug)["waiting"])
    mark = len(lines(LOG))
    terminal = hand(finished)
    facts["transcript"] = transcript(terminal, "fictional-step-done")
    facts["cleared"] = bool(wait("the notice to clear the step", lambda: not any(item["id"] == card["id"]
                                                                                 for item in steps()), timeout=60))
    facts["coordinator_turns"] = len(wait("the coordinator's turn", lambda: [
        entry for entry in lines(LOG)[mark:] if entry.get("role") == "l3" and "end" in entry], timeout=120))
    facts["after"] = alt("task", "status", slug)["waiting"]
    close(terminal)
    unfinished = "sleep 60; echo fictional-late-step-done"
    kept = publish("Run the long fictional step", unfinished)
    terminal = hand(unfinished)
    time.sleep(3)
    close(terminal)
    time.sleep(5)  # past the notice that the terminal ended first
    facts["kept_after_close"] = any(item["id"] == kept["id"] for item in steps())
    coordinator(f"FIXTURE-RUN: alt project step --withdraw {kept['id']} --reason 'Fixture row complete'")
    facts["passed"] = (slug in json.dumps(card.get("tasks")) and "Run the fictional step" in (facts["during"] or "")
                       and "Run the fictional step" not in (facts["before"] or "")
                       and "\nfictional-step-done" in facts["transcript"].replace("\r", "") and facts["cleared"]
                       and facts["coordinator_turns"] >= 1 and "Run the fictional step" not in (facts["after"] or "")
                       and facts["kept_after_close"])
    return facts


CHANNEL = """import json, os, socket, sys
for path in sys.argv[1:]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(30)
            client.connect(path)
            client.sendall(b"{}\\n")
            print(json.dumps({"socket": path, "answer": json.loads(client.recv(4096) or b"null")}))
    except OSError as error:
        print(json.dumps({"socket": path, "error": str(error)}))
print(json.dumps({"cwd": os.getcwd()}))
"""


def row_coordinator_channel() -> dict:
    """#943 A1, A2: a coordinator turn, here the fictional Codex CLI's, runs from a fresh folder in the private
    coordinator folder and is admitted to its own project's channel; another project's channel and a task worker are
    refused. The folder is the account's alone and each socket's path fits macOS's limit."""
    other = "fictional-other"
    repo = HOME / "Projects" / other
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=Fixture Operator", "-c", "user.email=fixture@example.invalid", "commit", "-q",
        "--allow-empty", "-m", "Fictional initial commit")
    alt("project", "add", other, "--path", str(repo), "--l2-engine", "codex", project=False)
    folder = HOME / ".config/altitude" / hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:12]
    sockets = {name: folder / hashlib.sha256(name.encode()).hexdigest()[:8] for name in (PROJECT, other)}
    wait("both projects' channels", lambda: all(path.is_socket() for path in sockets.values()), timeout=60)
    probe = TEMP / "channel.py"
    probe.write_text(CHANNEL)
    command = f"{PYTHON} -B {probe} {sockets[PROJECT]} {sockets[other]}"
    mark = len(lines(LOG))
    coordinator(f"FIXTURE-RUN: {command}")
    answers = [json.loads(line) for line in next(entry for entry in lines(LOG)[mark:] if entry.get("role") == "l3"
                                                 and entry.get("command") == command)["stdout"].splitlines()]
    slug = new_task("Coordinator channel from a worker", "Fictional task that tries its project's coordinator "
                    f"channel.\nFIXTURE-RUN: {PYTHON} -B {probe} {sockets[PROJECT]}\n")
    worker = [json.loads(line) for line in wait("the worker's attempt", lambda: ran(slug, str(probe)),
                                                timeout=90)["stdout"].splitlines()]
    stopped_cleanly(slug)
    # The service's own answers: an empty request past admission is an unknown read; one refused never reaches it.
    admitted = lambda answer: answer.get("answer") == {"error": "unknown L3 read"}  # noqa: E731
    refused = lambda answer: answer.get("answer") == {  # noqa: E731
        "error": "this socket serves only the project's running coordinator turn"}
    mode = lambda path: oct(path.stat().st_mode & 0o777)  # noqa: E731
    facts = {"coordinator": answers, "worker": worker, "folder_mode": mode(folder),
             "socket_modes": {name: mode(path) for name, path in sockets.items()},
             "socket_bytes": {name: len(str(path).encode()) for name, path in sockets.items()}}
    facts["passed"] = (admitted(answers[0]) and refused(answers[1])
                       and answers[2]["cwd"].startswith(f"{folder}/l3-codex-") and refused(worker[0])
                       and facts["folder_mode"] == "0o700" and max(facts["socket_bytes"].values()) <= 103
                       and all(path.stat().st_uid == UID for path in sockets.values()))
    return facts


def row_logout_login() -> dict:
    """#731 and #226: the account's desktop session ends and starts again without a restart; the service stops
    with the session and the next login starts it again."""
    sysctl = lambda name: shell(f"sysctl -n {name}").stdout.strip()
    before, booted = service_pid(), sysctl("kern.boottime")
    console = lambda: shell("stat -f %Su /dev/console").stdout.strip()
    facts = {"before": {"pid": before, "console": console()}}
    # loginwindow ends the session it owns; with automatic login it then logs the account in again.
    facts["logout"] = shell("sudo -n killall loginwindow").returncode
    try:
        wait("the service to stop with the session", lambda: service_pid() is None, timeout=90, every=1)
        facts["stopped"] = True
    except Failed:
        facts["stopped"] = False
    try:
        after = wait("the next login to start the service", lambda: (service_pid() or before) != before and healthy()
                     and service_pid(), timeout=180)
    except Failed as error:
        after = None
        facts["unavailable"] = f"{error}; console user {console()!r}"
    facts.update(after={"pid": after, "console": console()}, restarted=sysctl("kern.boottime") != booted)
    facts["passed"] = facts["stopped"] and after is not None and not facts["restarted"]
    return facts


def row_sign_in() -> dict:
    """#1025 checks 2 and 3: Altitude signs itself into Claude from Settings › Prerequisites under the account's own
    service, with the tests' fictional `claude setup-token` screen as the Claude CLI. The token is saved where only
    Altitude reads it, at mode 0600 with a save time from this sign-in, and appears in no answer or log; no sign-in
    folder or command remains after a finished, a cancelled or a sign-in interrupted by a service restart."""
    binary, store, setup_log = HOME / ".local/bin/claude", HOME / ".config/altitude/access", TEMP / "setup-token.json"
    binary.write_text(f"#!/bin/sh\n[ \"$1\" = setup-token ] || exit 1\nFAKE_SETUP_LOG={setup_log} exec {PYTHON} -B "
                      f"{SOURCE}/tests/fake_token_setup.py \"$@\"\n")
    binary.chmod(0o755)
    # The fixture's token, written there as joined string literals.
    token = "".join(re.findall(r'"([^"]*)"', re.search(r"^TOKEN = (.+)$", (SOURCE / "tests/fake_token_setup.py")
                                                         .read_text(), re.M)[1]))
    call = lambda body=None: json.loads(page("/api/sign-in/claude", body).read())  # noqa: E731
    leftovers = lambda: sorted(path.name for path in store.glob("claude.setup.*"))  # noqa: E731
    command = lambda: processes("fake_token_setup.py")  # noqa: E731
    gone = lambda what, check: wait(what, lambda: check() == [] or None, timeout=30) and check()  # noqa: E731
    answers, facts = [], {}
    try:
        started = time.time()
        opened = call({"action": "start"})
        answers += [opened, call({"action": "finish", "attempt": opened["attempt"],
                                  "code": "fixture-code#fixture-state"})]
        answers.append(wait("the saved sign-in", lambda: call()["saved"] and call(), timeout=90))
        saved = store / "claude.token"
        row = next((item for item in json.loads(page("/api/prerequisites").read())["items"]
                    if item.get("key") == "claude"), {})
        facts["finished"] = {"state": opened.get("state"), "link": bool(opened.get("link")),
                             "mode": oct(saved.stat().st_mode & 0o777), "fresh": saved.stat().st_mtime >= started - 1,
                             "matches": saved.read_text().strip() == token, "row": row.get("label"),
                             "setup_env": json.loads(setup_log.read_text()).get("env") or {},
                             "folders": gone("the finished sign-in's folder to go", leftovers)}
        cancelled = call({"action": "start"})
        answers.append(call({"action": "cancel", "attempt": cancelled["attempt"]}))
        facts["cancelled"] = {"state": cancelled.get("state"),
                              "folders": gone("the cancelled sign-in's folder to go", leftovers),
                              "command": gone("the cancelled sign-in's command to end", command)}
        interrupted = call({"action": "start"})
        during = {"folders": leftovers(), "command": bool(command())}
        facts["restart"] = {"state": interrupted.get("state"), "during": during, "service": restart(),
                            "folders": leftovers(), "command": command(), "saved": call()["saved"] is not None}
        logs = "".join(path.read_text(errors="replace") for path in (HOME / "Library/Logs/altitude").rglob("*")
                       if path.is_file())
        facts["token_shown"] = {"answers": any(token in json.dumps(answer) for answer in answers), "logs": token in logs}
    finally:
        binary.unlink(missing_ok=True)
        (store / "claude.token").unlink(missing_ok=True)
    finished, cancelled, restarted = facts["finished"], facts["cancelled"], facts["restart"]
    facts["passed"] = (finished["state"] == "waiting" and finished["link"] and finished["mode"] == "0o600"
                       and finished["fresh"] and finished["matches"] and finished["row"] == "Claude signed in"
                       and finished["folders"] == []
                       and finished["setup_env"].get("CLAUDE_CONFIG_DIR", "").startswith(f"{store}/claude.setup.")
                       and not finished["setup_env"].get("CLAUDE_CODE_OAUTH_TOKEN")
                       and cancelled["state"] == "waiting" and cancelled["folders"] == [] == cancelled["command"]
                       and restarted["state"] == "waiting" and restarted["during"]["folders"]
                       and restarted["during"]["command"] and restarted["folders"] == [] == restarted["command"]
                       and restarted["saved"] and facts["token_shown"] == {"answers": False, "logs": False})
    return facts


STATE: dict = {}
ROWS = {"coordinator-stop": row_coordinator_stop, "failed-turn": row_failed_turn, "status-unknown": row_status_unknown,
        "validation-restart": row_validation_restart, "grants": row_grants, "contention": row_contention,
        "incident": row_incident, "schedule": row_schedule, "quiet-point": row_quiet_point,
        "scratch": row_scratch, "operator-step": row_operator_step,
        "coordinator-channel": row_coordinator_channel, "logout-login": row_logout_login, "sign-in": row_sign_in}


def rows(names: list[str]) -> int:
    passed = True
    for name in names:
        STEPS.clear()
        started = time.monotonic()
        try:
            facts = ROWS[name]()
        except Exception as error:  # the row failed; the next row still runs and the record says why
            facts = {"passed": False, "error": f"{type(error).__name__}: {error}"}
        facts.update(row=name, seconds=round(time.monotonic() - started), steps=list(STEPS))
        passed = passed and facts["passed"]
        print(json.dumps(facts), flush=True)
    return 0 if passed else 1


# ---- Inside an owner's turn -----------------------------------------------------------------------------------------

def grant(question: str, revision: str, message: str | None) -> int:
    """Resolve the operator's latest answer to QUESTION (or cite MESSAGE, which already resolved it) and record it as
    this task's grant, as an owner does after reading a yes."""
    slug = os.environ["ALTITUDE_TASK"]
    reason = "The operator approved fictional commands for this check."
    if message is None:
        rows_ = json.loads(subprocess.run([str(ALT), "task", "messages", slug, "--json"], capture_output=True,
                                          text=True, check=True, timeout=60).stdout)
        message = next(row["id"] for row in reversed(rows_) if row.get("role") not in ("l2", "l3"))  # the operator's
        subprocess.run([str(ALT), "task", "resolve", slug, "--question", question, "--revision", revision, "--message",
                        message, "--disposition", "answered", "--reason", reason], check=True, timeout=60)
    subprocess.run([str(ALT), "task", "grant", slug, "--approval", message, "--question", question, "--revision", revision,
                    "--reason", reason], check=True, timeout=60)
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["setup"]:
        print(json.dumps(setup()))
    elif sys.argv[1:2] == ["rows"]:
        sys.exit(rows(sys.argv[2:]))
    elif sys.argv[1:2] == ["grant"]:
        sys.exit(grant(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None))
    else:
        sys.exit(__doc__)
