import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
from content_answer import answer
from failure_response import ReplyFailure, respond, fallback


class FailureResponseTests(unittest.TestCase):
    def setUp(self):
        # These tests exercise the explicitly enabled legacy error-reply option.
        original=bot.settings
        toggle=patch.object(bot,'settings',lambda:{**original(),'reply_failure_log_only':False})
        toggle.start();self.addCleanup(toggle.stop)

    def test_each_failure_uses_current_model_with_bounded_safe_context(self):
        async def scenario():
            for reason in ('web', 'image', 'image_limit', 'image_disabled', 'page_unavailable',
                    'invalid_answer', 'timeout', 'network', 'generic'):
                calls = []
                reserved = []
                def reserve():
                    reserved.append(True)
                    return True
                async def model(cfg, prompt, schema, images=None):
                    calls.append(prompt)
                    self.assertEqual(cfg['model'], 'gpt-6-luna')
                    self.assertEqual(cfg['reasoning_effort'], 'low')
                    self.assertTrue(cfg['fast_mode'])
                    self.assertEqual(cfg['_web_search_mode'], 'disabled')
                    self.assertEqual(cfg['model_timeout_seconds'], 12)
                    self.assertLessEqual(len(prompt), 2200)
                    self.assertIn('刚才那个图呢', prompt)
                    self.assertNotIn('private-secret', prompt)
                    self.assertEqual(schema.name, 'failure.schema.json')
                    return {'text': '这下卡住了（。'}
                result = await respond({**bot.settings(), '_reserve_extra_call': reserve},
                    [{'text': '刚才那个图呢', 'sender': '甲', 'is_bot': False}],
                    {'text': '再来一下', '_expressions': ['寄（']}, 'text',
                    ReplyFailure(reason), model, bot.ROOT)
                self.assertEqual(str(result), '这下卡住了（')
                self.assertEqual(result.action, 'chat')
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(reserved), 1)
            async def sensitive(cfg, prompt, schema):
                self.assertNotIn('private-secret', prompt)
                return {'text': '没接上（'}
            await respond({**bot.settings(), '_reserve_extra_call': lambda: True}, [], {}, 'text',
                RuntimeError('private-secret D:\\secret\\key.txt'), sensitive, bot.ROOT)
        asyncio.run(scenario())

    def test_recovery_failure_or_invalid_result_uses_preset_once(self):
        async def scenario():
            for bad in (RuntimeError('offline'), TimeoutError(), {}, {'text': ''},
                    {'text': 'x' * 61}, {'text': '报错 API token'}, {'text': '一句\n一句'},
                    {'text': 'https://example.com/private'}, {'text': 1}):
                calls = []
                async def model(*args):
                    calls.append(True)
                    if isinstance(bad, Exception):
                        raise bad
                    return bad
                result = await respond({**bot.settings(), '_reserve_extra_call': lambda: True},
                    [], {'text': '帮我看看'}, 'web', ReplyFailure('web'), model, bot.ROOT)
                self.assertEqual(str(result), fallback('web', ReplyFailure('web')))
                self.assertEqual(len(calls), 1)
        asyncio.run(scenario())

    def test_exhausted_budget_does_not_attempt_an_unaccounted_call(self):
        async def scenario():
            async def forbidden(*args):
                raise AssertionError('Model must not run past the budget')
            result = await respond({**bot.settings(), '_reserve_extra_call': lambda: False},
                [], {}, 'image', RuntimeError('failed'), forbidden, bot.ROOT)
            self.assertEqual(str(result), '寄，图没出来（')
        asyncio.run(scenario())

    def test_shutdown_cancellation_does_not_become_a_chat_reply(self):
        async def scenario():
            async def cancelled(*args):
                raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await respond({**bot.settings(), '_reserve_extra_call': lambda: True},
                    [], {}, 'text', TimeoutError(), cancelled, bot.ROOT)
        asyncio.run(scenario())

    def test_recovery_deadline_is_bounded_even_if_generator_hangs(self):
        async def scenario():
            calls = []
            async def hung(*args):
                calls.append(True)
                await asyncio.Event().wait()
            start = time.monotonic()
            result = await respond({**bot.settings(), 'model_timeout_seconds': .01,
                '_reserve_extra_call': lambda: True}, [], {}, 'text', TimeoutError(), hung, bot.ROOT)
            self.assertEqual(str(result), '卡住了（')
            self.assertEqual(len(calls), 1)
            self.assertLess(time.monotonic() - start, 2)
        asyncio.run(scenario())

    def test_detailed_failures_use_the_shared_error_route(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as root:
                async def invalid(*args):
                    return {'parts': []}
                for pages, reason in (([], 'page_unavailable'),
                        ([{'status': 'read', 'body': '正文'}], 'invalid_answer')):
                    with self.assertRaises(ReplyFailure) as raised:
                        await answer(bot.settings(), {'text': '详细总结'}, pages, [], invalid, root)
                    self.assertEqual(raised.exception.reason, reason)
        asyncio.run(scenario())

    def test_web_failure_generates_a_contextual_reply(self):
        async def scenario():
            roles = {'context': {'needs_web_search': True, 'search_query': 'Phigros更新',
                'confidence': 1, 'addressed_to_bot': True}}
            calls = []
            async def failed(*args):
                raise RuntimeError('no grounded result')
            async def model(cfg, prompt, schema, images=None):
                calls.append(schema.name)
                self.assertIn('Phigros', prompt)
                return {'text': '新谱这次没翻着（'}
            with patch.object(bot, 'analyze_roles', return_value=roles), \
                    patch.object(bot, 'researched_reply', failed), patch.object(bot, 'generate_json', model):
                result = await bot.generate_for_message(None,
                    {**bot.settings(), '_reserve_extra_call': lambda: True}, [], '被@',
                    {'text': 'Phigros最近更新啥', 'at_bot': True, '_audience': 'direct'})
            self.assertEqual(str(result), '新谱这次没翻着（')
            self.assertEqual(calls, ['failure.schema.json'])
        asyncio.run(scenario())

    def test_text_and_image_failures_keep_polling_without_restarting_task(self):
        for kind in ('text', 'image', 'image_limit', 'image_semantic'):
            with self.subTest(kind=kind):
                asyncio.run(self.scheduler_scenario(kind))

    async def scheduler_scenario(self, kind):
        cfg = {**bot.settings(), 'poll_seconds': 0, 'memory_enabled': False,
            'style_learning_enabled': False, 'passive_probability': 0,
            'mention_cooldown_seconds': 0, 'image_cooldown_seconds': 0,
            'max_images_per_hour': 0 if kind == 'image_limit' else 15}
        original_sleep = asyncio.sleep
        polls, original_calls, recovery_calls = 0, 0, 0
        ready = asyncio.Event()
        sent = []
        raw = {'message_id': 1, 'time': time.time(), 'user_id': 123,
            'sender': {'nickname': '甲'}, 'message': [{'type': 'at', 'data': {'qq': 169901502}},
            {'type': 'text', 'data': {'text': '你在吗' if kind == 'text' else
                '按你头像出一份LINE无语贴纸' if kind == 'image_semantic' else '画个猫表情包'}}]}
        async def api(client, env, action, params=None):
            nonlocal polls
            if action == 'get_login_info': return {'user_id': 169901502}
            if action == 'get_group_info': return {}
            if action == 'get_group_msg_history':
                if params.get('count') == 100: return {'messages': []}
                polls += 1
                if polls >= 4: ready.set()
                if polls > 20:
                    (bot.RUNTIME / 'STOP').touch()
                    raise AssertionError('Recovery stalled')
                raw['time']=__import__('time').time()
                return {'messages': [raw]}
            if action == 'send_group_msg':
                sent.append(params['message'])
                (bot.RUNTIME / 'STOP').touch()
                return {'message_id': 100}
            raise AssertionError(action)
        async def original(*args, **kwargs):
            nonlocal original_calls
            original_calls += 1
            if kind == 'image_semantic' and not kwargs.get('as_image'):
                return bot.ReplyText('', route='direct', action='generate_image', image_request='LINE贴纸')
            raise TimeoutError()
        async def recovery(*args, **kwargs):
            nonlocal recovery_calls
            recovery_calls += 1
            await ready.wait()
            return {'text': '这下卡住了（'}
        async def tick(seconds):
            await original_sleep(0)
            await original_sleep(0)
        with tempfile.TemporaryDirectory() as root, patch.object(bot, 'RUNTIME', Path(root)), \
                patch.object(bot, 'settings', return_value=cfg), patch.object(bot, 'api', api), \
                patch.object(bot, 'generate_for_message', original), \
                patch.object(bot, 'generate_json', recovery), patch.object(asyncio, 'sleep', tick):
            await bot.run()
            self.assertEqual(original_calls, 0 if kind == 'image_limit' else 2 if kind == 'image_semantic' else 1)
            self.assertEqual(recovery_calls, 1)
            self.assertEqual(len(sent), 1)
            self.assertTrue(any(s.get('data', {}).get('text') == '这下卡住了（' for s in sent[0]))
            self.assertFalse(any(s['type'] == 'image' for s in sent[0]))
            self.assertGreaterEqual(polls, 4)
            state = json.loads((bot.RUNTIME / 'state.json').read_text(encoding='utf-8'))
            self.assertEqual(state['model_calls_today'], 1 if kind == 'image_limit' else
                3 if kind == 'image_semantic' else 2)


if __name__ == '__main__':
    unittest.main()
