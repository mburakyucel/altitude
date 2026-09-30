"""#543: the exposure gate must refuse a newly readable or writable kernel interface."""
import json
from pathlib import Path
import unittest

from tests.container_proc_probe import differences


class ProcPolicy(unittest.TestCase):
    def test_approved_predicates_and_unexpected_exposure(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        for uid in (0,1000):
            with self.subTest(uid=uid):
                rows=[{'path':'/proc/sys/kernel/ns_last_pid','readable':True,'writable':True},
                      {'path':'/proc/kcore','readable':False,'writable':False}]
                self.assertEqual(differences({'uid':uid,'rows':rows},policy),[])
                rows[1]['readable']=True
                self.assertEqual(differences({'uid':uid,'rows':rows},policy),['/proc/kcore:readable'])
                rows.append({'path':'/proc/sys/new-interface','readable':True,'writable':True})
                self.assertIn('/proc/sys/new-interface:writable',differences({'uid':uid,'rows':rows},policy))
        self.assertTrue(differences({'uid':1001,'rows':[]},policy))

    def test_root_only_predicate_is_not_implicitly_available_to_application(self):
        policy=json.loads((Path(__file__).parent/'fixtures/container-proc-policy.json').read_text())
        row={'path':'/proc/sys/kernel/cad_pid','readable':True,'writable':True}
        self.assertEqual(differences({'uid':0,'rows':[row]},policy),[])
        self.assertEqual(len(differences({'uid':1000,'rows':[row]},policy)),2)
