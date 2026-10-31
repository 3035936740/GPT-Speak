import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from console_config import Config,atomic
from knowledge_base import KnowledgeBase,checked,terms


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=KnowledgeBase(self.root)
        cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(self.root/'settings.json',cfg)

    def save(self,title,content,group=100,scope='group',**kwargs):
        return self.store.save(group,dict(title=title,content=content,scope=scope,**kwargs))

    def test_scoped_retrieval_disable_and_no_unrelated_results(self):
        self.save('果园灌溉','番茄种植需要适当浇水，番茄幼苗避免积水。')
        self.save('共享教程','番茄移栽后应注意保温。',scope='shared')
        self.save('二群秘方','番茄秘方不能泄漏给一群。',group=200)
        self.save('停用资料','番茄停用资料。',enabled=False)
        rows=self.store.retrieve(100,'番茄种植如何浇水？')
        titles={row['title'] for row in rows}
        self.assertIn('果园灌溉',titles);self.assertIn('共享教程',titles)
        self.assertNotIn('二群秘方',titles);self.assertNotIn('停用资料',titles)
        self.assertEqual(self.store.retrieve(100,'quantum entanglement'),[])
        self.assertEqual({row['title'] for row in self.store.retrieve(200,'番茄')},{'共享教程','二群秘方'})

    def test_update_delete_reindex_and_revision_guard(self):
        saved=self.save('旧知识','苹果说明')
        row=self.store.view(100,identity=saved['id'])
        updated=self.store.save(100,{**row,'content':'香蕉说明'},saved['id'],saved['version'])
        self.assertEqual(self.store.retrieve(100,'苹果'),[])
        self.assertTrue(self.store.retrieve(100,'香蕉'))
        with self.assertRaises(ValueError):self.store.save(100,row,saved['id'],saved['version'])
        with self.assertRaises(ValueError):self.store.delete(100,saved['id'],saved['version'])
        with self.assertRaises(ValueError):self.store.view(200,identity=saved['id'])
        self.store.delete(100,saved['id'],updated['version'])
        self.assertEqual(self.store.retrieve(100,'香蕉'),[])
        self.assertTrue(list((self.root/'runtime/knowledge-backups').glob('*.sqlite3')))

    def test_budget_chunks_and_pagination(self):
        self.save('番茄长文','番茄种植浇水知识。"\n\\'*2000)
        rows=self.store.retrieve(100,'番茄浇水',budget=500)
        self.assertTrue(rows)
        self.assertLessEqual(len(json.dumps(rows,ensure_ascii=False,separators=(',',':'))),500)
        for n in range(22):self.save(str(n),'测试知识内容')
        page=self.store.view(100,offset=20)
        self.assertEqual(page['total'],23);self.assertEqual(len(page['rows']),3)
        self.assertEqual(self.store.view(200)['total'],0)
        self.assertEqual(self.store.view(100,query="' OR 1=1 --")['total'],0)

    def test_validation_config_and_empty_read_does_not_create_database(self):
        self.assertEqual(self.store.retrieve(100,'hello'),[]);self.assertFalse(self.store.path.exists())
        with self.assertRaises(ValueError):self.save('x','  \n ')
        with self.assertRaises(ValueError):self.save('x','binary\x00')
        with self.assertRaises(ValueError):checked(self.root,999)
        cfg=Config(self.root);values=cfg.load()
        self.assertTrue(values['knowledge_enabled']);self.assertEqual(values['knowledge_max_chars'],0)
        cfg.validate({**values,'knowledge_max_chars':0})
        cfg.validate({**values,'knowledge_max_chars':999999})
        with self.assertRaises(ValueError):cfg.validate({**values,'knowledge_max_chars':199})
        self.assertIn('番茄',terms('番茄 ABC'));self.assertIn('abc',terms('番茄 ＡＢＣ'))

    def test_large_structured_text_preserves_whitespace_and_indexes_tail(self):
        content='  settings:\r\n    enabled: true\r\n'+(' '*900000)+'\n    topic: 番茄浇水\n  '
        saved=self.save('配置.yaml',content)
        self.assertEqual(self.store.view(100,identity=saved['id'])['content'],content)
        self.assertTrue(self.store.retrieve(100,'番茄浇水'))

    def test_unlimited_retrieval_returns_all_matching_chunks_beyond_old_limits(self):
        for n in range(6):self.save('番茄资料'+str(n),('番茄浇水知识'+str(n)+'。')*700)
        rows=self.store.retrieve(100,'番茄浇水')
        self.assertEqual(len({row['id'] for row in rows}),6)
        self.assertGreater(len(rows),40)
        self.assertGreater(sum(len(row['content']) for row in rows),12000)
        bounded=self.store.retrieve(100,'番茄浇水',budget=500)
        self.assertLessEqual(len(json.dumps(bounded,ensure_ascii=False,separators=(',',':'))),500)

    def test_prompt_integration_respects_disable_and_total_budget(self):
        import bot
        self.save('番茄种植手册','番茄幼苗应避免积水，控制浇水量。')
        (self.root/'persona.txt').write_text('你是yonagi。',encoding='utf-8')
        cfg={**bot.settings(),'group_id':100,'max_prompt_chars':12800}
        target={'_id':'q','_uid':'11111','text':'番茄如何浇水？','sender':'小明','at_bot':True}
        with patch.object(bot,'ROOT',self.root):
            prompt=bot.build_prompt(cfg,[],'被@',dict(target))
            self.assertIn('番茄种植手册',prompt);self.assertIn('knowledge_references',prompt)
            self.assertLessEqual(len(prompt),12800)
            prompt=bot.build_prompt({**cfg,'knowledge_enabled':False},[],'被@',dict(target))
            self.assertNotIn('番茄种植手册',prompt)
            for n in range(10):self.save('番茄浇水资料'+str(n),'番茄幼苗浇水避免积水。'*500)
            prompt=bot.build_prompt({**cfg,'knowledge_max_chars':0},[],'被@',dict(target))
            self.assertLessEqual(len(prompt),12800)
            self.assertIn('"knowledge_references":[{',prompt)


