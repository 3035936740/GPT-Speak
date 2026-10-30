import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,AsyncMock
import bot
from console_config import Config,atomic
from failure_response import respond,ReplyFailure
from interaction import social_act
from peer_reply import quality_issue,gender_question


class SilentFailurePersonaTests(unittest.TestCase):
    def test_all_audiences_and_tasks_log_failure_without_generating_or_sending_text(self):
        async def run():
            async def forbidden(*args):raise AssertionError('No failure model call')
            def reserve():raise AssertionError('No extra quota reservation')
            for audience in ('direct','possibly_direct','open','others'):
                for kind in ('text','image','web'):
                    target={'_id':'123','text':'你是猫娘','at_bot':audience=='direct','_audience':audience}
                    with patch('failure_response.emit') as event:
                        reply=await respond({**bot.settings(),'_reserve_extra_call':reserve},[],target,kind,
                            ReplyFailure('timeout'),forbidden,bot.ROOT)
                    self.assertFalse(reply);self.assertEqual(reply.route,'silent')
                    self.assertEqual(event.call_args.kwargs['role'],'reply_failure')
                    self.assertEqual(event.call_args.kwargs['context_id'],'123')
        asyncio.run(run())

    def test_gender_topic_is_avoided_without_reciting_internal_rules(self):
        target={'text':'@169901502 yonagi是女生吗','_bot_name':'yonagi'}
        self.assertTrue(gender_question(target))
        self.assertEqual(quality_issue('设定上不主动强调性别，你直接叫我 yonagi 就好',target),'persona_rule_disclosure')
        self.assertEqual(quality_issue('你直接叫我 yonagi 就好',target),'gender_policy_explanation')
        self.assertEqual(quality_issue('怎么开始查户口了',target),'')
        self.assertEqual(quality_issue('女生啊',target),'gender_topic_answer')
        self.assertEqual(quality_issue('我是AI',{'text':'你是AI吗'}),'out_of_character_identity')
        self.assertEqual(social_act('你是猫娘'),'banter')
        self.assertEqual(quality_issue('Linux系统要求更新驱动',{'text':'怎么更新驱动'}),'')

    def test_gender_rule_disclosure_is_rewritten_once(self):
        async def run(folder):
            calls=[]
            target={'_id':'t','_uid':'3035936740','sender':'群友','_time':time.time(),
                'text':'yonagi是女生吗','at_bot':True,'_audience':'direct'}
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'t','should_reply':True,'conversation':'direct','action':'chat',
                    'text':'设定上不主动强调性别' if len(calls)==1 else '怎么开始查户口了',
                    'text_parts':[],'media_kind':'none','media_id':'','answer_complete':True}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate({**bot.settings(),'_reserve_extra_call':lambda:True},[],'点名',target)
            self.assertEqual(str(reply),'怎么开始查户口了');self.assertEqual(len(calls),2)
            self.assertIn('自然避开',calls[-1])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_failure_switch_persists_and_inherits(self):
        with tempfile.TemporaryDirectory() as folder,patch('console_config.validate_model'):
            root=Path(folder);atomic(root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200]})
            self.assertTrue(Config(root,100).load()['reply_failure_log_only'])
            Config(root).set('reply_failure_log_only','off')
            self.assertFalse(Config(root,100).load()['reply_failure_log_only'])
            Config(root,100).set('reply_failure_log_only','on')
            self.assertTrue(Config(root,100).load()['reply_failure_log_only'])
            self.assertFalse(Config(root,200).load()['reply_failure_log_only'])

    def test_direct_reply_failure_in_poll_loop_sends_nothing_and_keeps_polling(self):
        async def run(folder,mode):
            polls=0;generations=0;sent=[];sleep=asyncio.sleep
            cfg={**bot.settings(),'passive_probability':0,'memory_enabled':False,'style_learning_enabled':False,
                'background_image_reading_enabled':False,'proactive_topics_enabled':False}
            raw={'message_id':1,'time':time.time(),'user_id':3035936740,'sender':{'nickname':'群友'},
                'message':[{'type':'at','data':{'qq':'169901502'}},{'type':'text','data':{'text':'你是猫娘'}}]}
            async def api(client,env,action,params=None):
                nonlocal polls
                if action=='get_login_info':return {'user_id':169901502}
                if action=='get_group_info':return {}
                if action=='get_group_msg_history':
                    if params.get('count')==100:return {'messages':[]}
                    polls+=1
                    if polls>=6:(bot.RUNTIME/'STOP').touch()
                    raw['time']=time.time()
                    return {'messages':[raw]}
                if action=='send_group_msg':sent.append(params);return {'message_id':999}
                raise AssertionError(action)
            async def failed(*args,**kwargs):
                nonlocal generations
                generations+=1
                if mode=='raw_identity':return bot.ReplyText('我是AI')
                raise ValueError('Required prompt content exceeds character budget')
            async def forbidden(*args,**kwargs):raise AssertionError('No error reply generation')
            async def tick(seconds):await sleep(0);await sleep(0)
            if mode=='saved_identity':
                pending={'_id':'1','_uid':'3035936740','sender':'群友','text':'你是猫娘',
                    '_time':time.time(),'at_bot':True,'_audience':'direct',
                    '_remaining_delivery':[{'action':'send_group_msg','params':{'group_id':cfg['group_id'],
                        'message':[{'type':'text','data':{'text':'我是AI'}}]}}]}
                (Path(folder)/'pending-mentions.json').write_text(json.dumps([pending]),encoding='utf-8')
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'settings',return_value=cfg),\
                    patch.object(bot,'api',api),patch.object(bot,'generate_for_message',failed),\
                    patch.object(bot,'generate_json',forbidden),patch.object(bot,'maintain_images',AsyncMock()),\
                    patch.object(asyncio,'sleep',tick):
                await bot.run()
            self.assertEqual(generations,1) # The new poll message runs; saved delivery does not.
            self.assertEqual(sent,[]);self.assertGreaterEqual(polls,6)
        for mode in ('failed','raw_identity','saved_identity'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder,mode))


if __name__=='__main__':unittest.main()
