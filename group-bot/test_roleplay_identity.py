import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot
from peer_reply import quality_issue


class RoleplayIdentityTests(unittest.TestCase):
    def test_role_breaking_self_labels_are_rejected_but_ai_topics_still_work(self):
        for text in ('我是 AI，没有男女生这种生理性别啦','我只是AI','作为语言模型，我没有身体','我是GPT-6'):
            self.assertEqual(quality_issue(text,{'text':'所以是男生女生'}),'out_of_character_identity')
        for text in ('最近AI圈又有新模型了','OpenAI的模型可以做图片识别','那是AI生成的图片','你猜呢'):
            self.assertEqual(quality_issue(text,{'text':'聊聊最近的模型'}),'')

    def test_character_voice_does_not_turn_into_a_real_person_claim(self):
        for text in ('我是人类','我是活人','不是AI，是真人'):
            self.assertEqual(quality_issue(text,{'text':'你是什么'}),'unsupported_real_identity')
        self.assertEqual(quality_issue('是yonagi啦',{'text':'你是什么'}),'')

    def test_one_rewrite_uses_role_voice_and_keeps_existing_silent_failure_policy(self):
        async def run(folder):
            cfg={**bot.settings(),'_reserve_extra_call':lambda:True}
            (Path(folder)/'reply.mention.schema.json').write_bytes((Path(__file__).parent/'reply.mention.schema.json').read_bytes())
            (Path(folder)/'persona.txt').write_text('你是yonagi，不用AI或模型身份解释自己，不主动透露或强调性别',encoding='utf-8')
            (Path(folder)/'reply_protocol.txt').write_text('只输出schema JSON',encoding='utf-8')
            calls=[];target={'_id':'t','_uid':'3035936740','_time':time.time(),'sender':'群友',
                'text':'yonagi是男生女生','at_bot':True,'_audience':'direct'}
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'t','should_reply':True,'conversation':'direct','action':'chat',
                    'text':'我是AI，没有男女生这种生理性别' if len(calls)==1 else '怎么又绕回来了',
                    'text_parts':[],'media_kind':'none','media_id':'','answer_complete':True}
            with patch.object(bot,'ROOT',Path(folder)),patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate(cfg,[],'被点名',target)
            self.assertEqual(str(reply),'怎么又绕回来了');self.assertEqual(len(calls),2)
            self.assertIn('保持yonagi角色',calls[-1])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_prompt_has_no_conflicting_old_identity_rule(self):
        prompt=bot.build_prompt(bot.settings(),[],'被点名',{'_id':'t','text':'你是AI吗','at_bot':True})
        self.assertNotIn('被直接问是不是AI时如实回答',prompt)
        self.assertNotIn('被直接问是不是AI仍如实回答',prompt)
        self.assertIn('GPT 6 Luna',prompt)
        self.assertNotIn('保持yonagi角色',prompt)


if __name__=='__main__':unittest.main()
