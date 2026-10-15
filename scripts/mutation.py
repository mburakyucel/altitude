#!/usr/bin/env python3
"""Mutation run over Altitude's core modules: which small wrong edits their fast unit tests would not notice.

A periodic measurement, never a check. The run copies the working tree, without ignored files, to a scratch
folder, installs the pinned mutmut into a scratch virtual environment, mutates the modules below one small edit
at a time (a flipped comparison, a dropped call, a swapped return) and runs, for each mutant, the selected tests
that reach its function. It writes RESULTS/report.md, per module the mutants killed, survived, unreached and
timed out, then each survivor's diff grouped by function, and keeps mutmut's output in RESULTS/mutmut.log.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fnmatch
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO / "scripts" / "mutation-requirements.txt"

# Where a silent regression costs most: task records and their state transitions, Git policy, the task door
# and landing. Of tasks.py only the functions that move a task between states are mutated.
TRANSITIONS = ("_move", "dispatch", "block", "report", "continue_report", "resume", "requeue", "reject", "done",
               "finalize_completion")
MODULES = {
    "altitude/state.py": ["altitude.state.*"],
    "altitude/tasks.py": [f"altitude.tasks.x_{name}__mutmut_*" for name in TRANSITIONS],
    "altitude/git_policy.py": ["altitude.git_policy.*"],
    "altitude/door.py": ["altitude.door.*"],
    "altitude/land.py": ["altitude.land.*"],
}
# The fast unit tests that exercise those modules; browser, container and VM lanes stay out.
TESTS = [f"tests/test_{name}.py" for name in (
    "lifecycle", "runtime_storage", "edit_count", "concurrency", "clean_close", "resume_hold", "task_status",
    "faults", "worker_end", "decisions", "question_groups", "conversation_decisions", "reported_continuation",
    "continuation_journey",
    "git_policy", "git_policy_integration", "worker_git_folders", "checkout_preservation",
    "task_door", "codex_door",
    "land", "land_adoption", "land_contention", "land_continuation", "merge_approval_journey",
    "recorded_merge_approval", "merge_policy", "parallel_checks",
)]
STATUSES = ("killed", "survived", "no tests", "timeout")
RESULT = re.compile(r"^\s+(\S+): (.+)$")


def lines(values):
    return "".join(f"\n    {value}" for value in values)


def copy_tree(source: Path) -> None:
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO,
                            check=True, capture_output=True).stdout
    for name in filter(None, listed.decode().split("\0")):
        path = REPO / name
        if os.path.lexists(path):
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, source / name, follow_symlinks=False)


def configure(source: Path, modules: dict) -> None:
    # Fork server without warm-up: each worker imports tests/support.py itself and so gets its own temporary
    # root. Workers forked from a process that had imported it share one root, and the first to exit removes
    # it for all the others, so most mutants would fail their tests for that reason alone.
    (source / "setup.cfg").write_text(
        "[mutmut]\nsource_paths = altitude\n"
        f"only_mutate ={lines(modules)}\n"
        f"also_copy ={lines(sorted(p.name for p in source.iterdir() if p.name not in ('altitude', 'tests')))}\n"
        "pytest_add_cli_args = -p no:cacheprovider\n"
        f"pytest_add_cli_args_test_selection ={lines(TESTS)}\n"
        "process_isolation = forkserver\nforkserver_warmup = none\n")


def strays(root: int, venv: Path) -> set[int]:
    """Processes of the scratch environment's Python that are no longer below mutmut's process `root`."""
    listed = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], check=True, capture_output=True,
                            text=True).stdout
    table = [(int(pid), int(ppid), rest[0] if rest else "") for pid, ppid, *rest in
             (line.split(None, 2) for line in listed.splitlines())]
    below, grown = {root}, True
    while grown:
        grown = False
        for pid, ppid, _ in table:
            if ppid in below and pid not in below:
                below.add(pid)
                grown = True
    return {pid for pid, _, command in table if command.startswith(str(venv)) and pid not in below}


def stop(pids) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_mutmut(command: list[str], source: Path, env: dict, venv: Path, log) -> int:
    """mutmut stops a worker that runs too long but not the processes its tests started; those wait forever
    for the worker and pile up. Each minute this stops the ones already orphaned a minute before, and at the
    end the rest."""
    process = subprocess.Popen(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    seen = set()
    while True:
        try:
            returncode = process.wait(timeout=60)
            break
        except subprocess.TimeoutExpired:
            current = strays(process.pid, venv)
            stop(current & seen)
            seen = current
    stop(strays(process.pid, venv))
    return returncode


def function_of(mutant: str) -> str:
    """`altitude.land.xǁLandingǁmerge__mutmut_3` names `Landing.merge`; `altitude.state.x_now__mutmut_1`, `now`."""
    name = mutant.rsplit(".", 1)[1].rsplit("__mutmut_", 1)[0]
    return ".".join(filter(None, name[1:].split("ǁ"))) if name.startswith("xǁ") else name[2:]


def classify(results: str, modules: dict = MODULES) -> dict[str, dict[str, list[str]]]:
    """Mutant names of the selected modules by module and status, from `mutmut results --all true`."""
    found = {module: {} for module in modules}
    for line in results.splitlines():
        match = RESULT.match(line)
        if not match:
            continue
        mutant, status = match.groups()
        for module, patterns in modules.items():
            if any(fnmatch.fnmatchcase(mutant, pattern) for pattern in patterns):
                found[module].setdefault(status, []).append(mutant)
    return found


def render(found: dict, diffs: dict[str, str], heading: str) -> str:
    table = ["| Module | Mutants | Killed | Survived | No tests | Timeout | Other |", "|---|--:|--:|--:|--:|--:|--:|"]
    sections = []
    for module, by_status in found.items():
        counts = [len(by_status.get(status, [])) for status in STATUSES]
        total = sum(map(len, by_status.values()))
        table.append(f"| `{module}` | {total} | " + " | ".join(map(str, counts)) + f" | {total - sum(counts)} |")
        section = [f"## `{module}`"]
        survivors = {}
        for mutant in by_status.get("survived", []):
            survivors.setdefault(function_of(mutant), []).append(mutant)
        for function, mutants in sorted(survivors.items()):
            section.append(f"### `{function}`: {len(mutants)} survived")
            section += [f"```diff\n{diffs[mutant].strip()}\n```" for mutant in mutants]
        unreached = {}
        for mutant in by_status.get("no tests", []):
            unreached[function_of(mutant)] = unreached.get(function_of(mutant), 0) + 1
        if unreached:
            section.append("No selected test reaches: " + ", ".join(
                f"`{function}` ({count})" for function, count in sorted(unreached.items())) + ".")
        others = sorted(mutant for status, mutants in by_status.items() if status not in STATUSES[:3]
                        for mutant in mutants)
        if others:
            section.append("Timed out or otherwise unsettled: " + ", ".join(f"`{m}`" for m in others) + ".")
        if len(section) == 1:
            section.append("Every mutant was killed." if total else "No mutants.")
        sections.append("\n\n".join(section))
    return "\n\n".join([heading, "\n".join(table), *sections]) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("results", type=Path, help="new or empty directory for report.md and mutmut.log")
    parser.add_argument("modules", nargs="*", choices=list(MODULES), metavar="module",
                        help=f"only these, for a rerun after new tests (default: all of {', '.join(MODULES)})")
    args = parser.parse_args()
    modules = {module: MODULES[module] for module in args.modules or MODULES}
    if args.results.exists() and any(args.results.iterdir()):
        parser.error(f"{args.results} is not empty")
    # Read before RESULTS exists, so a results folder inside the checkout is not a local change.
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, check=True, capture_output=True,
                            text=True).stdout.strip()
    changed = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True,
                             text=True).stdout.strip()
    args.results.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    scratch = Path(tempfile.mkdtemp(prefix="altitude-mutation-"))
    try:
        source, venv = scratch / "source", scratch / "venv"
        copy_tree(source)
        configure(source, modules)
        subprocess.run([sys.executable, "-m", "venv", venv], check=True)
        subprocess.run([venv / "bin" / "python", "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "--no-cache-dir", "--require-hashes", "--only-binary=:all:", "-r", REQUIREMENTS], check=True)
        # A child Python a test starts (a Git hook, `bin/alt`) runs the original code. mutmut sees calls only in
        # the test process, and an inherited mutant or mode broke children: they looked for mutmut's settings
        # in their own folder, or waited forever and outlived the worker mutmut stopped.
        site = subprocess.run([venv / "bin" / "python", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                              check=True, capture_output=True, text=True).stdout.strip()
        Path(site, "altitude_mutation.pth").write_text('import os; os.environ.pop("MUTANT_UNDER_TEST", None)\n')
        mutmut = str(venv / "bin" / "mutmut")
        # Git hooks and `bin/alt` start `python3` from PATH, and the mutated modules import mutmut.
        env = {k: v for k, v in os.environ.items() if k != "ALTITUDE_ACTOR"}
        env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        patterns = [pattern for module in modules.values() for pattern in module]
        with open(args.results / "mutmut.log", "wb") as log:
            returncode = run_mutmut([mutmut, "run", *patterns], source, env, venv, log)
        if returncode:
            print(f"mutmut run failed ({returncode}); see {args.results / 'mutmut.log'}", file=sys.stderr)
            return returncode
        listed = subprocess.run([mutmut, "results", "--all", "true"], cwd=source, env=env, check=True,
                                capture_output=True, text=True).stdout
        found = classify(listed, modules)
        survivors = [mutant for by_status in found.values() for mutant in by_status.get("survived", [])]
        with ThreadPoolExecutor(os.cpu_count()) as pool:
            shown = pool.map(lambda mutant: subprocess.run([mutmut, "show", mutant], cwd=source, env=env, check=True,
                                                           capture_output=True, text=True).stdout, survivors)
            # `mutmut show` prints its own "# name: status" line before the diff.
            diffs = {mutant: text.split("\n", 1)[1] for mutant, text in zip(survivors, shown)}
        version = subprocess.run([mutmut, "--version"], cwd=source, check=True, capture_output=True,
                                 text=True).stdout.strip()
        minutes = (time.monotonic() - started) / 60
        heading = (f"# Mutation run\n\nCommit `{commit}`{' with local changes' if changed else ''}, {version}, "
                   f"{datetime.now(timezone.utc).replace(microsecond=0).isoformat()}, {minutes:.0f} minutes on "
                   f"{os.cpu_count()} CPUs. Tests: " + ", ".join(f"`{Path(t).stem}`" for t in TESTS) + ".")
        (args.results / "report.md").write_text(render(found, diffs, heading))
        print(f"Wrote {args.results / 'report.md'} after {minutes:.0f} minutes.")
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
