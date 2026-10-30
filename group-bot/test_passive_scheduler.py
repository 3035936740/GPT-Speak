import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import bot
from passive_scheduler import PassiveScheduler


class PassiveSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'passive.json'
        self.cfg={**bot.settings(),'passive_probability':.35,'passive_cooldown_seconds':0,
            'adaptive_participation_enabled':False,'passive_trigger_mode':'frequency'}
        self.scheduler=PassiveScheduler(self.path,'169901502')
        self.events=patch('passive_scheduler.emit');self.events.start();self.addCleanup(self.events.stop)

    def item(self,id,stamp=1000,**extra):
        return {'_id':str(id),'_uid':'3035936740','_time':stamp,'sender':'群友',
            'text':'想当杯子','at_bot':False,'_audience':'open',**extra}

    def test_separate_polls_accumulate_three_messages_at_35_percent(self):
        history=[]
        for id in range(1,4):
            item=self.item(id,1000+id);history.append(item);self.scheduler.observe([item],1000+id)
            result=self.scheduler.check(self.cfg,history,1000+id)
            self.assertEqual(result['threshold'],3)
            self.assertEqual(result['should_trigger'],id==3)
        self.assertFalse(result['target']['at_bot'])
        self.assertTrue(result['target']['_passive_turn'])
        self.assertEqual(result['target']['_passive_batch_ids'],['1','2','3'])

    def test_idle_compensation_requires_real_fresh_messages(self):
        self.scheduler.observe([self.item(1)],1000)
        self.assertFalse(self.scheduler.check(self.cfg,[],1000)['should_trigger'])
        result=self.scheduler.check(self.cfg,[],1040)
        self.assertEqual(result['reason'],'idle_compensation')
        self.scheduler.consume(['1'],1040)
        self.assertEqual(self.scheduler.check(self.cfg,[],2000)['reason'],'no_new_messages')
        self.scheduler.observe([self.item(2,2000)],2000)
        self.assertEqual(self.scheduler.check(self.cfg,[],2181)['reason'],'no_new_messages')

    def test_disabled_and_quota_blocked_do_not_consume_or_force_output(self):
        self.scheduler.observe([self.item(id) for id in range(3)],1000)
        self.assertEqual(self.scheduler.check({**self.cfg,'passive_probability':0},[],1000)['reason'],'frequency_disabled')
        self.assertEqual(self.scheduler.check(self.cfg,[],1000,blocked='model_budget')['reason'],'model_budget')
        self.assertTrue(self.scheduler.check(self.cfg,[],1000)['should_trigger'])

    def test_cooldown_retains_messages_then_triggers(self):
        self.scheduler.observe([self.item(id) for id in range(3)],1000)
        cfg={**self.cfg,'passive_cooldown_seconds':30}
        self.assertEqual(self.scheduler.check(cfg,[],1000,last_send=990)['reason'],'cooldown')
        self.assertTrue(self.scheduler.check(cfg,[],1020,last_send=990)['should_trigger'])

    def test_pictures_and_background_summary_remain_available(self):
        item=self.item(1,text='[图片，未读取内容]',_images=[{'file':'image.png'}])
        self.scheduler.observe([item],1000)
        item['text']='[图片摘要：一只猫捧着杯子]'
        result=self.scheduler.check({**self.cfg,'passive_probability':1},[],1000)
        self.assertTrue(result['should_trigger'])
        self.assertIn('猫捧着杯子',result['target']['text'])

    def test_other_human_thread_is_context_not_a_direct_call(self):
        item=self.item(1,text='@123 摸摸耳朵',_at_ids=['123'],_audience='others')
        self.scheduler.observe([item],1000)
        result=self.scheduler.check({**self.cfg,'passive_probability':1},[],1000)
        self.assertTrue(result['should_trigger'])
        self.assertEqual(result['target']['_audience'],'others')
        self.assertFalse(result['target']['at_bot'])

    def test_direct_calls_never_enter_passive_queue(self):
        self.scheduler.observe([self.item(1,at_bot=True),self.item(2,_audience='possibly_direct')],1000)
        self.assertEqual(self.scheduler.check(self.cfg,[],1000)['pending_count'],0)

    def test_new_config_persists_with_group_inheritance_and_validation(self):
        from console_config import Config, atomic
        root=Path(self.temp.name);atomic(root/'settings.json',{**self.cfg,'group_id':100,'chat_group_whitelist':[100,200]})
        with patch('console_config.validate_model'):
            global_config=Config(root);local=Config(root,100)
            global_config.set('passive_trigger_mode','dynamic')
            self.assertEqual(Config(root,200).load()['passive_trigger_mode'],'dynamic')
            local.set('passive_trigger_mode','frequency')
            self.assertEqual(Config(root,100).load()['passive_trigger_mode'],'frequency')
            local.reset('passive_trigger_mode')
            self.assertEqual(Config(root,100).load()['passive_trigger_mode'],'dynamic')
            for key,value in [('passive_trigger_mode','random'),('passive_window_seconds','181'),('passive_retry_backoff_seconds','-1')]:
                with self.assertRaises(ValueError):global_config.set(key,value)

    def test_dynamic_expected_rounds_are_deduplicated_across_polls(self):
        cfg={**self.cfg,'passive_trigger_mode':'dynamic','passive_probability':.01}
        item=self.item(1);self.scheduler.observe([item],1000)
        for stamp in (1000,1001,1041,1082):self.scheduler.check(cfg,[],stamp)
        self.assertEqual(len(self.scheduler.data['rounds']),1)
        self.scheduler.consume(['1'],1082)
        self.scheduler.observe([item],1083)
        self.assertFalse(self.scheduler.pending)
        restored=PassiveScheduler(self.path,'169901502');restored.observe([item],1083)
        self.assertFalse(restored.pending)

    def test_burst_counts_one_turn_and_forced_calls_deduplicate(self):
        target=self.item(1,_passive_turn=True)
        self.scheduler.delivered(target,1000);self.scheduler.delivered(target,1001)
        self.assertEqual(len(self.scheduler.data['replies']),1)
        direct=self.item(2,at_bot=True)
        self.scheduler.forced(direct,1000);self.scheduler.forced(direct,1001)
        self.assertEqual(len(self.scheduler.data['forced']),1)

    def test_reply_schema_executes_accepted_plan_without_second_silence_vote(self):
        async def model(cfg,prompt,schema,images=None):
            props=json.loads(schema.read_text(encoding='utf-8'))['properties']
            self.assertEqual(props['should_reply']['const'],True)
            self.assertEqual(props['conversation']['enum'],['open'])
            self.assertNotIn('silent',props['reply_mode']['enum'])
            self.assertEqual(props['action']['enum'],['chat'])
            return {'should_reply':True}
        target=self.item(1,_passive_turn=True,_accepted_plan={'action':'reply'})
        with patch.object(bot,'RUNTIME',Path(self.temp.name)),patch.object(bot,'generate_json',model):
            result=asyncio.run(bot.generate_reply_json(self.cfg,'本轮规划已决定接话',target))
        self.assertTrue(result['should_reply'])

    def test_planner_can_still_choose_silence(self):
        from social_planner import apply_plan
        target=self.item(1,_passive_turn=True)
        plan={'action':'silent','topic':'朋友聊天','expects_response':False,'wait_seconds':0,
            'expression_ids':[],'memory_ids':[],'memory_query':''}
        result=apply_plan(self.cfg,target,{'confidence':.9,'plan':plan})
        self.assertEqual(result.route,'silent')

    def test_bot_poll_loop_reaches_generation_without_a_mention(self):
        async def scenario():
            polls=0;sent=[];targets=[];sleep=asyncio.sleep
            cfg={**self.cfg,'memory_enabled':False,'style_learning_enabled':False,
                'background_image_reading_enabled':False,'proactive_topics_enabled':False}
            stamp=__import__('time').time()
            messages=[{'message_id':str(id),'time':stamp,'user_id':3035936740,
                'sender':{'nickname':'群友'},'message':[{'type':'text','data':{'text':text}}]}
                for id,text in enumerate(['想当杯子','这群全是猫猫','猫猫开会了'],1)]
            async def api(client,env,action,params=None):
                nonlocal polls
                if action=='get_login_info':return {'user_id':169901502}
                if action=='get_group_info':return {}
                if action=='get_group_msg_history':
                    if params.get('count')==100:return {'messages':[]}
                    polls+=1
                    if polls>10:
                        (bot.RUNTIME/'STOP').touch();raise AssertionError('Passive turn never started')
                    for message in messages:message['time']=__import__('time').time()
                    return {'messages':messages[:min(polls,3)]}
                if action=='send_group_msg':
                    sent.append(params);(bot.RUNTIME/'STOP').touch();return {'message_id':999}
                raise AssertionError(action)
            async def generate(client,cfg,history,trigger,target,**kwargs):
                targets.append(target);return '猫猫议会（'
            async def tick(seconds):await sleep(0);await sleep(0)
            with patch.object(bot,'RUNTIME',Path(self.temp.name)),patch.object(bot,'settings',return_value=cfg),\
                    patch.object(bot,'api',api),patch.object(bot,'generate_for_message',generate),\
                    patch.object(bot,'maintain_images',AsyncMock()),patch.object(asyncio,'sleep',tick):
                await bot.run()
            self.assertEqual(len(sent),1);self.assertEqual(len(targets),1)
            self.assertTrue(targets[0]['_passive_turn']);self.assertFalse(targets[0]['at_bot'])
            self.assertEqual(len(targets[0]['_passive_batch_ids']),3)
        asyncio.run(scenario())


if __name__=='__main__':unittest.main()
