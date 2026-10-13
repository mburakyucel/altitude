"""Trusted controller for the fixed Mac guest browser validation lane.

Candidate code runs only in the throwaway Linux guest. `--bootstrap-only` is a
non-browser budget diagnostic under the standing VM grant, never browser acceptance.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from altitude import config, platform
from scripts import linux_vm_mac as vm

LIMIT = 1_000_000_000
MARGIN = 128_000_000
GUEST = 'ubuntu@127.0.0.1:'


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


class Budget:
    """Trusted provisioning observes counters; candidate fixture code runs offline."""
    def __init__(self, results):
        self.host = self.guest = 0
        self.samples = []
        self.results = results
        self.closed = threading.Event()
        self.error = None

    def host_bytes(self, count):
        self.host += count
        self.check()

    def check(self):
        if self.host + self.guest > LIMIT - MARGIN:
            raise RuntimeError('Download budget reached its shutdown margin')

    def observe(self, machine):
        value = int(machine.ssh("python3 -c \"from pathlib import Path;print(sum(int((p/'statistics/rx_bytes').read_text()) for p in Path('/sys/class/net').iterdir() if p.name!='lo'))\"", timeout=10).stdout)
        if value < self.guest:
            raise RuntimeError('Guest download counter reset during trusted provisioning')
        self.guest = value
        self.samples.append({'host_bytes': self.host, 'guest_bytes': value})
        dump(self.results / 'download-samples.json', self.samples)
        self.check()

    def watch(self, machine):
        while not self.closed.is_set():
            try:
                self.observe(machine)
            except Exception as error:
                self.error = repr(error)
                machine.halt(force=True)
                return
            self.closed.wait(1)


def seed(cache):
    """Reuse only the existing VM lane's Ubuntu cache, under its shared lock."""
    target = cache / vm.IMAGE
    source = platform.validation_guest_seed_cache()
    if target.exists() or source is None:
        return False
    source = source / vm.IMAGE
    names = ('rootfs.img', 'vmlinux', 'initrd', 'image.json')
    if source.is_symlink() or not all((source / n).is_file() and not (source / n).is_symlink() for n in names):
        return False
    with vm.macos.lock():
        target.mkdir(parents=True)
        try:
            for name in names:
                subprocess.run(['cp', '-c', str(source / name), str(target / name)], check=True, timeout=120)
        except BaseException:
            shutil.rmtree(target)
            raise
    return True


def identity(root):
    commit = root.name if re.fullmatch('[0-9a-f]{40}', root.name) else subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    repository = config.REPO if root.name == commit else root
    tree = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', commit + '^{tree}'], text=True).strip()
    return repository, commit, tree


def archive(root, target):
    repository, commit, tree = identity(root)
    subprocess.run(['git', '-C', str(repository), 'archive', '--format=tar', '-o', str(target), commit], check=True)
    return {'commit': commit, 'tree': tree, 'sha256': vm.sha256(target)}


