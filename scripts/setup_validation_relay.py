#!/usr/bin/env python3
"""Install the reviewed Linux relay under an explicit administrator setup grant.

No default action changes the machine. --install writes only the relay's account, private
configuration, copied source and four service/socket units. It neither starts nor restarts Altitude.
The input JSON is private and follows validation_relay.FIELDS. Its key/config/known_hosts
inputs require root-only mode0600 beneath immutable root-owned directories. Successful setup
consumes the source private key. Never generate it under an operator/worker-readable account.

Prerequisite: the administrator maintains /usr/bin/python3 and all its standard/system site
packages as trusted OS software. Isolated mode excludes user startup paths; this installer
does not audit the operating system's Python supply chain. Initial setup refuses existing state.
Rotation verifies a staged replacement before switching; the previous key remains until a
separate --retire-old after the owner verifies a daemon roundtrip. Finish active transfers first.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import validation_relay as relay

ACCOUNT = 'altitude-validation-relay'
DESTINATION = Path('/usr/local/lib/altitude-validation-relay')
PRIVATE = Path('/etc/altitude-validation-relay')
UNITS = Path('/etc/systemd/system')
RUNTIME = (Path('/run/altitude-validation-relay'), Path('/run/altitude-validation-attester'))
UNIT_NAMES = (f'{ACCOUNT}.service', f'{ACCOUNT}.socket', 'altitude-validation-attester@.service',
              'altitude-validation-attester.socket')
SOCKET_NAMES = ('altitude-validation-attester.socket', f'{ACCOUNT}.socket')

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
CollectMode=inactive-or-failed

[Service]
User={uid}
ExecStart=/bin/sh -c 'exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LISTEN_PID=$$LISTEN_PID LISTEN_FDS=$$LISTEN_FDS /usr/bin/python3 -I -B /usr/local/lib/altitude-validation-relay/scripts/validation_attester.py'
UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH BASH_ENV ENV PYTHONPATH PYTHONHOME
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=tmpfs
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
RuntimeMaxSec=18
TimeoutStopSec=2
KillMode=control-group
UMask=0077
StandardOutput=null
StandardError=journal
'''


ATTESTER_SOCKET = '''[Unit]
Description=Administrator-routed Altitude validation identity socket

[Socket]
ListenSequentialPacket=/run/altitude-validation-attester/control.sock
Accept=yes
MaxConnections=1
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


def private_source(path: Path) -> None:
    relay.protected_path(path)
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise ValueError('Setup inputs require root-only mode 0600')


def root_directory(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Relay destinations require immutable administrator-owned directories')


def command(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def write_new(path: Path, data: bytes, mode: int = 0o644, gid: int = 0) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, 'wb') as target:
            os.fchown(target.fileno(), 0, gid)
            os.fchmod(target.fileno(), mode)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def install(config_file: Path) -> None:
    if os.getuid() != 0:
        raise ValueError('Relay setup requires the approved administrator action')
    private_source(config_file)
    source = json.loads(config_file.read_text())
    relay.validate_config(source)
    relay.protected_path(Path('/usr/bin/python3').resolve())
    identity_source = Path(source['identity_file'])
    if len({config_file.resolve(), identity_source.resolve(), Path(source['known_hosts']).resolve()}) != 3:
        raise ValueError('Private setup inputs must be distinct files')
    for name in ('identity_file', 'known_hosts'):
        private_source(Path(source[name]))
    relay.protected_path(Path(source['daemon_executable']))
    # Initial setup never overwrites another installation or an existing dedicated identity.
    try:
        pwd.getpwnam(ACCOUNT)
    except KeyError:
        pass
    else:
        raise ValueError('Relay identity already exists; initial setup does not rotate or replace it')
    try:
        grp.getgrnam(ACCOUNT)
    except KeyError:
        pass
    else:
        raise ValueError('Relay group already exists; initial setup refuses adoption')
    directories = (DESTINATION, PRIVATE, *RUNTIME)
    for path in (*directories, *(UNITS / name for name in UNIT_NAMES)):
        if path.exists() or path.is_symlink():
            raise ValueError('Relay installation already exists; initial setup refuses replacement')
        root_directory(path.parent)
    for location in (Path('/run/systemd/system'), Path('/usr/lib/systemd/system'), Path('/lib/systemd/system')):
        if any((location / name).exists() or (location / name).is_symlink() for name in UNIT_NAMES):
            raise ValueError('Relay unit name is already installed')
    bus_uid = relay.platform.validation_system_bus_identity()
    if bus_uid == source['daemon_uid']:
        raise ValueError('System bus requires an identity separate from the daemon')
    daemon = pwd.getpwuid(source['daemon_uid'])
    group = grp.getgrgid(daemon.pw_gid).gr_name
    created, identity_attempted, activated = [], False, False
    try:
        identity_attempted = True
        command(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                 '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', ACCOUNT])
        account = pwd.getpwnam(ACCOUNT)
        if (account.pw_uid in (daemon.pw_uid, 0, bus_uid) or
                account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin') or
                account.pw_gid in os.getgrouplist(daemon.pw_name, daemon.pw_gid) or
                grp.getgrgid(account.pw_gid).gr_mem):
            raise ValueError('Relay and daemon require distinct nonroot identities')
        for directory in directories:
            directory.mkdir(mode=0o755)
            created.append(directory)
            os.chown(directory, 0, 0)
            directory.chmod(0o755)
        for name in ('altitude', 'scripts'):
            package = DESTINATION / name
            package.mkdir(mode=0o755)
            package.chmod(0o755)

        def write(path, data, mode=0o644, gid=0):
            write_new(path, data, mode, gid)
            created.append(path)

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
        public.update(relay_uid=account.pw_uid, system_bus_uid=bus_uid)
        write(PRIVATE / 'attester.json', (json.dumps(public) + '\n').encode())
        for name, content in zip(UNIT_NAMES, (SERVICE, socket_unit(group), attester_service(daemon.pw_uid), ATTESTER_SOCKET)):
            write(UNITS / name, content.encode())
        command(['/usr/bin/systemctl', 'daemon-reload'])
        activated = True
        command(['/usr/bin/systemctl', 'enable', '--now', *SOCKET_NAMES])
        # The only remaining private key is in the relay-only destination. Failed setup keeps its input.
        identity_source.unlink()
    except BaseException:
        rollback_failed = False
        if activated:
            try:
                command(['/usr/bin/systemctl', 'disable', '--now', *SOCKET_NAMES])
            except (OSError, subprocess.SubprocessError):
                rollback_failed = True
        for path in reversed(created):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                rollback_failed = True
        try:
            command(['/usr/bin/systemctl', 'daemon-reload'])
            if identity_attempted:
                try:
                    pwd.getpwnam(ACCOUNT)
                except KeyError:
                    pass
                else:
                    command(['/usr/sbin/userdel', ACCOUNT])
                try:
                    grp.getgrnam(ACCOUNT)
                except KeyError:
                    pass
                else:
                    command(['/usr/sbin/groupdel', ACCOUNT])
        except (OSError, subprocess.SubprocessError):
            rollback_failed = True
        if rollback_failed:
            raise RuntimeError('Relay setup rollback is incomplete; admission must remain disabled') from None
        raise


def installed_file(path: Path, gid: int) -> None:
    relay.protected_path(path)
    info = path.lstat()
    if stat.S_IMODE(info.st_mode) != 0o640 or info.st_gid != gid:
        raise ValueError('Installed private files require the dedicated relay group and mode0640')


def installed_config(path: Path, account) -> dict:
    installed_file(path, account.pw_gid)
    config = json.loads(path.read_text())
    relay.validate_config(config)
    identity = Path(config['identity_file'])
    if (identity.parent != PRIVATE or not (identity.name == 'identity_file' or
            re.fullmatch(r'identity_file-[a-f0-9]{32}', identity.name)) or
            Path(config['known_hosts']) != PRIVATE / 'known_hosts'):
        raise ValueError('Installed credential paths are outside the relay installation')
    return config


def probe(config: dict, account) -> None:
    """Read-only forced-command authentication probe, running as the credential UID."""
    ident = uuid.uuid4().hex
    request = {'operation': 'status', 'run_id': ident}
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(relay.ssh_command(config), input=relay.platform.validation_frame_encode(request),
                stdout=output, stderr=subprocess.DEVNULL, timeout=120, check=False,
                user=account.pw_uid, group=account.pw_gid, extra_groups=(),
                env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent'},
                preexec_fn=relay.platform.validation_relay_transfer_limits)
        if result.returncode != 0 or output.tell() > 16384:
            raise ValueError('Replacement credential verification failed')
        output.seek(0)
        if relay.platform.validation_frame_read(output) != {'run_id': ident, 'status': 'absent'} or output.read(1):
            raise ValueError('Replacement credential verification failed')


def replace_config(data: bytes, gid: int) -> None:
    temporary = PRIVATE / 'config.next.json'
    write_new(temporary, data, 0o640, gid)
    try:
        os.replace(temporary, PRIVATE / 'config.json')
    finally:
        temporary.unlink(missing_ok=True)


def rotate(config_file: Path) -> None:
    if os.getuid() != 0:
        raise ValueError('Credential rotation requires the approved administrator action')
    private_source(config_file)
    replacement = json.loads(config_file.read_text())
    relay.validate_config(replacement)
    source = Path(replacement['identity_file'])
    private_source(source)
    private_source(Path(replacement['known_hosts']))
    account = pwd.getpwnam(ACCOUNT)
    current_path, previous_path = PRIVATE / 'config.json', PRIVATE / 'config.previous.json'
    current = installed_config(current_path, account)
    installed_file(Path(current['identity_file']), account.pw_gid)
    installed_file(Path(current['known_hosts']), account.pw_gid)
    if previous_path.exists() or previous_path.is_symlink():
        raise ValueError('Previous identity still needs verified retirement before another rotation')
    comparison = {**replacement, 'identity_file': current['identity_file'], 'known_hosts': current['known_hosts']}
    if comparison != current or Path(replacement['known_hosts']).read_bytes() != Path(current['known_hosts']).read_bytes():
        raise ValueError('Rotation changes only the client key; endpoint, account and host pin stay fixed')
    candidate_key = PRIVATE / f'identity_file-{uuid.uuid4().hex}'
    candidate = {**current, 'identity_file': str(candidate_key)}
    old_bytes, previous_written, switched = current_path.read_bytes(), False, False
    write_new(candidate_key, source.read_bytes(), 0o640, account.pw_gid)
    try:
        probe(candidate, account)
        write_new(previous_path, old_bytes, 0o640, account.pw_gid)
        previous_written = True
        switched = True
        replace_config((json.dumps(candidate) + '\n').encode(), account.pw_gid)
        command(['/usr/bin/systemctl', 'try-restart', f'{ACCOUNT}.service'])
        probe(candidate, account)
        source.unlink()
    except BaseException:
        try:
            if switched:
                replace_config(old_bytes, account.pw_gid)
                command(['/usr/bin/systemctl', 'try-restart', f'{ACCOUNT}.service'])
            candidate_key.unlink(missing_ok=True)
            if previous_written:
                previous_path.unlink(missing_ok=True)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('Credential rotation rollback is incomplete; retain both identities for recovery') from None
        raise


def retire_old() -> None:
    """Explicit administrator completion after recorded daemon validation; never an implicit rotation step."""
    if os.getuid() != 0:
        raise ValueError('Credential retirement requires the approved administrator action')
    account = pwd.getpwnam(ACCOUNT)
    current = installed_config(PRIVATE / 'config.json', account)
    previous_path = PRIVATE / 'config.previous.json'
    previous = installed_config(previous_path, account)
    old_key, current_key = Path(previous['identity_file']), Path(current['identity_file'])
    if old_key == current_key or {**previous, 'identity_file': current['identity_file']} != current:
        raise ValueError('Previous credential identity does not match this rotation')
    installed_file(current_key, account.pw_gid)
    installed_file(Path(current['known_hosts']), account.pw_gid)
    if old_key.exists() or old_key.is_symlink():
        installed_file(old_key, account.pw_gid)
    probe(current, account)
    old_key.unlink(missing_ok=True)
    previous_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--install', action='store_true', help='Perform separately authorized initial administrator setup')
    actions.add_argument('--rotate', action='store_true', help='Verify and switch the client key, retaining the previous identity')
    actions.add_argument('--retire-old', action='store_true', help='Remove prior local key after recorded daemon verification')
    parser.add_argument('--config', type=Path, help='Private JSON configuration file; never a task artifact')
    args = parser.parse_args()
    if not (args.install or args.rotate or args.retire_old):
        print('No changes made. Review this installer and obtain scoped setup authority before --install --config.')
        return 0
    if (args.install or args.rotate) and args.config is None:
        parser.error('--install and --rotate require --config')
    if args.retire_old and args.config is not None:
        parser.error('--retire-old uses only installed private configuration')
    try:
        if args.install:
            install(args.config)
        elif args.rotate:
            rotate(args.config)
        else:
            retire_old()
    except RuntimeError:
        print('Relay setup failed and rollback is incomplete; keep remote validation disabled and inspect setup locally.', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Relay setup failed; inspect the private setup locally without publishing configuration.', file=sys.stderr)
        return 1
    if args.install:
        print('Relay socket installed. Verify daemon admission and worker refusal before using remote validation.')
    elif args.rotate:
        print('Replacement identity authenticated; prior key retained. Verify the daemon roundtrip before --retire-old.')
    else:
        print('Prior local key retired. Remote authorized-key retirement remains separately scoped work.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
