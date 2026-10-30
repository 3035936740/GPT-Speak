import asyncio
from pathlib import Path
import tempfile
import time
import unittest

import bot
from emoji_library import ReplyText


class ImageIntentTests(unittest.TestCase):
    def test_avatar_request_and_common_drawing_forms_take_image_lane(self):
        for text in ['@169901502 画你头像设定的表情包,line风格的q版无语表情',
                '画张无语表情', '给我画一个头像插图', '做个LINE无语表情包',
                '整张无语贴纸', '能不能帮我画头像', '把这个图画成LINE风格']:
            with self.subTest(text=text):
                self.assertTrue(bot.wants_image({'at_bot': True, 'text': text}))
        target = {'_uid': '3035936740', '_self_id': '169901502', 'text': '画你头像设定的表情包'}
        self.assertEqual(bot.requested_avatars(target), [('169901502', 'yonagi当前QQ头像')])

    def test_discussion_capability_and_negative_requests_do_not_auto_generate(self):
        for text in ['你会画图吗', '你能生图吗', '别人给我画了一张图',
                '昨天生成的图挺好看', '不要给我画图', '别生成图片', '这个生图模型怎么样']:
            with self.subTest(text=text):
                self.assertFalse(bot.wants_image({'at_bot': True, 'text': text}))

    def test_semantic_request_returns_an_image_action_instead_of_a_fake_refusal(self):
        async def scenario():
            original = bot.generate_json
            try:
                async def fake(*args, **kwargs):
                    return {'reply_to': '1', 'conversation': 'direct', 'should_reply': True,
                        'text': '', 'action': 'generate_image', 'image_request': '根据头像设计LINE无语贴纸',
                        'media_kind': 'none', 'media_id': ''}
                bot.generate_json = fake
                result = await bot.generate(bot.settings(), [], '被@',
                    {'_id': '1', 'at_bot': True, 'text': '按你头像出一份LINE无语贴纸'})
                self.assertTrue(result)
                self.assertEqual(str(result), '')
                self.assertEqual(result.action, 'generate_image')
            finally:
                bot.generate_json = original
        asyncio.run(scenario())

    def test_semantic_image_action_reaches_image_worker_and_is_sent_once(self):
        async def scenario():
            originals = bot.RUNTIME, bot.settings, bot.api, bot.generate_for_message, bot.asyncio.sleep
            normal, calls, sent = bot.settings(), [], []
            now = time.time()
            request = {'message_id': '1', 'time': now, 'user_id': 'a', 'sender': {'nickname': '人'},
                'message': [{'type': 'at', 'data': {'qq': 169901502}},
                    {'type': 'text', 'data': {'text': '按你头像出一份LINE无语贴纸'}}]}
            polls = 0
            async def api(client, env, action, params=None):
                nonlocal polls
                if action == 'get_login_info': return {'user_id': 169901502}
                if action == 'get_group_info': return {}
                if action == 'get_group_msg_history':
                    polls += 1
                    if polls > 25:
                        (bot.RUNTIME / 'STOP').touch()
                        raise AssertionError('Image action not dispatched')
                    request['time']=__import__('time').time()
                    return {'messages': [] if params.get('count') == 100 else [request]}
                if action == 'send_group_msg':
                    sent.append(params['message'])
                    if any(s['type'] == 'image' for s in params['message']):
                        (bot.RUNTIME / 'STOP').touch()
                    return {'message_id': str(len(sent) + 100)}
                raise AssertionError(action)
            async def generate(client, cfg, context, trigger, target, as_image=False):
                calls.append(as_image)
                if not as_image:
                    return ReplyText('', route='direct', action='generate_image', image_request='LINE无语头像贴纸')
                self.assertEqual(target['_render_request'], 'LINE无语头像贴纸')
                path = bot.RUNTIME / 'test.png'
                path.write_bytes(b'image-worker-result-for-mocked-transport')
                return path
            async def tick(seconds):
                await originals[-1](0)
                await originals[-1](0)
            with tempfile.TemporaryDirectory() as folder:
                try:
                    bot.RUNTIME = Path(folder)
                    bot.settings = lambda: {**normal, 'poll_seconds': 0, 'memory_enabled': False,
                        'style_learning_enabled': False, 'passive_probability': 0, 'image_cooldown_seconds': 0}
                    bot.api, bot.generate_for_message, bot.asyncio.sleep = api, generate, tick
                    await bot.run()
                    self.assertEqual(calls, [False, True])
                    self.assertEqual(sum(s['type'] == 'image' for message in sent for s in message), 1)
                    self.assertEqual(len(sent), 1)  # internal generation stays silent; only the image is sent
                finally:
                    bot.RUNTIME, bot.settings, bot.api, bot.generate_for_message, bot.asyncio.sleep = originals
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