def cleanup(area, cache):
    """Recovery targets a recorded process lifetime, never a name or recycled PID."""
    area = area.resolve()
    receipt = area / 'guest-owner.json'
    owner = json.loads(receipt.read_text()) if receipt.exists() else {}
    process = owner.get('process')
    if not process and (area / 'guest/disk.img').exists():
        raise RuntimeError('Guest disk has no process ownership receipt; retain it for recovery')
    if process and platform.process_identity_live(process):
        os.kill(process['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while platform.process_identity_live(process) and time.monotonic() < deadline:
            time.sleep(.1)
        if platform.process_identity_live(process):
            os.kill(process['pid'], signal.SIGKILL)
            deadline = time.monotonic() + 5
            while platform.process_identity_live(process) and time.monotonic() < deadline:
                time.sleep(.1)
        if platform.process_identity_live(process):
            raise RuntimeError('Owned VM helper remains alive')
    work = area / 'guest'
    if work.is_symlink():
        raise RuntimeError('Guest working directory is a symlink')
    shutil.rmtree(work, ignore_errors=False) if work.exists() else None
    result = {'vm_stopped': not process or not platform.process_identity_live(process), 'work_removed': not work.exists()}
    dump(area / 'guest-cleanup.json', result)
    return result


def guest_file(machine, value, path, work):
    source = work / ('transfer-' + Path(path).name)
    dump(source, value)
    machine.copy(str(source), GUEST + path)
    source.unlink()


def network_probe(machine, targets):
    program = '''import json,socket,sys
result={}
for name,(host,port) in json.loads(sys.argv[1]).items():
    try:
        with socket.create_connection((host,port),timeout=3): result[name]=True
    except OSError: result[name]=False
print(json.dumps(result))'''
    return json.loads(machine.ssh(shlex.join(['python3', '-c', program, json.dumps(targets)]), timeout=15).stdout)


def network_filter(machine, denied_port, host_addresses):
    # Private destinations include the Mac's NAT gateway; DNS is the only exception.
    host_ipv4 = ', '.join(str(ipaddress.ip_address(a)) for a in host_addresses
                         if ipaddress.ip_address(a).version == 4)
    hosts = f'ip daddr {{ {host_ipv4} }} reject' if host_ipv4 else ''
    rules = f'''table inet altitude_validation {{
 chain output {{ type filter hook output priority 0; policy accept;
 tcp dport {denied_port} reject
 oifname "lo" accept
 {hosts}
 ip daddr {{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 192.0.0.0/24, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4 }} reject
 meta nfproto ipv6 reject
 ip daddr 1.1.1.1 udp dport 53 accept
 ip daddr 1.1.1.1 tcp dport 53 accept
 udp dport 53 reject
 tcp dport 53 reject
 }}
}}'''
    machine.ssh('sudo -n resolvectl dns enp0s1 1.1.1.1 && printf %s ' + shlex.quote(rules) + ' > input/network.nft && sudo -n nft -f input/network.nft')
    # A finite 8 MB/s ingress cap makes the 128 MB stop margin cover polling and shutdown.
    machine.ssh('sudo -n tc qdisc add dev enp0s1 clsact && sudo -n tc filter add dev enp0s1 ingress protocol all matchall action police rate 64mbit burst 8mb conform-exceed drop')


def verify_protection(root, identity):
    """Check deployed preflight evidence copied before candidate guest administration runs."""
    protection = json.loads((root / 'protection.json').read_text())
    sandbox = json.loads((root / 'browser-sandbox.json').read_text())
    network = json.loads((root / 'network-controls.json').read_text())
    if protection['identity'] != identity or protection['passed'] is not True:
        raise RuntimeError('Protection preflight identity differs')
    renderers = sandbox.get('renderers', [])
    if (sandbox.get('passed') is not True or sandbox.get('options', {}).get('chromiumSandbox') is not True
            or not sandbox.get('version') or not re.fullmatch('[0-9a-f]{64}', str(sandbox.get('executable_sha256', '')))
            or 'You are adequately sandboxed.' not in sandbox.get('sandbox', '')
            or not renderers or any(not str(r.get('uid', '')).isdigit() or r.get('uid') == '0' or r.get('seccomp') != '2'
                                    or r.get('own_user_namespace') is not True
                                    or r.get('own_pid_namespace') is not True for r in renderers)):
        raise RuntimeError('Required Chromium protection evidence failed')
    if network.get('passed') is not True or set(network.get('attempts', {})) != {'gateway', 'host_listener', 'internet'} or any(a.get('refused') is not True for a in network['attempts'].values()):
        raise RuntimeError('Browser network control evidence failed')
    for name in ('network', 'sandbox'):
        container = json.loads((root / (name + '-container.json')).read_text())[0]
        host, mounts = container['HostConfig'], {m['Destination']: m for m in container['Mounts']}
        if (host.get('NetworkMode') != 'none' or host.get('Privileged') or host.get('Devices')
                or container['Config']['User'] != '1000:1000' or container.get('Image') != identity['browser_image']
                or set(mounts) != {'/candidate', '/results', '/sockets'} or mounts['/candidate']['RW']):
            raise RuntimeError('Browser container protection evidence differs')
    return {'passed': True, 'version': sandbox['version'], 'executable_sha256': sandbox['executable_sha256']}


def verify_journeys(root, identity):
    browser = json.loads((root / 'browser.json').read_text())
    ipc = json.loads((root / 'ipc-controls.json').read_text())
    if browser.get('passed') is not True or ipc.get('passed') is not True or set(ipc.get('refused', [])) != {'version', 'action', 'instance', 'keys', 'values', 'order'}:
        raise RuntimeError('Browser journey or fixture IPC controls failed')
    container = json.loads((root / 'journeys-container.json').read_text())[0]
    if container.get('Image') != identity['browser_image']:
        raise RuntimeError('Journey browser tooling differs from verified preflight image')
    for viewport in ('phone', 'desktop'):
        tls = json.loads((root / viewport / 'tls-controls.json').read_text())
        if tls != {'untrusted_ca_rejected': True, 'wrong_hostname_rejected': True}:
            raise RuntimeError('Browser TLS controls failed')


def run(args):
    area, cache = args.area.resolve(), args.cache.resolve()
    results, work = area / 'results', area / 'guest'
    results.mkdir(parents=True, exist_ok=True)
    work.mkdir()
    record = {'passed': False, 'run_id': args.run_id, 'commit': args.commit, 'tree': args.tree,
              'scope': 'non-browser bootstrap diagnostic' if args.bootstrap_only else 'fixed Mac validation guest actual-image fictional browser',
              'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    machine = thread = monitor = original_ssh = None
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    budget = Budget(results)
    started = time.monotonic()
    deadline = started + args.timeout
    def phase_seconds(bound):
        remaining = int(deadline - time.monotonic() - 90)
        if remaining <= 0:
            raise RuntimeError('Guest phase budget exhausted; time reserved for cleanup')
        return min(bound, remaining)
    try:
        candidate = area / 'work'
        _, commit, tree = identity(candidate)
        if (commit, tree) != (args.commit, args.tree):
            raise RuntimeError('Captured candidate identity differs')
        record['seeded_existing_ubuntu_cache'] = seed(cache)
        record['image'] = vm.base_image(cache, budget=budget.host_bytes)
        original = vm.user_data
        vm.user_data = lambda key: original(key).replace('packages: [git, gh, openssl]',
            'packages: [git, gh, openssl, podman, crun, uidmap, dbus-bin, slirp4netns, nftables]')
        vm.CPUS = platform.validation_guest_cpus()
        record['limits'] = {'cpus': vm.CPUS, 'memory_bytes': vm.MEMORY_GIB * vm.GIB,
                            'disk_bytes': vm.DISK_GIB * vm.GIB, 'minimum_free_bytes': vm.FLOOR,
                            'download_bytes': LIMIT, 'controller_seconds': args.timeout}
        machine = vm.Machine(work, cache / vm.IMAGE)
        machine.start(on_spawn=lambda pid: dump(area / 'guest-owner.json', {'process': platform.process_identity(pid)}))
        original_ssh = machine.ssh
        machine.ssh = lambda command, timeout=60, check=True: original_ssh(command, timeout=phase_seconds(timeout), check=check)
        machine.wait_ready(time.monotonic() + phase_seconds(600))
        record['boot_seconds'] = time.monotonic() - started
        record['guest_runtime'] = machine.ssh('uname -a; podman --version; crun --version').stdout
        resources = "import json,os;from pathlib import Path;print(json.dumps({'cpus':os.cpu_count(),'memory_kib':int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemTotal:'))),'disk_bytes':int(Path('/sys/class/block/vda/size').read_text())*512}))"
        record['guest_resources'] = json.loads(machine.ssh(shlex.join(['python3', '-c', resources])).stdout)
        if (record['guest_resources']['cpus'] != vm.CPUS
                or record['guest_resources']['memory_kib'] * 1024 > vm.MEMORY_GIB * vm.GIB
                or record['guest_resources']['disk_bytes'] != vm.DISK_GIB * vm.GIB):
            raise RuntimeError('Observed guest resources differ from fixed limits')
        budget.observe(machine)
        thread = threading.Thread(target=budget.watch, args=(machine,))
        thread.start()
        machine.ssh('mkdir -p input/controller input/browser-build input/releases')
        controller = work / 'controller.tar'
        record['controller_source'] = archive(SOURCE, controller)
        machine.copy(str(controller), GUEST + 'input/')
        machine.ssh('tar -xf input/controller.tar -C input/controller')
        bundle = work / 'source.bundle'
        subprocess.run(['git', '-C', str(candidate), 'bundle', 'create', str(bundle), 'HEAD'], check=True, timeout=120)
        machine.copy(str(bundle), GUEST + 'input/')
        machine.ssh('git clone -q input/source.bundle input/source && git -C input/source remote set-url origin https://github.com/mburakyucel/altitude')
        source = work / 'source.tar'
        record['source'] = archive(candidate, source)
        identity_record = {key: record[key] for key in ('run_id', 'commit', 'tree', 'controller_sha256')}
        identity_record['source_sha256'] = record['source']['sha256']
        gateway = str(ipaddress.ip_address(machine.ssh("ip -4 route show default | awk '{print $3}'").stdout.strip()))
        host_addresses = platform.validation_guest_host_addresses()
        record['denied_host_addresses'] = host_addresses
        with socket.create_server((gateway, 0)) as listener:
            listener.settimeout(1)
            targets = {'gateway': [gateway, listener.getsockname()[1]], 'host_listener': [gateway, listener.getsockname()[1]], 'internet': ['1.1.1.1', 443]}
            record['network_before_filter'] = network_probe(machine, targets)
            if not all(record['network_before_filter'].values()):
                raise RuntimeError('Guest network positive controls failed')
            network_filter(machine, args.port, host_addresses)
            record['network_after_filter'] = network_probe(machine, targets)
            if record['network_after_filter']['host_listener']:
                raise RuntimeError('Guest provisioning can reach Mac stand-in listener')
            identity_record['network_targets'] = targets
            guest_file(machine, identity_record, 'input/validation-identity.json', work)
            monitor = pool.submit(machine.ssh, 'sudo -n env ALTITUDE_FICTIONAL_VM=1 timeout 3300s python3 input/controller/tests/container_bus_monitor.py input/bus-evidence', timeout=3310, check=False)
            machine.ssh("timeout 10s sh -c 'until test -f input/bus-evidence/ready; do sleep .1; done'")
            control = 'timeout 8s busctl --system call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager StopUnit ss '
            machine.ssh(control + 'altitude-fictional-missing.scope replace', check=False)
            # Image context contains public dependency manifests only, never a guest key.
            for name in ('package.json', 'pnpm-lock.yaml'):
                machine.copy(str(SOURCE / 'web' / name), GUEST + 'input/browser-build/')
            machine.copy(str(SOURCE / 'scripts/container_browser.Containerfile'), GUEST + 'input/browser-build/Containerfile')
            stages = [
                ('browser-image', 'podman --cgroup-manager=cgroupfs build --network=slirp4netns:allow_host_loopback=false --file input/browser-build/Containerfile --tag localhost/altitude-browser input/browser-build', 600),
            ]
            for version in ('v0.1.0-rc.2', 'v0.1.0-rc.3'):
                build = 'pnpm config set --global store-dir /opt/browser/store && python3 scripts/build_release.py --version ' + shlex.quote(version) + ' --output /output'
                command = ['podman', '--cgroup-manager=cgroupfs', 'run', '--rm', '--userns=keep-id', '--user', '1000:1000', '--network=slirp4netns:allow_host_loopback=false', '--volume', '/home/ubuntu/input/source:/work:ro', '--volume', '/home/ubuntu/input/releases:/output', '--workdir', '/work', '--env', 'PYTHONPATH=/work', 'localhost/altitude-browser', 'sh', '-c', build]
                stages.append(('release-' + version, shlex.join(command), 240))
            for name, command, bound in stages:
                before = time.monotonic()
                result = machine.ssh(command, timeout=bound, check=False)
                (results / (name + '.log')).write_text(result.stdout + result.stderr)
                record.setdefault('stages', []).append({'name': name, 'exit': result.returncode, 'seconds': time.monotonic() - before})
                budget.observe(machine)
                if result.returncode:
                    diagnostics = machine.ssh('podman info --debug; podman ps --all --external; journalctl --user --no-pager --lines=100; sudo -n dmesg | tail -60', timeout=30, check=False)
                    (results / (name + '-runtime.log')).write_text(diagnostics.stdout + diagnostics.stderr)
                    raise RuntimeError('Guest stage failed: ' + name)
                if name == 'browser-image':
                    identity_record['browser_image'] = machine.ssh("podman image inspect --format '{{.Id}}' localhost/altitude-browser").stdout.strip()
                    guest_file(machine, identity_record, 'input/validation-identity.json', work)
            machine.ssh('cp input/releases/altitude-v0.1.0-rc.2.tar.gz input/releases/altitude-v0.1.0-rc.3.tar.gz input/')
            before = time.monotonic()
            prepared = machine.ssh('python3 input/controller/scripts/container_browser_guest.py prepare', timeout=600, check=False)
            (results / 'prepare.log').write_text(prepared.stdout + prepared.stderr)
            record['prepare_seconds'] = time.monotonic() - before
            budget.observe(machine)
            if prepared.returncode:
                raise RuntimeError('Actual base images did not prepare')
            record['network_unplug_event'] = machine.unplug()
            record['network_after_unplug'] = network_probe(machine, targets)
            if any(record['network_after_unplug'].values()):
                raise RuntimeError('Unplugged guest retains network access')
        budget.closed.set()
        thread.join(15)
        marker = {'run_id': args.run_id, 'unplugged': True, 'helper_event': 'unplugged', 'probes': record['network_after_unplug']}
        guest_file(machine, marker, 'input/network-unplugged.json', work)
        if not args.bootstrap_only:
            preflight = machine.ssh('python3 input/controller/scripts/container_browser_guest.py preflight', timeout=120, check=False)
            (results / 'preflight.log').write_text(preflight.stdout + preflight.stderr)
            protected = results / 'protection-evidence'
            protected.mkdir()
            machine.copy(GUEST + 'input/browser-results/.', str(protected))
            if preflight.returncode:
                raise RuntimeError('Deployed protection preflight failed')
            record['protection'] = verify_protection(protected, identity_record)
            result = machine.ssh('python3 input/controller/scripts/container_browser_guest.py run', timeout=1180, check=False)
            (results / 'browser.log').write_text(result.stdout + result.stderr)
            record['browser_exit'] = result.returncode
            machine.copy(GUEST + 'input/browser-results', str(results))
            inner = json.loads((results / 'browser-results/guest.json').read_text())
            if result.returncode or not inner['passed']:
                raise RuntimeError('Guest browser acceptance failed')
            verify_journeys(results / 'browser-results', identity_record)
        cleaned = original_ssh('python3 input/controller/scripts/container_browser_guest.py cleanup', timeout=60, check=False)
        (results / 'fixture-cleanup.log').write_text(cleaned.stdout + cleaned.stderr)
        if cleaned.returncode:
            raise RuntimeError('Guest fixture cleanup failed')
        machine.ssh(control + 'altitude-fictional-ending.scope replace', check=False)
        machine.ssh('sudo -n kill -TERM "$(cat input/bus-evidence/ready)"')
        watched = monitor.result()
        machine.copy(GUEST + 'input/bus-evidence', str(results))
        calls = json.loads((results / 'bus-evidence/calls.json').read_text())
        record['manager_unexpected'] = [c for c in calls if c['allowed'] is None]
        record['manager_controls'] = {name: any(c['allowed'] == 'control' and name in c['message'] for c in calls) for name in ('altitude-fictional-missing.scope', 'altitude-fictional-ending.scope')}
        record['passed'] = watched.returncode == 0 and not record['manager_unexpected'] and all(record['manager_controls'].values()) and not budget.error
    except Exception as error:
        record['error'] = repr(error)
        if isinstance(error, subprocess.TimeoutExpired):
            output = b'\n'.join(v if isinstance(v, bytes) else str(v).encode() for v in (error.stdout, error.stderr) if v)
            (results / 'timed-out-stage.log').write_bytes(output)
    finally:
        budget.closed.set()
        if thread:
            thread.join(15)
        if machine:
            try:
                machine.stop()
            except Exception as error:
                record.update(passed=False, stop_error=repr(error))
        for name in ('helper.log', 'console.log'):
            if (work / name).is_file():
                shutil.copyfile(work / name, results / name)
        try:
            record.update(cleanup(area, cache))
        except Exception as error:
            record.update(passed=False, cleanup_error=repr(error))
        if monitor:
            try:
                watched = monitor.result(timeout=30)
                (results / 'manager-monitor.log').write_text(watched.stdout + watched.stderr)
            except Exception as error:
                record['monitor_error'] = repr(error)
        pool.shutdown(wait=True)
        record['downloads'] = {'host': budget.host, 'guest': budget.guest, 'limit': LIMIT, 'stop_margin': MARGIN, 'error': budget.error}
        record['seconds'] = time.monotonic() - started
        dump(results / 'container-browser-vm.json', record)
    return 0 if record['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', nargs='?', choices=('cleanup',))
    parser.add_argument('--area', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--commit')
    parser.add_argument('--tree')
    parser.add_argument('--port', type=int)
    parser.add_argument('--bootstrap-only', action='store_true')
    parser.add_argument('--timeout', type=int, default=3300, help='total finite controller budget, including cleanup')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if args.operation == 'cleanup':
        cleanup(args.area, args.cache)
        return 0
    if not all((args.run_id, args.commit, args.tree, args.port)):
        parser.error('fixed validation identity and denied port required')
    if not 120 <= args.timeout <= 3480:
        parser.error('controller timeout must fit the validation hour and cleanup reserve')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
