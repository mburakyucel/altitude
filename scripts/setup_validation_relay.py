#!/usr/bin/env python3
"""Install the reviewed Linux relay under an explicit administrator setup grant.

No default action changes the machine. --install writes only the relay's account, private
configuration, copied source and four service/socket units. It neither starts nor restarts Altitude.
The input JSON is private and follows validation_relay.FIELDS; its key/known_hosts paths
refer to the independently provisioned, dedicated SSH material. Never print this JSON.

Prerequisite: the administrator maintains /usr/bin/python3 and all its standard/system site
packages as trusted OS software. Isolated mode excludes user startup paths; this installer
does not audit the operating system's Python supply chain. Before reinstalling/rotating,
finish active transfers: setup reloads only the relay and keyless verifier's own services.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import validation_relay as relay

ACCOUNT = 'altitude-validation-relay'
DESTINATION = Path('/usr/local/lib/altitude-validation-relay')
PRIVATE = Path('/etc/altitude-validation-relay')

SERVICE = '''[Unit]
Description=Bounded Altitude remote validation credential relay
Requires=altitude-validation-relay.socket
After=altitude-validation-relay.socket

[Service]
User=altitude-validation-relay
Group=altitude-validation-relay
ExecStart=/bin/sh -c 'exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LISTEN_PID=$$LISTEN_PID LISTEN_FDS=$$LISTEN_FDS /usr/bin/python3 -I -B /usr/local/lib/altitude-validation-relay/scripts/validation_relay.py'
UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH BASH_ENV ENV PYTHONPATH PYTHONHOME
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=yes
MemoryMax=1536M
TasksMax=8
LimitFSIZE=402653192
UMask=0077
StandardOutput=null
StandardError=journal
'''


def attester_service(uid: int) -> str:
    return f'''[Unit]
Description=Keyless Altitude validation identity verifier
Requires=altitude-validation-attester.socket
After=altitude-validation-attester.socket

[Service]
User={uid}
ExecStart=/bin/sh -c 'exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LISTEN_PID=$$LISTEN_PID LISTEN_FDS=$$LISTEN_FDS /usr/bin/python3 -I -B /usr/local/lib/altitude-validation-relay/scripts/validation_attester.py'
UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH BASH_ENV ENV PYTHONPATH PYTHONHOME
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=yes
BindReadOnlyPaths=/run/user/{uid}/systemd/private
PrivateTmp=yes
PrivateDevices=yes
PrivateNetwork=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX
LockPersonality=yes
MemoryMax=192M
TasksMax=4
UMask=0077
StandardOutput=null
StandardError=journal
'''


ATTESTER_SOCKET = '''[Unit]
Description=Administrator-routed Altitude validation identity socket

[Socket]
ListenSequentialPacket=/run/altitude-validation-attester/control.sock
SocketUser=root
SocketGroup=altitude-validation-relay
SocketMode=0660
DirectoryMode=0755
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
'''


def socket_unit(group: str) -> str:
    return f'''[Unit]
Description=Altitude remote validation daemon-only control socket

[Socket]
ListenStream=/run/altitude-validation-relay/control.sock
SocketUser=root
SocketGroup={group}
SocketMode=0660
DirectoryMode=0755
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
'''


def install(config_file: Path) -> None:
    if os.getuid() != 0:
        raise ValueError('Relay setup requires the approved administrator action')
    source = json.loads(config_file.read_text())
    relay.validate_config(source)
    relay.protected_path(Path('/usr/bin/python3').resolve())
    # Validate source paths before creating anything. Existing secrets stay where the operator put them.
    for name in ('identity_file', 'known_hosts', 'daemon_executable'):
        path = Path(source[name])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Required setup file is unavailable')
    relay.protected_path(Path(source['daemon_executable']))
    bus_uid = relay.platform.validation_system_bus_identity()
    if bus_uid == source['daemon_uid']:
        raise ValueError('System bus requires an identity separate from the daemon')
    daemon = pwd.getpwuid(source['daemon_uid'])
    group = grp.getgrgid(daemon.pw_gid).gr_name
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                        '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', ACCOUNT], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        account = pwd.getpwnam(ACCOUNT)
    if (account.pw_uid == daemon.pw_uid or account.pw_uid == 0 or account.pw_uid == bus_uid or
            account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin') or
            account.pw_gid in os.getgrouplist(daemon.pw_name, daemon.pw_gid) or
            grp.getgrgid(account.pw_gid).gr_mem):
        raise ValueError('Relay and daemon require distinct nonroot identities')
    for directory in (DESTINATION, DESTINATION / 'altitude', DESTINATION / 'scripts', PRIVATE,
                      Path('/run/altitude-validation-relay'), Path('/run/altitude-validation-attester')):
        for ancestor in (directory, *directory.parents):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
                raise ValueError('Relay destinations require immutable administrator-owned directories')
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chown(directory, 0, 0)
        directory.chmod(0o755)

    def write(path, data, mode=0o644, gid=0):
        # Never follow an existing destination symlink during administrator setup.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(data)
            os.fchown(target.fileno(), 0, gid)
            os.fchmod(target.fileno(), mode)

    for relative in ('altitude/platform.py', 'scripts/validation_relay.py', 'scripts/validation_attester.py'):
        write(DESTINATION / relative, (ROOT / relative).read_bytes())
    write(DESTINATION / 'altitude/__init__.py', b'')
    write(DESTINATION / 'scripts/__init__.py', b'')
    for name in ('identity_file', 'known_hosts'):
        destination = PRIVATE / name
        write(destination, Path(source[name]).read_bytes(), 0o640, account.pw_gid)
        source[name] = str(destination)
    write(PRIVATE / 'config.json', (json.dumps(source) + '\n').encode(), 0o640, account.pw_gid)
    public = {name: source[name] for name in ('daemon_uid', 'daemon_cgroup', 'daemon_executable')}
    public['relay_uid'] = account.pw_uid
    public['system_bus_uid'] = bus_uid
    write(PRIVATE / 'attester.json', (json.dumps(public) + '\n').encode())
    units = Path('/etc/systemd/system')
    write(units / f'{ACCOUNT}.service', SERVICE.encode())
    write(units / f'{ACCOUNT}.socket', socket_unit(group).encode())
    write(units / 'altitude-validation-attester.service', attester_service(daemon.pw_uid).encode())
    write(units / 'altitude-validation-attester.socket', ATTESTER_SOCKET.encode())
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/systemctl', 'enable', '--now', 'altitude-validation-attester.socket', f'{ACCOUNT}.socket'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/systemctl', 'try-restart', 'altitude-validation-attester.service', f'{ACCOUNT}.service'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Perform separately authorized administrator setup')
    parser.add_argument('--config', type=Path, help='Private JSON configuration file; never a task artifact')
    args = parser.parse_args()
    if not args.install:
        print('No changes made. Review this installer and obtain scoped setup authority before --install --config.')
        return 0
    if args.config is None:
        parser.error('--install requires --config')
    try:
        install(args.config)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        print('Relay setup failed; inspect the private setup locally without publishing configuration.', file=sys.stderr)
        return 1
    print('Relay socket installed. Verify daemon admission and worker refusal before using remote validation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
