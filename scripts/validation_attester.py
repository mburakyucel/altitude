#!/usr/bin/env python3
"""Keyless fixed identity proof, launched only by the administrator-owned activation service."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import stat
import sys

SOURCE = Path(__file__).absolute().parent.parent
if __name__ == '__main__':
    # This precedes imports from the installed tree; worker-owned bytecode is refused too.
    if SOURCE != Path('/usr/local/lib/altitude-validation-relay') or not sys.flags.isolated:
        raise SystemExit('Validation verifier requires its isolated administrator-installed source')
    for entry in (SOURCE, *SOURCE.parents, *SOURCE.rglob('*')):
        info = entry.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise SystemExit('Validation verifier source is not immutable')
sys.path.insert(0, str(SOURCE))
from altitude import platform
from scripts.validation_relay import protected_path

CONFIG = Path('/etc/altitude-validation-relay/attester.json')


def load_config() -> dict:
    protected_path(CONFIG)
    value = json.loads(CONFIG.read_text())
    if (not isinstance(value, dict) or set(value) != {'daemon_uid', 'daemon_cgroup', 'daemon_executable', 'relay_uid', 'system_bus_uid'} or
            type(value['daemon_uid']) is not int or type(value['relay_uid']) is not int or
            type(value['system_bus_uid']) is not int or value['system_bus_uid'] < 0 or
            value['system_bus_uid'] in (value['daemon_uid'], value['relay_uid']) or
            value['daemon_uid'] <= 0 or value['relay_uid'] <= 0 or value['daemon_uid'] == value['relay_uid'] or
            not isinstance(value['daemon_cgroup'], str) or not value['daemon_cgroup'].startswith('/') or
            not isinstance(value['daemon_executable'], str)):
        raise ValueError('Invalid validation verifier configuration')
    protected_path(Path(value['daemon_executable']))
    return value


def handle(connection, config: dict) -> None:
    descriptor = None
    nonce = ''
    allowed = False
    connection.settimeout(platform.VALIDATION_ATTEST_SECONDS - 2)
    try:
        if not platform.validation_attester_relay(connection, config['relay_uid']):
            return
        request, descriptor = platform.validation_attestation_read(connection, with_descriptor=True)
        nonce = request.get('nonce', '')
        if set(request) != {'nonce'} or not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{32}', nonce):
            raise ValueError('Invalid validation verifier request')
        allowed = platform.validation_attester_query(descriptor)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        allowed = False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    platform.validation_attestation_send(connection, {'nonce': nonce, 'allowed': allowed})


def main() -> int:
    try:
        if not sys.flags.isolated:
            raise ValueError('Validation verifier requires isolated Python')
        platform.validation_attester_confine()
        for path in (Path(__file__).resolve(), Path(platform.__file__).resolve(),
                     Path(__file__).resolve().with_name('validation_relay.py')):
            protected_path(path)
        config = load_config()
        if os.getuid() != config['daemon_uid'] or os.getuid() == 0:
            raise ValueError('Validation verifier requires the configured unprivileged identity')
        if len(sys.argv) == 3 and sys.argv[1] == '--prove':
            descriptor = int(sys.argv[2])
            if descriptor < 3:
                raise ValueError('Invalid validation verifier descriptor')
            allowed = platform.validation_attest_daemon(descriptor, config)
            print('allowed' if allowed else 'denied')
            return 0
        if len(sys.argv) != 1:
            raise ValueError('Unsupported validation verifier invocation')
        if os.environ.get('LISTEN_PID') != str(os.getpid()) or os.environ.get('LISTEN_FDS') != '1':
            raise ValueError('Validation verifier requires its administrator-owned socket')
        with socket.socket(fileno=3) as listener:
            if listener.type != socket.SOCK_SEQPACKET:
                raise ValueError('Invalid validation verifier socket type')
            while True:
                connection, _ = listener.accept()
                with connection:
                    try:
                        handle(connection, config)
                    except OSError:
                        pass
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print('Validation verifier identity or configuration is unavailable', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
