import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
import bot
from forward_messages import ForwardMessages,HISTORY_NOTE,timestamp,view
from console_config import atomic,Config
from debug_references import message_metadata
from recent_chat_journal import compact
from content_reference import assets


class ForwardMessageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(self.root/'settings.json',cfg)
        self.cfg=Config(self.root).load();self.reader=ForwardMessages(self.root/'runtime')
        self.stamp=datetime(2026,10,30,12,34,56,tzinfo=timezone.utc).timestamp()
        self.nodes=[{'sender':{'nickname':'小明'},'time':self.stamp+n,'message':[{'type':'text','data':{'text':'历史内容'+str(n)}}]} for n in range(7)]
        self.message={'message_id':123,'user_id':11111,'group_id':100,'time':time.time(),'sender':{'nickname':'转发者'},'message':[{'type':'forward','data':{'id':'forward-one'}}]}

    def test_one_card_is_one_merged_text_with_all_original_dates_and_times(self):
        async def run():
            calls=[]
            async def api(action,params):calls.append((action,params));return {'messages':self.nodes}
            expanded=await self.reader.expand(self.message,api,self.cfg)
            item=bot.chat_item(expanded,22222)
            self.assertEqual(item['_id'],'123');self.assertEqual(item['_forward']['count'],7)
            self.assertEqual(len(item['_forward']['preview']),4)
            self.assertIn('[聊天记录]',item['text']);self.assertIn(HISTORY_NOTE,item['text'])
            for n in range(7):self.assertIn('['+timestamp(self.stamp+n)+'] 小明：历史内容'+str(n),item['text'])
            self.assertIn('2026-10-30 20:34:56',item['text'])
            self.assertFalse(item['at_bot']);self.assertEqual(item['_images'],[])
            self.assertEqual(message_metadata(item)['forward']['count'],7)
            self.assertEqual(view(self.root,100,123)['text'],item['text'])
            await self.reader.expand(self.message,api,self.cfg);self.assertEqual(len(calls),1)
            with self.assertRaises(ValueError):view(self.root,200,123)
            with self.assertRaises(ValueError):view(self.root,300,123)
        asyncio.run(run())

    def test_embedded_nodes_and_images_are_plain_text_unless_enabled(self):
        async def run():
            nodes=[{'type':'node','data':{'name':'原发送者','time':self.stamp,'content':[{'type':'text','data':{'text':'旧图'}},{'type':'image','data':{'url':'https://gchat.qpic.cn/test'}}]}}]
            row={**self.message,'message':[{'type':'forward','data':{'id':'embedded','content':nodes}}]}
            async def no_api(*args):self.fail('Embedded nodes should not need API')
            expanded=await self.reader.expand(row,no_api,self.cfg)
            self.assertIn('旧图[图片]',bot.message_text(expanded));self.assertEqual(bot.image_urls(expanded),[])
            expanded=await self.reader.expand(row,no_api,{**self.cfg,'forward_images_enabled':True})
            self.assertEqual(bot.image_urls(expanded),['https://gchat.qpic.cn/test'])
        asyncio.run(run())

    def test_disabled_reading_keeps_card_marker_without_fetching_body(self):
        async def run():
            async def no_api(*args):self.fail('Disabled reading must not fetch')
            expanded=await self.reader.expand(self.message,no_api,{**self.cfg,'forward_messages_enabled':False})
            self.assertIn('[聊天记录]',bot.message_text(expanded));self.assertEqual(expanded['_forward']['status'],'disabled')
        asyncio.run(run())

    def test_failed_fetch_does_not_turn_card_into_empty_message(self):
        async def run():
            async def api(*args):raise TimeoutError()
            expanded=await self.reader.expand(self.message,api,self.cfg)
            self.assertEqual(expanded['_forward']['status'],'unavailable');self.assertIn('[聊天记录]',bot.message_text(expanded))
        asyncio.run(run())

    def test_nested_history_retains_inner_senders_times_and_loop_is_not_replayed(self):
        async def run():
            async def api(action,params):
                if params['message_id']=='forward-one':return {'messages':[{'sender':{'nickname':'外层'},'time':self.stamp,'message':[{'type':'forward','data':{'id':'inner'}}]}]}
                return {'messages':[self.nodes[0],{'sender':{'nickname':'内层'},'time':self.stamp+2,'message':[{'type':'forward','data':{'id':'forward-one'}}]}]}
            expanded=await self.reader.expand(self.message,api,self.cfg)
            self.assertIn('小明：历史内容0',expanded['_forward_text']);self.assertIn('嵌套记录未展开',expanded['_forward_text'])
        asyncio.run(run())

    def test_forward_history_links_do_not_become_current_web_requests_and_journal_keeps_text(self):
        item={'_id':'123','_uid':'11111','_time':time.time(),'text':'[聊天记录]\n'+HISTORY_NOTE+'\nhttps://example.com/old\n'+('完整历史'*700),
            '_forward':{'count':7,'preview':[]},'_forward_outer_text':'帮我总结这份记录','sender':'转发者'}
        self.assertEqual(assets(item)['links'],[])
        self.assertEqual(compact(item)['text'],item['text'])
        with patch.object(bot,'ROOT',self.root):
            (self.root/'persona.txt').write_text('你是yonagi',encoding='utf-8')
            (self.root/'reply_protocol.txt').write_text('按问题回答',encoding='utf-8')
            prompt=bot.build_prompt({**self.cfg,'knowledge_enabled':False},[],'收到聊天记录',item)
        self.assertIn('"non_live":true',prompt);self.assertIn('非实时消息',prompt)
        self.assertIn('完整历史'*100,prompt);self.assertLessEqual(len(prompt),12800)

    def test_forward_defaults_and_metadata_are_explicit(self):
        self.assertTrue(self.cfg['forward_messages_enabled']);self.assertFalse(self.cfg['forward_images_enabled'])
        self.assertEqual(bot.message_text(self.message),'[聊天记录]')
        self.assertEqual(timestamp('invalid'),'时间未知')

    def test_full_detail_endpoint_is_authenticated_and_scoped(self):
        async def prepare():
            async def api(*args):return {'messages':self.nodes}
            await self.reader.expand(self.message,api,self.cfg)
        asyncio.run(prepare())
        from debug_webui import DebugWebUI
        server=DebugWebUI(self.root,lambda value:{'ok':True});server.start()
        try:
            with httpx.Client(base_url=server.base_url,trust_env=False) as client:
                path='/api/forward-message?group=100&message_id=123'
                self.assertEqual(client.get(path).status_code,401)
                headers={'X-QQ-Debug-Key':server.token}
                result=client.get(path,headers=headers)
                self.assertEqual(result.status_code,200,result.text)
                self.assertIn('历史内容6',result.json()['text']);self.assertIn('2026-10-30 20:34:56',result.json()['text'])
                self.assertEqual(client.get('/api/forward-message?group=200&message_id=123',headers=headers).status_code,400)
        finally:server.close()

    def test_long_forward_is_one_marked_document_and_fits_model_total_budget(self):
        item={'_id':'123','_uid':'11111','_time':time.time(),'text':'[聊天记录]\n'+HISTORY_NOTE+'\n[2026-10-30 20:34:56] 原发送者：'+('很长的旧聊天内容'*4000),
            '_forward':{'count':7,'preview':[]},'_forward_outer_text':'总结一下','sender':'转发者'}
        (self.root/'persona.txt').write_text('你是yonagi',encoding='utf-8')
        (self.root/'reply_protocol.txt').write_text('按当前问题回答',encoding='utf-8')
        with patch.object(bot,'ROOT',self.root):prompt=bot.build_prompt({**self.cfg,'knowledge_enabled':False},[],'被@',item)
        payload=json.loads(prompt[prompt.index('{"target":'):])
        self.assertEqual(payload['target']['message_id'],'123')
        self.assertTrue(payload['target']['forwarded_history']['non_live'])
        self.assertTrue(payload['target']['forwarded_history']['truncated'])
        self.assertIn('2026-10-30 20:34:56',payload['target']['text']);self.assertLessEqual(len(prompt),12800)
