"""Fictional native-image admission probe, run only by the authorized container gate.

Imports the packaged application; never invokes an engine or reads host data. This establishes
instance/admission persistence and systemd job ownership, not full task/onboarding acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, "/opt/altitude")
from altitude import config, platform, state as S

assert platform.containerized() and os.getuid() == 1000
stage = sys.argv[1]
saved = config.HOME / "admission-fixture.json"
project = platform.CONTAINER_PROJECTS / "admission-fixture.txt"

def state():
    result = platform.container_lifecycle()
    assert result["instance"], result
    return result

def change(action):
    return platform.change_container_lifecycle(action, state()["instance"])

def refused():
    @config.admitted_provider
    def fictional_provider():
        raise AssertionError("A paused admission invoked the fixture provider")
    try:
        fictional_provider()
    except config.AdmissionPaused:
        return
    raise AssertionError("Paused admission did not refuse")

def eventually(check):
    for _ in range(50):
        if check():
            return
        time.sleep(.1)
    raise AssertionError("Timed out waiting for the fictional native transition")

if stage == "prepare":
    initial = state()
    assert initial["ready"], initial
    project.write_text("Fictional project survives replacement.\n")
    S.write_json(saved, {"instance": initial["instance"], "project": project.read_text(),
                       "ca": hashlib.sha256((config.TLS_DIR / "ca.crt").read_bytes()).hexdigest()})
    pid_file = config.HOME / "admission-fixture-pids.json"
    child = ("import json,os,time; from pathlib import Path; child=os.fork(); "
             "os.setsid() if child == 0 else None; "
             f"p=Path({str(pid_file)!r}); "
             "p.with_suffix('.tmp').write_text(json.dumps([os.getpid(),child])) if child else None; "
             "p.with_suffix('.tmp').replace(p) if child else None; "
             "time.sleep(30)")
    unit = "altitude-admission-fixture"
    command = platform.job_command(unit, [sys.executable, "-c", child], dict(os.environ), runtime_max=40)
    runner = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        eventually(pid_file.exists)
        pids = json.loads(pid_file.read_text())
        assert all(Path(f"/proc/{pid}").exists() for pid in pids)
        assert not change("pause")["ready"]
        refused()
        subprocess.run([platform.SYSTEMCTL, "--user", "restart", platform.SERVICE], check=True, timeout=20)
        assert not state()["ready"] and state()["instance"] == initial["instance"]
        assert platform.job_active(unit, dict(os.environ))
        assert all(Path(f"/proc/{pid}").exists() for pid in pids)
        platform.job_stop(unit, timeout=10)
        eventually(lambda: not platform.job_active(unit, dict(os.environ)))
        eventually(lambda: all(not Path(f"/proc/{pid}").exists() for pid in pids))
        stdout, stderr = runner.communicate(timeout=10)
        assert runner.returncode is not None
    finally:
        platform.job_stop(unit, timeout=10)
        if runner.poll() is None:
            runner.kill()
        runner.communicate(timeout=10)
    assert change("continue")["ready"]
    assert not change("pause")["ready"]
    result = {"daemon_restart_preserves_pause": True, "independent_job_survives": True,
              "stop_cleans_detached_descendant": True, "paused_provider_refused": True}
elif stage in ("restarted", "replaced"):
    receipt = S.read_json(saved)
    current = state()
    assert not current["ready"], current
    assert (current["instance"] == receipt["instance"]) == (stage == "restarted")
    assert project.read_text() == receipt["project"]
    assert hashlib.sha256((config.TLS_DIR / "ca.crt").read_bytes()).hexdigest() == receipt["ca"]
    refused()
    if stage == "replaced":
        try:
            platform.change_container_lifecycle("continue", receipt["instance"])
        except ValueError:
            pass
        else:
            raise AssertionError("A stale instance released replacement work")
    assert change("continue")["ready"]
    with config.provider_admission() as held:
        assert held is None
    result = {"stage": stage, "identity_and_pause_preserved": True, "persistent_project_and_ca": True,
              "host_continue_releases_admission": True}
else:
    raise ValueError("Unknown fixture stage")
print(json.dumps(result))
