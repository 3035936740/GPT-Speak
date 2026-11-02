"""Exercise the real polling, routing, background generation and delivery with fake IO."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_chat_is_delivered_before_background_file_finishes(self):
        defaults=bot.settings();original_sleep=asyncio.sleep
        started=asyncio.Event();ordinary_sent=asyncio.Event();sent=[];polls=0
        def message(identity,text):return {'message_id':identity,'time':time.time(),'user_id':3035936740,
            'sender':{'nickname':'测试者'},'message':[{'type':'at','data':{'qq':169901502}},
                {'type':'text','data':{'text':text}}]}
        async def api(client,env,action,params=None):
            nonlocal polls
            if action=='get_login_info':return {'user_id':169901502}
            if action=='get_group_info':return {}
            if action=='get_group_member_list':return []
            if action=='get_group_member_info':return {'user_id':params['user_id'],'role':'member'}
            if action=='get_group_file_system_info':return {}
            if action=='get_group_msg_history':
                polls+=1
                if polls>3000:raise AssertionError('background file or ordinary reply stalled')
                return {'messages':[] if params.get('count')==100 else
                    [message(1,'写一个完整HTML页面发给我')]+([message(2,'早上好呀')] if started.is_set() else [])}
            if action=='send_group_msg':
                text=''.join(s['data'].get('text','') for s in params['message'] if s['type']=='text');sent.append(text)
                if '日常聊天正常继续' in text:ordinary_sent.set()
                if '给你做好了，看看这只小家伙' in text:(bot.RUNTIME/'STOP').touch()
                return {'message_id':str(100+len(sent))}
            if action=='upload_group_file':
                self.assertTrue(ordinary_sent.is_set());sent.append('UPLOAD');return {'file_id':'uploaded'}
            raise AssertionError(action)
        async def model(cfg,prompt,schema,images=None):
            properties=json.loads(Path(schema).read_text(encoding='utf-8'))['properties']
            if 'filename' in properties:
                self.assertEqual(cfg['model'],'gpt-6.1-sol');self.assertEqual(cfg['reasoning_effort'],'high')
                started.set();await asyncio.wait_for(ordinary_sent.wait(),5)
                return {'filename':'page.html','content':'<html><body>完成</body></html>',
                    'summary':'页面制作完成','complete':True}
            if set(properties)=={'text'}:
                self.assertIn('QQ已确认',prompt);self.assertIn('页面制作完成',prompt)
                self.assertIn(defaults['bot_name'],prompt);self.assertIn('不回答或重新执行原任务',prompt)
                self.assertNotEqual(cfg['model'],'gpt-6.1-sol')
                return {'text':'给你做好了，看看这只小家伙'}
            return {'reply_to':'2','conversation':'direct','should_reply':True,'text':'日常聊天正常继续',
                'action':'reply','media_kind':'none','media_id':''}
        async def tick(seconds):await original_sleep(0);await original_sleep(0)
        with tempfile.TemporaryDirectory() as folder:
            cfg={**defaults,'enabled':True,'receive_only':False,'large_tasks_enabled':True,
                'large_task_model':'gpt-6.1-sol','large_task_reasoning_effort':'high','poll_seconds':0,
                'multi_role_enabled':False,'memory_enabled':False,'style_learning_enabled':False,
                'at_reply_probability':1,'quote_reply_probability':1,
                'passive_probability':0,'mention_cooldown_seconds':0,'background_image_recognition_enabled':False}
            (Path(folder)/'settings.json').write_text(json.dumps(cfg),encoding='utf-8')
            for name in ('persona.txt','reply_protocol.txt'):
                (Path(folder)/name).write_text((bot.ROOT/name).read_text(encoding='utf-8'),encoding='utf-8')
            for schema in bot.ROOT.glob('*.schema.json'):
                (Path(folder)/schema.name).write_bytes(schema.read_bytes())
            with patch.object(bot,'ROOT',Path(folder)),patch.object(bot,'RUNTIME',Path(folder)/'runtime'),\
                    patch.object(bot,'settings',lambda:dict(cfg)),patch.object(bot,'api',api),\
                    patch.object(bot,'generate_json',model),patch.object(bot.asyncio,'sleep',tick):
                bot.RUNTIME.mkdir()
                running=asyncio.create_task(bot.run())
                try:await asyncio.wait_for(asyncio.shield(running),10)
                finally:
                    (bot.RUNTIME/'STOP').touch()
                    await asyncio.wait_for(running,5)
            self.assertEqual(sent.count('UPLOAD'),1)
            self.assertEqual(len(sent),3)  # ordinary chat, file, persona-based completion; no start announcement
            self.assertLess(next(i for i,text in enumerate(sent) if '日常聊天正常继续' in text),sent.index('UPLOAD'))
            self.assertIn('给你做好了，看看这只小家伙',sent[-1]);self.assertNotIn('页面制作完成',sent[-1])
