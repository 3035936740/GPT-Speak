import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

import bot
from conversation_focus import ConversationFocus
from dialogue import audience, bot_name_mentioned, direct_candidates, reply_lane
from emoji_library import ReplyText


class FollowupTests(unittest.TestCase):
    def test_comprehension_feedback_a_minute_later_is_a_candidate_without_at(self):
        now=time.time()
        reply={'_id':'reply','_uid':'bot','is_bot':True,'_time':now-106,
            'text':'焰魔正面举盾时免伤','_reply_to_uid':'a','_reply_to_text':'总结一下'}
        target={'_id':'feedback','_uid':'a','_time':now,'text':'什么几把玩意，看不懂啊'}
        self.assertEqual(audience(target,[reply],'bot'),'possibly_direct')
        self.assertTrue(target['_feedback_on_answer'])
        self.assertEqual(direct_candidates([{**target,'_audience':'possibly_direct'}])[0],
            {**target,'_audience':'possibly_direct'})
        from reply_behavior import preferences
        from unittest.mock import patch
        with patch('reply_behavior.random.random',return_value=0):
            style=preferences(bot.settings(),True,target)
        self.assertFalse(style['prefer_reaction'])
        self.assertFalse(style['allow_no_words_reaction'])
        for words in ['没懂','太绕了，讲人话','答非所问','不是这个意思']:
            self.assertEqual(audience({**target,'text':words},[reply],'bot'),'possibly_direct')
        self.assertEqual(audience({**target,'_uid':'b'},[reply],'bot'),'open')
        self.assertEqual(audience({**target,'_at_ids':['b']},[reply],'bot'),'others')
        self.assertEqual(audience({**target,'_time':now+20},[reply],'bot'),'open')
        switched={'_id':'other-thread','_uid':'a','_time':now-30,'text':'@b 这个怎么装',
            '_audience':'others'}
        self.assertEqual(audience(target,[reply,switched],'bot'),'open')

    def test_contextual_questions_and_names_do_not_depend_on_an_at(self):
        now = time.time()
        mine = {'_id': 'bot1', '_uid': 'bot', 'is_bot': True, '_time': now,
            'text': '可以用ffmpeg合并', '_reply_to_uid': 'a', '_reply_to_text': '帧合并怎么做'}
        def item(text, uid='a', ats=()):
            return {'_id': 'new', '_uid': uid, '_time': now + 20, 'text': text, '_at_ids': ats}
        self.assertEqual(audience(item('那你怎么看'), [mine], 'bot'), 'possibly_direct')
        self.assertEqual(audience(item('那具体怎么弄'), [mine], 'bot'), 'possibly_direct')
        self.assertEqual(audience(item('yonagi 帮我看看'), [], 'bot'), 'direct')
        self.assertEqual(audience(item('EP你还在吗'), [], 'bot'), 'direct')
        for text in ['yonagi说话','yonagi是什么','叫yonagi出来','Ethereal Paradise你是？','EP说句话']:
            named=item(text)
            self.assertEqual(audience(named,[], 'bot'),'direct')
            self.assertTrue(named['_named_bot'])
        self.assertFalse(bot_name_mentioned('epic游戏挺好玩'))
        self.assertEqual(audience({**item('@b yonagi说话'), '_at_ids':['b']}, [], 'bot'),'direct')
        human = {'_id': 'h1', '_uid': 'b', '_time': now + 10, 'text': '我去吃饭'}
        self.assertEqual(audience(item('那具体怎么弄'), [mine, human], 'bot'), 'possibly_direct')
        self.assertEqual(audience(item('那具体怎么弄', uid='b'), [mine, human], 'bot'), 'open')
        self.assertEqual(audience(item('那你怎么看', ats=['b']), [mine], 'bot'), 'others')
        to_human = {'_id': 'h2', '_uid': 'a', '_time': now + 10, 'text': '@b 水管修好了吗', '_audience': 'others'}
        answer = {'_id': 'h3', '_uid': 'b', '_time': now + 15, 'text': '没有，接头不知道怎么装'}
        self.assertEqual(audience(item('那具体怎么弄'), [mine, to_human, answer], 'bot'), 'others')

    def test_ambiguous_drawing_request_does_not_generate_before_confirmation(self):
        cfg = {'image_generation_enabled': True}
        request = {'_image_request': True, '_audience': 'possibly_direct'}
        self.assertEqual(reply_lane(request, cfg), 'text')
        self.assertEqual(reply_lane({**request, '_audience': 'direct'}, cfg), 'image')

    def test_focus_survives_restart_without_storing_all_chat_history(self):
        with tempfile.TemporaryDirectory() as folder:
            focus = ConversationFocus(Path(folder) / 'focus.json')
            reply = {'_id': 'b1', '_time': time.time(), 'text': '用ffmpeg', 'is_bot': True}
            focus.remember(reply, {'_id': 'h1', '_uid': 'a', 'text': '怎么合并'})
            restored = ConversationFocus(focus.path)
            snapshot = {'_id': 'b1', '_time': reply['_time'], 'text': reply['text'], 'is_bot': True}
            restored.annotate(snapshot)
            self.assertEqual(snapshot['_reply_to_uid'], 'a')
            self.assertTrue(restored.owns('b1'))

    def test_related_question_is_processed_with_zero_passive_probability(self):
        asyncio.run(self.scenario())

    def test_complaint_is_processed_with_zero_passive_probability(self):
        asyncio.run(self.scenario('什么几把玩意，看不懂啊',106))

    def test_name_without_space_is_forced_with_zero_passive_probability(self):
        asyncio.run(self.scenario('yonagi说话',10,'direct'))

    async def scenario(self, words='那具体怎么弄', age=10, expected_audience='possibly_direct'):
        originals = (bot.RUNTIME, bot.settings, bot.api, bot.generate_for_message, bot.asyncio.sleep)
        cfg = bot.settings()
        now, polls = time.time(), 0
        own = {'message_id': 'b1', 'time': now - age, 'user_id': 'bot',
            'sender': {'nickname': 'yonagi'}, 'message': [{'type': 'text', 'data': {'text': '试试ffmpeg'}}]}
        followup = {'message_id': 'h2', 'time': now, 'user_id': 'a',
            'sender': {'nickname': '甲'}, 'message': [{'type': 'text', 'data': {'text': words}}]}
        sent = []
        async def fake_api(client, env, action, params=None):
            nonlocal polls
            if action == 'get_login_info': return {'user_id': 'bot'}
            if action == 'get_group_info': return {}
            if action == 'get_group_msg_history':
                polls += 1
                if polls > 20: raise AssertionError('Followup never scheduled')
                followup['time']=__import__('time').time()
                return {'messages': [own] if params.get('count') == 100 else [own, followup]}
            if action == 'send_group_msg':
                sent.append(params['message'])
                (bot.RUNTIME / 'STOP').touch()
                return {'message_id': 'b2'}
            raise AssertionError(action)
        async def fake_generate(client, cfg, context, trigger, target, as_image=False):
            self.assertEqual(target['_audience'], expected_audience)
            self.assertFalse(target['at_bot']) # A lexical name never fabricates a native QQ @.
            return ReplyText('用这个命令（', route='direct')
        async def tick(seconds):
            await originals[-1](0)
            await originals[-1](0)
        with tempfile.TemporaryDirectory() as folder:
            try:
                bot.RUNTIME = Path(folder)
                focus=ConversationFocus(bot.RUNTIME/'conversation-focus.json')
                focus.remember({'_id':'b1','_time':now-age,'text':'试试ffmpeg','is_bot':True},
                    {'_id':'previous-request','_uid':'a','text':'怎么合并'})
                bot.settings = lambda: {**cfg, 'poll_seconds': 0, 'passive_probability': 0,
                    'passive_min_messages': 999, 'passive_cooldown_seconds': 100000,
                    'mention_cooldown_seconds': 100000, 'memory_enabled': False, 'expected_self_id': 'bot'}
                bot.api, bot.generate_for_message, bot.asyncio.sleep = fake_api, fake_generate, tick
                await asyncio.wait_for(bot.run(),10)
                self.assertEqual(len(sent), 1)
                self.assertIn('用这个命令', sent[0][0]['data']['text'])
                state = json.loads((bot.RUNTIME / 'state.json').read_text(encoding='utf-8'))
                self.assertEqual(len(state['reply_times']), 1)
            finally:
                bot.RUNTIME, bot.settings, bot.api, bot.generate_for_message, bot.asyncio.sleep = originals

    def test_semantic_reply_planner_can_accept_or_reject_a_possible_followup(self):
        async def scenario():
            original = bot.generate_json
            target = {'_id': 'x', '_audience': 'possibly_direct', 'text': '那怎么弄', 'at_bot': False}
            cfg = bot.settings()
            try:
                async def other(*args, **kwargs):
                    return {'reply_to': 'x', 'conversation': 'others', 'should_reply': False, 'text': '', 'media_kind': 'none', 'media_id': ''}
                bot.generate_json = other
                self.assertFalse(await bot.generate(cfg, [], '续问判断', target))
                async def mine(*args, **kwargs):
                    return {'reply_to': 'x', 'conversation': 'direct', 'should_reply': True, 'text': '可以这样（', 'media_kind': 'none', 'media_id': ''}
                bot.generate_json = mine
                answer = await bot.generate(cfg, [], '续问判断', target)
                self.assertEqual(answer.route, 'direct')
            finally:
                bot.generate_json = original
        asyncio.run(scenario())

    def test_old_quote_is_resolved_and_other_group_content_is_not_used(self):
        async def scenario():
            original = bot.api
            with tempfile.TemporaryDirectory() as folder:
                focus = ConversationFocus(Path(folder) / 'focus.json')
                item = {'_id': 'h1', '_reply_id': 'old-bot', 'text': '为什么这样', '_time': time.time()}
                try:
                    async def ours(*args, **kwargs):
                        return {'message_id': 'old-bot', 'group_id': 985569438, 'user_id': 'bot',
                            'sender': {'nickname': 'yonagi'}, 'time': time.time() - 3600,
                            'message': [{'type': 'text', 'data': {'text': '旧答案'}}]}
                    bot.api = ours
                    self.assertEqual(await bot.classify_audience(None, None, item, [], 'bot', focus), 'direct')
                    self.assertEqual(item['_quoted_message']['text'], '旧答案')
                    async def elsewhere(*args, **kwargs):
                        return {'message_id': 'old-bot', 'group_id': 111, 'user_id': 'bot'}
                    bot.api = elsewhere
                    other = {'_reply_id': 'old-bot', 'text': '为什么这样'}
                    self.assertEqual(await bot.classify_audience(None, None, other, [], 'bot', focus), 'others')
                    self.assertNotIn('_quoted_message', other)
                finally:
                    bot.api = original
        asyncio.run(scenario())

    def test_startup_waits_for_qq_and_preserves_message_budget(self):
        async def scenario():
            originals = bot.RUNTIME, bot.api, bot.asyncio.sleep
            attempts = 0
            async def fake_api(client, env, action, params=None):
                nonlocal attempts
                if action == 'get_login_info':
                    attempts += 1
                    if attempts == 1:
                        raise OSError('QQ offline')
                    return {'user_id': 169901502}
                if action == 'get_group_list':
                    return [] if attempts==2 else [{'group_id':985569438}]
                return {'group_id': 985569438}
            async def tick(seconds):
                saved = json.loads((bot.RUNTIME / 'state.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['waiting_for_qq'],attempts==1)
                self.assertEqual(saved['not_in_group'],attempts==2)
                self.assertEqual(saved['model_calls_today'], 7)
                await originals[2](0)
            with tempfile.TemporaryDirectory() as folder:
                try:
                    bot.RUNTIME = Path(folder)
                    (bot.RUNTIME / 'state.json').write_text(json.dumps({'model_calls_today': 7, 'reply_times': [123]}), encoding='utf-8')
                    bot.api, bot.asyncio.sleep = fake_api, tick
                    login, group = await bot.wait_for_qq(None, None, bot.settings())
                    self.assertEqual(login['user_id'], 169901502)
                    self.assertEqual(group['group_id'], 985569438)
                    self.assertEqual(attempts, 3)
                finally:
                    bot.RUNTIME, bot.api, bot.asyncio.sleep = originals
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
