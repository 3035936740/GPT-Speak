import asyncio
from pathlib import Path
import tempfile
import time
import unittest
import bot
from dialogue import audience, passive_candidate, fresh_reply, quote_needed, personal_claim


class DialogueTests(unittest.TestCase):
    def test_other_conversation_and_bot_followup(self):
        now = time.time()
        human = {'_id': '1', '_uid': 'a', '_time': now, 'text': '代购贵吗', 'is_bot': False}
        mine = {'_id': '2', '_uid': 'bot', '_time': now, 'text': '在呢', 'is_bot': True}
        self.assertEqual(audience({'text': '你钱多', '_time': now + 1}, [human], 'bot'), 'others')
        self.assertEqual(audience({'text': '你还在吗', '_time': now + 1}, [mine], 'bot'), 'possibly_direct')
        self.assertFalse(passive_candidate({'text': '@a 主人', '_at_ids': ['a']}, [], 'bot'))
        self.assertFalse(passive_candidate({'text': '[图片，未读取内容]'}, [], 'bot'))
        self.assertEqual(audience({'_reply_id': '2'}, [mine], 'bot'), 'direct')
        self.assertEqual(audience({'_reply_id': '1'}, [human], 'bot'), 'others')
        self.assertFalse(passive_candidate({'_id': '3', '_time': now + 1, 'text': '这很正常'}, [human], 'bot'))
        self.assertTrue(passive_candidate({'_id': '3', '_time': now + 1, 'text': '这很正常（', 'at_bot': True}, [human], 'bot'))

    def test_late_reply_and_personal_claim(self):
        target = {'_id': '1', '_time': 100, 'text': 'GLM重复输出', '_audience': 'open'}
        changed = [{'_time': 130 + i, 'text': text} for i, text in enumerate(['吃什么', '早饭次烧饼', '好饿'])]
        self.assertFalse(fresh_reply(target, changed, 150))
        self.assertTrue(fresh_reply({**target, 'at_bot': True}, changed, 150))
        self.assertTrue(quote_needed(target, changed, 150))
        self.assertTrue(personal_claim('那可能是，最近有点累', '是不是上班导致的'))
        self.assertFalse(personal_claim('没上班啊', '是不是上班导致的'))

    def test_polling_and_text_continue_during_image_job(self):
        asyncio.run(self.scenario())

    def test_shutdown_preserves_inflight_once(self):
        asyncio.run(self.scenario(stop_early=True))

    def test_market_only_reply_calls_native_forward_api(self):
        asyncio.run(self.scenario(native_reply=True))

    async def scenario(self, stop_early=False, native_reply=False):
        originals = (bot.RUNTIME, bot.settings, bot.api, bot.generate_for_message, bot.asyncio.sleep)
        normal = bot.settings()
        sent, polls = [], 0
        image_ready = asyncio.Event()
        stamp = time.time()
        def raw(key, text):
            return {'message_id': key, 'time': time.time(), 'user_id': 'a', 'sender': {'nickname': 'human'},
                'message': [{'type': 'at', 'data': {'qq': '169901502'}}, {'type': 'text', 'data': {'text': text}}]}
        async def fake_api(client, env, action, params=None):
            nonlocal polls
            if action == 'get_login_info':
                return {'user_id': 169901502}
            if action == 'get_group_info':
                return {}
            if action == 'get_group_msg_history':
                if params.get('count') == 100:
                    return {'messages': []}
                polls += 1
                if polls >= 5:
                    image_ready.set()
                if polls > 15:
                    (bot.RUNTIME / 'STOP').touch()
                    raise AssertionError('scheduler stalled')
                return {'messages': [raw('1', '画个海边插图')] + ([raw('2', '你还在吗')] if polls >= 2 else [])}
            if action == 'send_group_msg':
                sent.append(params['message'])
                if any(s['type'] == 'image' for s in params['message']):
                    (bot.RUNTIME / 'STOP').touch()
                return {'message_id': str(100 + len(sent))}
            if action == 'forward_group_single_msg':
                self.assertEqual(params, {'group_id': 985569438, 'message_id': '123'})
                sent.append([{'type': '_native_market_face'}])
                (bot.RUNTIME / 'STOP').touch()
                return None
            raise AssertionError(action)
        async def fake_generate(client, cfg, history, trigger, target, as_image=False):
            if as_image:
                await image_ready.wait()
                path = bot.RUNTIME / 'test.png'
                path.write_bytes(b'fake-image-for-mocked-transport')
                return path
            return bot.ReplyText('', [{'type': '_native_market_face', 'data': {'message_id': '123'}}]) if native_reply else '在呢'
        async def tick(seconds):
            await originals[-1](0)
            await originals[-1](0)
            if stop_early:
                (bot.RUNTIME / 'STOP').touch()
        with tempfile.TemporaryDirectory() as folder:
            try:
                bot.RUNTIME = Path(folder)
                bot.settings = lambda: {**normal, 'poll_seconds': 0, 'mention_cooldown_seconds': 0,
                    'image_cooldown_seconds': 0, 'passive_probability': 0, 'style_learning_enabled': False}
                bot.api = fake_api
                bot.generate_for_message = fake_generate
                bot.asyncio.sleep = tick
                await bot.run()
                if stop_early:
                    entries = __import__('json').loads((bot.RUNTIME / 'pending-mentions.json').read_text(encoding='utf-8'))
                    self.assertEqual([entry['_id'] for entry in entries], ['1'])
                    return
                if native_reply:
                    self.assertTrue(any(s['type'] == '_native_market_face' for msg in sent for s in msg))
                    self.assertFalse(any(s['type'] in {'mface', 'text'} and s.get('data', {}).get('text') == '[妈妈来了]' for msg in sent for s in msg))
                    state = __import__('json').loads((bot.RUNTIME / 'state.json').read_text(encoding='utf-8'))
                    self.assertEqual(len(state['reply_times']), 1)
                    return
                text_index = next(i for i, message in enumerate(sent) if any(s.get('data', {}).get('text') == '在呢' for s in message))
                image_index = next(i for i, message in enumerate(sent) if any(s['type'] == 'image' for s in message))
                self.assertLess(text_index, image_index)
                self.assertGreaterEqual(polls, 5)
                self.assertTrue((bot.RUNTIME / 'message-seen.json').exists())
            finally:
                bot.RUNTIME, bot.settings, bot.api, bot.generate_for_message, bot.asyncio.sleep = originals


if __name__ == '__main__':
    unittest.main()
