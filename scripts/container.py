#!/usr/bin/env python3
"""Build and manage Altitude's rootless, volume-only Linux container from a verified release archive."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import installation, platform

LABEL = "io.altitude.container"


def name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", value):
        raise ValueError("Choose a simple container/volume name, without a path or runtime options")
    return value


def build_command(root: Path, arguments: list[str], **kwargs) -> str:
    return platform.container_command(["--root", str(root / "store"), "--runroot", str(root / "run"),
                                       *arguments], runtime_dir=root / "runtime", storage_conf=root / "storage.conf", **kwargs)


def cleanup_build(root: Path) -> None:
    """Retire only this build's private store and pause helper, including after SIGKILL (#543)."""
    if not root.exists():
        return
    info = root.lstat()
    if not root.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Build cleanup requires its owned private directory")
    deadline = time.monotonic()+40  # stop-post45 seconds includes interpreter/filesystem margin
    def call(arguments, timeout=30):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Build cleanup budget exhausted; artifacts retained at {root}")
        return build_command(root,arguments,timeout=min(timeout,remaining))
    store = json.loads(call(["info", "--format=json"]))["store"]
    if Path(store["graphRoot"]) != root / "store" or Path(store["runRoot"]) != root / "run":
        raise RuntimeError("Build store identity changed; retain it for inspection")
    # Podman --external lists interrupted Buildah working containers too. Force
    # removal accepts those IDs; inventory is confined to the verified private store.
    containers = call(["ps", "--all", "--external", "--quiet"]).split()
    if containers:
        call(["rm", "--force", *containers], timeout=20)
    call(["rmi", "--force", "--all"], timeout=20)
    if call(["ps", "--all", "--external", "--quiet"]).strip() or call(["images", "--quiet"]).strip():
        raise RuntimeError("Private build resources remain; retain the directory")
    call(["system", "migrate"])
    # No more Podman calls after migration: they can create another pause helper.
    shutil.rmtree(root)


def build(archive: Path, checksum: str, tag: str, *, delegated: str | None = None,
          root: Path | None = None) -> dict:
    platform.container_runtime()
    if not re.fullmatch(r"localhost/[a-z0-9][a-z0-9/_.-]*:[a-zA-Z0-9_.-]+", tag):
        raise ValueError("Use a local image tag such as localhost/altitude:v0.1.0-rc.2")
    if delegated is None:
        import uuid
        unit = "altitude-container-build-" + uuid.uuid4().hex + ".service"
        root = Path(tempfile.mkdtemp(prefix="acb-"))
        (root / 'storage.conf').write_text('[storage]\ndriver="overlay"\n' +
            'graphroot='+json.dumps(str(root/'store'))+'\nrunroot='+json.dumps(str(root/'run'))+'\n')
        (root / "runtime").mkdir(mode=0o700)
        (root / "runtime/bus").symlink_to(f"/run/user/{os.getuid()}/bus")
        command = [sys.executable, "-c", "import json,sys;from pathlib import Path;"
            "from scripts.container import build;print(json.dumps(build(Path(sys.argv[1]),sys.argv[2],sys.argv[3],delegated=sys.argv[4],root=Path(sys.argv[5]))))",
            str(archive.resolve()), checksum, tag, unit, str(root)]
        cleanup = [sys.executable, "-c", "import sys;from pathlib import Path;"
                   "from scripts.container import cleanup_build;cleanup_build(Path(sys.argv[1]))", str(root)]
        try:
            return json.loads(platform.container_job(unit, command, wait=True, after_stop=cleanup))
        finally:
            if root.exists():
                # If the waiting client timed out, stop the exact build service before
                # touching its store. A refused stop retains evidence; never prune shared data.
                environment = platform.container_user_environment()
                if platform.job_active(unit, environment):
                    platform.container_stop_unit(unit, environment)
                    if platform.job_active(unit, environment):
                        raise RuntimeError(f"Build remains active; private artifacts retained at {root}")
                cleanup_build(root)
    parent = platform.container_parent(delegated)
    if root is None:
        raise ValueError("The build needs its private store")
    context = root / "context"
    app = context / "app"
    app.mkdir(parents=True)
    release = installation.extract(archive, checksum, app)
    packaging = app / "container"
    if not (packaging / "Containerfile").is_file():
        raise ValueError("This release predates container support; use a release containing container packaging")
    shutil.copyfile(packaging / "Containerfile", context / "Containerfile")
    build_command(root, ["build", "--force-rm", "--isolation=oci", "--network=slirp4netns",
                                    "--cgroup-parent", parent + "/image-build",
                                    "--memory=1g", "--cpu-quota=100000", "--tag", tag,
                                    "--label", f"{LABEL}=1", "--label", f"org.opencontainers.image.version={release['version']}",
                                    "--label", f"org.opencontainers.image.revision={release['commit']}",
                                    "--label", f"io.altitude.archive.sha256={checksum.lower()}", str(context)], timeout=480)
    image = json.loads(build_command(root, ["image", "inspect", tag]))[0]
    exported = root / "image.tar"
    build_command(root, ["save", "--format=oci-archive", "--output", str(exported), tag], timeout=60)
    platform.container_command(["load", "--input", str(exported)], timeout=60)
    imported = json.loads(platform.container_command(["image", "inspect", tag]))[0]
    if imported["Id"] != image["Id"]:
        raise RuntimeError("Loaded image differs from the completed private build")
    return imported


