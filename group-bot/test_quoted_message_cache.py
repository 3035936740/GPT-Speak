import asyncio
import unittest
from quoted_message_cache import QuotedMessageCache


class Bot:
    def __init__(self, account='1'):
        self.self_id, self.calls = account, 0
    async def call_api(self, action, **params):
        self.calls += 1
        await asyncio.sleep(0)
        return {'message_id': params['message_id'], 'message': [{'type': 'text', 'data': {'text': 'quoted'}}]}


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_fetch_and_isolation(self):
        cache, bot = QuotedMessageCache(), Bot()
        results = await asyncio.gather(*(cache.get(bot, {'message_id': 7}) for _ in range(8)))
        self.assertEqual(bot.calls, 1)
        results[0]['message'].clear()
        self.assertTrue((await cache.get(bot, {'message_id': '7'}))['message'])
        other = Bot('2')
        await cache.get(other, {'message_id': 7})
        self.assertEqual(other.calls, 1)
        self.assertFalse(cache.locks)

    async def test_seed_and_expiry(self):
        now = [0]
        cache, bot = QuotedMessageCache(clock=lambda: now[0], ttl=100), Bot()
        cache.remember('1', {'message_id': 7, 'message': ['first', 'second']})
        self.assertEqual((await cache.get(bot, {'message_id': 7}))['message'], ['first', 'second'])
        self.assertEqual(bot.calls, 0)
        now[0] = 100
        await cache.get(bot, {'message_id': 7})
        self.assertEqual(bot.calls, 1)

    async def test_failure_cooldown_and_bound(self):
        now = [0]
        cache, bot = QuotedMessageCache(clock=lambda: now[0], capacity=2), Bot()
        async def fail(action, **params):
            bot.calls += 1
            raise ValueError('not found')
        bot.call_api = fail
        for _ in range(4):
            with self.assertRaises(ValueError):
                await cache.get(bot, {'message_id': 7})
        self.assertEqual(bot.calls, 1)
        now[0] = 60
        with self.assertRaises(ValueError):
            await cache.get(bot, {'message_id': 7})
        self.assertEqual(bot.calls, 2)
        for identity in (8, 9, 10):
            cache.remember('1', {'message_id': identity, 'message': []})
        self.assertEqual(len(cache.entries), 2)

    async def test_recall_does_not_serve_stale_message(self):
        cache, bot = QuotedMessageCache(), Bot()
        await cache.get(bot, {'message_id': 7})
        cache.recall('1', 7)
        with self.assertRaisesRegex(ValueError, 'recalled'):
            await cache.get(bot, {'message_id': 7})
        self.assertEqual(bot.calls, 1)
