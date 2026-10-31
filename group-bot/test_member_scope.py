from pathlib import Path
import json
import tempfile
import unittest
from console_config import atomic,runtime,Config
from member_labels import MemberLabels,view
from image_archive import locked


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        atomic(self.root/'settings.json',{**cfg,'group_id':100,'chat_group_whitelist':[100,200],'member_labels_global_enabled':False})
        self.first=MemberLabels(runtime(self.root,100));self.second=MemberLabels(runtime(self.root,200));self.private=MemberLabels(self.root/'runtime-private')

    def test_explicit_global_block_works_with_default_switch_off(self):
        self.first.mark('22222','human',scope='group')
        result=self.first.mark('11111','bot','note',['alias'],True,scope='global')
        self.assertEqual(result['mark_scope'],'global')
        for store in (self.first,self.second,self.private):
            row=store.facts()['11111'];self.assertTrue(row['blocked']);self.assertEqual(row['aliases'],['alias']);self.assertEqual(row['label'],'bot')
        self.assertNotIn('22222',self.second.facts())
        self.assertFalse(Config(self.root).load()['member_labels_global_enabled'])
        self.assertTrue(view(self.root,100)['scoped_marks'])

    def test_switch_to_local_does_not_change_another_accounts_scope(self):
        self.first.mark('11111','bot',blocked=True,scope='global')
        self.first.mark('22222','AI',blocked=True,scope='global')
        self.first.mark('11111','bot',blocked=True,scope='group')
        self.assertTrue(self.first.facts()['11111']['blocked'])
        self.assertEqual(self.first.facts()['11111']['mark_scope'],'group')
        self.assertNotIn('11111',self.second.facts());self.assertNotIn('11111',self.private.facts())
        self.assertTrue(self.second.facts()['22222']['blocked'])
        self.assertEqual(self.private.facts()['22222']['mark_scope'],'global')

    def test_local_accounts_remain_independent_when_legacy_default_is_global(self):
        cfg=Config(self.root).load();atomic(self.root/'settings.json',{**cfg,'member_labels_global_enabled':True})
        self.first.mark('11111','bot',scope='group')
        self.assertNotIn('11111',self.second.facts())
        self.second.mark('11111','human',scope='group')
        self.assertEqual(self.first.facts()['11111']['label'],'bot');self.assertEqual(self.second.facts()['11111']['label'],'human')
        self.first.mark('22222','AI')
        self.assertEqual(self.second.facts()['22222']['label'],'AI')

    def test_model_writes_follow_account_scope_and_keep_other_profiles(self):
        self.first.mark('11111','human',scope='group')
        self.first.mark('22222','human',scope='global')
        with locked(self.first.lock_path):
            data=self.first.read();data['members']['11111']['model_note']='local note';self.first.save(data)
        with locked(self.second.lock_path):
            data=self.second.read();data['members']['22222']['model_note']='shared note';self.second.save(data)
        self.assertNotIn('11111',self.second.facts())
        self.assertEqual(self.first.facts()['11111']['note'],'local note')
        self.assertEqual(self.private.facts()['22222']['note'],'shared note')

    def test_private_scope_and_global_scope_can_be_selected_per_account(self):
        self.private.mark('11111','bot',blocked=True,scope='private')
        self.assertNotIn('11111',self.first.facts())
        self.private.mark('11111','bot',blocked=True,scope='global')
        self.assertTrue(self.second.facts()['11111']['blocked'])
        self.assertEqual(self.private.facts()['11111']['mark_scope'],'global')

    def test_clearing_global_type_keeps_explicit_block_and_scope(self):
        self.first.mark('11111','owner',blocked=True,scope='global')
        self.first.mark('11111','clear',scope='global')
        row=self.second.facts()['11111'];self.assertEqual(row['label'],'');self.assertTrue(row['blocked']);self.assertEqual(row['mark_scope'],'global')

    def test_invalid_scope_rejected_without_writing_profile(self):
        with self.assertRaises(ValueError):self.first.mark('11111','bot',scope='all')
        self.assertFalse(self.first.facts())
