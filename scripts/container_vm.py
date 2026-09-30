"""Build and test the container launcher in a disposable Ubuntu 24.04 amd64 VM.

Run `make container-vm RESULTS=DIR`; task owners use the validation runner.
The actual image, launcher, systemd and Podman run with fictional state. Sender
metadata and service-manager monitoring stay entirely inside the guest. The VM
needs internet for distribution/image packages, but receives no host credentials.
This lane proves no Mac, provider authentication or physical-device behavior.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts import installation_vm as vm


def run(results, cache, *, image_workflow=False, native_binary=None):
    results = results.resolve()
    results.mkdir()
    record = {'passed': False, 'scope': 'fictional Ubuntu VM committed Altitude container launcher and image lifecycle in disposable Linux VM'}
    if image_workflow:
        record['scope'] = 'fictional Ubuntu VM image confinement and deterministic application workflow/recovery'
    work = Path(tempfile.mkdtemp(prefix='acg-vm-'))
    machine = None
    monitor = None
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        missing = vm.missing_prerequisites()
        if missing:
            raise RuntimeError(str(missing))
        original_data = vm.user_data
        vm.user_data = lambda key: original_data(key).replace(
            'packages: [git, gh, openssl]',
            'packages: [git, gh, openssl, podman, crun, uidmap, dbus-bin]')
        cache.mkdir(parents=True, exist_ok=True)
        record['image'] = vm.base_image(cache)
        release = work / 'release'
        subprocess.run([sys.executable,str(REPO/'scripts/build_release.py'),'--version','v0.1.0-rc.2',
                        '--output',str(release)],check=True,timeout=240, cwd=REPO)
        machine = vm.Machine(work, cache / vm.IMAGE)
        machine.start()
        machine.wait_ready(time.monotonic() + 600)
        record['guest'] = machine.ssh('uname -a; podman --version; crun --version').stdout
        source = work / 'source.tar'
        subprocess.run(['git', '-C', str(REPO), 'archive', '--format=tar', '-o', str(source), 'HEAD'], check=True)
        record['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        record['source_commit'] = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
        machine.ssh('mkdir -p input/source')
        machine.copy(str(source), 'ubuntu@127.0.0.1:input/')
        machine.ssh('tar -xf input/source.tar -C input/source')
        identity = work/'source-identity'
        identity.write_text(record['source_commit']+'\n')
        machine.copy(str(release/'altitude-v0.1.0-rc.2.tar.gz'),str(identity),'ubuntu@127.0.0.1:input/')
        if native_binary:
            diagnostic = work/'native-sandbox'
            shutil.copyfile(native_binary, diagnostic)
            diagnostic.chmod(0o755)
            record['native_sandbox_sha256'] = vm.sha256(diagnostic)
            machine.copy(str(diagnostic),'ubuntu@127.0.0.1:input/')
        # Monitor only this fictional guest. Never read or connect to the real host's system bus.
        monitor = pool.submit(machine.ssh,
            'sudo -n env ALTITUDE_FICTIONAL_VM=1 timeout 1100s python3 input/source/tests/container_bus_monitor.py input/bus-evidence',
            timeout=1110, check=False)
        machine.ssh("timeout 10s sh -c 'until test -f input/bus-evidence/ready; do sleep .1; done'")
        # A nonexistent unit supplies a harmless positive control for the forbidden method detector.
        control = machine.ssh(
            'timeout 8s busctl --system call org.freedesktop.systemd1 /org/freedesktop/systemd1 '
            'org.freedesktop.systemd1.Manager StopUnit ss altitude-fictional-missing.scope replace', check=False)
        record['control'] = {'exit': control.returncode, 'stdout': control.stdout, 'stderr': control.stderr}
        guest_command = ['python3','input/source/tests/container_launcher_probe.py']
        if image_workflow:
            guest_command = ['python3','input/source/scripts/container_acceptance.py',
                '--archive','input/altitude-v0.1.0-rc.2.tar.gz',
                '--sha256',vm.sha256(release/'altitude-v0.1.0-rc.2.tar.gz'),
                '--results','input/product-result','--lifecycle','--workflow','--recovery']
            if native_binary:
                guest_command += ['--native-sandbox-binary','input/native-sandbox']
        run = machine.ssh(
            'XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus '
            + shlex.join(guest_command), timeout=1050, check=False)
        (results / 'probe.log').write_text(run.stdout + run.stderr)
        record['probe_exit'] = run.returncode
        machine.copy('ubuntu@127.0.0.1:input/product-result', str(results))
        end_control = machine.ssh('timeout 8s busctl --system call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager StopUnit ss altitude-fictional-ending.scope replace', check=False)
        record['ending_control_exit'] = end_control.returncode
        machine.ssh('sudo -n kill -TERM "$(cat input/bus-evidence/ready)"')
        watched = monitor.result()
        machine.copy('ubuntu@127.0.0.1:input/bus-evidence',str(results))
        (results / 'system-manager-monitor.log').write_text(watched.stdout + watched.stderr)
        record['monitor_exit'] = watched.returncode
        calls = json.loads((results/'bus-evidence/calls.json').read_text())
        exit_state = json.loads((results/'bus-evidence/exit.json').read_text())
        record['control_observed'] = any(call['allowed'] == 'control' and
            'altitude-fictional-missing.scope' in call['message'] for call in calls)
        record['ending_control_observed'] = any(call['allowed'] == 'control' and
            'altitude-fictional-ending.scope' in call['message'] for call in calls)
        record['unexpected_manager_calls'] = [call for call in calls if call['allowed'] is None]
        record['manager_calls'] = {kind: sum(call['allowed'] == kind for call in calls)
                                   for kind in {call['allowed'] for call in calls}}
        inner = json.loads((results / 'product-result/result.json').read_text())
        record['passed'] = (run.returncode == 0 and inner['passed']
                            and watched.returncode == 0 and exit_state['exit'] == -15 and record['control_observed']
                            and record['ending_control_observed'] and not record['unexpected_manager_calls'])
    except Exception as error:
        record['error'] = repr(error)
    finally:
        if machine:
            machine.stop()
            record['vm_stopped'] = machine.process is None or machine.process.poll() is not None
        if monitor:
            try:
                watched = monitor.result(timeout=110)
                (results / 'system-manager-monitor.log').write_text(watched.stdout + watched.stderr)
            except Exception as error:
                record['monitor_error'] = repr(error)
        pool.shutdown(wait=True)
        for name in ('console.log', 'qemu.log'):
            if (work / name).is_file():
                shutil.copyfile(work / name, results / name)
        shutil.rmtree(work)
        record['work_removed'] = not work.exists()
        (results / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record, indent=2), flush=True)
    return 0 if record['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--cache', type=Path, default=Path.home()/'.cache/altitude-installation-vm')
    parser.add_argument('--image-workflow', action='store_true', help='run image profile/lifecycle/workflow/recovery instead of launcher lifecycle')
    parser.add_argument('--native-sandbox-binary', type=Path, help='diagnostic executable for the image workflow lane; no provider calls')
    args = parser.parse_args()
    if args.native_sandbox_binary and not args.image_workflow:
        parser.error('--native-sandbox-binary needs --image-workflow')
    return run(args.results, args.cache, image_workflow=args.image_workflow, native_binary=args.native_sandbox_binary)


if __name__ == '__main__':
    raise SystemExit(main())
