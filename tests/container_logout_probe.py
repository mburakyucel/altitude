"""Fictional VM only: user-manager exit and a new login around the real container launcher."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from altitude import platform
from scripts import container, container_acceptance

EVIDENCE = Path.home() / 'input/product-result'
STATE = EVIDENCE / 'logout.json'
NAME = 'fictional-logout'


def prepare():
    EVIDENCE.mkdir()
    root = Path(tempfile.mkdtemp(prefix='acl-'))
    result = {'passed': False, 'root': str(root)}
    STATE.write_text(json.dumps(result))
    with container_acceptance.environment(root):
        archive = Path.home() / 'input/altitude-v0.1.0-rc.2.tar.gz'
        image = container.build(archive, hashlib.sha256(archive.read_bytes()).hexdigest(),
                                'localhost/altitude:logout-fixture')
        ident = container.start(image['Id'], NAME, 'logout-home', 'logout-projects',
                                '127.0.0.1', 'localhost', 19449, new_volumes=True)
        result.update(id=ident, lifecycle=container.lifecycle(NAME),
                      pid=container.owned(NAME)['State']['Pid'])
        container.execute(NAME, ['systemd-run', '--user', '--unit=fixture-logout-worker',
            '/bin/sh', '-c', 'trap "echo graceful > /home/altitude/logout-worker-stopped; exit 0" TERM; '
            'echo ready > /home/altitude/logout-worker-ready; while :; do sleep 1; done'])
        limit = time.monotonic() + 10
        while True:
            try:
                container.execute(NAME, ['test', '-f', '/home/altitude/logout-worker-ready'])
                break
            except RuntimeError:
                if time.monotonic() > limit:
                    raise
                time.sleep(.2)
        STATE.write_text(json.dumps(result))
        # This SSH process belongs to its login session, outside user@1000.service.
        # The request uses ONLY the fictional guest's user bus; no system-manager stop.
        subprocess.run(['systemctl', '--user', 'exit'], check=True, timeout=10)
        limit = time.monotonic() + platform.CONTAINER_STOP_WAIT
        while Path('/proc', str(result['pid'])).exists():
            if time.monotonic() > limit:
                raise RuntimeError('Container PID survived user-manager exit')
            time.sleep(.2)
        result['container_pid_gone_after_manager_exit'] = True
        STATE.write_text(json.dumps(result))


def verify():
    result = json.loads(STATE.read_text())
    root = Path(result['root'])
    with container_acceptance.environment(root, reuse=True):
        try:
            value = container.owned(NAME)
            result['stopped_state'] = value['State']
            if not platform.container_stopped(value) or value['State']['ExitCode']:
                raise RuntimeError('User-manager exit did not gracefully stop the exact container')
            platform.container_launch(NAME)
            after = container.lifecycle(NAME)
            if after['instance'] != result['lifecycle']['instance'] or not after['ready']:
                raise RuntimeError('New login changed same-container identity or admission')
            if container.execute(NAME, ['cat', '/home/altitude/logout-worker-stopped']).strip() != 'graceful':
                raise RuntimeError('Inner worker did not receive its graceful shutdown')
            result.update(same_instance_admitted_after_new_login=True,
                          inner_worker_graceful=True, passed=True)
        except Exception as error:
            result['error'] = repr(error)
        finally:
            try:
                platform.container_stop(NAME)
                platform.container_command(['rm', result['id']])
                for volume in ('logout-home', 'logout-projects'):
                    platform.container_command(['volume', 'rm', volume])
                platform.container_command(['rmi', '--all'])
                if platform.container_command(['ps', '--all', '--quiet']).strip():
                    raise RuntimeError('Logout fixture left containers')
                platform.container_command(['system', 'migrate'])
                shutil.rmtree(root)
                result['cleanup'] = {'runtime_removed': not root.exists()}
            except Exception as error:
                result.update(passed=False, cleanup_error=repr(error))
    (EVIDENCE / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        prepare()
    elif sys.argv[1] == 'verify':
        raise SystemExit(verify())
    else:
        raise SystemExit('Choose prepare or verify')
