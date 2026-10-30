import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import bot
from conversation_focus import ConversationFocus
from interaction import InteractionMemory
from transient_content import TransientContent
import web_reader


class TransientContentTests(unittest.TestCase):
    def test_expiry_while_idle_not_just_on_next_read(self):
        memory = TransientContent(ttl=.05)
        removed = threading.Event()
        original = memory._remove
        def remove(key):
            original(key)
            removed.set()
        memory._remove = remove
        memory.remember(1, {'_uid':'a','_id':'request'}, {'_id':'reply','text':'summary'})
        self.assertTrue(removed.wait(1))
        self.assertEqual(len(memory.entries), 0)

    def test_bounds_owner_quote_expiry_and_restart(self):
        now = [0.0]
        memory = TransientContent(ttl=120, capacity=2, summary_limit=80, clock=lambda:now[0])
        try:
            target={'_uid':'a','_id':'request','_content_source_id':'source'}
            memory.remember(1,target,{'_id':'reply','text':'概要'*100})
            self.assertEqual(len(memory.recall(1,{'_uid':'a','_reply_id':'reply'})['summary']),80)
            self.assertFalse(memory.recall(2,{'_uid':'a','_reply_id':'reply'}))
            self.assertFalse(memory.recall(1,{'_uid':'b','_reply_id':'reply'}))
            self.assertFalse(memory.recall(1,{'_uid':'a','_reply_id':'someone-else'}))
            self.assertFalse(memory.recall(1,{'_uid':'a','text':'今天午饭吃什么'}))
            self.assertTrue(memory.recall(1,{'_uid':'a','text':'刚才那个再说一下'}))
            self.assertFalse(TransientContent().recall(1,{'_uid':'a','_reply_id':'reply'}))
            for n in range(3): memory.remember(1,{**target,'_id':'request'+str(n)},{'_id':str(n),'text':str(n)})
            self.assertEqual(len(memory.entries),2)
            now[0]=121
            self.assertFalse(memory.recall(1,{'_uid':'a','text':'刚才那个'}))
            self.assertFalse(memory.entries)
        finally: memory.clear()

    def test_split_answer_and_comprehension_feedback_keep_both_parts(self):
        memory=TransientContent()
        try:
            target={'_uid':'a','_id':'question'}
            memory.remember(1,target,{'_id':'part1','text':'先绕到它背后打'})
            memory.remember(1,target,{'_id':'part2','text':'正面有盾，打不动'})
            self.assertEqual(len(memory.entries),1)
            feedback={'_uid':'a','text':'什么几把玩意，看不懂啊','_feedback_on_answer':True}
            self.assertEqual(memory.recall(1,feedback)['summary'],'先绕到它背后打\n正面有盾，打不动')
            self.assertTrue(memory.recall(1,{'_uid':'a','_reply_id':'part1'}))
            self.assertTrue(memory.recall(1,{'_uid':'a','_reply_id':'part2'}))
        finally: memory.clear()

    def test_webpages_are_fetched_again_without_a_process_cache(self):
        async def run():
            calls=[]
            def fetch(url):
                calls.append(url)
                return {'status':'read','body':str(len(calls))}
            with patch.object(web_reader,'fetch',fetch):
                a=await web_reader.read_pages(['https://example.com'])
                b=await web_reader.read_pages(['https://example.com'])
            self.assertEqual(len(calls),2)
            self.assertNotEqual(a[0]['body'],b[0]['body'])
            self.assertFalse(hasattr(web_reader,'CACHE'))
        asyncio.run(run())

    def test_originals_removed_after_success_error_and_cancellation(self):
        async def run(error):
            cfg=bot.settings()
            target={'_id':'1','_uid':'a','at_bot':True,'text':'总结一下',
                '_images':['https://gchat.qpic.cn/placeholder'],'_time':time.time()}
            captured=[]
            async def download(client,url,path,cfg): path.write_bytes(b'local-input')
            async def generate(cfg,context,trigger,adjusted,paths):
                captured.append((adjusted,list(paths)))
                if error: raise error
                return 'short answer'
            with tempfile.TemporaryDirectory() as root, patch.object(bot,'RUNTIME',Path(root)), \
                    patch.object(bot,'download_image',download), patch.object(bot,'generate',generate), \
                    patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
                try: await bot.generate_for_message(None,cfg,[],'被@',target)
                except (ValueError,asyncio.CancelledError): pass
                self.assertFalse(list(Path(root).glob('vision-*')))
            self.assertTrue(target['_ephemeral_content'])
            self.assertNotIn('_reference_pages',target)
            self.assertNotIn('_transient_summary',captured[0][0])
            self.assertTrue(all(not path.exists() for path in captured[0][1]))
        for error in (None, ValueError('test'), asyncio.CancelledError()):
            asyncio.run(run(error))

    def test_delivered_analysis_is_not_persisted_in_personal_memory_or_focus_text(self):
        with tempfile.TemporaryDirectory() as root:
            target={'_uid':'a','_id':'request','text':'总结一下','_ephemeral_content':True}
            reply={'_id':'reply','_time':time.time(),'is_bot':True,'text':'UNIQUE_WEB_CONTENT'}
            memory=InteractionMemory(Path(root)/'memory.json')
            memory.remember(reply,target)
            self.assertFalse(memory.turns)
            focus=ConversationFocus(Path(root)/'focus.json')
            focus.remember(reply,target)
            self.assertNotIn('UNIQUE_WEB_CONTENT',(Path(root)/'focus.json').read_text(encoding='utf-8'))
            reloaded={'_id':'reply','is_bot':True,'text':'UNIQUE_WEB_CONTENT'}
            ConversationFocus(Path(root)/'focus.json').annotate(reloaded)
            self.assertNotIn('UNIQUE_WEB_CONTENT',reloaded['text'])

    def test_page_body_and_recalled_summary_do_not_escape_finished_job(self):
        async def run():
            target={'_uid':'a','_id':'request','at_bot':True,'text':'https://example.com 总结一下'}
            captured=[]
            async def pages(urls):
                return [{'status':'read','body':'PAGE_BODY','url':urls[0]}]
            async def generate(cfg,context,trigger,adjusted,paths):
                self.assertEqual(adjusted['_reference_pages'][0]['body'],'PAGE_BODY')
                captured.append(adjusted)
                return 'short answer'
            with patch.object(bot,'read_pages',pages),patch.object(bot,'generate',generate), \
                    patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
                await bot.generate_for_message(None,bot.settings(),[],'被@',target)
            self.assertNotIn('_reference_pages',captured[0])
            self.assertNotIn('_reference_pages',target)
        asyncio.run(run())

    def test_delivery_and_shutdown_do_not_save_analysis_reply_in_state(self):
        async def run():
            cfg={**bot.settings(),'poll_seconds':0,'memory_enabled':False,
                'style_learning_enabled':False,'passive_probability':0}
            original_sleep=asyncio.sleep
            sent=[]
            polls=0
            message={'message_id':'request','time':time.time(),'user_id':123,'sender':{'nickname':'tester'},
                'message':[{'type':'at','data':{'qq':169901502}},{'type':'text','data':{'text':'总结一下'}}]}
            async def api(client,env,action,params=None):
                nonlocal polls
                if action=='get_login_info': return {'user_id':169901502}
                if action=='get_group_info': return {}
                if action=='get_group_msg_history':
                    polls+=1
                    if polls>25: raise AssertionError('Reply did not finish')
                    message['time']=__import__('time').time()
                    return {'messages':[] if params.get('count')==100 else [message]}
                if action=='send_group_msg':
                    sent.append(params)
                    (bot.RUNTIME/'STOP').touch()
                    return {'message_id':'reply'}
                raise AssertionError(action)
            async def generate(client,cfg,context,trigger,target,as_image=False):
                target['_ephemeral_content']=True
                return 'UNIQUE_ANALYSIS_CONTENT'
            async def tick(seconds):
                await original_sleep(0)
                await original_sleep(0)
            with tempfile.TemporaryDirectory() as root,patch.object(bot,'RUNTIME',Path(root)), \
                    patch.object(bot,'settings',return_value=cfg),patch.object(bot,'api',api), \
                    patch.object(bot,'generate_for_message',generate),patch.object(bot.asyncio,'sleep',tick):
                await bot.run()
                self.assertEqual(len(sent),1)
                for name in ['state.json','conversation-focus.json','interaction-memory.json','pending-mentions.json']:
                    path=Path(root)/name
                    if path.exists(): self.assertNotIn('UNIQUE_ANALYSIS_CONTENT',path.read_text(encoding='utf-8'))
                self.assertFalse(bot.TEMP_CONTENT.entries)
        asyncio.run(run())


if __name__ == '__main__': unittest.main()
