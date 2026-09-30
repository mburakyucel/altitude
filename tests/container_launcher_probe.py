"""Fictional VM only: actual committed launcher/image lifecycle, with private Podman state."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path.home() / 'input/source'))
from altitude import platform
from scripts import container, container_acceptance


def main():
    evidence = Path.home() / 'input/product-result'
    evidence.mkdir()
    root = Path(tempfile.mkdtemp(prefix='acp-'))
    archive = Path.home() / 'input/altitude-v0.1.0-rc.2.tar.gz'
    record = {'passed':False,'cleanup':{},'candidate':subprocess.check_output(
        ['cat',str(Path.home()/'input/source-identity')],text=True).strip()}
    actual = platform.container_command
    count = 0
    def command(arguments, **kwargs):
        nonlocal count
        count += 1
        item = {'arguments':arguments}
        try:
            value = actual(arguments, **kwargs)
            item['stdout'] = value
            return value
        except Exception as error:
            item['error'] = repr(error)
            raise
        finally:
            (evidence/f'command-{count:03d}.json').write_text(json.dumps(item,indent=2)+'\n')
    platform.container_command = command
    with container_acceptance.environment(root):
        isolated = False
        instances = ['fictional-primary','fictional-neighbor','fictional-replacement']
        try:
            record['runtime'] = platform.container_runtime()
            if any(not Path(record['runtime']['store'][key]).resolve().is_relative_to(root)
                   for key in ('graphRoot','runRoot')):
                raise RuntimeError('Not a private store')
            isolated = True
            image = container.build(archive,hashlib.sha256(archive.read_bytes()).hexdigest(),
                                    'localhost/altitude:fixture')
            record['image'] = image['Id']
            first = container.start(image['Id'],instances[0],'fictional-home','fictional-projects',
                                    '127.0.0.1','localhost',19443)
            before = container.lifecycle(instances[0])
            record['before'] = before
            if not before['ready']:
                raise RuntimeError('Fresh image is not admitted/ready')
            neighbor = container.start(image['Id'],instances[1],'neighbor-home','neighbor-projects',
                                       '127.0.0.1','localhost',19444)
            platform.container_stop(instances[0])
            stopped = container.owned(instances[0])
            record['graceful_stop'] = stopped['State']
            if stopped['State']['Running'] or stopped['State']['ExitCode']:
                raise RuntimeError('Primary did not stop gracefully')
            if not container.owned(instances[1])['State']['Running']:
                raise RuntimeError('Stopping primary stopped its unrelated neighbor')
            again = platform.container_launch(instances[0])
            after = container.lifecycle(instances[0])
            if again != first or after['instance'] != before['instance'] or not after['ready']:
                raise RuntimeError('Restart changed identity or admission')
            record['same_instance_restart'] = after
            platform.container_stop(instances[0])
            command(['rm',first])
            replacement = container.start(image['Id'],instances[2],'fictional-home','fictional-projects',
                                          '127.0.0.1','localhost',19443)
            held = container.lifecycle(instances[2])
            if held['ready'] or held['instance'] == before['instance']:
                raise RuntimeError('Replacement bypassed Continue or retained old identity')
            record['replacement_held'] = held
            shown = shlex.split(held['continue_command'])
            expected = ['python3','scripts/container.py','continue','--name',instances[2],
                        '--instance',held['instance']]
            if shown != expected:
                raise RuntimeError('Displayed Continue does not name the selected container instance')
            continued = subprocess.run(shown,cwd=Path.home()/'input/source',
                                       capture_output=True,text=True,check=True,timeout=40)
            record['replacement_continued'] = json.loads(continued.stdout)
            if not record['replacement_continued']['ready']:
                raise RuntimeError('Explicit Continue did not release the replacement')
            # Only this fictional guest's exact owned user unit. Parent death must
            # eventually retire the container, without touching the neighbor.
            subprocess.run(['systemctl','--user','kill','--kill-whom=main','--signal=KILL',
                            platform.container_unit(instances[2])],check=True,timeout=10)
            limit = time.monotonic()+55
            while platform.job_active(platform.container_unit(instances[2]),platform.container_user_environment()):
                if time.monotonic() > limit:
                    raise RuntimeError('Supervisor death left its unit alive')
                time.sleep(.5)
            record['supervisor_death'] = container.owned(instances[2])['State']
            if record['supervisor_death']['Running']:
                raise RuntimeError('Supervisor death left a running payload')
            if not container.owned(instances[1])['State']['Running']:
                raise RuntimeError('Supervisor death affected neighbor')
            record['passed'] = True
        except Exception as error:
            record['error'] = repr(error)
        finally:
            try:
                for instance in instances:
                    unit = platform.container_unit(instance)
                    subprocess.run(['systemctl','--user','stop',unit],capture_output=True,timeout=50)
                    log = subprocess.run(['journalctl','--user','--unit',unit,'--no-pager','--lines=100'],
                                         capture_output=True,text=True,timeout=10)
                    (evidence/(instance+'.log')).write_text(log.stdout+log.stderr)
                    if platform.job_active(unit,platform.container_user_environment()):
                        raise RuntimeError('Owned unit survived cleanup')
                if isolated:
                    command(['rm','--force','--all'],timeout=40)
                    for volume in json.loads(command(['volume','ls','--format','json'])):
                        command(['volume','rm',volume['Name']])
                    command(['rmi','--force','--all'],timeout=40)
                    if command(['ps','--all','--quiet']).strip() or command(['images','--quiet']).strip():
                        raise RuntimeError('Private inventory survived cleanup')
                    command(['system','migrate'])
                shutil.rmtree(root)
                record['cleanup'] = {'units_inactive':True,'runtime_removed':not root.exists()}
            except Exception as error:
                record.update(passed=False,cleanup_error=repr(error))
    (evidence/'result.json').write_text(json.dumps(record,indent=2)+'\n')
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
