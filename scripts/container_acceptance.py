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
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import platform
from scripts import container


@contextmanager
def environment(root: Path):
    saved = dict(os.environ)
    names = {"HOME": "home", "XDG_RUNTIME_DIR": "runtime", "XDG_DATA_HOME": "data",
             "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache"}
    for child in names.values():
        (root / child).mkdir(mode=0o700)
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
assert config.PUBLIC_HOST == 'localhost', config.PUBLIC_HOST
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
assert request('/api/overview')['update']['managed'] == 'image'
service = platform.status()
assert service['ActiveState'] == 'active'
daemon_status = (Path('/proc') / service['MainPID'] / 'status').read_text().splitlines()
assert next(line.split()[1] for line in daemon_status if line.startswith('NoNewPrivs:')) == '1'
for key in ['Seccomp:', 'CapEff:']:
    line = next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith(key))
    assert line.split()[1] == ('2' if key == 'Seccomp:' else '0000000000000000'), line
print(json.dumps({'health':health, 'machine':machine, 'daemon_no_new_privileges':True,
    'certificate':tls.info(), 'service':service}, indent=2))
'''


def run(archive: Path, checksum: str, evidence: Path) -> dict:
    evidence.mkdir(parents=True, exist_ok=False)
    root = Path(tempfile.mkdtemp(prefix="altitude-container-gate-"))
    result = {"passed": False, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "scope": "image bootstrap with network-none payload", "cleanup": [],
              "uncovered": ["actual engine sandbox in stripped image", "project/coordinator/task workflow",
                            "Stop/restart/recreation and volume lock concurrency", "image update/backup/recovery",
                            "published networking/HTTPS and browser onboarding", "Mac ARM64", "live authentication/providers"]}
    actual = platform.container_command
    sequence = 0
    started = time.monotonic()
    isolated = False
    def call(arguments, *, timeout=30, interactive=False):
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
            output = actual(arguments, timeout=limit, interactive=interactive)
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
                container.local_volume(volume)
            ident = call(["create", "--name", "altitude-bootstrap-fixture", "--network=none", "--cgroupns=private",
                          "--security-opt=unmask=/proc/*", "--memory=512m", "--cpus=1", "--pids-limit=256",
                          "--volume", "fixture-home:/home/altitude:nocopy",
                          "--volume", "fixture-projects:/home/altitude/Projects:nocopy", image["Id"]]).strip()
            result["container"] = ident
            result["container_inspect"] = json.loads(call(["inspect", ident]))[0]
            call(["start", ident])
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
                "altitude-volumes.service", "user@1000.service", "--property=ActiveState,SubState,Result,MainPID"]))
            if not ready:
                raise RuntimeError("Image application service did not start; inspect bootstrap.log and retained unit evidence")
            result["elevation_inventory"] = json.loads(call(["exec", "--user", "0", ident, "python3", "-c", INVENTORY], timeout=30))
            output = call(["exec", "--user", "1000", "--env", "HOME=/home/altitude", "--env", "XDG_RUNTIME_DIR=/run/user/1000",
                           ident, "python3", "-c", PROBE], timeout=30)
            result["bootstrap"] = json.loads(output)
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
                    if clean(["ps", "--all", "--quiet"]).strip() or json.loads(clean(["volume", "ls", "--format", "json"])):
                        raise RuntimeError("Fixture containers or volumes remain")
                result["pause_helpers_retired"] = platform.cleanup_container_pause(root / "home")
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
    args = parser.parse_args()
    outcome = run(args.archive.resolve(), args.sha256, args.results.resolve())
    print(json.dumps(outcome, indent=2))
    raise SystemExit(0 if outcome["passed"] else 1)
