"""Guest-only controls for the real daemon/browser fixture; no fixture HTTP routes."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from altitude import platform
from scripts import container, container_acceptance

ROOT=Path.home()/'input/browser-runtime'
STATE=Path.home()/'input/browser-state.json'
ARCHIVE=Path.home()/'input/altitude-v0.1.0-rc.2.tar.gz'


def fixture_image(archive, suffix):
    command=platform.container_command
    image=container.build(archive,hashlib.sha256(archive.read_bytes()).hexdigest(),'localhost/altitude:browser-base-'+suffix)
    ident=container.start(image['Id'],'fixture-browser-build','browser-build-home','browser-build-projects',
        '127.0.0.1','localhost',19448,new_volumes=True)
    command(['exec',ident,'mkdir','-p','/opt/fixture','/etc/systemd/user/altitude.service.d'])
    for source,dest in [('container_browser_daemon.py','daemon.py'),('container_browser_engine.py','engine')]:
        command(['cp',str(SOURCE/'tests'/source),ident+':/opt/fixture/'+dest])
    command(['exec',ident,'chmod','755','/opt/fixture/engine'])
    command(['exec',ident,'python3','-c',"from pathlib import Path;Path('/etc/systemd/user/altitude.service.d/fixture.conf').write_text('[Service]\\nExecStart=\\nExecStart=/usr/bin/python3 -B /opt/fixture/daemon.py\\n')"])
    # A committed test image must not inherit the build container's
    # admission identity. Production images are built without booting.
    command(['exec',ident,'python3','-c',
        "import sys;sys.path.insert(0,'/opt/altitude');from altitude.platform import CONTAINER_INSTANCE;CONTAINER_INSTANCE.unlink()"])
    platform.container_stop('fixture-browser-build')
    fixture=command(['commit',ident,'localhost/altitude:browser-fixture-'+suffix],timeout=45).strip()
    command(['rm',ident]); command(['volume','rm','browser-build-home','browser-build-projects'])
    return fixture,image['Id']


def run(action, value):
    ROOT.mkdir(exist_ok=True)
    with container_acceptance.environment(ROOT,reuse=True):
        command=platform.container_command
        if action=='prepare':
            fixture,base=fixture_image(ARCHIVE,'old')
            upgraded,new=fixture_image(ARCHIVE.with_name('altitude-v0.1.0-rc.3.tar.gz'),'new')
            STATE.write_text(json.dumps({'image':fixture,'upgrade':upgraded,'instances':[]}))
            return {'image':fixture,'source_image':base,'upgrade':upgraded,'upgrade_source_image':new,
                    'version_scope':'Two fixture release versions from the same application schema; no arbitrary migration claim'}
        state=json.loads(STATE.read_text())
        instance=value.get('instance','browser-desktop')
        if action=='start':
            container.start(state['image'],instance,instance+'-home',instance+'-projects',
                '127.0.0.1','localhost',19448,new_volumes=True)
            state['instances'].append(instance); STATE.write_text(json.dumps(state))
            return {'ca':platform.container_command(['exec',container.owned(instance)['Id'],
                'cat','/home/altitude/.config/altitude/tls/ca.crt'])}
        if action=='control':
            return container.execute(instance,['python3','-c',
                "import json,sys;from pathlib import Path;p=Path.home()/'.fixture-control.json';v=json.loads(p.read_text());v.update(json.loads(sys.argv[1]));p.write_text(json.dumps(v))",json.dumps(value['values'])])
        if action=='diagnostics':
            return {'journal':container.execute(instance,['journalctl','--user','-u','altitude.service','--no-pager','--lines=100'])}
        if action=='pair':
            return json.loads(container.execute(instance,['python3','-c',
                "import sys,json;sys.path.insert(0,'/opt/altitude');from altitude import access;print(json.dumps(access.issue_code()))"]))
        if action=='task':
            return json.loads(container.execute(instance,['python3','-c',
                "import sys,json;from pathlib import Path;sys.path.insert(0,'/opt/altitude');"
                "from altitude import state as S,tasks as T,engines,dispatch;"
                "t=S.load_task('atlas','browser-fixture-task');w=Path(t['worktree']);"
                "print(json.dumps({'state':t['state'],'session':t.get('session_id'),'stop_id':t.get('stop_id'),"
                "'hold':t.get('hold_merge'),'draft':(w/'fixture-draft.txt').read_text(),"
                "'inputs':[json.loads(s) for s in (w/'fixture-input.jsonl').read_text().splitlines()],"
                "'pending':len(T.pending('atlas',t['slug'])),'terminated':engines.worker_termination(t,job_root=dispatch.l2_job_root('atlas',t['slug']))}))"]))
        if action=='restart':
            platform.container_stop(instance); platform.container_launch(instance)
            return container.lifecycle(instance)
        if action=='replace':
            platform.container_stop(instance); container.recreate(instance)
            return container.lifecycle(instance)
        if action=='continue':
            return container.lifecycle(instance,'continue',expected=container.lifecycle(instance)['instance'])
        if action=='stop':
            platform.container_stop(instance); return {'stopped':True}
        if action=='backup':
            platform.container_stop(instance)
            saved=container.backup(instance,ROOT/('backup-'+instance))
            command(['rm',container.owned(instance)['Id']])
            container.start(state['upgrade'],instance,instance+'-home',instance+'-projects',
                '127.0.0.1','localhost',19448)
            upgraded=container.lifecycle(instance)
            if upgraded['ready']: raise RuntimeError('Version upgrade bypassed host Continue')
            version=json.loads(container.execute(instance,['python3','-c',
                "import sys,json;sys.path.insert(0,'/opt/altitude');from altitude import config;print(json.dumps(config.RELEASE['version']))"]))
            if version!='v0.1.0-rc.3': raise RuntimeError('Updated image version is not active')
            container.lifecycle(instance,'continue',expected=upgraded['instance'])
            container.execute(instance,['python3','-c',
                "from pathlib import Path;(Path.home()/'.fixture-after-upgrade').write_text('newer state')"])
            platform.container_stop(instance)
            restored=instance+'-restored'
            copy=container.restore_backup(ROOT/('backup-'+instance),restored+'-home',restored+'-projects')
            container.start(copy['image'],restored,copy['home'],copy['projects'],
                '127.0.0.1','localhost',19448)
            state['instances'].append(restored);STATE.write_text(json.dumps(state))
            receipt=container.lifecycle(restored)
            if receipt['ready']: raise RuntimeError('Restored registered projects were admitted automatically')
            observed=json.loads(container.execute(restored,['python3','-c',
                "import sys,json;sys.path.insert(0,'/opt/altitude');from altitude import config,state as S;"
                "t=S.load_task('atlas','browser-fixture-task');"
                "print(json.dumps({'projects':sorted(config.load_projects()),'operator':config.operator_name(),"
                "'task_session':t['session_id'],'hold':t['hold_merge'],'version':config.RELEASE['version'],"
                "'later_state_absent':not (config.HOME/'.fixture-after-upgrade').exists()}))"]))
            platform.container_stop(restored)
            return {'complete':saved['format']==1,'paused':not receipt['ready'],'upgraded_version':version,**observed}
        if action=='cleanup':
            for name in state['instances']:
                platform.container_stop_unit(platform.container_unit(name),platform.container_user_environment())
            command(['rm','--force','--all'],timeout=40)
            for volume in command(['volume','ls','--quiet']).split(): command(['volume','rm',volume])
            command(['rmi','--force','--all'],timeout=40)
            if command(['ps','--all','--quiet']).strip() or command(['volume','ls','--quiet']).strip():
                raise RuntimeError('Browser fixture resources survived cleanup')
            command(['system','migrate']); shutil.rmtree(ROOT)
            return {'runtime_removed':not ROOT.exists()}
        raise ValueError(action)


if __name__=='__main__':
    print(json.dumps(run(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else {})))