def local_volume(volume: str, *, lineage: str, pair: str, role: str, create: bool = False,
                 restored: str = '', operation: str = '') -> dict:
    name(volume)
    existing = json.loads(platform.container_command(["volume", "ls", "--format", "json"]))
    if not any(item.get("Name") == volume for item in existing):
        if not create:
            raise ValueError('The recorded volume is missing; it is never created implicitly')
        labels = {LABEL:'1', 'io.altitude.lineage':lineage, 'io.altitude.pair':pair,
                  'io.altitude.volume-role':role, 'io.altitude.restore':restored, 'io.altitude.operation':operation}
        platform.container_command(['volume','create', *[item for k,v in labels.items() for item in ('--label', f'{k}={v}')], volume])
    value = json.loads(platform.container_command(["volume", "inspect", volume]))[0]
    if value.get("Driver") != "local" or value.get("Options"):
        raise ValueError("Use plain local named volumes; host binds and network/shared filesystems are unsupported")
    labels = value.get('Labels') or {}
    if any(labels.get(key) != expected for key,expected in
           ((LABEL,'1'), ('io.altitude.lineage',lineage), ('io.altitude.pair',pair), ('io.altitude.volume-role',role))):
        raise ValueError('Use the recorded dedicated volume pair; unrelated or unlabeled experimental volumes are not adopted')
    return value


def volume_pair(home: str, projects: str, *, create: bool = False) -> dict:
    names = {item['Name'] for item in json.loads(platform.container_command(['volume','ls','--format','json']))}
    present = [name(v) in names for v in (home,projects)]
    if any(present) and create:
        raise ValueError('Fresh creation requires two unused volume names; omit --new-volumes to reuse a recorded pair')
    if any(present) != all(present):
        raise ValueError('One volume of the pair is missing; repair it before starting')
    if not any(present):
        if not create:
            raise ValueError('Both recorded volumes are required')
        lineage, pair = uuid.uuid4().hex, uuid.uuid4().hex
    else:
        info = json.loads(platform.container_command(['volume','inspect',home]))[0]
        labels = info.get('Labels') or {}
        lineage, pair = labels.get('io.altitude.lineage',''), labels.get('io.altitude.pair','')
    if any(not re.fullmatch('[0-9a-f]{32}', value) for value in (lineage,pair)):
        raise ValueError('Unlabeled experimental volumes are not adopted')
    with platform.container_lineage_lock(lineage):
        values = [local_volume(volume,lineage=lineage,pair=pair,role=role,create=create and not any(present))
                  for volume,role in ((home,'home'),(projects,'projects'))]
    restored = [v['Labels'].get('io.altitude.restore','') for v in values]
    if restored[0] != restored[1] or restored[0] and not re.fullmatch('[0-9a-f]{64}',restored[0]):
        raise ValueError('Restore volume identities do not match')
    return {'lineage':lineage,'pair':pair,'archive':restored[0], 'home':home,'projects':projects}


