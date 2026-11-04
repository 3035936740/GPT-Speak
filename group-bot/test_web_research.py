import asyncio
from datetime import datetime
import unittest
from unittest.mock import patch

import bot


class WebResearchTests(unittest.TestCase):
    def test_incomplete_ranking_retries_and_only_delivers_all_numbered_rows(self):
        async def scenario():
            source = 'https://example.com/official'
            calls = []
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                self.assertTrue(cfg['_research_require_live'])
                self.assertIn('打开外链中的官方报告', prompt)
                answer = '前100名的名单：' if len(calls) == 1 else '\n'.join(
                    f'{i}. 地区{i}' for i in range(1,101))
                return {'answer':answer,'source_url':source,'source_title':'榜单','published_date':''}
            cfg = {**bot.settings(), '_reserve_extra_call':lambda:True,
                   '_research_request':'你列出来前100',
                   '_research_pages':[{'status':'read','body':'这段简介只有前三名，没有完整数据。','url':source}]}
            with patch.object(bot,'generate_json',model):
                reply = await bot.researched_reply(cfg,'2026 幸福报告排名前100')
            self.assertEqual(len(calls),2)
            self.assertGreater(len(reply.chunks),1)
            self.assertTrue(all(len(part)<=450 for part in reply.chunks))
            self.assertIn('100. 地区100',reply)
            self.assertIn(source,reply)
        asyncio.run(scenario())

    def test_grounded_answer_validation(self):
        good = {'answer': 'Phigros 10月13日更了新谱，这手又得加班了（。',
            'source_url': 'https://example.com/official-update', 'source_title': 'update',
            'published_date': datetime.now().date().isoformat()}
        self.assertEqual(bot.validated_web_answer(good)['answer'],
            'Phigros 10月13日更了新谱，这手又得加班了（')
        self.assertIsNone(bot.validated_web_answer({**good, 'source_url': 'https://localhost/x'}))
        self.assertIsNone(bot.validated_web_answer({**good, 'answer': '我玩过，很难'}))

    def test_research_source_is_verified_but_not_announced_by_default(self):
        async def scenario():
            async def fake(cfg,prompt,schema,images=None):
                self.assertIn('供程序内部核验',prompt)
                return {'answer':'新谱是这几首，手要寄了（',
                    'source_url':'https://example.com/official','source_title':'official',
                    'published_date':datetime.now().date().isoformat()}
            with patch.object(bot,'generate_json',fake):
                reply=await bot.researched_reply(bot.settings(),'Phigros最近更新谱面')
            self.assertEqual(str(reply),'新谱是这几首，手要寄了（')
            self.assertNotIn('http',str(reply))
            self.assertEqual(reply.web_source['source_url'],'https://example.com/official')
        asyncio.run(scenario())

    def test_context_model_controls_search_and_query(self):
        async def scenario():
            cfg = {**bot.settings(), '_reserve_extra_call': lambda: True}
            target = {'_id':'2','_uid':'u','_self_id':'169901502','_time':datetime.now().timestamp(),
                'sender':'甲','text':'你看看','at_bot':True,'_audience':'direct','_media_choices':[],
                '_reply_style':{},'_recent_bot_replies':[]}
            context = [{'_id':'1','_uid':'u','_time':target['_time']-20,'sender':'甲',
                'text':'phigros最近更新的谱面你玩了吗','is_bot':False}]
            roles = {'context': {'addressed_to_bot':True,'intent':'request','needs_web_search':True,
                'search_query':'Phigros 2026年10月最近更新的谱面','confidence':.95,
                'reference_message_id':'1','note':'需要实时更新信息'}}
            async def researched(cfg, query):
                self.assertIn('Phigros', query)
                return bot.ReplyText('查到了（\nhttps://example.com/source')
            with patch.object(bot, 'analyze_roles', return_value=roles), \
                    patch.object(bot, 'researched_reply', researched), \
                    patch.object(bot.EmojiLibrary, 'contact_sheet', return_value=False):
                reply = await bot.generate_for_message(None, cfg, context, '被@', target)
            self.assertIn('查到了', str(reply))
            self.assertTrue(target['_ephemeral_content'])
        asyncio.run(scenario())

    def test_reply_planner_can_request_search_when_context_role_falls_back(self):
        async def scenario():
            cfg = {**bot.settings(), '_reserve_extra_call': lambda: True}
            target = {'_id':'2','_uid':'u','_self_id':'169901502','_time':datetime.now().timestamp(),
                'sender':'甲','text':'这两天又更了什么','at_bot':True,'_audience':'direct',
                '_media_choices':[],'_reply_style':{},'_recent_bot_replies':[]}
            planned = {'reply_to':'2','should_reply':True,'conversation':'direct','quote_reply':False,
                'at_message_ids':[],'text':'','text_parts':[],'reply_mode':'text','media_kind':'none',
                'media_id':'','action':'web_search','search_query':'Phigros最近更新的谱面',
                'image_request':'','media_observations':[]}
            async def planner(*args, **kwargs): return planned
            async def researched(cfg, query): return bot.ReplyText('搜到了（\nhttps://example.com/x', route='direct')
            with patch.object(bot, 'generate_json', planner), patch.object(bot, 'researched_reply', researched):
                reply = await bot.generate(cfg, [], '被@', target)
            self.assertIn('搜到了', str(reply))
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
