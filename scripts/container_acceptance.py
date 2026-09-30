#!/usr/bin/env python3
"""Finite Linux image-bootstrap gate. Uses disposable rootless storage and network-none payloads.

Results deliberately name uncovered full application, provider, browser and Mac acceptance. This
entry point runs only with authorized runtime access; it never installs a host tool or changes policy.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import platform
from scripts import container


def proc_exposure(command, ident: str, evidence: Path):
    """Retain the approved exposure inventory; any broader predicate fails release (#543)."""
    from tests.container_proc_probe import differences
    source=Path(__file__).resolve().parents[1]/'tests'
    policy=json.loads((source/'fixtures/container-proc-policy.json').read_text())
    program=(source/'container_proc_probe.py').read_text()
    controller=json.loads(command(['inspect',ident]))[0]
    failures=[]
    for uid in ('0','1000'):
        baseline_name='fixture-proc-default-'+uuid.uuid4().hex
        try:
            baseline=json.loads(command(['run','--rm','--name',baseline_name,'--cgroupns=private',
                '--cgroup-parent',controller['HostConfig']['CgroupParent'],'--network=slirp4netns',
                '--read-only','--memory=128m','--cpus=0.5','--pids-limit=64','--user',uid,
                '--entrypoint=python3',controller['Image'],'-c',program],timeout=30))
        finally:
            command(['rm','--force','--ignore',baseline_name],timeout=30)
        (evidence/('proc-default-'+uid+'.json')).write_text(json.dumps(baseline,indent=2)+'\n')
        value=json.loads(command(['exec','--user',uid,ident,'python3','-c',program],timeout=30))
        value['unexpected_exposure']=differences(value,policy,baseline)
        (evidence/('proc-exposure-'+uid+'.json')).write_text(json.dumps(value,indent=2)+'\n')
        failures.extend('uid'+uid+':'+item for item in value['unexpected_exposure'])
    if failures:
        raise RuntimeError('Unexpected proc exposure; stop for review: '+str(failures))


@contextmanager
def environment(root: Path, *, reuse=False):
    saved = dict(os.environ)
    names = {"HOME": "home", "XDG_RUNTIME_DIR": "runtime", "XDG_DATA_HOME": "data",
             "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache"}
    for child in names.values():
        (root / child).mkdir(mode=0o700, exist_ok=reuse)
    # Podman passes only XDG_RUNTIME_DIR to crun delete. A private replacement
    # loses the real user bus and crun falls back to the system manager (#543).
    # Preserve the same already-used bus for stripped children while retaining
    # private pause-process, OCI and storage state (all keyed by this directory).
    bus=root / 'runtime/bus'
    if not reuse or not bus.is_symlink():
        bus.symlink_to(f"/run/user/{os.getuid()}/bus")
    os.environ.clear()
    os.environ.update({key: str(root / child) for key, child in names.items()})
    os.environ.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", LANG="C.UTF-8",
                      DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{os.getuid()}/bus")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


INVENTORY = r'''
import json, os, stat
from pathlib import Path
assert os.getuid() == 0
elevated, examined = [], 0
def refused(error): raise error
for folder, directories, files in os.walk('/', onerror=refused, followlinks=False):
    if folder == '/':
        directories[:] = [name for name in directories if name not in {'proc','sys','dev','run','home','tmp'}]
    for name in files:
        path = Path(folder) / name
        try: info = path.lstat()
        except FileNotFoundError: continue
        if stat.S_ISREG(info.st_mode):
            examined += 1
            if info.st_mode & 0o6000 or 'security.capability' in os.listxattr(path):
                elevated.append(str(path))
assert not elevated, elevated
print(json.dumps({'examined':examined, 'elevated':elevated, 'excluded':'kernel/runtime/persistent-home/temp mounts'}))
'''

PROBE = r'''
import json, os, ssl, time, urllib.request
from pathlib import Path
import sys
sys.path.insert(0, '/opt/altitude')
from altitude import access, config, platform, tls
assert platform.containerized()
assert os.getuid() == 1000
assert config.HOST == '0.0.0.0', config.HOST
assert config.PUBLIC_HOST == 'container-fixture.invalid', config.PUBLIC_HOST
assert config.PORT == 19443, config.PORT
context = ssl.create_default_context(cafile=str(config.TLS_DIR / 'ca.crt'))
base = 'https://localhost:' + str(config.PORT)
def request(path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json', access.KEY_HEADER:access.machine_key()})
    with urllib.request.urlopen(req, context=context, timeout=5) as response:
        return json.load(response)
for attempt in range(16):
    try:
        health = request('/api/health')
        break
    except OSError:
        if attempt == 15: raise
        time.sleep(.25)
machine = request('/api/machine')
assert machine['deployment'] == 'container' and not machine['terminal'] and not machine['update_check']
assert machine['lifecycle']['ready'], machine['lifecycle']
assert request('/api/overview')['update']['managed'] == 'image'
service = platform.status()
assert service['ActiveState'] == 'active'
daemon_proc = Path('/proc') / service['MainPID']
environment = dict(item.split(b'=', 1) for item in (daemon_proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
expected = {'ALTITUDE_HOST':'0.0.0.0', 'ALTITUDE_PUBLIC_HOST':'container-fixture.invalid', 'ALTITUDE_PORT':'19443'}
service_environment = {key:environment.get(key.encode(), b'').decode() for key in expected}
assert service_environment == expected, service_environment
listeners = [row.split() for row in (daemon_proc / 'net/tcp').read_text().splitlines()[1:]]
assert any(row[1] == '00000000:4BF3' and row[3] == '0A'
           and platform.holds(int(service['MainPID']), 'socket:[' + row[9] + ']') for row in listeners), listeners
certificate = ssl._ssl._test_decode_cert(str(config.TLS_DIR / 'server.crt'))
assert ('DNS', 'container-fixture.invalid') in certificate['subjectAltName'], certificate
daemon_status = (daemon_proc / 'status').read_text().splitlines()
assert next(line.split()[1] for line in daemon_status if line.startswith('NoNewPrivs:')) == '1'
for key in ['Seccomp:', 'CapEff:']:
    line = next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith(key))
    assert line.split()[1] == ('2' if key == 'Seccomp:' else '0000000000000000'), line
print(json.dumps({'health':health, 'machine':machine, 'daemon_no_new_privileges':True,
    'certificate':tls.info(), 'service':service, 'service_environment':service_environment,
    'daemon_wildcard_listener':True, 'advertised_certificate_san':True}, indent=2))
'''


def run(archive: Path, checksum: str, evidence: Path, *, native_binary: Path | None = None,
        lifecycle: bool = False, workflow: bool = False, recovery: bool = False,
        delegated: str | None = None) -> dict:
    if delegated is None:
        unit = "altitude-container-gate-" + uuid.uuid4().hex + ".service"
        arguments = [sys.executable, str(Path(__file__).resolve()), "--archive", str(archive.resolve()),
                     "--sha256", checksum, "--results", str(evidence.resolve()), "--delegated", unit]
        for selected, flag in ((lifecycle, "--lifecycle"), (workflow, "--workflow"), (recovery, "--recovery")):
            if selected:
                arguments.append(flag)
        if native_binary is not None:
            arguments += ["--native-sandbox-binary", str(native_binary.resolve())]
        platform.container_job(unit, arguments, wait=True)
        return json.loads((evidence / "result.json").read_text())
    parent = platform.container_parent(delegated)
    evidence.mkdir(parents=True, exist_ok=False)
    root = Path(tempfile.mkdtemp(prefix="acg-"))
    result = {"passed": False, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "scope": "image bootstrap with network-none payload", "cleanup": [],
              "uncovered": ["actual engine sandbox in stripped image", "project/coordinator/task workflow",
                            "Stop/restart/recreation and volume lock concurrency", "image update/backup/recovery",
                            "published networking/HTTPS and browser onboarding", "Mac ARM64", "live authentication/providers"]}
    actual = platform.container_command
    sequence = 0
    started = time.monotonic()
    isolated = False
    def call(arguments, *, timeout=30, interactive=False, runtime_dir=None, storage_conf=None):
        nonlocal sequence
        sequence += 1
        remaining = int(480 - (time.monotonic() - started))
        if remaining <= 0:
            raise TimeoutError("The finite image gate exhausted its eight-minute budget")
        limit = min(timeout, remaining)
        if arguments[0] == "build":
            limit = min(limit, 300)
        # Retain only fictional fixture output. No host/user credential files enter this controller.
        try:
            output = actual(arguments, timeout=limit, interactive=interactive,
                            **({"runtime_dir": runtime_dir} if runtime_dir is not None else {}),
                            **({"storage_conf": storage_conf} if storage_conf is not None else {}))
        except Exception as exc:
            native = getattr(exc, "result", exc)
            def output_text(name):
                value = getattr(native, name, None)
                return value.decode(errors="replace") if isinstance(value, bytes) else value
            (evidence / f"command-{sequence:03d}.json").write_text(json.dumps({"arguments": arguments,
                "error": str(exc), "exit": getattr(native, "returncode", None),
                "stdout": output_text("stdout"), "stderr": output_text("stderr")}, indent=2))
            raise
        (evidence / f"command-{sequence:03d}.json").write_text(json.dumps({"arguments": arguments, "output": output}, indent=2))
        return output
    with environment(root):
        platform.container_command = call
        try:
            facts = platform.container_runtime()
            result["runtime"] = facts
            for key in ("graphRoot", "runRoot"):
                if not Path(facts["store"][key]).resolve().is_relative_to(root):
                    raise RuntimeError("Refuse fixture work outside its disposable runtime store")
            isolated = True
            image = container.build(archive, checksum, "localhost/altitude-bootstrap:fixture")
            result["image"] = {key: image.get(key) for key in ("Id", "Digest", "Architecture", "Labels")}
            for volume in ("fixture-home", "fixture-projects"):
                container.local_volume(volume,lineage='1'*32,pair='2'*32,role=volume.removeprefix('fixture-'),create=True)
            create = ["create", "--name", "altitude-bootstrap-fixture", "--network=none", "--cgroupns=private",
                          "--cgroup-parent", parent,
                          "--security-opt=unmask=/proc/*", "--memory=512m", "--cpus=1", "--pids-limit=256",
                          "--env", "ALTITUDE_PORT=19443", "--env", "ALTITUDE_PUBLIC_HOST=container-fixture.invalid",
                          "--volume", "fixture-home:/home/altitude:nocopy",
                          "--volume", "fixture-projects:/home/altitude/Projects:nocopy", image["Id"]]
            ident = call(create).strip()
            result["container"] = ident
            result["container_inspect"] = json.loads(call(["inspect", ident]))[0]
            call(["start", ident])
            proc_exposure(call,ident,evidence)
            result['proc_exposure_within_approved_predicates']=True
            ready = False
            for _ in range(30):
                try:
                    call(["exec", "--user", "1000", "--env", "XDG_RUNTIME_DIR=/run/user/1000",
                          ident, "systemctl", "--user", "is-active", "altitude.service"], timeout=5)
                    ready = True
                    break
                except RuntimeError:
                    time.sleep(1)
            (evidence / "bootstrap.log").write_text(call(["logs", ident]))
            (evidence / "unit-status.txt").write_text(call(["exec", ident, "systemctl", "show",
                "altitude-volumes.service", "user@1000.service", "--property=Id,ActiveState,SubState,Result,MainPID,ExecMainStatus"]))
            (evidence / "bootstrap-journal.txt").write_text(call(["exec", ident, "journalctl", "--no-pager", "-b",
                "-u", "altitude-volumes.service", "-u", "user@1000.service", "-n", "80"]))
            if not ready:
                raise RuntimeError("Image application service did not start; inspect bootstrap.log and retained unit evidence")
            if native_binary is not None:
                binary = native_binary.resolve(strict=True)
                mode = binary.stat().st_mode
                if not stat.S_ISREG(mode) or mode & 0o6000 or "security.capability" in os.listxattr(binary):
                    raise RuntimeError("The diagnostic must be an ordinary executable without elevation metadata")
                result["native_tool"] = {"sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
                target = "/usr/local/bin/altitude-fixture-native"
                call(["cp", str(binary), ident + ":" + target])
                call(["exec", ident, "chmod", "0755", target])
                source = Path(__file__).resolve().parent.parent / "tests/container_native_probe.py"
                result["native_probe_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                output = call(["exec", "--user", "1000", "--env", "HOME=/home/altitude", "--env", "XDG_RUNTIME_DIR=/run/user/1000",
                               ident, "python3", "-c", source.read_text(), target], timeout=90)
                native = json.loads(output)
                (evidence / "native-profiles.json").write_text(json.dumps(native, indent=2) + "\n")
                result["native_profiles"] = {"passed": native["gate_passed"], "failures": native["gate_failures"],
                                             "versions": native["versions"], "limits": native["limits"]}
                if not native["gate_passed"]:
                    raise RuntimeError("Actual packaged native profiles failed; inspect native-profiles.json")
                result["uncovered"].remove("actual engine sandbox in stripped image")
                result["uncovered"].append("provider-session/configuration-layer and other engine confinement parity")
            result["elevation_inventory"] = json.loads(call(["exec", "--user", "0", ident, "python3", "-c", INVENTORY], timeout=30))
            output = call(["exec", "--user", "1000", "--env", "HOME=/home/altitude", "--env", "XDG_RUNTIME_DIR=/run/user/1000",
                           ident, "python3", "-c", PROBE], timeout=30)
            result["bootstrap"] = json.loads(output)
            if lifecycle or workflow or recovery:
                source = Path(__file__).resolve().parent.parent / "tests/container_admission_probe.py"
                result["admission_probe_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                result["admission"] = []
                def probe(stage):
                    output = call(["exec", "--user", "1000", "--env", "HOME=/home/altitude",
                        "--env", "XDG_RUNTIME_DIR=/run/user/1000", ident, "python3", "-c",
                        source.read_text(), stage], timeout=60)
                    result["admission"].append(json.loads(output))
                def start_ready():
                    call(["start", ident])
                    for attempt in range(30):
                        try:
                            call(["exec", "--user", "1000", "--env", "XDG_RUNTIME_DIR=/run/user/1000",
                                  ident, "systemctl", "--user", "is-active", "altitude.service"], timeout=5)
                            call(["exec", "--user", "1000", "--env", "HOME=/home/altitude",
                                  "--env", "XDG_RUNTIME_DIR=/run/user/1000", ident, "python3", "-c",
                                  "import sys; sys.path.insert(0,'/opt/altitude'); from altitude import platform; platform.container_ready()"], timeout=15)
                            return
                        except RuntimeError:
                            if attempt == 29:
                                raise
                            time.sleep(1)
                if lifecycle:
                    locker = ("import sys,time;from pathlib import Path;sys.path.insert(0,'/opt/altitude');"
                              "from altitude.platform import container_volume_locks;"
                              "locks=container_volume_locks(Path('/locks/home'),Path('/locks/projects'));"
                              "locks.__enter__();print('directory-locks-held',flush=True);time.sleep(120)")
                    helper = ["--name", "fixture-lock-holder", "--network=none", "--read-only",
                              "--cap-drop=all", "--security-opt=no-new-privileges", "--user=1000:1000",
                              "--cgroup-parent", parent, "--memory=128m", "--cpus=1", "--pids-limit=32",
                              "--volume", "fixture-home:/locks/home:nocopy",
                              "--volume", "fixture-projects:/locks/projects:nocopy",
                              "--entrypoint=python3", image["Id"], "-c", locker]
                    try:
                        call(["run", "--rm", *helper], timeout=10)
                    except RuntimeError as exc:
                        if "Another Altitude container owns this volume" not in str(exc):
                            raise
                    else:
                        raise RuntimeError("Backup-style helper acquired a running controller's volumes")
                    call(["stop", "--time", "10", ident])
                    locked = call(["run", "--detach", *helper]).strip()
                    for attempt in range(20):
                        if "directory-locks-held" in call(["logs", locked]):
                            break
                        if attempt == 19:
                            raise RuntimeError("Directory-lock helper never became ready")
                        time.sleep(.2)
                    call(["start", ident])
                    for attempt in range(30):
                        try:
                            units = call(["exec", ident, "systemctl", "show", "altitude-volumes.service",
                                          "user@1000.service", "--property=Id,ActiveState,Result"])
                        except RuntimeError:
                            if attempt == 29:
                                raise
                            time.sleep(.2)
                            continue
                        if "ActiveState=failed" in units and "ActiveState=active" not in units:
                            break
                        if attempt == 29:
                            raise RuntimeError("Racing controller was not refused before its user manager started")
                        time.sleep(.2)
                    result["volume_lock_refusal"] = units
                    call(["rm", "--force", locked])
                    call(["stop", "--time", "10", ident])
                    start_ready()
                    result["volume_lock_races_and_recovery"] = True
                    call(["exec", "--user", "1000", "--env", "XDG_RUNTIME_DIR=/run/user/1000", ident,
                          "systemd-run", "--user", "--unit=fixture-lock-owner-worker", "/bin/sleep", "120"])
                    worker = call(["exec", "--user", "1000", "--env", "XDG_RUNTIME_DIR=/run/user/1000", ident,
                                   "systemctl", "--user", "show", "fixture-lock-owner-worker.service", "--property=MainPID", "--value"]).strip()
                    if not worker.isdigit() or int(worker) <= 0:
                        raise RuntimeError("Lock-owner-death worker did not start")
                    call(["exec", ident, "systemctl", "kill", "--signal=KILL", "altitude-volumes.service"])
                    for attempt in range(40):
                        manager = call(["exec", ident, "systemctl", "show", "user@1000.service", "--property=ActiveState", "--value"]).strip()
                        if manager in ("inactive", "failed"):
                            break
                        if attempt == 39:
                            raise RuntimeError("Lock-owner death left the user manager active")
                        time.sleep(.5)
                    call(["exec", ident, "python3", "-c", "import os,sys;assert not os.path.exists('/proc/'+sys.argv[1])", worker])
                    result["lock_owner_death_stops_user_work"] = True
                    call(["stop", "--time", "10", ident])
                    start_ready()
                    probe("prepare")
                    call(["stop", "--time", "10", ident])
                    start_ready()
                    probe("restarted")  # Continues before replacement: the new identity must close admission again.
                    call(["stop", "--time", "10", ident])
                    call(["rm", ident])
                    ident = call(create).strip()
                    result["replacement_container"] = ident
                    start_ready()
                    probe("replaced")
                    result["uncovered"].remove("Stop/restart/recreation and volume lock concurrency")
                    result["uncovered"].append("full task Stop/resume across image replacement")
                if workflow or recovery:
                    source = Path(__file__).resolve().parent.parent / "tests/container_workflow_probe.py"
                    result["workflow_probe_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                    result["workflow"] = []
                    scenarios = [("prepare", "replaced")] if workflow else []
                    if recovery:
                        scenarios += [("prepare-claim", "recover-claim"), ("prepare-launching", "recover-launching")]
                    result["workflow_replacement_containers"] = []
                    for stages in scenarios:
                        for index, stage in enumerate(stages):
                            if index:
                                call(["stop", "--time", "10", ident])
                                call(["rm", ident])
                                ident = call(create).strip()
                                result["workflow_replacement_containers"].append(ident)
                                start_ready()
                            output = call(["exec", "--user", "1000", "--env", "HOME=/home/altitude",
                                "--env", "XDG_RUNTIME_DIR=/run/user/1000", ident, "python3", "-c",
                                source.read_text(), stage], timeout=60)
                            result["workflow"].append({"stage": stage, **json.loads(output)})
                    result["uncovered"].remove("project/coordinator/task workflow")
                    if lifecycle:
                        result["uncovered"].remove("full task Stop/resume across image replacement")
                    result["uncovered"].extend(["daemon-driven workflow scheduling and browser onboarding",
                        "running-task recovery and actual provider launch interruption during replacement",
                        "other engine protocol and provider/session confinement compatibility"])
                    if not recovery:
                        result["uncovered"].append("native prelaunch and ambiguous-claim reconciliation after replacement")
            result["passed"] = True
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            # Cleanup retains its own finite budget even when the test exhausts its deadline.
            platform.container_command = actual
            cleanup_deadline = time.monotonic() + 90
            def clean(arguments, *, timeout=30):
                remaining = int(cleanup_deadline - time.monotonic())
                if remaining <= 0:
                    raise TimeoutError("Fixture cleanup exhausted its ninety-second command budget")
                return actual(arguments, timeout=min(timeout, remaining))
            try:
                if isolated:
                    for ident in clean(["ps", "--all", "--quiet", "--no-trunc"]).split():
                        clean(["rm", "--force", ident])
                        result["cleanup"].append({"container": ident})
                    for volume in json.loads(clean(["volume", "ls", "--format", "json"])):
                        clean(["volume", "rm", volume["Name"]])
                        result["cleanup"].append({"volume": volume["Name"]})
                    images = list(dict.fromkeys(clean(["images", "--all", "--quiet", "--no-trunc"]).split()))
                    if images:
                        clean(["rmi", "--force", *images])
                        result["cleanup"].append({"images": images})
                    if (clean(["ps", "--all", "--quiet"]).strip()
                            or json.loads(clean(["volume", "ls", "--format", "json"]))
                            or clean(["images", "--all", "--quiet"]).strip()):
                        raise RuntimeError("Fixture containers, volumes or images remain")
                    # Only the verified private, empty store is eligible. Podman
                    # knows both built-in and catatonit pause helpers (#543).
                    # No later Podman call may recreate its pause process.
                    clean(["system", "migrate"])
                    result["pause_retired_by_podman"] = True
                shutil.rmtree(root)
                result["temporary_directory_removed"] = not root.exists()
            except Exception as exc:
                result.update(passed=False, cleanup_error=str(exc), retained_runtime=str(root))
    (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--delegated", help=argparse.SUPPRESS)
    parser.add_argument("--native-sandbox-binary", type=Path,
                        help="optional installed native diagnostic executable; copied as test tooling, never used for a provider session")
    parser.add_argument("--lifecycle", action="store_true",
                        help="also test daemon/container restart, job descendants and replacement admission using fictional data")
    parser.add_argument("--workflow", action="store_true",
                        help="also test real application workflow with a deterministic CLI, native jobs and retained-volume replacement")
    parser.add_argument("--recovery", action="store_true",
                        help="also inject claimed/uncertain resume states and verify recovery after real container replacement")
    args = parser.parse_args()
    outcome = run(args.archive.resolve(), args.sha256, args.results.resolve(), native_binary=args.native_sandbox_binary,
                  lifecycle=args.lifecycle, workflow=args.workflow, recovery=args.recovery, delegated=args.delegated)
    print(json.dumps(outcome, indent=2))
    raise SystemExit(0 if outcome["passed"] else 1)