def start(image: str, instance: str, home: str, projects: str, bind: str, public_host: str, port: int,
          *, new_volumes: bool = False) -> str:
    platform.container_runtime()
    name(instance)
    if name(home) == name(projects):
        raise ValueError("Home and projects need separate named volumes")
    address = ipaddress.ip_address(bind)
    private = any(address in ipaddress.ip_network(network) for network in
                  ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))
    if not (address.is_loopback or private):
        raise ValueError("Publish to one loopback or private LAN/VPN address, not every interface or a public address")
    if not 1024 <= port <= 65535:
        raise ValueError("Choose an unprivileged port between 1024 and 65535")
    try:
        public_ip = ipaddress.ip_address(public_host)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}\Z)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", public_host):
            raise ValueError("Name the host/IP devices open over HTTPS, without a URL or port") from None
        public_ip = None
    if public_ip and (public_ip.is_unspecified or public_ip.is_multicast):
        raise ValueError("HTTPS needs a reachable host address")
    if address.is_loopback and not (public_host == "localhost" or public_ip and public_ip.is_loopback):
        raise ValueError("A loopback publication needs a localhost or loopback HTTPS identity")
    if not address.is_loopback and (public_host == "localhost" or public_ip and public_ip != address):
        raise ValueError("The advertised HTTPS address must match the private address published on the host")
    image_info = json.loads(platform.container_command(["image", "inspect", image]))[0]
    if (image_info.get("Labels") or {}).get(LABEL) != "1":
        raise ValueError("Select an Altitude image built from a verified release")
    identity = volume_pair(home, projects, create=new_volumes)
    publish = f"[{address}]" if address.version == 6 else str(address)
    return platform.container_launch(instance, [
        "--name", instance, "--hostname", instance, "--label", f"{LABEL}=1",
        "--label", f"io.altitude.bind={address}",
        '--label', 'io.altitude.lineage='+identity['lineage'], '--label', 'io.altitude.pair='+identity['pair'],
        '--env', 'ALTITUDE_VOLUME_LINEAGE='+identity['lineage'], '--env', 'ALTITUDE_VOLUME_PAIR='+identity['pair'],
        '--env', 'ALTITUDE_RESTORE_SHA='+identity['archive'],
        "--network=slirp4netns", "--cgroupns=private", "--security-opt=unmask=/proc/*",
        "--memory=4g", "--cpus=2", "--pids-limit=1024", "--stop-timeout=30",
        "--volume", f"{home}:/home/altitude:nocopy", "--volume", f"{projects}:/home/altitude/Projects:nocopy",
        "--publish", f"{publish}:{port}:{port}", "--env", f"ALTITUDE_PORT={port}",
        "--env", f"ALTITUDE_PUBLIC_HOST={public_host}", image_info["Id"]])


def owned(instance: str) -> dict:
    return platform.container_owned(name(instance))


def execute(instance: str, command: list[str], *, interactive: bool = False) -> str:
    value = owned(instance)
    return platform.container_command(["exec", *(["-it"] if interactive else []), "--user", "1000:1000",
                                       "--workdir", "/home/altitude",
                                       "--env", "HOME=/home/altitude", "--env", "XDG_RUNTIME_DIR=/run/user/1000",
                                       "--env", "PATH=" + platform.CONTAINER_USER_PATH,
                                       value["Id"], *command], timeout=3600 if interactive else 30, interactive=interactive)


