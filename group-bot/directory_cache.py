"""Coalesce read-only QQ directory requests across conversation workers."""
import asyncio
import json
import time

DIRECTORY_TTL = 86400
DEFAULTS = {key: DIRECTORY_TTL for key in (
    'group_directory_refresh_seconds', 'friend_directory_refresh_seconds',
    'member_directory_refresh_seconds', 'avatar_cache_seconds')}


def cache_seconds(cfg, action):
    key = {'get_friend_list': 'friend_directory_refresh_seconds',
           'get_group_member_list': 'member_directory_refresh_seconds'}.get(
               action, 'group_directory_refresh_seconds')
    return cfg.get(key, DIRECTORY_TTL)
ACTIONS = {'get_login_info', 'get_group_list', 'get_friend_list',
           'get_group_info', 'get_group_member_list'}


class DirectoryCache:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.values = {}
        self.failures = {}
        self.lock = asyncio.Lock()

    def clear(self, scope='all', group=None):
        def matches(key):
            action, params = key[1], json.loads(key[2])
            if scope == 'members':
                return action == 'get_group_member_list' and str(params.get('group_id')) == str(group)
            if scope == 'friends':
                return action == 'get_friend_list'
            if scope == 'groups':
                return action in {'get_group_list', 'get_group_info'}
            return True
        for entries in (self.values, self.failures):
            for key in list(entries):
                if matches(key):
                    del entries[key]

    async def call(self, bot, action, params, ttl=DIRECTORY_TTL):
        if action not in ACTIONS:
            return await bot.call_api(action, **params)
        key = (str(bot.self_id), action, json.dumps(params, sort_keys=True))
        async with self.lock:
            failed = self.failures.get(key)
            if failed and self.clock() - failed[0] < 60:
                raise failed[1]
            cached = self.values.get(key)
            if cached and self.clock() - cached[0] < ttl:
                return cached[1]
            try:
                data = await bot.call_api(action, **params)
            except Exception as exc:
                self.failures[key] = (self.clock(), exc)
                raise
            self.failures.pop(key, None)
            self.values[key] = (self.clock(), data)
            return data


def invalidate(root, scope, group=None):
    import httpx
    from console_config import Config
    from nonebot_bridge import headers
    cfg = Config(root).load()
    response = httpx.post(cfg['nonebot_bridge_url'].rstrip('/') + '/gpt-speak/directory-refresh',
                          headers=headers(cfg), json={'scope': scope, 'group': group}, timeout=10)
    response.raise_for_status()
