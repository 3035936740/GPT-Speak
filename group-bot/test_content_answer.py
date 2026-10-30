import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
from content_answer import detailed_request,valid_parts,answer,answer_budget,source_excerpt
from content_reference import resolve
from dialogue import audience


class ContentAnswerTests(unittest.TestCase):
    def test_explicit_detail_and_empty_intro(self):
        self.assertTrue(detailed_request('@169901502 https://example.com 详细总结一下机制'))
        self.assertFalse(detailed_request('总结一下'))
        self.assertFalse(valid_parts(['机制不少，我给你捋个精简版：']))
        self.assertTrue(valid_parts(['机制：举盾免伤，绕背打']*2))
        self.assertFalse(valid_parts(['甲'*121]))
        self.assertFalse(valid_parts(['甲'*110,'乙'*110,'丙'*110]))
        self.assertFalse(valid_parts(['甲'*120,'可阻止它回血']))
        self.assertEqual(answer_budget('详细总结一下机制'),(120,300))
        self.assertEqual(answer_budget('完整逐条总结，不要省略'),(200,540))

    def test_source_excerpt_keeps_complete_relevant_blocks(self):
        blocks=['开头简介'*30,'装饰背景'*40,'正面举盾免疫伤害，绕后可以攻击'*8,
            '残血进入二阶段，注意躲开震地'*8,'无关历史'*40]
        result,cut=source_excerpt('\n'.join(blocks),'详细总结Boss机制',500)
        self.assertTrue(cut)
        self.assertIn('举盾免疫',result)
        self.assertIn('二阶段',result)
        self.assertNotIn('无关历史',result)

    def test_detailed_source_bypasses_short_chat_and_gallery_and_clears_originals(self):
        async def run():
            cfg={**bot.settings(),'_reserve_extra_call':lambda:True}
            target={'_id':'detail','_uid':'a','at_bot':True,'_time':time.time(),
                '_learned_style':{'ending_habits':[{'form':'（'}]},
                '_expressions':['绷不住了'],
                'text':'https://example.com 详细总结一下机制'}
            captures=[]
            async def pages(urls,limit):
                self.assertEqual(limit,18000)
                return [{'status':'read','body':'机制说明'*3000,'title':'Boss'}]
            async def generate(cfg,prompt,schema,images=None):
                self.assertEqual(cfg['max_prompt_chars'],20000)
                self.assertIn('source_pages',prompt)
                self.assertGreater(prompt.count('机制说明'),700)
                self.assertLessEqual(len(prompt),20000)
                self.assertNotIn('media_choices',prompt)
                self.assertIn('group_language_observations',prompt)
                self.assertIn('绷不住了',prompt)
                captures.append(schema)
                self.assertEqual(json.loads(schema.read_text(encoding='utf-8'))['properties']['reply_to']['const'],'detail')
                self.assertEqual(json.loads(schema.read_text(encoding='utf-8'))['properties']['parts']['items']['maxLength'],120)
                return {'reply_to':'detail','parts':['正面打不动，绕到背后：'+'说明'*40,'掉血会换阶段：'+'打法'*40]}
            with patch.object(bot,'read_pages',pages),patch.object(bot,'generate_json',generate), \
                    patch.object(bot.EmojiLibrary,'contact_sheet',side_effect=AssertionError('Detailed answer must not select a sticker')):
                result=await bot.generate_for_message(None,cfg,[],'被@',target)
            self.assertGreater(len(str(result)),120)
            self.assertEqual(len(result.chunks),2)
            self.assertFalse(result.segments)
            self.assertTrue(target['_ephemeral_content'])
            self.assertNotIn('_reference_pages',target)
            self.assertFalse(captures[0].exists())
        asyncio.run(run())

    def test_empty_intro_gets_one_repair_and_not_a_sticker(self):
        async def run():
            calls=[]
            async def generate(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'detail','parts':['我给你捋个精简版：'] if len(calls)==1 else ['正面举盾时免伤，绕到背后打它']}
            with tempfile.TemporaryDirectory() as root:
                result=await answer({**bot.settings(),'_reserve_extra_call':lambda:True},{'_id':'detail','text':'详细总结'},
                    [{'status':'read','body':'Boss facts'}],[],generate,root)
            self.assertEqual(len(calls),2)
            self.assertIn('绕到背后',str(result))
        asyncio.run(run())

    def test_oversized_answer_is_rewritten_not_sliced(self):
        async def run():
            calls=[]
            async def generate(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'detail','parts':['说明'*200] if len(calls)==1 else
                    ['别对着盾硬打，绕背','它残血会换阶段，留好闪避（']}
            with tempfile.TemporaryDirectory() as root:
                result=await answer({**bot.settings(),'_reserve_extra_call':lambda:True},{'_id':'detail','text':'详细总结机制'},
                    [{'status':'read','body':'Boss facts'}],[],generate,root)
            self.assertEqual(len(calls),2)
            self.assertEqual(result.chunks,['别对着盾硬打，绕背','它残血会换阶段，留好闪避（'])
            self.assertLessEqual(sum(map(len,result.chunks)),300)
        asyncio.run(run())

    def test_short_summary_bad_format_is_repaired_once(self):
        async def run():
            calls=[]
            async def generate(cfg,prompt,schema,images=None):
                calls.append(prompt)
                spec=json.loads(schema.read_text(encoding='utf-8'))
                self.assertEqual(spec['properties']['text']['maxLength'],120)
                return {'reply_to':'short','conversation':'direct','should_reply':True,
                    'text':'多余文字'*80 if len(calls)==1 else '别正面打盾，绕背打',
                    'media_kind':'none','media_id':''}
            with patch.object(bot,'generate_json',generate):
                result=await bot.generate({**bot.settings(),'_reserve_extra_call':lambda:True},[],'被@',
                    {'_id':'short','at_bot':True,'text':'总结一下'})
            self.assertEqual(len(calls),2)
            self.assertIn('绕背打',str(result))
            self.assertNotIn('刚没接上',str(result))
        asyncio.run(run())

    def test_quote_reopens_original_link_and_new_group_member_feedback_is_considered(self):
        now=time.time()
        reply={'_id':'reply','is_bot':True,'_time':now-21,'_reply_to_uid':'a','text':'机制不少，给你捋个精简版：',
            '_reply_to_id':'original','_reply_to_text':'https://example.com 详细总结一下机制'}
        target={'_id':'b','_uid':'b','_time':now,'text':'何意味'}
        self.assertEqual(audience(target,[reply],'bot'),'possibly_direct')
        self.assertTrue(target['_feedback_on_answer'])
        resolved=resolve({'_reply_id':'reply','text':'你怎么不说'},[reply])
        self.assertEqual(resolved['links'],['https://example.com'])
        self.assertEqual(resolved['source_id'],'original')

    def test_complete_detailed_reply_is_delivered_in_two_messages(self):
        async def run():
            cfg={**bot.settings(),'poll_seconds':0,'memory_enabled':False,'style_learning_enabled':False,'passive_probability':0}
            original_sleep=asyncio.sleep
            sent=[];polls=0
            message={'message_id':'detail','time':time.time(),'user_id':123,'sender':{'nickname':'tester'},
                'message':[{'type':'at','data':{'qq':169901502}},
                    {'type':'text','data':{'text':'https://example.com 详细总结一下机制'}}]}
            async def api(client,env,action,params=None):
                nonlocal polls
                if action=='get_login_info':return {'user_id':169901502}
                if action=='get_group_info':return {}
                if action=='get_group_msg_history':
                    polls+=1
                    if polls>25:raise AssertionError('Detailed reply was not delivered')
                    message['time']=__import__('time').time()
                    return {'messages':[] if params.get('count')==100 else [message]}
                if action=='send_group_msg':
                    sent.append(params['message'])
                    if len(sent)==2:(bot.RUNTIME/'STOP').touch()
                    return {'message_id':str(100+len(sent))}
                raise AssertionError(action)
            async def pages(urls,limit):return [{'status':'read','body':'真实机制说明'}]
            async def generate(cfg,prompt,schema,images=None):
                return {'reply_to':'detail','parts':['正面盾打不动：'+'事实'*40,'阶段与应对：'+'技巧'*40]}
            async def tick(seconds):await original_sleep(0);await original_sleep(0)
            with tempfile.TemporaryDirectory() as root,patch.object(bot,'RUNTIME',Path(root)), \
                    patch.object(bot,'settings',return_value=cfg),patch.object(bot,'api',api), \
                    patch.object(bot,'read_pages',pages),patch.object(bot,'generate_json',generate), \
                    patch.object(bot.asyncio,'sleep',tick):
                await bot.run()
                self.assertEqual(len(sent),2)
                self.assertTrue(all(any(segment['type']=='text' for segment in msg) for msg in sent))
                self.assertFalse(any(segment['type']=='image' for msg in sent for segment in msg))
                state=json.loads((Path(root)/'state.json').read_text(encoding='utf-8'))
                self.assertEqual(len(state['reply_times']),2)
        asyncio.run(run())

    def test_saved_reply_pointer_can_reopen_source_without_restoring_old_analysis(self):
        async def run():
            from conversation_focus import ConversationFocus
            with tempfile.TemporaryDirectory() as root:
                focus=ConversationFocus(Path(root)/'focus.json')
                focus.remember({'_id':'old-reply','_time':time.time(),'is_bot':True,'text':'正文已发出'},
                    {'_id':'source','_uid':'a','text':'https://example.com 详细总结一下机制','_ephemeral_content':True})
                target={'_id':'followup','_reply_id':'old-reply','_uid':'a','text':'你怎么不说'}
                result=await bot.classify_audience(None,None,target,[],169901502,focus)
                self.assertEqual(result,'direct')
                reference=resolve(target,[])
                self.assertEqual(reference['links'],['https://example.com'])
                self.assertEqual(reference['source_id'],'source')
                self.assertNotIn('正文已发出',target['_quoted_message']['text'])
        asyncio.run(run())


if __name__=='__main__':unittest.main()
