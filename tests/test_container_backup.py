"""Backup admission and private transport: real files/locks, fictional runtime seam."""
import io
import json
import os
import sys
from pathlib import Path
import tarfile
from unittest import mock

from altitude import platform, container_archive
from scripts import container
from tests.support import AltitudeCase, container_namespace_metadata


class BackupTests(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(platform,'container_user_environment',return_value={'XDG_RUNTIME_DIR':str(self.tmp)})
        self.enterContext(mock.patch.dict(os.environ,{'XDG_DATA_HOME':str(self.tmp/'data')}))

    def test_unfinished_transfer_is_persistent_discoverable_and_explicitly_recoverable(self):
        store=str(self.tmp/'store')
        self.patch(platform,'container_runtime',side_effect=lambda:{'store':{'graphRoot':store},
            'transfers':platform.container_transfer_records(store)})
        self.patch(platform,'container_job',side_effect=RuntimeError('original interruption'))
        self.patch(platform,'job_active',return_value=False)
        output=self.tmp/'backup'; output.mkdir(mode=0o700)
        (output/'data.tar').write_bytes(b'private partial')
        op={'action':'backup','directory':str(output),'identity':{'lineage':'1'*32,'home':'h','projects':'p'}}
        with mock.patch.object(container,'cleanup_transfer',side_effect=RuntimeError('cleanup unavailable')):
            with self.assertRaisesRegex(RuntimeError,'original interruption.*cleanup unavailable'):
                container.transfer_job(op)
        rows=platform.container_transfer_records(store)
        self.assertEqual([row['id'] for row in rows],[op['id']])
        self.assertEqual(platform.container_transfer_records('unrelated store'),[])
        record=platform.container_transfer_root()/op['id']/'operation.json'
        self.assertEqual(record.stat().st_mode&0o777,0o600)
        self.assertEqual(record.parent.stat().st_mode&0o777,0o700)
        self.patch(platform,'container_command',return_value='')
        self.assertTrue(container.recover_transfer(op['id'])['record_removed'])
        self.assertFalse((output/'data.tar').exists())
        self.assertEqual(platform.container_transfer_records(store),[])

    def test_explicit_transfer_recovery_refuses_an_active_unit(self):
        self.patch(platform,'container_runtime',return_value={'transfers':[{'id':'a'*32}]})
        root=platform.container_transfer_root(create=True)/('a'*32); root.mkdir(mode=0o700)
        (root/'operation.json').write_text(json.dumps({'identity':{'lineage':'b'*32},'unit':'owned.service'}))
        self.patch(platform,'job_active',return_value=True)
        cleanup=self.patch(container,'cleanup_transfer')
        with self.assertRaisesRegex(ValueError,'still active'): container.recover_transfer('a'*32)
        cleanup.assert_not_called()

    def test_same_lineage_lock_is_exclusive_and_unlink_not_used(self):
        with platform.container_lineage_lock('1'*32):
            with self.assertRaisesRegex(RuntimeError,'Another operation'):
                with platform.container_lineage_lock('1'*32): self.fail()
            with platform.container_lineage_lock('2'*32): pass
        with platform.container_lineage_lock('1'*32): pass
        self.assertTrue((self.tmp/'altitude-container-locks'/('1'*32)).is_file())

    def test_unlabeled_running_mount_user_prevents_backup(self):
        values=[{'Id':'other','Config':{'Labels':{}},'Mounts':[{'Type':'volume','Name':'home'}],'State':{'Running':True}}]
        self.patch(platform,'container_command',side_effect=['other',json.dumps(values)])
        with self.assertRaisesRegex(RuntimeError,'Stop the other'):
            platform.container_copy_available('1'*32,['home','projects'])

    def test_same_lineage_other_pair_and_pending_unit_prevent_admission(self):
        for state in (True,False):
            values=[{'Id':'other','Config':{'Labels':{'io.altitude.lineage':'1'*32,'io.altitude.unit':'altitude-container-other.service'}},
                     'Mounts':[{'Type':'volume','Name':'different'}], 'State':{'Running':state}}]
            with mock.patch.object(platform,'container_command',side_effect=['other',json.dumps(values)]), \
                 mock.patch.object(platform,'job_active',return_value=True):
                with self.assertRaisesRegex(RuntimeError,'Stop the other'):
                    platform.container_copy_available('1'*32,['home','projects'])

    def test_existing_copy_is_excluded_only_by_exact_id(self):
        values=[{'Id':'self','Config':{'Labels':{'io.altitude.lineage':'1'*32}},'State':{'Running':True}}]
        self.patch(platform,'container_command',side_effect=['self',json.dumps(values)])
        platform.container_copy_available('1'*32,['home','projects'],except_id='self')

    def test_binary_transfer_never_uses_text_capture_or_journal(self):
        self.patch(platform,'container_arguments',return_value=[sys.executable,'-c',
            "import os;os.write(1,b'private\\x00bytes');os.write(2,b'not retained')"])
        target=io.BytesIO()
        platform.container_binary(['fixture'],target=target,seconds=5)
        self.assertEqual(target.getvalue(),b'private\x00bytes')

    def test_output_limit_does_not_limit_runtime_store_files(self):
        unrelated=self.tmp/'runtime-store'
        self.patch(platform,'container_arguments',return_value=[sys.executable,'-c',
            "import os,sys;open(sys.argv[1],'wb').write(b'x'*8192);os.write(1,b'too large')",str(unrelated)])
        target=io.BytesIO()
        with self.assertRaisesRegex(RuntimeError,'byte limit'):
            platform.container_binary(['fixture'],target=target,seconds=5,max_bytes=4)
        self.assertEqual(unrelated.stat().st_size,8192)
        self.assertLessEqual(len(target.getvalue()),4)

    def test_stopped_backup_refuses_before_output_directory_creation(self):
        self.patch(platform,'container_runtime',return_value={})
        self.patch(container,'owned',return_value={'State':{'Running':True}})
        path=self.tmp/'backup'
        with self.assertRaisesRegex(ValueError,'Stop the container'): container.backup('fixture',path)
        self.assertFalse(path.exists())

    def test_missing_volume_pair_is_never_silently_created(self):
        command=self.patch(platform,'container_command',return_value='[]')
        with self.assertRaisesRegex(ValueError,'required'): container.volume_pair('home','projects')
        self.assertEqual(command.call_args_list,[mock.call(['volume','ls','--format','json'])])

    def test_cleanup_refuses_someone_elses_helper(self):
        record=self.tmp/'operation.json'
        record.write_text(json.dumps({'id':'ours','helper':'fixture'}))
        command=self.patch(platform,'container_command',side_effect=['other',json.dumps([{
            'Id':'other','Config':{'Labels':{'io.altitude.backup':'unrelated'}}}])])
        with self.assertRaisesRegex(RuntimeError,'ownership changed'): container.cleanup_transfer(record)
        self.assertFalse(any(c.args[0][0]=='rm' for c in command.call_args_list))
        self.assertTrue(record.exists())

    def test_backup_permissions_checksum_and_completion_are_required(self):
        directory=self.tmp/'backup'; directory.mkdir(mode=0o700)
        for filename in ('data.tar','image.tar'):
            path=directory/filename; path.write_bytes(b'fixture'); path.chmod(0o600)
        home,projects=self.tmp/'home',self.tmp/'projects'; home.mkdir(); projects.mkdir()
        with container_namespace_metadata(self.tmp), (directory/'data.tar').open('wb') as target:
            container_archive.export(target,home,projects)
        manifest={'format':1,'lineage':'1'*32,'image':'2'*64,
                  'data':container.file_identity(directory/'data.tar',100000),
                  'image_archive':container.file_identity(directory/'image.tar',100)}
        (directory/'manifest.json').write_text(json.dumps(manifest)); (directory/'manifest.json').chmod(0o600)
        self.assertEqual(container.verify_backup(directory),manifest)
        (directory/'data.tar').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'): container.verify_backup(directory)
        (directory/'data.tar').chmod(0o644)
        with self.assertRaisesRegex(ValueError,'private regular'): container.verify_backup(directory)

    def test_disappeared_unrelated_container_does_not_refuse_admission(self):
        self.patch(platform,'container_command',side_effect=['unrelated',RuntimeError('missing'),''])
        platform.container_copy_available('1'*32,['home','projects'])

    def test_inspection_failure_of_existing_container_still_refuses(self):
        self.patch(platform,'container_command',side_effect=['unrelated',RuntimeError('unavailable'),'unrelated'])
        with self.assertRaisesRegex(RuntimeError,'unavailable'):
            platform.container_copy_available('1'*32,['home','projects'])

    def test_helper_reason_codes_do_not_expose_file_content(self):
        for error,code in ((OSError('private'),70),(ValueError('private'),72),
                           (RuntimeError('Another Altitude container owns this volume: private'),71)):
            with mock.patch.object(container_archive,'helper',side_effect=error):
                with self.assertRaises(SystemExit) as raised: container_archive.entrypoint('export',{})
                self.assertEqual(raised.exception.code,code)

    def test_zero_exit_truncated_transport_cannot_publish_manifest(self):
        directory=self.tmp/'backup'; directory.mkdir(mode=0o700)
        identity={'lineage':'1'*32,'pair':'2'*32,'home':'h','projects':'p','archive':''}
        operation={'action':'backup','identity':identity,'directory':str(directory),'instance':'fixture','image':'3'*64}
        record=directory/'operation.json'; record.write_text(json.dumps(operation))
        self.patch(platform,'container_copy_available')
        self.patch(container,'owned',return_value={'State':{'Running':False,'Status':'exited','Pid':0},'Image':'3'*64})
        self.patch(container,'instance_pair_unlocked',return_value=identity)
        self.patch(container,'transfer_helper',side_effect=lambda *args,**kw:kw['target'].write(b'truncated'))
        with self.assertRaises(tarfile.TarError): container.transfer_worker(record)
        self.assertFalse((directory/'manifest.json').exists())

    def test_image_archive_cannot_repoint_existing_tag(self):
        def archive(tag):
            path=self.tmp/'image.tar'
            with tarfile.open(path,'w') as target:
                for name,data in [('index.json',{'manifests':[{'digest':'sha256:'+'3'*64,
                    'annotations':{'org.opencontainers.image.ref.name':tag}}]}),
                    ('blobs/sha256/'+'3'*64,{'config':{'digest':'sha256:'+'2'*64}})]:
                    encoded=json.dumps(data).encode(); member=tarfile.TarInfo(name); member.size=len(encoded)
                    target.addfile(member,io.BytesIO(encoded))
            return path
        container.image_archive_identity(archive('2'*64),'2'*64)
        with self.assertRaisesRegex(ValueError,'mutable image tag'):
            container.image_archive_identity(archive('localhost/altitude:current'),'2'*64)

    def test_bootstrap_refuses_partial_or_wrong_restore_before_chown(self):
        self.enterContext(container_namespace_metadata(self.tmp))
        home=self.tmp/'home'; projects=home/'Projects'; projects.mkdir(parents=True)
        proc=self.tmp/'proc'; (proc/'self').mkdir(parents=True)
        (proc/'self/mountinfo').write_text(f'1 0 8:2 / {home} rw - ext4 /dev/f rw\n2 0 8:2 / {projects} rw - ext4 /dev/f rw\n')
        self.patch(platform,'PROC',proc); self.patch(platform,'CONTAINER_PROJECTS',projects)
        self.patch(platform,'containerized',return_value=True); self.patch(os,'getuid',return_value=0)
        chown=self.patch(os,'chown')
        with mock.patch.dict(os.environ,{'ALTITUDE_RESTORE_SHA':'3'*64,'ALTITUDE_VOLUME_LINEAGE':'1'*32,'ALTITUDE_VOLUME_PAIR':'2'*32}):
            with self.assertRaises(FileNotFoundError): platform.container_bootstrap()
            (home/'.altitude-restore.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'completion'): platform.container_bootstrap()
        chown.assert_not_called()
