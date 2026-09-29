"""Native diagnostic against the packaged image profiles; fictional data and no provider session.

Executed by container_acceptance.py in a disposable container, never against operator state.
The application deployment is root-owned, so its unsandboxed write control must already deny.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tomllib

home = Path('/home/altitude')
binary = Path(sys.argv[1])
assert binary.is_file() and not binary.stat().st_mode & 0o6000
assert "security.capability" not in os.listxattr(binary)
os.environ['ALTITUDE_HOME'] = str(home / 'sandbox-fixture-state')
os.environ['CODEX_HOME'] = str(home / '.codex')
sys.path.insert(0, '/opt/altitude')
from altitude import config, engines, l3

def command(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=20, check=True)

project = home / 'Projects/demo'
workspace = project / '.claude/worktrees/task'
sibling = project / '.claude/worktrees/other'
runtime = home / 'coordinator-runtime'
tls = home / '.config/altitude/tls'
for path in (project, runtime, tls, config.ROOT, home / '.codex'):
    path.mkdir(parents=True, exist_ok=True)
command(['git', 'init', '--initial-branch=main', str(project)])
command(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
         'commit', '--allow-empty', '-m', 'fictional baseline'], project)
command(['git', 'worktree', 'add', '-b', 'task', str(workspace)], project)
command(['git', 'worktree', 'add', '-b', 'other', str(sibling)], project)
config.PROJECTS_FILE.write_text(json.dumps({'fixture': {'path': str(project)}}))
git_roots = engines._git_dirs(workspace)
targets = {'workspace': workspace, 'git_common': git_roots[0], 'git_worktree': git_roots[-1],
           'state': config.ROOT, 'project': project, 'sibling': sibling,
           'deployment': config.SOURCE, 'tls': tls, 'runtime': runtime,
           'protected_codex': workspace / '.codex', 'protected_agents': workspace / '.agents',
           'temporary': Path('/tmp/task-native-write')}
for path in targets.values():
    path.mkdir(parents=True, exist_ok=True)
broker = l3.verb_socket_path('fixture')
broker.parent.mkdir(parents=True, exist_ok=True)
unix_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
unix_server.bind(str(broker)); unix_server.listen(8)
tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
tcp_server.bind(('127.0.0.1', 0)); tcp_server.listen(8)
assert tcp_server.getsockname()[1] not in (8890, 8891)
spec = {'targets': {k: str(v) for k, v in targets.items()}, 'broker': str(broker),
        'port': tcp_server.getsockname()[1],
        # Probe the real endpoints independently of how the profile represents denial.
        'controls': {'user_bus': '/run/user/1000/bus',
                     'user_manager_private': '/run/user/1000/systemd/private'}}
child = r'''
import errno, json, os, socket, subprocess, sys
from pathlib import Path
spec = json.loads(sys.argv[1]); role = sys.argv[2]
out = {'role': role, 'writes': {}, 'connections': {}}
for name, directory in spec['targets'].items():
    path = Path(directory) / ('probe-' + role)
    try:
        path.write_text('fictional write'); path.unlink()
        out['writes'][name] = {'allowed': True}
    except OSError as e:
        out['writes'][name] = {'allowed': False, 'errno': e.errno}
for name, family, address in [(name, socket.AF_UNIX, path) for name, path in spec['controls'].items()] + [
                             ('broker', socket.AF_UNIX, spec['broker']),
                             ('loopback', socket.AF_INET, ('127.0.0.1', spec['port']))]:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(1); s.connect(address)
        out['connections'][name] = {'connected': True}
    except OSError as e:
        out['connections'][name] = {'connected': False, 'errno': e.errno, 'error': str(e)}
out['security'] = [x for x in Path('/proc/self/status').read_text().splitlines()
                   if x.startswith(('Seccomp:', 'CapEff:', 'NoNewPrivs:'))]
if role == 'task':
    git = subprocess.run(['git', '-C', spec['targets']['workspace'], '-c', 'user.name=Fixture',
                          '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty',
                          '-m', 'sandbox fixture'], capture_output=True, text=True, timeout=10)
    out['git_commit'] = {'exit': git.returncode, 'stderr': git.stderr}
print(json.dumps(out))
'''
records = {}
records['unsandboxed_control'] = json.loads(command(
    ['/usr/bin/python3', '-c', child, json.dumps(spec), 'control']).stdout)
profiles = {'task': engines.codex_sandbox(workspace, extra_roots=git_roots),
            'coordinator': engines.codex_l3_permissions(runtime, project='fixture')}
for role, settings in profiles.items():
    args = [str(binary), 'sandbox']
    # Select the actual application profile without translating its rules.
    args += ['-P', tomllib.loads('\n'.join(settings))['default_permissions']]
    for value in settings:
        args += ['-c', value]
    args += ['-C', str(workspace if role == 'task' else runtime),
             '/usr/bin/python3', '-c', child, json.dumps(spec), role]
    try:
        run = subprocess.run(args, capture_output=True, text=True, timeout=30)
        row = {'settings': settings, 'exit': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        try:
            row['observed'] = json.loads(run.stdout)
        except ValueError:
            row['unavailable'] = 'Native sandbox did not produce fixture observation.'
    except subprocess.TimeoutExpired:
        row = {'settings': settings, 'unavailable': 'Native sandbox timed out after 30 seconds.'}
    records[role] = row
unix_server.close(); tcp_server.close(); broker.unlink()
records['versions'] = {name: command(args).stdout.strip() for name, args in {
    'native_diagnostic': [str(binary), '--version'], 'bwrap': ['bwrap', '--version'],
    'git': ['git', '--version'], 'systemd': ['/lib/systemd/systemd', '--version']}.items()}
records['limits'] = ['No provider, real credentials, external network or real coordinator broker. Image daemon stays on its separate state root.',
                     'Bus connection tests send no messages; TCP reaches only a fictional loopback listener.',
                     'No coordinator MCP session or real authentication compatibility claim.']
failures = []
control = records['unsandboxed_control']
for name, result in control['writes'].items():
    if result['allowed'] != (name != 'deployment'):
        failures.append('unsandboxed control differs from image ownership for ' + name)
if not all(r['connected'] for r in control['connections'].values()):
    failures.append('unsandboxed socket controls failed')
for role, writable in [('task', {'workspace', 'git_common', 'git_worktree', 'state', 'temporary'}),
                       ('coordinator', {'runtime'})]:
    row = records[role]
    if row.get('exit') != 0 or 'observed' not in row:
        failures.append(role + ': native sandbox unavailable')
        continue
    observed = row['observed']
    security = dict(line.split(':', 1) for line in observed['security'])
    for key, expected in [('CapEff', '0000000000000000'), ('NoNewPrivs', '1'), ('Seccomp', '2')]:
        if security.get(key, '').strip() != expected:
            failures.append(role + ': unexpected ' + key)
    for name in targets:
        if observed['writes'][name]['allowed'] != (name in writable):
            failures.append(role + ': unexpected write permission for ' + name)
    for name, outcome in observed['connections'].items():
        expected = role == 'task' and name in {'broker', 'loopback'}
        if outcome['connected'] != expected:
            failures.append(role + ': unexpected connection permission for ' + name)
    if role == 'task' and observed['git_commit']['exit'] != 0:
        failures.append('task: git commit failed')
records['gate_failures'] = failures
records['gate_passed'] = not failures
print(json.dumps(records, indent=2))