class KnowledgeWebTests(unittest.TestCase):
    def test_authenticated_crud_and_group_isolation(self):
        from debug_console import Console,Supervisor
        from debug_webui import DebugWebUI
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
            cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(root/'settings.json',cfg)
            console=Console(root,Supervisor(root,start=False))
            server=DebugWebUI(root,console.web_action);server.start()
            try:
                with httpx.Client(base_url=server.base_url,trust_env=False) as client,patch.object(console,'draw'):
                    headers={'X-QQ-Debug-Key':server.token}
                    self.assertEqual(client.get('/api/knowledge?group=100').status_code,401)
                    self.assertEqual(client.get('/api/knowledge?group=999',headers=headers).status_code,400)
                    response=client.post('/api/action',headers=headers,json={'action':'knowledge','group':100,'values':{'title':'指南','content':'独有测试资料'}})
                    self.assertEqual(response.status_code,200,response.text)
                    saved=response.json()['result']
                    console.execute('/knowledge 独有测试资料')
                    self.assertIn('独有测试资料',console.notice)
                    console.execute('/help knowledge')
                    self.assertIn('YAML、JSON、INI',console.notice)
                    self.assertEqual(client.get('/api/knowledge?group=100',headers=headers).json()['total'],1)
                    self.assertEqual(client.get('/api/knowledge?group=200',headers=headers).json()['total'],0)
                    self.assertEqual(client.get('/api/knowledge?group=200&id='+saved['id'],headers=headers).status_code,400)
                    response=client.post('/api/action',headers=headers,json={'action':'knowledge','mode':'delete','group':100,'id':saved['id'],'expected':saved['version']})
                    self.assertEqual(response.status_code,200,response.text)
            finally:server.close()

    def test_knowledge_endpoint_accepts_beyond_generic_body_limits_and_preserves_scope(self):
        from debug_webui import DebugWebUI
        from knowledge_base import KnowledgeBase
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
            cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(root/'settings.json',cfg)
            def dispatch(value):
                store,group=checked(root,value['group'])
                return {'result':store.save(group,value['values'])}
            server=DebugWebUI(root,dispatch);server.start()
            try:
                with httpx.Client(base_url=server.base_url,trust_env=False,timeout=60) as client:
                    headers={'X-QQ-Debug-Key':server.token}
                    self.assertEqual(client.post('/api/knowledge',json={}).status_code,401)
                    self.assertEqual(client.post('/api/knowledge',headers=headers,json={'action':'command'}).status_code,400)
                    content='  config:\n'+(' '*(13*1024*1024))+'\n    tail: 番茄资料\n  '
                    value={'action':'knowledge','group':100,'values':{'title':'大配置.ini','content':content}}
                    result=client.post('/api/knowledge',headers=headers,json=value)
                    self.assertEqual(result.status_code,200,result.text[:300])
                    identity=result.json()['result']['id']
                    row=client.get('/api/knowledge?group=100&id='+identity,headers=headers).json()
                    self.assertEqual(row['content'],content)
                    self.assertEqual(client.get('/api/knowledge?group=200',headers=headers).json()['total'],0)
                    self.assertTrue(KnowledgeBase(root).retrieve(100,'番茄资料'))
                    # The dedicated text endpoint does not remove other action limits.
                    bounded_value={**value,'values':{'title':'其他接口限制','content':'x'*(4*1024*1024)}}
                    self.assertEqual(client.post('/api/action',headers=headers,json=bounded_value).status_code,413)
            finally:server.close()


if __name__=='__main__':unittest.main()
