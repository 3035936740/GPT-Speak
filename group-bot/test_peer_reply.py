import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot
from interaction import social_act
from peer_reply import quality_issue,scene
from style_memory import StyleMemory


class PeerReplyTests(unittest.TestCase):
    def test_social_play_is_an_act_not_a_literal_question(self):
        for text in ('敲你。','你是一个可爱people。','我傻了怎么办？','摸你一下'):
            self.assertEqual(social_act(text),'banter')
        self.assertEqual(social_act('我感冒头发晕'),'statement')
        self.assertEqual(social_act('代码中的函数括号怎么匹配'),'question')

    def test_bot_echo_is_a_repeat_chain_not_new_style_training(self):
        now=time.time();old={'_id':'b','_uid':'bot','text':'欸，怎么突然喊妈妈','_time':now-30,'is_bot':True}
        target={'_id':'h','_uid':'a','text':old['text'],'_time':now}
        hint=scene(target,[old]);self.assertTrue(hint['repeated_from_bot'])
        self.assertEqual(hint['repeat_source_ids'],['b'])
        with tempfile.TemporaryDirectory() as folder:
            memory=StyleMemory(Path(folder)/'style.json');memory.observe(target,[old])
            self.assertEqual(memory.samples,[])
            memory.observe({**target,'_id':'new','text':'好好笑'},[old])
            self.assertEqual(len(memory.samples),1)

    def test_self_explanation_and_topic_bleed_are_flagged_but_real_health_reply_is_not(self):
        target={'_social_act':'role_play','text':'主人','_peer_scene':{'current_health_mention':False}}
        self.assertEqual(quality_issue('怎么啦主人，我刚才不是接你那句妈妈嘛）',target),'defensive_self_explanation')
        self.assertEqual(quality_issue('宝宝来围观感冒人了，记得多喝水休息呀',target),'unrequested_topic_advice')
        self.assertEqual(quality_issue('哎，敲我干嘛（',{**target,'_social_act':'banter'}),'role_play_interrogation')
        self.assertEqual(quality_issue('量下体温吧',{'_social_act':'statement','_peer_scene':{'current_health_mention':True}}),'')
        self.assertEqual(quality_issue('主人叫我做什么',{'_social_act':'question'}),'')

    def test_punctuation_streak_is_observed_without_rewriting_code(self):
        now=time.time();history=[{'_id':str(n),'_time':now-20+n,'is_bot':True,'text':'接梗（'} for n in range(3)]
        result=scene({'_id':'t','_time':now,'text':'括号函数结束了吗'},history)
        self.assertEqual(result['recent_bot_bracket_streak'],3)
        self.assertTrue(result['punctuation_topic'])

    def test_peer_style_failure_gets_one_model_rewrite_without_a_preset(self):
        async def run(folder):
            calls=[];reserved=[]
            target={'_id':'t','_time':time.time(),'_uid':'3035936740','sender':'测试群友',
                'text':'主人','at_bot':True,'_audience':'direct'}
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'t','conversation':'direct','should_reply':True,'action':'chat',
                    'text':'我刚才不是接你那句妈妈嘛）' if len(calls)==1 else '叫我干活是吧',
                    'text_parts':[],'media_kind':'none','media_id':'','answer_complete':True}
            def reserve():reserved.append(True);return True
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate({**bot.settings(),'_reserve_extra_call':reserve},[],'点名',target)
            self.assertEqual(str(reply),'叫我干活是吧')
            self.assertEqual(len(calls),2);self.assertEqual(len(reserved),1)
            self.assertIn('自辩',calls[-1])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_core_persona_is_preserved_in_the_prompt(self):
        target={'_id':'t','_time':time.time(),'text':'敲你。','at_bot':True,
            '_role_analysis':{'context':{'note':'内部规划叙述不要念出'},'emotion':{'mood':'playful'},
                'vision':{'description':'实际可见的挥手小猫'}}}
        prompt=bot.build_prompt(bot.settings(),[],'点名',target)
        self.assertIn('GPT 6 Luna',prompt);self.assertIn('成年女性的成熟心智',prompt)
        self.assertIn('白色大肥龙',prompt)
        self.assertIn('群聊表达：',prompt)
        self.assertIn('不是配额',prompt)
        self.assertNotIn('内部规划叙述不要念出',prompt)
        self.assertIn('playful',prompt);self.assertIn('实际可见的挥手小猫',prompt)
        self.assertEqual(target['_role_analysis']['context']['note'],'内部规划叙述不要念出')


if __name__=='__main__':unittest.main()