def lifecycle(instance: str, action: str = "status", *, expected: str | None = None) -> dict:
    prefix = "import sys,json; sys.path.insert(0,'/opt/altitude'); from altitude import platform; "
    if action == "continue":
        if not expected or not re.fullmatch(r"[0-9a-f]{32}", expected):
            raise ValueError("Pass --instance with the identity from the status or browser notice you inspected")
        value = owned(instance)
        lineage = value['Config']['Labels'].get('io.altitude.lineage','')
        with platform.container_lineage_lock(lineage):
            platform.container_copy_available(lineage,[m['Name'] for m in value['Mounts'] if m.get('Type')=='volume'],except_id=value['Id'])
            return json.loads(execute(instance, ["python3", "-c", prefix +
                "print(json.dumps(platform.change_container_lifecycle(sys.argv[1],sys.argv[2])))", action, expected]))
    observed = json.loads(execute(instance, ["python3", "-c", prefix + "print(json.dumps(platform.container_lifecycle()))"]))
    if action == "status":
        return observed
    if not observed or not observed.get("instance"):
        raise ValueError("Container identity is unavailable; repair its startup before continuing")
    return json.loads(execute(instance, ["python3", "-c", prefix +
        "print(json.dumps(platform.change_container_lifecycle(sys.argv[1],sys.argv[2])))", action, observed["instance"]]))


def file_identity(path: Path, limit: int) -> dict:
    digest = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('Backup payload must be a regular private file')
        while value := source.read(1024*1024):
            size += len(value)
            if size > limit:
                raise ValueError('Backup payload exceeds its limit')
            digest.update(value)
    return {'sha256':digest.hexdigest(), 'bytes':size}


def instance_pair(value: dict) -> dict:
    mounts = {m['Destination']:m for m in value.get('Mounts',[]) if m.get('Type')=='volume'}
    if set(mounts) != {'/home/altitude','/home/altitude/Projects'}:
        raise ValueError('Backup requires the exact two launcher volumes')
    identity = volume_pair(mounts['/home/altitude']['Name'], mounts['/home/altitude/Projects']['Name'])
    labels = value['Config'].get('Labels') or {}
    if any(labels.get('io.altitude.'+key) != identity[key] for key in ('pair','lineage')):
        raise ValueError('Container and volume identities disagree')
    return identity


def transfer_helper(operation: dict, action: str, source=None, target=None):
    identity = operation['identity']
    descriptor = {key:identity[key] for key in ('lineage','pair')}
    descriptor['archive'] = operation.get('data_sha', '0'*64)
    parent = platform.container_parent(operation['unit'])
    platform.container_command(['create','--name',operation['helper'], '--label','io.altitude.backup='+operation['id'],
        '--cgroup-parent',parent, '--cgroupns=private','--network=none','--read-only','--read-only-tmpfs=false',
        '--security-opt=no-new-privileges','--cap-drop=all','--cap-add=CHOWN,DAC_OVERRIDE,FOWNER',
        '--memory=1g','--cpus=1','--pids-limit=64','--log-driver=none','--systemd=false',
        '--user=0:0','--workdir=/opt/altitude', *(['--interactive'] if action=='restore' else []),
        '--volume',identity['home']+':/backup/home:nocopy', '--volume',identity['projects']+':/backup/projects:nocopy',
        '--entrypoint=/usr/bin/python3', operation['image'], '-I','-B','-c',
        "import sys,json;sys.path.insert(0,'/opt/altitude');from altitude.container_archive import entrypoint;"
        "entrypoint(sys.argv[1],json.loads(sys.argv[2]))", action,json.dumps(descriptor)], timeout=30)
    inspected=json.loads(platform.container_command(['inspect',operation['helper']]))[0]
    if inspected['HostConfig']['LogConfig']['Type']!='none':
        raise RuntimeError('Private archive helper logging is not disabled')
    platform.container_binary(['start','--attach', *(['--interactive'] if action=='restore' else []), operation['helper']],
                              source=source,target=target,seconds=1500)


