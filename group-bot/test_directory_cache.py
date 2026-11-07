import asyncio
import unittest
from directory_cache import DirectoryCache


class Bot:
    self_id = '123'
    def __init__(self):
        self.calls = []
    async def call_api(self, action, **params):
        self.calls.append((action, params))
        await asyncio.sleep(0)
        return len(self.calls)


class DirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_clear(self):
        cache, bot = DirectoryCache(), Bot()
        for group in (1, 2):
            await cache.call(bot, 'get_group_member_list', {'group_id': group})
        await cache.call(bot, 'get_friend_list', {})
        cache.clear('members', 1)
        for group in (1, 2):
            await cache.call(bot, 'get_group_member_list', {'group_id': group})
        await cache.call(bot, 'get_friend_list', {})
        self.assertEqual(len(bot.calls), 4)

    async def test_failure_cooldown(self):
        now = [0]
        cache, bot = DirectoryCache(lambda: now[0]), Bot()
        async def fail(action, **params):
            bot.calls.append(action)
            raise ValueError('unavailable')
        bot.call_api = fail
        for _ in range(4):
            with self.assertRaises(ValueError):
                await cache.call(bot, 'get_group_list', {})
        self.assertEqual(len(bot.calls), 1)
        now[0] = 60
        with self.assertRaises(ValueError):
            await cache.call(bot, 'get_group_list', {})
        self.assertEqual(len(bot.calls), 2)

    async def test_coalesced_daily_and_manual(self):
        now = [0]
        cache = DirectoryCache(lambda: now[0])
        bot = Bot()
        await asyncio.gather(*(cache.call(bot, 'get_group_list', {}) for _ in range(8)))
        self.assertEqual(len(bot.calls), 1)
        now[0] = 86399
        await cache.call(bot, 'get_group_list', {})
        self.assertEqual(len(bot.calls), 1)
        now[0] = 86400
        await cache.call(bot, 'get_group_list', {})
        self.assertEqual(len(bot.calls), 2)
        cache.clear()
        await cache.call(bot, 'get_group_list', {})
        self.assertEqual(len(bot.calls), 3)

    async def test_sends_uncached_and_groups_separate(self):
        cache, bot = DirectoryCache(), Bot()
        for group in (1, 2, 1):
            await cache.call(bot, 'get_group_member_list', {'group_id': group})
        self.assertEqual(len(bot.calls), 2)
        for _ in range(2):
            await cache.call(bot, 'send_group_msg', {'group_id': 1, 'message': 'x'})
        self.assertEqual(len(bot.calls), 4)

    async def test_configurable_ttl_applies_to_existing_cache(self):
        from directory_cache import DEFAULTS, cache_seconds
        self.assertTrue(all(value == 86400 for value in DEFAULTS.values()))
        self.assertEqual(cache_seconds({'friend_directory_refresh_seconds': 90}, 'get_friend_list'), 90)
        now = [0]
        cache, bot = DirectoryCache(lambda: now[0]), Bot()
        await cache.call(bot, 'get_friend_list', {}, 86400)
        now[0] = 120
        await cache.call(bot, 'get_friend_list', {}, 90)
        self.assertEqual(len(bot.calls), 2)
