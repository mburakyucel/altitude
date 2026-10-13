"""Guest-only controls for the real daemon/browser fixture; no fixture HTTP routes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from altitude import platform
from scripts import container, container_acceptance

ROOT=Path.home()/'input/browser-runtime'
STATE=Path.home()/'input/browser-state.json'
ARCHIVE=Path.home()/'input/altitude-v0.1.0-rc.2.tar.gz'
BASES=Path.home()/'input/browser-base-images.json'


def fixture_image(archive, suffix, base=None):
    command=platform.container_command
    image=(json.loads(command(['image','inspect',base]))[0] if base else
           container.build(archive,hashlib.sha256(archive.read_bytes()).hexdigest(),'localhost/altitude:browser-base-'+suffix))
    if base and image['Id'] != base:
        raise RuntimeError('Captured production image identity differs')
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


def build_base_pair(root, unit, archive):
    """The two fictional versions share one disposable delegated build store."""
    images={}
    for suffix,source in [('old',archive),('new',archive.with_name('altitude-v0.1.0-rc.3.tar.gz'))]:
        checksum=hashlib.sha256(source.read_bytes()).hexdigest()
        image=container.build(source,checksum,'localhost/altitude:browser-base-'+suffix,
                              delegated=unit,root=root)
        images[suffix]={'id':image['Id'],'archive_sha256':checksum}
        shutil.rmtree(root/'context')
        (root/'image.tar').unlink()
    return images


def prepare_bases():
    """Online build phase: no fixture code runs or production container boots."""
    ROOT.mkdir(exist_ok=True)
    with container_acceptance.environment(ROOT,reuse=True):
        root=Path(tempfile.mkdtemp(prefix='acp-'))
        unit='altitude-container-build-'+uuid.uuid4().hex+'.service'
        (root/'storage.conf').write_text('[storage]\ndriver="overlay"\n'+
            'graphroot='+json.dumps(str(root/'store'))+'\nrunroot='+json.dumps(str(root/'run'))+'\n')
        (root/'runtime').mkdir(mode=0o700)
        (root/'runtime/bus').symlink_to(f'/run/user/{os.getuid()}/bus')
        command=[sys.executable,'-c','import json,sys;from pathlib import Path;'
                 'from tests.container_browser_probe import build_base_pair;'
                 'print(json.dumps(build_base_pair(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]))))',
                 str(root),unit,str(ARCHIVE)]
        cleanup=[sys.executable,'-c','import sys;from pathlib import Path;'
                 'from scripts.container import cleanup_build;cleanup_build(Path(sys.argv[1]))',str(root)]
        try:
            images=json.loads(platform.container_job(unit,command,wait=True,after_stop=cleanup))
        finally:
            if root.exists():
                environment=platform.container_user_environment()
                if platform.job_active(unit,environment):
                    platform.container_stop_unit(unit,environment)
                    if platform.job_active(unit,environment):
                        raise RuntimeError('Paired build remains active; retain private evidence')
                container.cleanup_build(root)
        BASES.write_text(json.dumps(images))
        return images


def cleanup():
    """Retire created fixture resources; never query units that were not started."""
    if not ROOT.exists():
        return {'runtime_removed':True}
    with container_acceptance.environment(ROOT,reuse=True):
        command=platform.container_command
        names=set(command(['ps','--all','--format','{{.Names}}']).splitlines())
        if STATE.exists():
            names.update(json.loads(STATE.read_text())['instances'])
        owned={'fixture-browser-build','browser-phone','browser-desktop',
               'browser-phone-restored','browser-desktop-restored'}
        if not names<=owned:
            raise RuntimeError('Unexpected running fixture resource; retain evidence')
        for name in names:
            platform.container_stop_unit(platform.container_unit(name),platform.container_user_environment())
        command(['rm','--force','--all'],timeout=40)
        for volume in command(['volume','ls','--quiet']).split(): command(['volume','rm',volume])
        command(['rmi','--force','--all'],timeout=40)
        if command(['ps','--all','--quiet']).strip() or command(['volume','ls','--quiet']).strip():
            raise RuntimeError('Browser fixture resources survived cleanup')
        command(['system','migrate'])
        shutil.rmtree(ROOT)
        STATE.unlink(missing_ok=True)
        BASES.unlink(missing_ok=True)
        return {'runtime_removed':not ROOT.exists()}


def run(action, value, *, captured_bases=False):
    if action=='cleanup':
        return cleanup()
    ROOT.mkdir(exist_ok=True)
    with container_acceptance.environment(ROOT,reuse=True):
        command=platform.container_command
        if action=='prepare':
            bases=json.loads(BASES.read_text()) if captured_bases else {}
            if captured_bases:
                for suffix,archive in [('old',ARCHIVE),('new',ARCHIVE.with_name('altitude-v0.1.0-rc.3.tar.gz'))]:
                    image=bases[suffix]
                    if not isinstance(image['id'],str) or not image['id'] or image['archive_sha256']!=hashlib.sha256(archive.read_bytes()).hexdigest():
                        raise RuntimeError('Captured production image/archive identity differs')
            fixture,base=fixture_image(ARCHIVE,'old',bases['old']['id'] if captured_bases else None)
            upgraded,new=fixture_image(ARCHIVE.with_name('altitude-v0.1.0-rc.3.tar.gz'),'new',bases['new']['id'] if captured_bases else None)
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
            return {'complete':saved['complete'],'paused':not receipt['ready'],'upgraded_version':version,**observed}
        raise ValueError(action)


if __name__=='__main__':
    print(json.dumps(run(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else {})))