def cleanup_transfer(record: Path):
    """Exact operation cleanup from ExecStopPost, even when the waiting client died."""
    operation = json.loads(record.read_text())
    deadline=time.monotonic()+40
    def call(arguments, timeout=10):
        remaining=deadline-time.monotonic()
        if remaining<=0: raise RuntimeError('Transfer cleanup timed out; retain its exact operation record')
        return platform.container_command(arguments,timeout=min(remaining,timeout))
    ids = call(['ps','--all','--quiet','--filter','name=^'+operation['helper']+'$']).split()
    if ids:
        values = json.loads(call(['inspect',*ids]))
        if len(values)!=1 or values[0]['Config']['Labels'].get('io.altitude.backup') != operation['id']:
            raise RuntimeError('Backup helper ownership changed; retain operation for inspection')
        call(['rm','--force',values[0]['Id']],timeout=20)
    if operation['action']=='backup':
        directory = Path(operation['directory'])
        if not (directory/'manifest.json').exists():
            for filename in ('data.tar','image.tar','manifest.tmp'):
                (directory/filename).unlink(missing_ok=True)
        shutil.rmtree(record.parent)
    elif not (record.parent/'complete').exists():
        for volume in (operation['identity']['home'],operation['identity']['projects']):
            names = call(['volume','ls','--quiet']).split()
            if volume in names:
                value = json.loads(call(['volume','inspect',volume]))[0]
                if value['Labels'].get('io.altitude.operation') != operation['id']:
                    raise RuntimeError('Restore volume ownership changed; retain it for inspection')
                call(['volume','rm',volume])
        shutil.rmtree(record.parent)
    else:
        shutil.rmtree(record.parent)


def image_archive_identity(path: Path, expected: str):
    """An OCI import cannot repoint application tags: archive only the immutable ID."""
    with tarfile.open(path,'r:') as archive:
        def document(member_name):
            member=archive.getmember(member_name)
            if not member.isreg() or member.size>4*1024**2:
                raise ValueError('Invalid OCI metadata')
            return json.load(archive.extractfile(member))
        index=document('index.json')
        manifests=index.get('manifests',[])
        if len(manifests)!=1: raise ValueError('Backup needs exactly one OCI image')
        descriptor=manifests[0]
        annotation=descriptor.get('annotations',{}).get('org.opencontainers.image.ref.name')
        if annotation not in (None,expected,'sha256:'+expected):
            raise ValueError('Backup OCI archive must not carry a mutable image tag')
        digest=descriptor.get('digest','')
        if not re.fullmatch('sha256:[0-9a-f]{64}',digest): raise ValueError('Invalid OCI digest')
        manifest=document('blobs/sha256/'+digest[7:])
        if manifest.get('config',{}).get('digest') != 'sha256:'+expected:
            raise ValueError('OCI configuration identity differs from the source image')


