"""Fictional VM only: actual committed launcher/image lifecycle, with private Podman state."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
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
        number = count
        item = {'arguments':arguments}
        try:
            value = actual(arguments, **kwargs)
            item['stdout'] = value
            return value
        except Exception as error:
            item['error'] = repr(error)
            raise
        finally:
            (evidence/f'command-{os.getpid()}-{number:03d}.json').write_text(json.dumps(item,indent=2)+'\n')
    platform.container_command = command
    with container_acceptance.environment(root):
        isolated = False
        instances = ['fictional-primary','fictional-neighbor','fictional-replacement','fictional-tls-failure']
        try:
            record['runtime'] = platform.container_runtime()
            if any(not Path(record['runtime']['store'][key]).resolve().is_relative_to(root)
                   for key in ('graphRoot','runRoot')):
                raise RuntimeError('Not a private store')
            isolated = True
            def pause_state():
                files = list((root/'runtime').rglob('pause.pid'))
                if len(files) != 1:
                    raise RuntimeError(f'Expected one private pause PID file, found {files}')
                pid = int(files[0].read_text())
                process = Path('/proc')/str(pid)
                state = {'pid':pid,'cgroup':(process/'cgroup').read_text().strip(),
                         'start':(process/'stat').read_text().rsplit(')',1)[1].split()[19]}
                expected = '0::/user.slice/user-1000.slice/user@1000.service/user.slice/podman-pause-'
                if not state['cgroup'].startswith(expected) or not state['cgroup'].endswith('.scope'):
                    raise RuntimeError(f'Pause helper is not independent of the launcher/build: {state}')
                return state
            record['pause_before_build'] = pause_state()
            image = container.build(archive,hashlib.sha256(archive.read_bytes()).hexdigest(),
                                    'localhost/altitude:fixture')
            record['pause_after_build'] = pause_state()
            if record['pause_before_build'] != record['pause_after_build']:
                raise RuntimeError('Successful build replaced the shared runtime pause helper')
            record['image'] = image['Id']
            first = container.start(image['Id'],instances[0],'fictional-home','fictional-projects',
                                    '127.0.0.1','localhost',19443,new_volumes=True)
            before = container.lifecycle(instances[0])
            record['before'] = before
            if not before['ready']:
                raise RuntimeError('Fresh image is not admitted/ready')
            container.execute(instances[0],['python3','-c',
                "from pathlib import Path;import os,struct; p=Path('/home/altitude/private-fixture');"
                "p.write_text('backup-secret-sentinel-7392');p.chmod(0o600);"
                "os.setxattr(p,'user.fixture',b'private attribute');"
                "acl=struct.pack('<I',2)+b''.join(struct.pack('<HHI',*e) for e in "
                "[(1,6,0xffffffff),(2,4,0),(4,0,0xffffffff),(16,0,0xffffffff),(32,0,0xffffffff)]);"
                "os.setxattr(p,'system.posix_acl_access',acl)"])
            def metadata(instance):
                return json.loads(container.execute(instance,['python3','-c',
                    "import os,json,base64;from pathlib import Path;r={};"
                    "paths=['private-fixture','.config/altitude/tls/ca.key','.config/altitude/tls/server.key'];"
                    "\nfor n in paths:\n p=Path('/home/altitude')/n;s=p.stat();"
                    "r[n]={'uid':s.st_uid,'gid':s.st_gid,'mode':s.st_mode&0o7777,'mtime':s.st_mtime_ns,"
                    "'attrs':{k:base64.b64encode(os.getxattr(p,k)).decode() for k in os.listxattr(p)}}"
                    "\nprint(json.dumps(r))"]))
            original_metadata=metadata(instances[0])
            addresses = json.loads(subprocess.check_output(['ip','-j','-4','address'],text=True))
            private_ip = next(info['local'] for row in addresses for info in row['addr_info']
                              if info.get('scope')=='global')
            neighbor = container.start(image['Id'],instances[1],'neighbor-home','neighbor-projects',
                                       private_ip,'neighbor.fixture.invalid',19444,new_volumes=True)
            record['non_localhost_publication'] = {'bind':private_ip,'public_host':'neighbor.fixture.invalid'}
            # Remove the frontend AND its waiting systemd-run client before the
            # build unit. ExecStopPost must work with no Python finally fallback.
            build_root = Path(tempfile.mkdtemp(prefix='acb-'))
            building = {}
            unit_file = evidence/'interrupted-build-unit'
            job = platform.container_job
            def observe_job(unit, args, **kwargs):
                unit_file.write_text(unit)
                return job(unit, args, **kwargs)
            def interruptible_build():
                os.setsid()
                with mock.patch.object(container.tempfile,'mkdtemp',return_value=str(build_root)), \
                     mock.patch.object(platform,'container_job',side_effect=observe_job):
                    container.build(archive,hashlib.sha256(archive.read_bytes()).hexdigest(),
                                    'localhost/altitude:interrupted')
            builder = multiprocessing.get_context('fork').Process(target=interruptible_build)
            builder.start()
            try:
                limit = time.monotonic()+180
                while True:
                    if not builder.is_alive() or time.monotonic()>limit:
                        raise RuntimeError('No live build container available for interruption')
                    if unit_file.exists() and (build_root/'runtime/bus').exists():
                        ids = container.build_command(build_root,['ps','--all','--external','--quiet']).split()
                        if ids:
                            building.update(unit=unit_file.read_text(),working_containers=ids)
                            break
                    time.sleep(.2)
                private_pause = int(next((build_root/'runtime').rglob('pause.pid')).read_text())
                pause_start = Path(f'/proc/{private_pause}/stat').read_text().rsplit(')',1)[1].split()[19]
                os.killpg(builder.pid,signal.SIGKILL)
                builder.join(timeout=5)
                building['client_exit_before_unit_stop'] = builder.exitcode
                if builder.exitcode != -signal.SIGKILL:
                    raise RuntimeError('Frontend did not exit before build interruption')
                subprocess.run(['systemctl','--user','kill','--kill-whom=main','--signal=KILL',
                                building['unit']],check=True,timeout=10)
                limit = time.monotonic()+platform.CONTAINER_STOP_WAIT
                while platform.job_active(building['unit'],platform.container_user_environment()):
                    if time.monotonic()>limit:
                        raise RuntimeError('Build stop-post did not finish')
                    time.sleep(.2)
                try:
                    current = Path(f'/proc/{private_pause}/stat').read_text().rsplit(')',1)[1].split()[19]
                except FileNotFoundError:
                    current = None
                if current == pause_start:
                    raise RuntimeError('Client-independent cleanup left its private pause helper alive')
                building['private_pause_retired'] = True
            finally:
                if builder.is_alive():
                    os.killpg(builder.pid,signal.SIGKILL)
                builder.join(timeout=5)
                if unit_file.exists():
                    platform.container_stop_unit(unit_file.read_text(),platform.container_user_environment())
            if build_root.exists():
                raise RuntimeError('Interrupted build did not remove its private artifacts')
            if any(not container.owned(instance)['State']['Running'] for instance in instances[:2]):
                raise RuntimeError('Build cleanup affected a running deployment')
            record['interrupted_build'] = building
            unit = platform.container_unit(instances[0])
            supervisor = int(subprocess.check_output(['systemctl','--user','show',unit,'--property=MainPID','--value'],text=True))
            children = Path(f'/proc/{supervisor}/task/{supervisor}/children').read_text().split()
            attached = [int(pid) for pid in children if b'--attach\0' in Path(f'/proc/{pid}/cmdline').read_bytes()]
            if len(attached) != 1:
                raise RuntimeError('Cannot identify the exact attached runtime client')
            descriptor = os.pidfd_open(attached[0])
            try:
                signal.pidfd_send_signal(descriptor,signal.SIGKILL)
            finally:
                os.close(descriptor)
            limit=time.monotonic()+platform.CONTAINER_STOP_WAIT
            while platform.job_active(unit,platform.container_user_environment()):
                if time.monotonic()>limit:
                    raise RuntimeError('Attach failure did not finish cleanup')
                time.sleep(.2)
            record['attach_failure'] = container.owned(instances[0])['State']
            if record['attach_failure']['Running'] or record['attach_failure']['ExitCode']:
                raise RuntimeError('Attach failure did not stop the container gracefully')
            platform.container_launch(instances[0])
            https = platform.container_https
            def mismatched_tls(address,facts):
                return https(address,{**facts,'host':'wrong.fixture.invalid'})
            with mock.patch.object(platform,'container_https',side_effect=mismatched_tls):
                try:
                    container.start(image['Id'],instances[3],'failed-home','failed-projects','127.0.0.1','localhost',19445,new_volumes=True)
                except RuntimeError as error:
                    if 'Container startup failed' not in str(error) or 'certificate' not in str(error).lower():
                        raise
                    record['readiness_refusal'] = str(error)
                else:
                    raise RuntimeError('Real TLS mismatch did not refuse readiness')
            if container.owned(instances[3])['State']['Running']:
                raise RuntimeError('Failed readiness left its container running')
            if not container.owned(instances[1])['State']['Running']:
                raise RuntimeError('Readiness/attach failure affected its neighbor')
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
            continued = subprocess.run(shown,cwd=SOURCE,
                                       capture_output=True,text=True,check=True,timeout=40)
            record['replacement_continued'] = json.JSONDecoder().raw_decode(continued.stdout)[0]
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
            platform.container_recreatable(container.owned(instances[2]))
            container.recreate(instances[2])
            recovered=container.lifecycle(instances[2])
            if recovered['ready'] or recovered['instance']==held['instance']:
                raise RuntimeError('Supervisor-death recreation did not require new-instance Continue')
            record['supervisor_death_recreated_paused']=True
            container.lifecycle(instances[2],'continue',expected=recovered['instance'])
            platform.container_stop(instances[2])
            if not container.owned(instances[1])['State']['Running']:
                raise RuntimeError('Supervisor death affected neighbor')
            record['pause_after_failures'] = pause_state()
            if record['pause_before_build'] != record['pause_after_failures']:
                raise RuntimeError('Build/application failure replaced the neighboring runtime pause helper')
            backup_dir=root/'private-backup'
            record['backup']=container.backup(instances[2],backup_dir)
            restored=container.restore_backup(backup_dir,'restored-home','restored-projects')
            restored_name='fixture-restored'
            instances.append(restored_name)
            container.start(restored['image'],restored_name,'restored-home','restored-projects','127.0.0.1','localhost',19446)
            restored_state=container.lifecycle(restored_name)
            if restored_state['ready'] or restored_state['instance']==held['instance']:
                raise RuntimeError('Restored copy did not require deliberate new-instance continuation')
            content=container.execute(restored_name,['cat','/home/altitude/private-fixture'])
            if content!='backup-secret-sentinel-7392': raise RuntimeError('Private file did not round trip')
            if metadata(restored_name)!=original_metadata:
                raise RuntimeError('Private/TLS ownership, mode, time or ACL attributes changed on restore')
            record['restored_private_tls_metadata']=original_metadata
            record['restored_private_file_and_new_paused_identity']=True
            try:
                refusal_since=time.time()
                platform.container_launch(instances[2])
            except RuntimeError:
                refused=subprocess.run(['journalctl','--user','-u',platform.container_unit(instances[2]),
                    '--since',f'@{refusal_since:.6f}','--no-pager'],capture_output=True,text=True,check=True,timeout=10)
                if 'Stop the other active container using this volume pair or backup lineage first' not in refused.stdout:
                    raise RuntimeError('Original refusal was not the intended copy-admission check')
            else:
                raise RuntimeError('Original started while restored copy was active')
            platform.container_stop(restored_name)
            platform.container_launch(instances[2])
            try:
                refusal_since=time.time()
                platform.container_launch(restored_name)
            except RuntimeError:
                refused=subprocess.run(['journalctl','--user','-u',platform.container_unit(restored_name),
                    '--since',f'@{refusal_since:.6f}','--no-pager'],capture_output=True,text=True,check=True,timeout=10)
                if 'Stop the other active container using this volume pair or backup lineage first' not in refused.stdout:
                    raise RuntimeError('Restored refusal was not the intended copy-admission check')
            else:
                raise RuntimeError('Restored copy started while original was active')
            journal=subprocess.run(['journalctl','--user','--no-pager'],capture_output=True,timeout=15,check=True).stdout
            if b'backup-secret-sentinel-7392' in journal:
                raise RuntimeError('Private archive content reached the user journal')
            record['backup_restore']={'private_file':True,'paused_new_identity':True,
                'both_copy_start_orders_refused':True,'journal_sentinel_absent':True}
            record['passed'] = True
        except Exception as error:
            record['error'] = repr(error)
        finally:
            try:
                for instance in instances:
                    unit = platform.container_unit(instance)
                    platform.container_stop_unit(unit,platform.container_user_environment())
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
