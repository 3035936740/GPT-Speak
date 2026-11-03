import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock
import bot
import large_tasks
import task_history as history
from console_config import Config,atomic


class TaskHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_input_file_manifest_without_reading_binary_into_page(self):
        identity=history.begin(self.folder,self.target,self.cfg)
        folder=self.folder/'generated-files'/identity/'inputs';folder.mkdir(parents=True)
        (folder/'1-happy_music.mid').write_bytes(b'MThd\0binary')
        row=history.view(self.root,100,identity)
        self.assertEqual(row['input_files'],[{'name':'happy_music.mid','bytes':11,'status':'saved'}])
        self.assertNotIn('binary',row['artifact'])
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.cfg={**bot.settings(),'enabled':True,'group_id':100,'chat_group_whitelist':[100,200],
            'large_tasks_enabled':True,'large_task_retention_hours':2}
        atomic(self.root/'settings.json',self.cfg);self.folder=self.root/'runtime';self.folder.mkdir()
        self.target={'_self_id':'169901502','_id':'t1','_uid':'11111','sender':'测试用户','text':'写一个HTML文件','_time':time.time()}
        async def api(action,params):return {'user_id':169901502,'role':'admin'} if action=='get_group_member_info' else {}
        self.api=api

    async def test_chunks_are_written_before_next_call_and_failed_partial_is_visible(self):
        calls=[]
        async def model(cfg,prompt,schema):
            calls.append(prompt)
            if len(calls)==1:return {'filename':'a.html','content':'<html><body>partial','complete':False,'summary':''}
            row=history.view(self.root,100)['rows'][0]
            detail=history.view(self.root,100,row['id'])
            self.assertEqual(detail['artifact'],'<html><body>partial')
            self.assertNotIn('conversation',detail);self.assertNotIn('context_boundary',detail)
            return {'filename':'a.html','content':'','complete':True,'summary':''}
        with self.assertRaisesRegex(ValueError,'empty_content'):
            await large_tasks.generate(self.cfg,self.target,[],self.api,model,self.folder)
        row=history.view(self.root,100)['rows'][0];self.assertEqual(row['status'],'failed')
        detail=history.view(self.root,100,row['id']);self.assertEqual(detail['validation']['issue'],'empty_content')
        self.assertEqual(detail['artifact'],'<html><body>partial')
        self.assertFalse(history.view(self.root,200)['rows'])

    async def test_complete_chunk_retains_file_without_claiming_upload(self):
        result={'filename':'a.html','content':'<html><body>done</body></html>','complete':True,'summary':'done'}
        reply,status=await large_tasks.generate(self.cfg,self.target,[],self.api,AsyncMock(return_value=result),self.folder)
        self.assertEqual(status['status'],'ready');self.assertEqual(history.view(self.root,100,reply.task_view_id)['artifact'],result['content'])
        self.assertEqual(history.view(self.root,100,reply.task_view_id)['status'],'ready')

    async def test_retention_zero_keeps_old_terminal_tasks_and_expiry_removes_only_owned_copy(self):
        identity=history.begin(self.folder,self.target,self.cfg)
        folder=self.folder/'generated-files'/identity;folder.mkdir(parents=True);(folder/'draft.txt').write_text('partial')
        history.update(self.folder,identity,status='failed',finished=time.time()-9000)
        history.cleanup(self.folder,{**self.cfg,'large_task_retention_hours':0});self.assertTrue(folder.exists())
        history.cleanup(self.folder,self.cfg);self.assertFalse(folder.exists());self.assertFalse(history.view(self.root,100)['rows'])

    async def test_disabled_endpoint_hides_tasks_and_rejects_bad_ids(self):
        identity=history.begin(self.folder,self.target,self.cfg)
        with self.assertRaises(ValueError):history.view(self.root,100,'../bad')
        atomic(self.root/'settings.json',{**self.cfg,'large_tasks_enabled':False})
        self.assertFalse(history.view(self.root,100)['enabled'])
        self.assertTrue((self.folder/'task-history'/(identity+'.json')).exists())

    async def test_retention_config_accepts_zero_and_rejects_negative(self):
        Config(self.root).set('large_task_retention_hours','0')
        self.assertEqual(Config(self.root).load()['large_task_retention_hours'],0)
        with self.assertRaises(ValueError):Config(self.root).set('large_task_retention_hours','-1')

    async def test_all_groups_are_sorted_and_details_remain_group_scoped(self):
        from console_config import runtime
        first=history.begin(self.folder,self.target,self.cfg)
        other=runtime(self.root,200);other.mkdir()
        second=history.begin(other,{**self.target,'_id':'other'},self.cfg)
        rows=history.view(self.root,100,scope='all')['rows']
        self.assertEqual({(r['group_id'],r['id']) for r in rows},{(100,first),(200,second)})
        with self.assertRaises(ValueError):history.view(self.root,100,second)
        with self.assertRaises(ValueError):history.view(self.root,999,scope='all')
        (self.root/'group-config/200').mkdir(parents=True)
        atomic(self.root/'group-config/200/settings.json',{'large_tasks_enabled':False})
        self.assertEqual(len(history.view(self.root,100,scope='all')['rows']),1)
