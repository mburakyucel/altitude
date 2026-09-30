"""Private build cleanup cannot delete or migrate the operator's shared store (#543)."""
import json
from pathlib import Path
import subprocess
from unittest import mock

from tests.support import AltitudeCase
from altitude import platform
from scripts import container


class ContainerBuildTests(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'private-build'
        self.root.mkdir(mode=0o700)

    def test_cleanup_refuses_misdirected_store_before_mutation(self):
        command = self.patch(container, 'build_command', return_value=json.dumps(
            {'store': {'graphRoot':'/unrelated','runRoot':str(self.root/'run')}}))
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            container.cleanup_build(self.root)
        command.assert_called_once_with(self.root, ['info','--format=json'])
        self.assertTrue(self.root.exists())

    def test_cleanup_includes_external_build_containers_and_migration_is_last(self):
        calls=[]
        def command(root,args,**kwargs):
            self.assertEqual(root,self.root)
            calls.append(args)
            if args[0]=='info':
                return json.dumps({'store':{'graphRoot':str(root/'store'),'runRoot':str(root/'run')}})
            if args[0]=='ps':
                return 'fictional-build-id\n' if sum(c[0]=='ps' for c in calls)==1 else ''
            return ''
        self.patch(container,'build_command',side_effect=command)
        container.cleanup_build(self.root)
        self.assertIn(['rm','--force','fictional-build-id'],calls)
        self.assertEqual(calls[-1],['system','migrate'])
        self.assertFalse(self.root.exists())

    def test_cleanup_failure_retains_exact_private_artifacts(self):
        self.patch(container,'build_command',side_effect=RuntimeError('fixture runtime refused'))
        with self.assertRaisesRegex(RuntimeError,'refused'):
            container.cleanup_build(self.root)
        self.assertTrue(self.root.exists())

    def test_failed_wait_stops_exact_build_then_cleans_and_commands_compile(self):
        self.patch(platform,'container_runtime',return_value={})
        self.patch(container.tempfile,'mkdtemp',return_value=str(self.root))
        def job(unit,args,**kwargs):
            compile(args[2],'<builder>','exec')
            compile(kwargs['after_stop'][2],'<cleanup>','exec')
            raise subprocess.TimeoutExpired('fixture wait',660)
        self.patch(platform,'container_job',side_effect=job)
        self.patch(platform,'container_user_environment',return_value={})
        self.patch(platform,'job_active',side_effect=[True,False])
        stop=self.patch(container.subprocess,'run',return_value=subprocess.CompletedProcess([],0))
        clean=self.patch(container,'cleanup_build')
        with self.assertRaises(subprocess.TimeoutExpired):
            container.build(Path('/fictional.tar'),'a'*64,'localhost/altitude:fixture')
        self.assertEqual(stop.call_args.args[0][:3],['systemctl','--user','stop'])
        clean.assert_called_once_with(self.root)


if __name__=='__main__':
    import unittest
    unittest.main()
