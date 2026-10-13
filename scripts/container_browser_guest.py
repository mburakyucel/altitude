"""Fixed guest-local controller for the admitted, offline fictional browser lane.

The trusted VM controller calls prepare while online, then unplugs the NIC before
run. Browser code receives two Unix sockets, never a guest administration channel.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'tests'))
import container_browser_probe as fixture
from scripts import container_acceptance
from altitude import platform

INPUT = Path.home() / 'input'
CANDIDATE = INPUT / 'source'
RESULTS = INPUT / 'browser-results'
SOCKETS = INPUT / 'browser-sockets'
MAX_FRAME = 65536
INSTANCES = ('browser-phone', 'browser-desktop')
ACTIONS = frozenset(('prepare', 'start', 'pair', 'control', 'task', 'restart',
                     'replace', 'continue', 'backup', 'stop', 'diagnostics', 'cleanup'))
BROWSER = 'altitude-fictional-browser'


class Protocol:
    """Validate the complete message before invoking any fixed fixture action."""
    def __init__(self, invoke):
        self.invoke = invoke
        self.prepared = False
        self.active = None
        self.finished = set()
        self.replaced = False
        self.closed = False

    def call(self, request):
        if not isinstance(request, dict) or request.get('version') != 1 or type(request.get('version')) is not int:
            raise ValueError('Unsupported fixture protocol version')
        action = request.get('action')
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError('Unsupported fixture action')
        keys = {'version', 'action'}
        if action not in ('prepare', 'cleanup'):
            keys.add('instance')
            if request.get('instance') not in INSTANCES:
                raise ValueError('Unsupported fixture instance')
        if action == 'control':
            keys.add('values')
            values = request.get('values')
            if not isinstance(values, dict) or not values or not set(values) <= {'installed', 'signed_in', 'intro_failure'} or any(type(v) is not bool for v in values.values()):
                raise ValueError('Unsupported fixture control values')
        if set(request) != keys:
            raise ValueError('Unsupported fixture message keys')
        if self.closed:
            raise ValueError('Fixture already cleaned up')
        if action == 'prepare':
            if self.prepared:
                raise ValueError('Fixture already prepared')
        elif not self.prepared:
            raise ValueError('Prepare fixture first')
        elif action == 'start':
            expected = INSTANCES[len(self.finished)] if len(self.finished) < len(INSTANCES) else None
            if self.active is not None or request['instance'] != expected:
                raise ValueError('Invalid viewport start order')
        elif action != 'cleanup':
            if request['instance'] != self.active:
                raise ValueError('Instance is not active')
            if action == 'continue' and not self.replaced:
                raise ValueError('Replace instance before Continue')
            if action in ('replace', 'restart', 'backup') and self.replaced:
                raise ValueError('Continue replacement first')
        result = self.invoke(action, {k: v for k, v in request.items() if k not in ('version', 'action')})
        if action == 'prepare':
            self.prepared = True
        elif action == 'start':
            self.active = request['instance']
        elif action == 'replace':
            self.replaced = True
        elif action == 'continue':
            self.replaced = False
        elif action == 'stop':
            self.finished.add(self.active)
            self.active = None
            self.replaced = False
        elif action == 'cleanup':
            self.closed = True
        return result


def receive(connection, count):
    chunks = bytearray()
    while len(chunks) < count:
        value = connection.recv(count - len(chunks))
        if not value:
            raise ValueError('Truncated fixture frame')
        chunks.extend(value)
    return bytes(chunks)


def framed_request(connection, protocol):
    connection.settimeout(360)
    try:
        size = struct.unpack('!I', receive(connection, 4))[0]
        if not 0 < size <= MAX_FRAME:
            raise ValueError('Fixture request exceeds frame bound')
        result = protocol.call(json.loads(receive(connection, size)))
        response = {'version': 1, 'ok': True, 'result': result}
        data = json.dumps(response).encode()
        if len(data) > MAX_FRAME:
            raise ValueError('Fixture response exceeds frame bound')
    except (ValueError, RuntimeError, OSError) as error:
        data = json.dumps({'version': 1, 'ok': False, 'error': str(error)[:2000]}).encode()
    connection.sendall(struct.pack('!I', len(data)) + data)


class ControlServer(socketserver.ThreadingUnixStreamServer):
    # Serial requests preserve the fixture order and prevent overlapping administration.
    daemon_threads = True
    block_on_close = False
    def __init__(self, path, protocol):
        self.protocol = protocol
        self.action_lock = threading.Lock()
        super().__init__(str(path), ControlHandler)


class ControlHandler(socketserver.BaseRequestHandler):
    def handle(self):
        with self.server.action_lock:
            framed_request(self.request, self.server.protocol)


class HTTPSRelay(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, path, handler):
        self.connections = set()
        self.connection_lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__(path, handler)

    def server_close(self):
        self.stopping.set()
        with self.connection_lock:
            for connection in self.connections:
                connection.close()
        super().server_close()


class HTTPSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        # The only upstream is the existing fixture's published HTTPS endpoint.
        try:
            with socket.create_connection(('127.0.0.1', 19448), timeout=10) as upstream:
                with self.server.connection_lock:
                    self.server.connections.update((self.request, upstream))
                self.request.setblocking(False)
                upstream.setblocking(False)
                peers = {self.request: upstream, upstream: self.request}
                deadline = time.monotonic() + 1050
                while time.monotonic() < deadline and not self.server.stopping.is_set():
                    ready, _, _ = select.select(list(peers), [], [], 1)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = peers[source]
                        destination.settimeout(10)
                        destination.sendall(data)
                        destination.setblocking(False)
        except OSError:
            return
        finally:
            with self.server.connection_lock:
                self.server.connections.discard(self.request)
                if 'upstream' in locals():
                    self.server.connections.discard(upstream)


def browser_command(arguments, *, timeout):
    command = platform.container_arguments(['--events-backend=file', *arguments])
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout)


def ipc_negative_controls(path):
    refused = []
    for name, request in (
        ('version', {'version': 2, 'action': 'prepare'}),
        ('action', {'version': 1, 'action': 'exec'}),
        ('instance', {'version': 1, 'action': 'start', 'instance': 'unrelated'}),
        ('keys', {'version': 1, 'action': 'prepare', 'path': '/unrelated'}),
        ('values', {'version': 1, 'action': 'control', 'instance': INSTANCES[0], 'values': {'installed': 'yes'}}),
        ('order', {'version': 1, 'action': 'start', 'instance': INSTANCES[0]}),
    ):
        body = json.dumps(request).encode()
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(path))
            client.sendall(struct.pack('!I', len(body)) + body)
            count = struct.unpack('!I', receive(client, 4))[0]
            if count > MAX_FRAME:
                raise RuntimeError('IPC control response exceeds bound')
            result = json.loads(receive(client, count))
            if result.get('ok') is not False:
                raise RuntimeError('IPC negative control accepted: ' + name)
            refused.append(name)
    return {'passed': True, 'refused': refused}


def launch_arguments(command):
    return ['run', '--name', BROWSER, '--pull=never', '--network=none', '--userns=keep-id',
            '--user', f'{os.getuid()}:{os.getgid()}', '--cgroups=disabled',
            '--add-host', 'altitude-wrong.invalid:127.0.0.1',
            '--volume', f'{CANDIDATE}:/candidate:ro', '--volume', f'{RESULTS}:/results:rw',
            '--volume', f'{SOCKETS}:/sockets:rw', '--workdir', '/candidate',
            '--env', 'ALTITUDE_BROWSER_PACKAGE=/opt/browser/package.json',
            '--env', 'PLAYWRIGHT_BROWSERS_PATH=/opt/browser/browsers',
            'localhost/altitude-browser', *command]


def container_identity(text):
    value = json.loads(text)[0]
    host = value['HostConfig']
    mounts = {mount['Destination']: mount for mount in value['Mounts']}
    if (host['NetworkMode'] != 'none' or host.get('Privileged') or host.get('Devices') or
            value['Config']['User'] != f'{os.getuid()}:{os.getgid()}' or
            set(mounts) != {'/candidate', '/results', '/sockets'} or mounts['/candidate']['RW']):
        raise RuntimeError('Browser container confinement differs from fixed configuration')
    return {'container': value['Id'], 'image': value['Image'], 'network': host['NetworkMode'],
            'user': value['Config']['User'], 'userns': host.get('UsernsMode'),
            'cgroups': host.get('CgroupMode'), 'mounts': mounts}


def cleanup():
    removed = browser_command(['rm', '--force', '--ignore', BROWSER], timeout=40)
    if removed.returncode:
        raise RuntimeError('Browser container cleanup failed: ' + removed.stderr[-2000:])
    if fixture.ROOT.exists():
        with container_acceptance.environment(fixture.ROOT, reuse=True):
            environment = platform.container_user_environment()
            for instance in ('fixture-browser-build', *INSTANCES,
                             *(name + '-restored' for name in INSTANCES)):
                platform.container_stop_unit(platform.container_unit(instance), environment)
            if fixture.STATE.exists():
                fixture.run('cleanup', {})
            else:
                # A partial online build has no fixture instances or state yet.
                platform.container_command(['rm', '--force', '--all'], timeout=40)
                platform.container_command(['rmi', '--force', '--all'], timeout=40)
                platform.container_command(['system', 'migrate'])
                import shutil
                shutil.rmtree(fixture.ROOT)
    for name in ('control.sock', 'https.sock'):
        (SOCKETS / name).unlink(missing_ok=True)
    return {'browser_removed': True, 'runtime_removed': not fixture.ROOT.exists(),
            'sockets_removed': not any(SOCKETS.glob('*.sock'))}


def disconnected_context():
    marker = json.loads((INPUT / 'network-unplugged.json').read_text())
    identity = json.loads((INPUT / 'validation-identity.json').read_text())
    # Independently inspect guest link state; the controller receipt alone is insufficient.
    links = {p.name: (p / 'carrier').read_text().strip()
             for p in Path('/sys/class/net').iterdir() if p.name != 'lo'}
    if (marker.get('run_id') != identity['run_id'] or marker.get('unplugged') is not True or
            marker.get('helper_event') != 'unplugged' or not isinstance(marker.get('probes'), dict) or
            any(value == '1' for value in links.values())):
        raise RuntimeError('Guest network is still connected')
    for field, args in [('commit', ['rev-parse', 'HEAD']), ('tree', ['rev-parse', 'HEAD^{tree}'])]:
        actual = subprocess.check_output(['git', '-C', str(CANDIDATE), *args], text=True, timeout=10).strip()
        if identity[field] != actual:
            raise RuntimeError('Candidate ' + field + ' differs from validation identity')
    if (CANDIDATE / 'scripts/browser_launch.mjs').read_bytes() != (SOURCE / 'scripts/browser_launch.mjs').read_bytes():
        raise RuntimeError('Candidate browser launch contract differs from deployed protection preflight')
    return marker, identity, links


def browser_config(identity):
    RESULTS.mkdir(exist_ok=True)
    SOCKETS.mkdir(mode=0o700, exist_ok=True)
    config = {'results': '/results', 'certutil': '/usr/bin/certutil',
              'control_socket': '/sockets/control.sock', 'https_socket': '/sockets/https.sock',
              'network_targets': identity['network_targets']}
    (RESULTS / 'config.json').write_text(json.dumps(config))


def browser_stage(name, command, bound):
    start = time.monotonic()
    try:
        output = browser_command(launch_arguments(command), timeout=bound)
        (RESULTS / (name + '.log')).write_text(output.stdout + output.stderr)
        inspect = browser_command(['inspect', BROWSER], timeout=10)
        (RESULTS / (name + '-container.json')).write_text(inspect.stdout)
        if output.returncode or inspect.returncode:
            raise RuntimeError(f'Browser {name} failed; evidence retained')
        return {'exit': output.returncode, 'seconds': time.monotonic() - start,
                'configuration': container_identity(inspect.stdout)}
    finally:
        removed = browser_command(['rm', '--force', '--ignore', BROWSER], timeout=20)
        if removed.returncode:
            raise RuntimeError('Browser stage cleanup failed')


def preflight():
    """Deployed protection code runs before importing any candidate guest administration."""
    marker, identity, links = disconnected_context()
    browser_config(identity)
    receipt = {'passed': False, 'identity': identity, 'network_unplugged': marker, 'guest_links': links}
    for name in ('browser_sandbox.mjs', 'browser_launch.mjs', 'container_browser_network.mjs'):
        shutil.copyfile(SOURCE / 'scripts' / name, RESULTS / name)
    try:
        receipt['network'] = browser_stage('network', ['node', '/results/container_browser_network.mjs', '/results/config.json'], 20)
        receipt['sandbox'] = browser_stage('sandbox', ['node', '/results/browser_sandbox.mjs', '/results/browser-sandbox.json'], 60)
        receipt['passed'] = True
    finally:
        (RESULTS / 'protection.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def run():
    global fixture
    marker, identity, links = disconnected_context()
    # Candidate fixture administration is imported only after the physical cutoff.
    spec = importlib.util.spec_from_file_location('candidate_browser_fixture', CANDIDATE / 'tests/container_browser_probe.py')
    candidate_fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate_fixture)
    fixture = candidate_fixture
    browser_config(identity)
    receipt = {'passed': False, 'identity': identity, 'network_unplugged': marker, 'guest_links': links}
    servers = []
    try:
        protocol = Protocol(lambda action, value: fixture.run(action, value, captured_bases=True))
        for server in (ControlServer(SOCKETS / 'control.sock', protocol),
                       HTTPSRelay(str(SOCKETS / 'https.sock'), HTTPSHandler)):
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        receipt['ipc'] = ipc_negative_controls(SOCKETS / 'control.sock')
        (RESULTS / 'ipc-controls.json').write_text(json.dumps(receipt['ipc']))
        receipt['journeys'] = browser_stage('journeys', ['node', 'scripts/container_browser.mjs', '/results/config.json'], 1050)
        receipt['passed'] = True
    except Exception as error:
        receipt['error'] = repr(error)
    finally:
        try:
            browser_command(['rm', '--force', '--ignore', BROWSER], timeout=40)
        except Exception as error:
            receipt.update(passed=False, browser_stop_error=repr(error))
        for server in servers:
            server.shutdown()
            server.server_close()
        try:
            control = next((s for s in servers if isinstance(s, ControlServer)), None)
            if control and not control.action_lock.acquire(timeout=30):
                raise RuntimeError('Fixture administration did not stop within cleanup bound')
            try:
                receipt['cleanup'] = cleanup()
            finally:
                if control:
                    control.action_lock.release()
        except Exception as error:
            receipt.update(passed=False, cleanup_error=repr(error))
        (RESULTS / 'guest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'preflight', 'run', 'cleanup'))
    action = parser.parse_args().action
    result = fixture.prepare_bases() if action == 'prepare' else preflight() if action == 'preflight' else run() if action == 'run' else cleanup()
    print(json.dumps(result))
    return 1 if result.get('passed') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
