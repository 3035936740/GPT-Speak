import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
import chat_language as language
from console_config import DEFAULTS,Config,atomic
from role_analysis import analyze,checked


class ShorthandTests(unittest.TestCase):
    def target(self):
        stamp=time.time()
        quote={'_id':'quoted','_uid':'99999','is_bot':True,'sender':'白色大肥龙','text':'不要随便喊别人老婆啊','_time':stamp-300}
        return {'_id':'current','_uid':'11111','_self_id':'99999','sender':'群友','at_bot':True,'_audience':'direct',
            'text':'[引用回复]@99999 csncsn','_reply_id':'quoted','_quoted_message':quote,'_time':stamp}

    def test_default_and_config_switch(self):
        self.assertTrue(DEFAULTS[language.KEY]);self.assertTrue(language.context({},self.target()))
        target=self.target();language.bind({},target)
        self.assertIn('_shorthand_context',target)
        language.bind({language.KEY:False},target)
        self.assertNotIn('_shorthand_context',target);self.assertFalse(target['_pinyin_guess_enabled'])
        self.assertEqual(language.context({language.KEY:False},target),{})
        with tempfile.TemporaryDirectory() as folder:
            example=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
            atomic(Path(folder)/'settings.json',example)
            Config(folder).set(language.KEY,'off');self.assertFalse(Config(folder).load()[language.KEY])

    def test_repetition_and_unlisted_abbreviations_have_no_hardcoded_gloss(self):
        for token in ['csncsn','nbnhhsh','xswl','awsl','zzwzzw','ｃｓｎｃｓｎ']:
            rows=language.candidates(token)
            self.assertEqual(len(rows),1)
            self.assertNotIn('possible_reading',rows[0]);self.assertNotIn('speech_act',rows[0])
        self.assertEqual(language.candidates('csncsn')[0]['repeated_unit'],'csn')
        self.assertEqual(language.candidates('zzwzzw')[0]['repeated_unit'],'zzw')

    def test_english_code_commands_urls_and_confirmed_names_are_not_guessed(self):
        for text in ['hello','this is my test','/csncsn 5','def csncsn(x):','https://example.com/csncsn','API cfg配置怎么写','some_csncsn_name']:
            self.assertEqual(language.candidates(text),[],text)
        self.assertEqual(language.candidates('gpt',names=['GPT']),[])

    def test_repeated_slang_has_quote_context_without_fixed_translation(self):
        target=self.target();before=deepcopy(target);packet=language.context({},target)
        self.assertTrue(packet['compact_turn']);self.assertEqual(packet['candidates'][0]['normalized'],'csncsn')
        prompt=bot.build_prompt(bot.settings(),[target['_quoted_message']],'at',target)
        self.assertIn('不要随便喊别人老婆',prompt);self.assertIn('shorthand_context',prompt)
        self.assertIn('猜测',prompt);self.assertNotIn('草死',prompt)
        self.assertEqual(before['text'],target['text'])

    def test_disabled_feature_has_no_prompt_hints(self):
        cfg={**bot.settings(),language.KEY:False};target=self.target()
        prompt=bot.build_prompt(cfg,[],'at',target)
        self.assertNotIn('shorthand_context',prompt);self.assertNotIn('拼音缩写：',prompt)
        self.assertNotIn('shorthand_reading',prompt)

    def test_guesses_cannot_claim_unseen_evidence_or_become_definite_facts(self):
        packet=language.context({},self.target())
        reading={'surface':'csncsn','possible_reading':'可能是粗口或朋友互损，具体不确定','speech_act':'hostile_or_vulgar',
            'confidence':'medium','evidence_message_ids':['current','quoted']}
        accepted=language.checked_reading(reading,packet,{'current','quoted'})
        self.assertTrue(accepted['tentative'])
        self.assertIsNone(language.checked_reading({**reading,'surface':'someone_else'},packet,{'current','quoted'}))
        self.assertIsNone(language.checked_reading({**reading,'evidence_message_ids':['not_visible']},packet,{'current','quoted'}))
        self.assertIsNone(language.checked_reading({**reading,'confidence':[]},packet,{'current','quoted'}))
        no_evidence=language.checked_reading({**reading,'evidence_message_ids':[],'confidence':'high'},packet,{'current'})
        self.assertEqual(no_evidence['confidence'],'low')

    def test_wrong_affirmation_is_flagged_but_reaction_or_honest_uncertainty_is_allowed(self):
        target=self.target();language.bind({},target)
        self.assertEqual(language.quality_issue('嗯，这回是我认认真真接住',target),'unsupported_shorthand_affirmation')
        self.assertEqual(language.quality_issue('刚才那句提醒得认真了',target),'')
        for text in ['突然这么凶啊（','这串我没看懂','我没读懂了','这句并不算正常接话','你这串是什么意思','……']:
            self.assertEqual(language.quality_issue(text,target),'')
        target['_pinyin_guess_enabled']=False
        self.assertEqual(language.quality_issue('这回我认真接住了',target),'')

    def test_context_model_decodes_in_existing_call_and_passes_tentative_result_to_reply(self):
        async def run(folder):
            target=self.target();calls=[];reserved=[]
            async def model(cfg,prompt,schema,images=None):
                calls.append(schema.name)
                if schema.name.startswith('context'):
                    self.assertIn('shorthand_context',prompt);self.assertIn('不要随便喊别人老婆',prompt)
                    self.assertIn('shorthand_reading',json.loads(schema.read_text(encoding='utf-8'))['properties'])
                    return {'addressed_to_bot':True,'intent':'reaction','needs_web_search':False,'search_query':'','confidence':.8,
                        'reference_message_id':'quoted','note':'语气需结合前文',
                        'shorthand_reading':{'surface':'csncsn','possible_reading':'可能是不雅的挑衅或互损',
                            'speech_act':'hostile_or_vulgar','confidence':'medium','evidence_message_ids':['current','quoted']}}
                return {'mood':'playful','reply_tone':'playful','confidence':.7,'note':'未确定是否敌意'}
            cfg={**bot.settings(),'multi_role_enabled':True,'_reserve_extra_call':lambda:reserved.append(1) or True}
            roles=await analyze(cfg,[target['_quoted_message']],target,[],model,folder)
            self.assertEqual(len(calls),2);self.assertEqual(len(reserved),2)
            self.assertTrue(roles['context']['shorthand_reading']['tentative'])
            prompt=bot.build_prompt(cfg,[],'at',{**target,'_role_analysis':roles})
            self.assertIn('可能是不雅的挑衅或互损',prompt)
            self.assertIn('"tentative":true',prompt)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_bad_affirmation_uses_existing_budgeted_quality_repair(self):
        async def run(folder):
            calls=[];reserved=[];target=self.target()
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'current','conversation':'direct','should_reply':True,'action':'chat',
                    'text':'嗯，这回是我认认真真接住' if len(calls)==1 else '突然这么凶啊（',
                    'text_parts':[],'media_kind':'none','media_id':'','answer_complete':True}
            cfg={**bot.settings(),'_reserve_extra_call':lambda:reserved.append(1) or True}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                result=await bot.generate(cfg,[target['_quoted_message']],'at',target)
            self.assertEqual(str(result),'突然这么凶啊（');self.assertEqual(len(calls),2);self.assertEqual(len(reserved),1)
            self.assertIn('unsupported_shorthand_affirmation',calls[1])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_no_budget_suppresses_wrong_affirmation_without_extra_call(self):
        async def run(folder):
            async def model(*args,**kwargs):
                return {'reply_to':'current','conversation':'direct','should_reply':True,'action':'chat',
                    'text':'嗯，这回是我认认真真接住','text_parts':[],'media_kind':'none','media_id':'','answer_complete':True}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',side_effect=model) as call:
                result=await bot.generate({**bot.settings(),'_reserve_extra_call':lambda:False},[],'at',self.target())
            self.assertFalse(str(result));self.assertEqual(call.call_count,1)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_disabled_analysis_does_not_request_or_accept_guesses(self):
        async def run(folder):
            async def model(cfg,prompt,schema,images=None):
                self.assertNotIn('拼音缩写：',prompt)
                spec=json.loads(schema.read_text(encoding='utf-8'))
                self.assertNotIn('shorthand_reading',spec['properties'])
                if schema.name.startswith('context'):
                    return {'addressed_to_bot':True,'intent':'reaction','needs_web_search':False,'search_query':'','confidence':.8,
                        'reference_message_id':'quoted','note':'不额外解码','shorthand_reading':{'surface':'csncsn'}}
                return {'mood':'playful','reply_tone':'playful','confidence':.7,'note':''}
            cfg={**bot.settings(),language.KEY:False,'multi_role_enabled':True,'_reserve_extra_call':lambda:True}
            target=self.target();roles=await analyze(cfg,[target['_quoted_message']],target,[],model,folder)
            self.assertNotIn('shorthand_reading',roles['context'])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_candidate_sticker_ids_remain_parseable(self):
        target={**self.target(),'_media_choices':[{'id':'sticker1','kind':'sticker'}]}
        prompt=bot.build_sticker_prompt(bot.settings(),[],target)
        self.assertEqual(bot.prompt_media_ids(prompt),['sticker1'])


if __name__=='__main__':unittest.main()
