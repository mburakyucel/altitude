"""The macOS VM lane's `mutation` phase, run inside the offline guest by its account from the revision's files made a
Git checkout (`scripts/installation_macos_vm.py`): two mutation runs of the task door one after the other, each
interrupted as Ctrl-C interrupts it once mutmut runs, with a process planted outside mutmut whose command names the
run's scratch folder, as a test's leftover does. Prints one JSON object: each run's scratch folder, what named it
during and after the run, and whether the planted process ended."""
import glob
import json
import os
import signal
import subprocess
import sys
import time

SHARED = "/Users/Shared/altitude-vm"


def folders() -> list[str]:
    return sorted(glob.glob("/tmp/altitude-mutation-*"))


def naming(folder: str) -> list[str]:
    """The lines of `ps`'s listing, as the run's own cleanup reads it, whose command names FOLDER."""
    listed = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return [line.strip() for line in listed.splitlines() if folder in line]


def interrupted(n: int) -> dict:
    before = folders()
    with open(f"{SHARED}/mutation-{n}.log", "wb") as log:
        run = subprocess.Popen([sys.executable, "-B", "scripts/mutation.py", f"{SHARED}/mutation-{n}",
                                "altitude/door.py"], stdout=log, stderr=subprocess.STDOUT)
    folder, mutmut, deadline = None, False, time.monotonic() + 300
    while time.monotonic() < deadline and run.poll() is None and not mutmut:
        folder = next((found for found in folders() if found not in before), None)
        mutmut = bool(folder) and any("/venv/bin/mutmut run" in line for line in naming(folder))
        time.sleep(1)
    # Named as a test's leftover names it: through TMPDIR, the run's resolved folder (/tmp is /private/tmp here).
    planted = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(900)",
                                f"{os.path.realpath(folder)}/planted"], start_new_session=True) if mutmut else None
    time.sleep(15)
    during = naming(folder) if folder else []
    run.send_signal(signal.SIGINT)
    try:
        code = run.wait(timeout=120)
    except subprocess.TimeoutExpired:
        run.kill()
        code = "did not end"
    time.sleep(2)
    ended = planted is not None and planted.poll() is not None
    after = naming(folder) if folder else None
    if planted is not None and not ended:
        planted.kill()
    return {"folder": folder, "mutmut": mutmut, "during": during, "exit": code, "planted_ended": ended,
            "folder_left": bool(folder) and os.path.exists(folder), "after": after}


if __name__ == "__main__":
    runs = [interrupted(1), interrupted(2)]
    print(json.dumps({"runs": runs, "left": folders(), "passed": all(
        run["mutmut"] and run["exit"] not in (0, "did not end") and run["planted_ended"] and not run["folder_left"]
        and run["after"] == [] and any("/planted" in line for line in run["during"]) for run in runs)
        and runs[0]["folder"] != runs[1]["folder"] and folders() == []}))
