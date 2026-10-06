#!/usr/bin/env python3
"""Private credential relay. Install only through the reviewed, separately authorized setup.

No request controls an endpoint, filesystem path, SSH option or host command. The trusted
daemon sends bounded data to the single forced command configured on the remote account.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).absolute().parent.parent
if __name__ == '__main__':
    # Check before package imports, including any existing bytecode and package initializers.
    if SOURCE != Path('/usr/local/lib/altitude-validation-relay') or not sys.flags.isolated:
        raise SystemExit('Validation relay requires its isolated administrator-installed source')
    for entry in (SOURCE, *SOURCE.parents, *SOURCE.rglob('*')):
        info = entry.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise SystemExit('Validation relay source is not immutable')
sys.path.insert(0, str(SOURCE))
from altitude import platform

CONFIG = Path('/etc/altitude-validation-relay/config.json')
FIELDS = {'daemon_uid', 'daemon_cgroup', 'daemon_executable', 'endpoint', 'account',
          'identity_file', 'known_hosts'}


def protected_path(path: Path, *, private: bool = False) -> None:
    """Reject replaceable code/config or credentials exposed to the worker account."""
    if not path.is_absolute():
        raise ValueError('Relay paths must be absolute')
    for item in (path, *path.parents):
        info = item.lstat()
        directory = item != path
        if (info.st_uid != 0 or info.st_mode & 0o022 or
                not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
            raise ValueError('Relay files and parents require immutable administrator ownership')
        if item == path and private and (info.st_mode & 0o007 or info.st_gid != os.getgid()):
            raise ValueError('Relay credentials must be private to its service group')


def validate_config(value: dict) -> None:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Invalid relay configuration fields')
    if type(value['daemon_uid']) is not int or value['daemon_uid'] <= 0:
        raise ValueError('Relay requires a nonroot daemon identity')
    if not isinstance(value['daemon_cgroup'], str) or not re.fullmatch(r'/[A-Za-z0-9_.@:/\\-]+', value['daemon_cgroup']):
        raise ValueError('Invalid daemon cgroup')
    if not isinstance(value['endpoint'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', value['endpoint']):
        raise ValueError('Invalid private endpoint')
    if not isinstance(value['account'], str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', value['account']):
        raise ValueError('Invalid remote service account')
    for name in ('daemon_executable', 'identity_file', 'known_hosts'):
        if not isinstance(value[name], str) or '\x00' in value[name] or not Path(value[name]).is_absolute():
            raise ValueError('Relay paths must be absolute')


def load_config(path: Path = CONFIG) -> dict:
    protected_path(path, private=True)
    value = json.loads(path.read_text())
    validate_config(value)
    protected_path(Path(value['daemon_executable']))
    for name in ('identity_file', 'known_hosts'):
        protected_path(Path(value[name]), private=True)
    return value


def validate_request(value: dict) -> None:
    operation = value.get('operation')
    keys = {'operation', 'run_id'}
    if operation == 'submit':
        keys |= {'argv', 'payload', 'duration'}
        argv = value.get('argv')
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 256 or
                any(not isinstance(arg, str) or '\x00' in arg or len(arg) > 65536 for arg in argv) or
                not isinstance(value.get('payload'), dict) or type(value.get('duration')) is not int or
                not 120 < value['duration'] <= 3600):
            raise ValueError('Invalid validation submission')
    elif operation == 'result':
        if 'received' in value:
            keys.add('received')
            if not isinstance(value['received'], str) or not re.fullmatch('[a-f0-9]{64}', value['received']):
                raise ValueError('Invalid result acknowledgement')
    elif operation not in ('status', 'cancel'):
        raise ValueError('Unsupported validation operation')
    if set(value) != keys or not isinstance(value.get('run_id'), str) or not re.fullmatch('[a-f0-9]{32}', value['run_id']):
        raise ValueError('Invalid validation request')


def ssh_command(config: dict) -> list[str]:
    return ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'IdentityAgent=none', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
            '-o', 'GSSAPIAuthentication=no', '-o', 'PasswordAuthentication=no',
            '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'PermitLocalCommand=no',
            '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UserKnownHostsFile=' + config['known_hosts'],
            '-i', config['identity_file'], '-l', config['account'], '--', config['endpoint'],
            'altitude-validation-v1']


def exchange(config: dict, request: dict, output, timeout: int) -> None:
    """One fixed SSH exchange; its reply remains on disk under the file-size cap."""
    result = subprocess.run(ssh_command(config), input=platform.validation_frame_encode(request),
                            stdout=output, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                            preexec_fn=platform.validation_relay_transfer_limits)
    if result.returncode != 0:
        raise ValueError('Remote transport failed')
    # Structural frame validation only. Do not decode/re-encode up to 384 MiB here.
    platform.validation_frame_size(output)


def forward(config: dict, request: dict, output) -> None:
    """Write one response frame. Only a fresh submit gets a pre-send reachability probe.

    The daemon assigns a fresh run ID and never resubmits it after uncertain admission.
    A failed status probe therefore proves this submit was never sent. Any transport
    failure after starting submit remains uncertain, including a lost SSH reply.
    """
    validate_request(request)
    submitted = request['operation'] != 'submit'
    try:
        if not submitted:
            with tempfile.TemporaryFile() as probe:
                exchange(config, {'operation': 'status', 'run_id': request['run_id']}, probe,
                         platform.VALIDATION_REACHABILITY_SECONDS)
                if probe.seek(0, os.SEEK_END) > 16384:
                    raise ValueError('Invalid reachability response')
                probe.seek(0)
                response = platform.validation_frame_read(probe)
                if response != {'run_id': request['run_id'], 'status': 'absent'}:
                    # Never replay an existing identity or a cancellation tombstone.
                    if response.get('run_id') != request['run_id'] or response.get('status') not in {
                            'running', 'finished', 'cancelled'}:
                        raise ValueError('Invalid reachability response')
                    probe.seek(0)
                    platform.validation_frame_forward(probe, output.write)
                    return
            submitted = True
        exchange(config, request, output, platform.VALIDATION_TRANSFER_SECONDS)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        response = {'status': 'unavailable', 'error': 'The Mac is unavailable or its validation transport failed'}
        if not submitted:
            response['accepted'] = False
        output.seek(0)
        output.truncate()
        output.write(platform.validation_frame_encode(response))


def handle(connection, config: dict) -> None:
    connection.settimeout(platform.VALIDATION_REQUEST_SECONDS)
    try:
        if not platform.validation_relay_peer(connection, config):
            response = {'status': 'unavailable', 'accepted': False,
                        'error': 'Only the configured daemon may use this relay'}
        else:
            with connection.makefile('rb') as stream:
                request = platform.validation_frame_read(stream)
            validate_request(request)
            response = None
    except (OSError, ValueError, TypeError):
        response = {'status': 'unavailable', 'accepted': False, 'error': 'Invalid validation relay request'}
    if response is not None:
        connection.sendall(platform.validation_frame_encode(response))
        return
    # A disconnected recipient after forwarding cannot prove nonadmission.
    with tempfile.TemporaryFile() as output:
        forward(config, request, output)
        platform.validation_frame_forward(output, connection.sendall)


def main() -> int:
    try:
        if not sys.flags.isolated:
            raise ValueError('Relay requires isolated Python')
        if os.getuid() == 0:
            raise ValueError('Relay requires its dedicated nonroot service identity')
        config = load_config()
        protected_path(Path(__file__).resolve())
        protected_path(Path(platform.__file__).resolve())
        # systemd socket activation owns the socket path and its connection permissions.
        if os.environ.get('LISTEN_PID') != str(os.getpid()) or os.environ.get('LISTEN_FDS') != '1':
            raise ValueError('Relay requires its dedicated socket unit')
        with socket.socket(fileno=3) as listener:
            while True:
                connection, _ = listener.accept()
                with connection:
                    try:
                        handle(connection, config)
                    except OSError:
                        pass  # A disconnected daemon has no recipient; never print private transport data.
    except (OSError, ValueError, TypeError, KeyError):
        print('Validation relay configuration or identity is unavailable', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
