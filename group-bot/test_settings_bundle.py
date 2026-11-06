import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from console_config import atomic,Config,runtime,read_json
from settings_bundle import export_bundle,preview,apply_bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200],mcp_env_file='napcat.env',webui_key='my-local-key')
        atomic(self.root/'settings.json',cfg)
        (self.root/'napcat.env').write_text('NAPCAT_HOST=http://localhost:3000\nNAPCAT_TOKEN=secret\nUNRELATED=value\n',encoding='utf-8')
        (self.root/'persona.txt').write_text('全局人设',encoding='utf-8');(self.root/'reply_protocol.txt').write_text('回复规则',encoding='utf-8')
        scope=self.root/'group-config/200';scope.mkdir(parents=True)
        atomic(scope/'settings.json',{'bot_name':'本群昵称','enabled':False})
        (scope/'persona.txt').write_text('本群人设',encoding='utf-8')
        folder=runtime(self.root,200);folder.mkdir(exist_ok=True)
        atomic(folder/'member-labels.json',{'members':{'12345':{'manual_label':'human','aliases':['昵称B']}}})
        self.model=patch('console_config.validate_model');self.model.start();self.addCleanup(self.model.stop)
        self.tasks=patch('windows_image_cleanup.sync_tasks');self.task_mock=self.tasks.start();self.addCleanup(self.tasks.stop)

    def test_round_trip_all_settings_and_inherited_prompts(self):
        bundle=export_bundle(self.root)
        self.assertEqual(bundle['connection'],{})
        self.assertNotIn('UNRELATED',bundle['connection'])
        self.assertEqual(bundle['groups']['200']['prompts']['persona'],'本群人设')
        self.assertIsNone(bundle['groups']['200']['prompts']['reply_protocol'])
        bundle['settings']['bot_name']='导入昵称';bundle['settings']['chat_group_whitelist']=[200]
        bundle['settings']['codex_exe']='untrusted.exe';bundle['settings']['mcp_env_file']='../../outside.env'
        local=Config(self.root).load();summary=preview(self.root,bundle)
        self.assertEqual(Config(self.root).load()['bot_name'],local['bot_name'])
        result=apply_bundle(self.root,bundle,summary['expected'])
        self.assertTrue(Path(result['backup'],'manifest.json').exists())
        self.assertEqual(json.loads(Path(result['backup'],'previous-settings.json').read_text(encoding='utf-8'))['format'],'qq-group-bot-settings')
        self.assertEqual(Config(self.root).load()['bot_name'],'导入昵称')
        self.assertEqual(Config(self.root).load()['codex_exe'],local['codex_exe'])
        self.assertEqual(Config(self.root).load()['mcp_env_file'],'napcat.env')
        self.assertEqual(Config(self.root,200).load()['enabled'],False)
        self.assertFalse((self.root/'group-config/200/reply_protocol.txt').exists())
        self.assertEqual(read_json(runtime(self.root,200)/'member-labels.json')['members']['12345']['aliases'],['昵称B'])

    def test_shared_marks_and_connection_fields_follow_import_connection_switch(self):
        (self.root/'runtime-shared').mkdir()
        atomic(self.root/'runtime-shared/member-labels.json',{'members':{'12345':{'manual_label':'owner','aliases':['name']}}})
        bundle=export_bundle(self.root)
        self.assertEqual(bundle['global_members']['12345']['manual_label'],'owner')
        bundle['settings']['nonebot_bridge_url']='http://new.test:8080'
        summary=preview(self.root,bundle)
        apply_bundle(self.root,bundle,summary['expected'])
        self.assertEqual(Config(self.root).load()['nonebot_bridge_url'],'http://127.0.0.1:8080')
        summary=preview(self.root,bundle,True)
        apply_bundle(self.root,bundle,summary['expected'],True)
        self.assertEqual(Config(self.root).load()['nonebot_bridge_url'],'http://new.test:8080')

    def test_per_account_global_scope_survives_settings_transfer(self):
        from member_labels import MemberLabels
        marks=MemberLabels(runtime(self.root,100))
        marks.mark('12345','owner',blocked=False,scope='global')
        bundle=export_bundle(self.root)
        self.assertEqual(bundle['member_scopes'],{'12345':'global'})
        atomic(self.root/'runtime-shared/member-labels.json',{'members':{},'scopes':{}})
        summary=preview(self.root,bundle);apply_bundle(self.root,bundle,summary['expected'])
        self.assertEqual(MemberLabels(runtime(self.root,200)).facts()['12345']['manual_label'],'owner')
        self.assertEqual(MemberLabels(self.root/'runtime-private').facts()['12345']['mark_scope'],'global')


    def test_connection_requires_explicit_option_and_preserves_other_env(self):
        bundle=export_bundle(self.root);bundle['settings']['nonebot_access_token']='replacement'
        apply_bundle(self.root,bundle,preview(self.root,bundle)['expected'])
        self.assertIn('secret',(self.root/'napcat.env').read_text())
        self.assertNotEqual(Config(self.root).load()['nonebot_access_token'],'replacement')
        apply_bundle(self.root,bundle,preview(self.root,bundle,True)['expected'],True)
        self.assertEqual(Config(self.root).load()['nonebot_access_token'],'replacement')
        content=(self.root/'napcat.env').read_text();self.assertIn('secret',content);self.assertIn('UNRELATED=value',content)

    def test_invalid_package_is_rejected_without_writes(self):
        bundle=export_bundle(self.root);before=(self.root/'settings.json').read_bytes()
        for change in ('version','path','whitelist','unknown','group_global'):
            value=copy.deepcopy(bundle)
            if change=='version':value['version']=99
            if change=='path':value['groups']['../../escape']=value['groups'].pop('200')
            if change=='whitelist':value['settings']['chat_group_whitelist']=[]
            if change=='unknown':value['settings']['invalid_field']=True
            if change=='group_global':value['groups']['200']['settings']['webui_key']='bad'
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):preview(self.root,value)
        self.assertEqual((self.root/'settings.json').read_bytes(),before)

    def test_changed_settings_require_new_preview(self):
        bundle=export_bundle(self.root);summary=preview(self.root,bundle)
        atomic(self.root/'settings.json',{**Config(self.root).load(),'bot_name':'另一页面已修改'})
        with self.assertRaises(ValueError):apply_bundle(self.root,bundle,summary['expected'])

    def test_partial_failure_rolls_back_all_files(self):
        bundle=export_bundle(self.root);bundle['prompts']['persona']='新内容'
        before={path:path.read_bytes() for path in [self.root/'settings.json',self.root/'persona.txt']}
        self.task_mock.side_effect=RuntimeError('schedule failed')
        with self.assertRaises(RuntimeError):apply_bundle(self.root,bundle,preview(self.root,bundle)['expected'])
        for path,content in before.items():self.assertEqual(path.read_bytes(),content)


if __name__=='__main__':unittest.main()
