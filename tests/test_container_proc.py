"""#543: the exposure gate must refuse a newly readable or writable kernel interface."""
import json
from pathlib import Path
import unittest

from tests.container_proc_probe import differences, require_tuple


class ProcPolicy(unittest.TestCase):
    def test_measured_descendants_are_exact_read_only_predicates_for_both_principals(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        paths=[p for p in policy['readable'] if p.startswith(('/proc/acpi/','/proc/scsi/'))]
        self.assertEqual(len(paths),11)
        for uid in (0,1000):
            baseline={'uid':uid,'rows':[]}
            for path in paths:
                row={'path':path,'readable':True,'writable':False}
                self.assertEqual(differences({'uid':uid,'rows':[row]},policy,baseline),[])
                row['writable']=True
                self.assertEqual(differences({'uid':uid,'rows':[row]},policy,baseline),[path+':writable'])
            for path in ('/proc/acpi/other','/proc/scsi/sg/new','/proc/scsi/sg/devices/child'):
                self.assertEqual(differences({'uid':uid,'rows':[{'path':path,'readable':True}]},policy,baseline),
                                 [path+':readable'])

    def test_another_runtime_or_architecture_cannot_inherit_the_measured_allowlist(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        value=policy['tuple']
        info={'host':{'distribution':{'distribution':value['distribution'],'version':value['release']},
            'arch':value['architecture'],'kernel':value['kernel'],
            'ociRuntime':{'version':value['crun']+'\nfixture metadata'}},'version':{'Version':value['podman']}}
        self.assertEqual(require_tuple(info,value['network'],policy),value)
        info['host']['arch']='arm64'
        with self.assertRaisesRegex(RuntimeError,'Unreviewed'):require_tuple(info,value['network'],policy)
        info['host']['arch']=value['architecture'];info['host']['kernel']='another-kernel'
        with self.assertRaisesRegex(RuntimeError,'Unreviewed'):require_tuple(info,value['network'],policy)

    def test_unknown_default_mask_and_new_subtree_access_fail_closed(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        row={'path':'/proc/irq/fixture','mode':'-rw-r--r--','readable':True,'writable':False}
        baseline={'uid':0,'rows':[row], 'mounts':[{'path':'/proc/unknown-masked'}]}
        current={'uid':0,'rows':[row]}
        self.assertEqual(differences(current,policy,baseline),
                         ['/proc/unknown-masked:uninventoried default protection'])
        baseline['mounts']=[]
        current['rows']=[dict(row,writable=True)]
        self.assertEqual(differences(current,policy,baseline),['/proc/irq/fixture:writable'])
        baseline['rows']=[]
        self.assertIn('/proc/irq/fixture:readable',differences(current,policy,baseline))

    def test_approved_predicates_and_unexpected_exposure(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        for uid in (0,1000):
            with self.subTest(uid=uid):
                rows=[{'path':'/proc/sys/kernel/ns_last_pid','mode':'-rw-rw-rw-','readable':True,'writable':True},
                      {'path':'/proc/kcore','readable':False,'writable':False}]
                baseline={'uid':uid,'rows':[dict(rows[0],writable=False)]}
                self.assertEqual(differences({'uid':uid,'rows':rows},policy,baseline),[])
                rows[1]['readable']=True
                self.assertEqual(differences({'uid':uid,'rows':rows},policy,baseline),['/proc/kcore:readable'])
                rows.append({'path':'/proc/sys/new-interface','readable':True,'writable':True})
                self.assertIn('/proc/sys/new-interface:writable',differences({'uid':uid,'rows':rows},policy))
        self.assertTrue(differences({'uid':1001,'rows':[]},policy))

    def test_root_only_predicate_is_not_implicitly_available_to_application(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        row={'path':'/proc/sys/kernel/cad_pid','mode':'-rw-------','readable':True,'writable':True}
        self.assertEqual(differences({'uid':0,'rows':[row]},policy,{'uid':0,'rows':[dict(row,writable=False)]}),[])
        self.assertEqual(len(differences({'uid':1000,'rows':[row]},policy)),2)

    def test_default_read_only_tunable_is_not_new_exposure_but_masks_and_writes_still_refuse(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        rows=[{'path':'/proc/sys/net/example','mode':'-rw-r--r--','readable':True,'writable':False},
              {'path':'/proc/kcore','mode':'crw-rw-rw-','readable':True,'writable':True}]
        baseline={'uid':0,'rows':rows}
        current={'uid':0,'rows':[rows[0],dict(rows[1],mode='-r--------',writable=False)]}
        self.assertEqual(differences(current,policy,baseline),['/proc/kcore:readable'])
        current['rows'][0]=dict(rows[0],writable=True)
        self.assertIn('/proc/sys/net/example:writable',differences(current,policy,baseline))