def transfer_worker(record: Path) -> dict:
    from altitude.container_archive import MAX_BYTES, restore
    operation = json.loads(record.read_text())
    identity = operation['identity']
    directory = Path(operation['directory'])
    with platform.container_lineage_lock(identity['lineage']):
        platform.container_copy_available(identity['lineage'],[identity['home'],identity['projects']])
        if operation['action']=='backup':
            value = owned(operation['instance'])
            if not platform.container_stopped(value) or value['Image'] != operation['image'] or instance_pair_unlocked(value) != identity:
                raise ValueError('The stopped source changed; backup refused')
            with (directory/'data.tar').open('xb') as data:
                os.fchmod(data.fileno(),0o600)
                transfer_helper(operation,'export',target=data)
                data.flush(); os.fsync(data.fileno())
            with (directory/'data.tar').open('rb') as received:
                restore(received)  # helper exit0/EOF is not proof of complete transport (review F1)
            with (directory/'image.tar').open('xb') as image:
                os.fchmod(image.fileno(),0o600)
                # Saving by immutable ID carries no application tag to overwrite on load.
                platform.container_binary(['save','--format=oci-archive',operation['image']],target=image,seconds=120,max_bytes=8*1024**3)
                image.flush(); os.fsync(image.fileno())
            image_archive_identity(directory/'image.tar',operation['image'])
            result = {'format':1, 'lineage':identity['lineage'], 'image':operation['image'],
                      'image_labels':operation['image_labels'],
                      'data':file_identity(directory/'data.tar',MAX_BYTES+64*1024**2),
                      'image_archive':file_identity(directory/'image.tar',8*1024**3)}
            with (directory/'manifest.tmp').open('x') as output:
                os.fchmod(output.fileno(),0o600)
                json.dump(result,output,indent=2); output.flush(); os.fsync(output.fileno())
            os.rename(directory/'manifest.tmp',directory/'manifest.json')
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
            return {'backup':str(directory),'complete':True}
        manifest = operation['manifest']
        verify_backup(directory,manifest)
        image_archive_identity(directory/'image.tar',operation['image'])
        platform.container_binary(['load','--input',str(directory/'image.tar')],seconds=120)
        image = json.loads(platform.container_command(['image','inspect',operation['image']]))[0]
        if image['Id'] != operation['image'] or any((image.get('Labels') or {}).get(k)!=v for k,v in manifest['image_labels'].items()):
            raise ValueError('Restored image differs from the recorded application image')
        for role in ('home','projects'):
            local_volume(identity[role],lineage=identity['lineage'],pair=identity['pair'],role=role,create=True,
                         restored=operation['data_sha'],operation=operation['id'])
        with (directory/'data.tar').open('rb') as data:
            transfer_helper(operation,'restore',source=data)
        with (record.parent/'complete').open('x') as complete:
            complete.write('restored\n'); complete.flush(); os.fsync(complete.fileno())
        fd=os.open(record.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        return {'image':operation['image'],**identity,'complete':True}


def instance_pair_unlocked(value: dict) -> dict:
    """Inspect without recursively acquiring the already held lineage lock."""
    mounts={m['Destination']:m['Name'] for m in value['Mounts'] if m.get('Type')=='volume'}
    labels=value['Config']['Labels']
    identity={'lineage':labels['io.altitude.lineage'],'pair':labels['io.altitude.pair'],
              'home':mounts['/home/altitude'],'projects':mounts['/home/altitude/Projects']}
    values=[local_volume(identity[role],lineage=identity['lineage'],pair=identity['pair'],role=role) for role in ('home','projects')]
    restored=[v['Labels'].get('io.altitude.restore','') for v in values]
    if restored[0]!=restored[1]: raise ValueError('Volume restore identity changed')
    return {**identity,'archive':restored[0]}


def transfer_job(operation: dict) -> dict:
    operation.update(id=uuid.uuid4().hex)
    operation['store']=platform.container_runtime()['store']['graphRoot']
    root=platform.container_transfer_root(create=True)/operation['id']
    root.mkdir(mode=0o700)
    record=root/'operation.json'
    operation['unit']='altitude-container-transfer-'+operation['id']+'.service'
    operation['helper']='altitude-transfer-'+operation['id']
    with record.open('x') as target:
        os.fchmod(target.fileno(),0o600); json.dump(operation,target)
        target.flush(); os.fsync(target.fileno())
    for directory in (root,root.parent):
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    command=[sys.executable,'-c','import sys,json;from pathlib import Path;from scripts.container import transfer_worker;'
             'print(json.dumps(transfer_worker(Path(sys.argv[1]))))',str(record)]
    cleanup=[sys.executable,'-c','import sys;from pathlib import Path;from scripts.container import cleanup_transfer;'
             'cleanup_transfer(Path(sys.argv[1]))',str(record)]
    try:
        return json.loads(platform.container_job(operation['unit'],command,wait=True,after_stop=cleanup,seconds=1800))
    except Exception as original:
        try:
            environment=platform.container_user_environment()
            if platform.job_active(operation['unit'],environment):
                platform.container_stop_unit(operation['unit'],environment)
            if record.exists() and not platform.job_active(operation['unit'],environment):
                cleanup_transfer(record)
        except Exception as cleanup:
            raise RuntimeError(f'Transfer failed ({original}); cleanup also failed ({cleanup}). Retained operation {operation["id"]}; inspect transfers before recovery.') from original
        raise


def recover_transfer(identity: str) -> dict:
    runtime=platform.container_runtime()
    if not re.fullmatch('[0-9a-f]{32}',identity): raise ValueError('Use the exact retained transfer ID')
    found=next((v for v in runtime['transfers'] if v['id']==identity),None)
    if found is None: raise ValueError('No retained operation in this runtime store')
    record=platform.container_transfer_root()/identity/'operation.json'
    operation=json.loads(record.read_text())
    with platform.container_lineage_lock(operation['identity']['lineage']):
        if platform.job_active(operation['unit'],platform.container_user_environment()):
            raise ValueError('Transfer service is still active; recovery refuses')
        cleanup_transfer(record)
    return {'recovered':identity,'record_removed':not record.exists()}


def backup(instance: str, directory: Path) -> dict:
    platform.container_runtime()
    value=owned(instance)
    if not platform.container_stopped(value):
        raise ValueError('Stop the container before a consistent backup')
    identity=instance_pair(value)
    image=json.loads(platform.container_command(['image','inspect',value['Image']]))[0]
    labels={key:(image.get('Labels') or {}).get(key) for key in
            (LABEL,'org.opencontainers.image.revision','io.altitude.archive.sha256')}
    if labels[LABEL]!='1' or not all(labels.values()): raise ValueError('Image release identity is unavailable')
    directory=directory.absolute()
    directory.mkdir(mode=0o700)  # exclusive: never replace an existing backup
    return transfer_job({'action':'backup','directory':str(directory),'instance':instance,
        'identity':identity,'image':value['Image'],'image_labels':labels})


def verify_backup(directory: Path, manifest: dict | None = None) -> dict:
    from altitude.container_archive import MAX_BYTES, restore
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('Use your own private backup directory (mode0700), not a shared/untrusted archive')
    for filename in ('manifest.json','data.tar','image.tar'):
        info=(directory/filename).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1:
            raise ValueError('Backup files must be owned private regular files')
    if manifest is None:
        with (directory/'manifest.json').open() as source:
            manifest=json.loads(source.read(65536))
    if manifest.get('format')!=1 or not re.fullmatch('[0-9a-f]{32}',manifest.get('lineage','')) or not re.fullmatch('[0-9a-f]{64}',manifest.get('image','')):
        raise ValueError('Unsupported backup manifest')
    if (file_identity(directory/'data.tar',MAX_BYTES+64*1024**2)!=manifest['data'] or
            file_identity(directory/'image.tar',8*1024**3)!=manifest['image_archive']):
        raise ValueError('Backup size or checksum differs from the completed manifest')
    with (directory/'data.tar').open('rb') as received:
        restore(received)
    return manifest


def restore_backup(directory: Path, home: str, projects: str) -> dict:
    platform.container_runtime()
    directory=directory.absolute()
    manifest=verify_backup(directory)
    if name(home)==name(projects): raise ValueError('Use separate new restore volumes')
    existing=platform.container_command(['volume','ls','--quiet']).split()
    if home in existing or projects in existing: raise ValueError('Restore requires two new volume names')
    identity={'lineage':manifest['lineage'],'pair':uuid.uuid4().hex,'home':home,'projects':projects,
              'archive':manifest['data']['sha256']}
    return transfer_job({'action':'restore','directory':str(directory),'identity':identity,
        'image':manifest['image'],'manifest':manifest,'data_sha':manifest['data']['sha256']})


def recreate(instance: str) -> str:
    value=owned(instance)
    identity=instance_pair(value)
    environment=dict(item.split('=',1) for item in value['Config']['Env'] if '=' in item)
    bind=value['Config']['Labels']['io.altitude.bind']
    with platform.container_lineage_lock(identity['lineage']):
        platform.container_recreatable(value)
        platform.container_copy_available(identity['lineage'],[identity['home'],identity['projects']],except_id=value['Id'])
        platform.container_command(['rm','--force',value['Id']],timeout=30)
    # The new supervisor rechecks admission under the same lineage lock. If another
    # copy starts meanwhile it refuses; both original data volumes remain intact.
    return start(value['Image'],instance,identity['home'],identity['projects'],bind,
                 environment['ALTITUDE_PUBLIC_HOST'],int(environment['ALTITUDE_PORT']))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    image = sub.add_parser("build", help="verify the existing application release and build a local image")
    image.add_argument("--archive", required=True, type=Path)
    image.add_argument("--sha256", required=True)
    image.add_argument("--tag", required=True)
    run = sub.add_parser("start", help="create a rootless controller with two persistent named volumes")
    run.add_argument("--image", required=True)
    run.add_argument("--name", default="altitude")
    run.add_argument("--home-volume", default="altitude-home")
    run.add_argument("--projects-volume", default="altitude-projects")
    run.add_argument("--bind", default="127.0.0.1")
    run.add_argument("--public-host", default="localhost")
    run.add_argument("--port", type=int, default=8890)
    run.add_argument('--new-volumes', action='store_true', help='explicitly create a fresh empty pair; omit for existing/restored volumes')
    save=sub.add_parser('backup', help='privately archive both stopped volumes and their exact image')
    save.add_argument('--name',default='altitude')
    save.add_argument('--output',type=Path,required=True)
    restore=sub.add_parser('restore', help='restore your trusted private backup into two new volumes, ready and paused')
    restore.add_argument('--backup',type=Path,required=True)
    restore.add_argument('--name',required=True)
    restore.add_argument('--home-volume',required=True)
    restore.add_argument('--projects-volume',required=True)
    restore.add_argument('--bind',default='127.0.0.1')
    restore.add_argument('--public-host',default='localhost')
    restore.add_argument('--port',type=int,default=8890)
    sub.add_parser("preflight", help="check local rootless-runtime prerequisites without starting containers")
    sub.add_parser('transfers',help='inspect active or unfinished private backup/restore records')
    recover=sub.add_parser('recover-transfer',help='clean one inactive interrupted transfer, preserving completed backups')
    recover.add_argument('--id',required=True)
    for action in ("status", "pause", "continue", "stop", "restart", "recreate", "remove", "pair", "shell", "certificate"):
        command = sub.add_parser(action)
        command.add_argument("--name", default="altitude")
        if action == "continue":
            command.add_argument("--instance", required=True, help="instance identity shown in the status you inspected")
        if action == "certificate":
            command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "build":
            print(json.dumps(build(args.archive, args.sha256, args.tag), indent=2))
        elif args.action == "preflight":
            print(json.dumps(platform.container_runtime(), indent=2))
        elif args.action=='transfers':
            print(json.dumps(platform.container_runtime()['transfers'],indent=2))
        elif args.action=='recover-transfer':
            print(json.dumps(recover_transfer(args.id),indent=2))
        elif args.action == "start":
            print(start(args.image, args.name, args.home_volume, args.projects_volume, args.bind, args.public_host, args.port,new_volumes=args.new_volumes))
        elif args.action=='backup':
            print(json.dumps(backup(args.name,args.output),indent=2))
        elif args.action=='restore':
            restored=restore_backup(args.backup,args.home_volume,args.projects_volume)
            print(start(restored['image'],args.name,args.home_volume,args.projects_volume,args.bind,args.public_host,args.port))
        elif args.action == "status":
            state = owned(args.name)["State"]
            print(json.dumps({"container": state, "lifecycle": lifecycle(args.name) if state["Running"] else None}, indent=2))
        elif args.action in ("pause", "continue"):
            print(json.dumps(lifecycle(args.name, args.action, expected=getattr(args, "instance", None)), indent=2))
            if args.action == "continue":
                print("Queued coordinator work and authorized task requests may now run; existing holds still apply.")
        elif args.action == "restart":
            platform.container_stop(args.name)
            print(platform.container_launch(args.name))
        elif args.action=='recreate':
            print(recreate(args.name))
        elif args.action in ("stop", "remove"):
            value = owned(args.name)
            if args.action == "remove" and value["State"]["Running"]:
                raise ValueError("Stop this container before removal; both data volumes are retained")
            if args.action == "stop":
                platform.container_stop(args.name)
            else:
                print(platform.container_command(["rm", value["Id"]], timeout=60))
        elif args.action == "certificate":
            facts = execute(args.name, ["python3", "-c", "import sys,json; sys.path.insert(0,'/opt/altitude'); "
                                       "from altitude import tls; print(json.dumps(tls.info(),indent=2))"])
            certificate = execute(args.name, ["cat", "/home/altitude/.config/altitude/tls/ca.crt"])
            with args.output.open("x") as target:
                target.write(certificate)
            print(facts)
            print(f"Public CA saved to {args.output}. Compare its fingerprint before trusting it on a device.")
        else:
            print(execute(args.name, ["/bin/bash", "--noprofile", "--norc"] if args.action == "shell" else ["alt", "pair"],
                          interactive=args.action == "shell"))
    except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Container operation refused: {exc}\n")


if __name__ == "__main__":
    main()
