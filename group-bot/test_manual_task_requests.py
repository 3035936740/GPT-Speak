from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot
from console_config import atomic
import manual_task_requests as requests
from large_tasks import resolve_request
from session_control import allows


class ManualTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        atomic(self.root/'settings.json',{**bot.settings(),'enabled':True,'group_id':100,'chat_group_whitelist':[100],'large_tasks_enabled':True,'receive_only':False})
        self.folder=self.root/'runtime';self.folder.mkdir();atomic(self.folder/'state.json',{'pid':os.getpid()})
        self.target={'_id':'1','_uid':'11111','_time':time.time()-100,'text':'逆行你没有镜像翻转啊，听起来就是平行八五问题非常严重','_reply_id':'file',
            '_quoted_message':{'_id':'file','is_bot':True,'text':'[文件：animation.html]','_files':[{'name':'animation.html','id':'qq-file'}]}}
        (self.folder/'recent-chat.jsonl').write_text(json.dumps(self.target,ensure_ascii=False)+'\n',encoding='utf-8')

    def test_specific_file_correction_routes_to_repair_but_banter_does_not(self):
        route=resolve_request(self.target,[]);self.assertTrue(route['repair'])
        self.assertIsNone(resolve_request({**self.target,'text':'这个真有意思'},[]))
        self.assertIsNone(resolve_request({**self.target,'_quoted_message':{},'_reply_id':'','text':'动画有意思'},[]))

    def test_operator_click_is_idempotent_and_claimed_only_once(self):
        requests.enqueue(self.root,100,'1');requests.enqueue(self.root,100,'1')
        target=requests.claim(self.folder);self.assertTrue(target['_force_file_task']);self.assertEqual(target['_reply_id'],'file')
        self.assertIsNone(requests.claim(self.folder))
        atomic(self.folder/'session-control.json',{'created':time.time()-20,'paused':False})
        self.assertTrue(allows(self.folder,target));self.assertFalse(allows(self.folder,self.target))
        requests.finish(self.folder,target,'accepted')
        requests.enqueue(self.root,100,'1');self.assertIsNone(requests.claim(self.folder))

    def test_disabled_paused_blocked_and_missing_messages_are_rejected(self):
        with self.assertRaises(ValueError):requests.enqueue(self.root,100,'missing')
        with patch('member_policy.blocked_accounts',return_value={'11111'}):
            with self.assertRaises(ValueError):requests.enqueue(self.root,100,'1')
        atomic(self.folder/'session-control.json',{'paused':True})
        with self.assertRaises(ValueError):requests.enqueue(self.root,100,'1')
