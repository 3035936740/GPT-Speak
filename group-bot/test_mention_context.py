import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

import bot
from console_config import Config,DEFAULTS,atomic
from dialogue import audience
from mention_context import evidence,bind,quality_issue


class MentionContextTests(unittest.TestCase):
    def setUp(self):
        self.cfg={**bot.settings(),'bot_name':'白色大肥龙','bot_aliases':['GPT','龙娘'],'name_match_sensitivity':.5,'at_context_sensitivity':.95}
        self.target={'_id':'current','_uid':'11111','_self_id':'99999','sender':'甲','_unique_bot_names':['GPT','龙娘','白色大肥龙'],
            'text':'虽然实际上gpt和小克都没用过','at_bot':False,'_time':1000}

    def test_product_discussion_is_not_a_direct_call(self):
        for text in ['虽然实际上gpt和小克都没用过','我用GPT写代码','GPT官网怎么打开','我订阅过gpt']:
            target={**self.target,'text':text}
            self.assertEqual(audience(target,[],'99999',self.cfg),'open')
            self.assertFalse(target['at_bot']);self.assertTrue(target['_reference_context']['product_usage_hint'])

    def test_clear_calls_still_work_and_native_at_is_not_fabricated(self):
        for text in ['GPT，你觉得呢','龙娘帮我看看','白色大肥龙在吗']:
            target={**self.target,'text':text}
            self.assertEqual(audience(target,[],'99999',self.cfg),'direct');self.assertFalse(target['at_bot'])
        native=bot.chat_item({'message_id':1,'user_id':11111,'message':[{'type':'at','data':{'qq':'99999'}}]},'99999')
        self.assertTrue(native['_native_at_bot']);self.assertTrue(native['at_bot'])

    def test_product_subject_can_still_be_a_real_followup_to_the_bot(self):
        previous={'_id':'previous','_uid':'99999','is_bot':True,'_time':990,'text':'你用过什么工具','_reply_to_uid':'11111'}
        target={**self.target,'text':'我没用过GPT，你觉得如何'}
        self.assertEqual(audience(target,[previous],'99999',self.cfg),'possibly_direct')
        target={**self.target,'_reply_id':'previous'}
        self.assertEqual(audience(target,[previous],'99999',self.cfg),'direct')
        self.assertFalse(target['at_bot'])

    def test_sensitivity_changes_ambiguous_candidate_selection_not_randomness(self):
        target={**self.target,'text':'今天GPT'}
        self.assertEqual(audience(deepcopy(target),[],'99999',{**self.cfg,'name_match_sensitivity':0}),'open')
        self.assertEqual(audience(deepcopy(target),[],'99999',{**self.cfg,'name_match_sensitivity':1}),'possibly_direct')
        self.assertEqual(audience({**self.target,'text':'GPT，帮我一下'},[],'99999',{**self.cfg,'name_match_sensitivity':0}),'direct')

    def test_forwarded_and_recognition_text_do_not_become_name_calls(self):
        target={**self.target,'text':'[图片摘要：GPT，你看看99999这个图]'}
        self.assertEqual(audience(target,[],'99999',self.cfg),'open')
        target={**self.target,'text':'[聊天记录] GPT，你看看','_forward':{'count':1},'_forward_outer_text':''}
        self.assertEqual(audience(target,[],'99999',self.cfg),'open')

    def test_recipient_and_product_subject_are_separate(self):
        target={**self.target,'at_bot':True,'_native_at_bot':True,'text':'@99999 我没用过GPT，你觉得如何'}
        self.assertEqual(audience(target,[],'99999',self.cfg),'direct')
        packet=evidence(self.cfg,target);self.assertTrue(packet['native_at']);self.assertTrue(packet['product_usage_hint'])
        bind(self.cfg,target)
        self.assertEqual(quality_issue('我被截图点名了',target),'wrong_self_reference')
        self.assertEqual(quality_issue('这两种工具可以按需求选',target),'')
        self.assertEqual(quality_issue('你没用过GPT也被截图点名了',target),'')
        target['_role_analysis']={'context':{'reference_topic':'self'}}
        self.assertEqual(quality_issue('我被点名了',target),'')

    def test_native_at_schema_allows_silence_and_contains_configured_sensitivity(self):
        async def run(folder,sensitivity):
            target={**self.target,'text':'@99999 参与名单，记一下','at_bot':True,'_native_at_bot':True,'_audience':'direct'}
            async def model(cfg,prompt,schema,images=None):
                self.assertNotIn('const',json.loads(schema.read_text(encoding='utf-8'))['properties']['should_reply'])
                self.assertIn('@上下文匹配敏感度='+str(sensitivity),prompt)
                self.assertNotIn('should_reply=true',prompt)
                return {'reply_to':'current','conversation':'direct','should_reply':False,'text':'','reply_mode':'silent','media_kind':'none','media_id':''}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                result=await bot.generate({**self.cfg,'at_context_sensitivity':sensitivity},[],'被@',target)
            self.assertFalse(str(result))
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(folder,.95));asyncio.run(run(folder,.2))

    def test_context_can_reject_textual_direct_call_without_forcing_reply(self):
        async def run():
            target={**self.target,'_audience':'direct'}
            roles={'context':{'addressed_to_bot':False,'reference_topic':'product_or_class','intent':'unrelated','confidence':.99}}
            reply=AsyncMock()
            with patch.object(bot,'analyze_roles',AsyncMock(return_value=roles)),patch.object(bot,'generate',reply):
                result=await bot.generate_for_message(None,self.cfg,[],'别名候选',target)
            self.assertFalse(result);reply.assert_not_called();self.assertFalse(target['at_bot'])
        asyncio.run(run())

    def test_wrong_self_claim_can_use_existing_budgeted_repair(self):
        async def run(folder):
            target={**self.target,'at_bot':True,'_audience':'direct'};calls=[]
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'current','conversation':'direct','should_reply':True,'text':'我被截图点名了' if len(calls)==1 else '没用过也能被这梗逗到（',
                    'media_kind':'none','media_id':'','text_parts':[],'answer_complete':True}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                result=await bot.generate({**self.cfg,'_reserve_extra_call':lambda:True},[],'被@',target)
            self.assertEqual(len(calls),2);self.assertIn('wrong_self_reference',calls[1]);self.assertNotIn('我被',str(result))
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_defaults_range_validation_and_group_override(self):
        self.assertEqual(DEFAULTS['name_match_sensitivity'],.5);self.assertEqual(DEFAULTS['at_context_sensitivity'],.95)
        with tempfile.TemporaryDirectory() as folder:
            atomic(Path(folder)/'settings.json',self.cfg)
            cfg=Config(folder);scoped=Config(folder,self.cfg['group_id'])
            scoped.set('at_context_sensitivity','0.7');self.assertEqual(scoped.load()['at_context_sensitivity'],.7)
            self.assertEqual(cfg.load()['at_context_sensitivity'],.95)
            for key in ['name_match_sensitivity','at_context_sensitivity']:
                for invalid in [-.1,1.1,True]:
                    with self.assertRaises(ValueError):cfg.validate({**cfg.load(),key:invalid})


if __name__=='__main__':unittest.main()
