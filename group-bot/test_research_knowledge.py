import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bot
from knowledge_base import KnowledgeBase
from role_analysis import analyze
from research_evidence import checked,summary


class ResearchKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'persona.txt').write_text('你是yonagi',encoding='utf-8')
        (self.root/'reply_protocol.txt').write_text('直接回答当前问题',encoding='utf-8')
        self.cfg={**bot.settings(),'group_id':100,'knowledge_enabled':True,'knowledge_max_chars':0,'_reserve_extra_call':lambda:True}
        self.target={'_id':'current','text':'帮我看看testify定数和物量谱师等','at_bot':True,'_audience':'direct','_media_choices':[],'_reply_style':{},'_recent_bot_replies':[]}
        KnowledgeBase(self.root).save(100,{'title':'arcaea_charts','source':'arcaea_charts.json',
            'content':json.dumps({'testify':{'charts':[{'rating':12.0,'notes':2221,'chart':'Silent¦END'}]}},ensure_ascii=False)})

    def test_context_routing_receives_knowledge_before_search_decision(self):
        async def run():
            async def roles(cfg,context,target,*args,**kwargs):
                self.assertIn('2221',json.dumps(target['_knowledge_references'],ensure_ascii=False))
                return {'context':{'addressed_to_bot':True,'intent':'question','needs_web_search':False,'search_query':'','confidence':1}}
            async def reply(cfg,context,trigger,target,images=None):return bot.ReplyText('Testify BYD定数12.0，2221物量，Silent¦END')
            with patch.object(bot,'ROOT',self.root),patch.object(bot,'analyze_roles',roles),patch.object(bot,'generate',reply),patch.object(bot,'researched_reply') as web:
                result=await bot.generate_for_message(None,self.cfg,[],'被@',dict(self.target))
            self.assertIn('2221',result);web.assert_not_called()
        asyncio.run(run())

    def test_web_failure_falls_back_to_real_local_knowledge_without_claiming_web_evidence(self):
        async def run():
            roles={'context':{'needs_web_search':True,'search_query':'testify定数','confidence':1,'addressed_to_bot':True}}
            async def answer(cfg,context,trigger,target,images=None):
                self.assertTrue(cfg['_knowledge_fallback']);self.assertEqual(cfg['_web_search_mode'],'disabled')
                prompt=bot.build_prompt(cfg,context,trigger,target)
                self.assertIn('2221',prompt);self.assertIn('不声称已联网核实',prompt)
                return bot.ReplyText('库里Testify BYD是12.0，2221物量，Silent¦END')
            with patch.object(bot,'ROOT',self.root),patch.object(bot,'analyze_roles',return_value=roles),patch.object(bot,'researched_reply',side_effect=RuntimeError('Live research produced no web tool evidence')),patch.object(bot,'generate',answer),patch.object(bot,'generated_failure_reply') as failure:
                result=await bot.generate_for_message(None,self.cfg,[],'被@',dict(self.target))
            self.assertIn('库里',result);failure.assert_not_called()
        asyncio.run(run())

    def test_missing_disabled_or_other_group_knowledge_does_not_bypass_web_validation(self):
        with patch.object(bot,'ROOT',self.root):
            self.assertEqual(bot.knowledge_for_message({**self.cfg,'knowledge_enabled':False},self.target),[])
            self.assertEqual(bot.knowledge_for_message({**self.cfg,'group_id':200},self.target),[])
            self.assertEqual(bot.knowledge_for_message(self.cfg,{**self.target,'text':'quantum computing'}),[])
            self.assertIsNone(asyncio.run(bot.knowledge_after_web_failure({**self.cfg,'_reserve_extra_call':lambda:False},[],'被@',dict(self.target))))

    def test_context_model_prompt_includes_actual_knowledge_and_no_forced_web_on_look_request(self):
        async def run():
            target={**self.target,'_knowledge_references':[{'title':'谱面库','content':'testify 12.0 2221 Silent¦END'}]}
            async def generate(cfg,prompt,path,images=None):
                if path.name.startswith('context'):
                    self.assertIn('2221',prompt);self.assertIn('不等于必须联网',prompt)
                    return {'addressed_to_bot':True,'intent':'question','needs_web_search':False,'search_query':'','confidence':1,'reference_message_id':'current','note':''}
                return {'mood':'neutral','reply_tone':'casual','confidence':1,'note':''}
            result=await analyze({**self.cfg,'multi_role_enabled':True},[],target,[],generate,self.root)
            self.assertFalse(result['context']['needs_web_search'])
        asyncio.run(run())

    def test_local_fallback_cannot_select_web_search_again(self):
        async def run():
            async def generate(cfg,prompt,schema,images=None):
                spec=json.loads(schema.read_text(encoding='utf-8'))
                self.assertEqual(spec['properties']['action']['enum'],['chat'])
                return {'action':'chat'}
            with patch.object(bot,'generate_json',generate):
                await bot.generate_reply_json({**self.cfg,'_knowledge_fallback':True},'reply',self.target)
        asyncio.run(run())


class ResearchEvidenceTests(unittest.TestCase):
    def encode(self,*events):return '\n'.join(json.dumps(event) for event in events).encode()
    def test_completed_snake_case_camel_case_and_direct_response_events(self):
        for event in (
            {'type':'item.completed','item':{'type':'web_search','id':'a'}},
            {'method':'item/completed','params':{'item':{'type':'webSearch','id':'a'}}},
            {'type':'response.output_item.done','item':{'type':'web_search_call','status':'completed','id':'a'}},
            {'type':'web_search_call','status':'completed','id':'a'}):
            with self.subTest(event=event):self.assertEqual(checked(self.encode(event),True),1)
    def test_started_failed_and_model_written_search_claims_are_not_evidence(self):
        for event in (
            {'type':'item.started','item':{'type':'web_search','id':'a'}},
            {'type':'item.completed','item':{'type':'web_search_call','status':'failed','id':'a'}},
            {'type':'item.completed','item':{'type':'agent_message','text':'web_search source_url https://example.com'}}):
            with self.subTest(event=event):self.assertEqual(checked(self.encode(event),True),0)
    def test_forbidden_tools_and_search_outside_live_lane_still_rejected(self):
        for kind in ('mcpToolCall','commandExecution','fileChange'):
            with self.assertRaises(RuntimeError):checked(self.encode({'method':'item/started','params':{'item':{'type':kind}}}),True)
        with self.assertRaises(RuntimeError):checked(self.encode({'type':'item.completed','item':{'type':'web_search'}}),False)
    def test_diagnostics_contain_only_event_types_and_counts(self):
        result=summary(self.encode({'type':'item.completed','item':{'type':'agent_message','text':'private-message secret=123'}}))
        self.assertNotIn('private-message',json.dumps(result));self.assertNotIn('secret',json.dumps(result))
