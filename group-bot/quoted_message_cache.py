"""Bounded business cache; the official adapter still owns all QQ transport."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import time


class QuotedMessageCache:
    def __init__(self, clock=time.monotonic, ttl=86400, cooldown=60, capacity=2048):
        self.clock, self.ttl, self.cooldown, self.capacity = clock, ttl, cooldown, capacity
        self.entries = OrderedDict()
        self.locks = {}

    def key(self, account, identity):
        return str(account), str(identity)

    def remember(self, account, data):
        if not isinstance(data, dict) or data.get('message_id') is None or 'message' not in data:
            return
        self._put(self.key(account, data['message_id']), deepcopy(data), None)

    def _put(self, key, data, error):
        self.entries[key] = (self.clock(), data, error)
        self.entries.move_to_end(key)
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)

    def recall(self, account, identity):
        self._put(self.key(account, identity), None, ValueError('Quoted message recalled'))

    async def get(self, bot, params):
        # Preserve uncommon API options instead of changing their semantics.
        if set(params) != {'message_id'}:
            return await bot.call_api('get_msg', **params)
        key = self.key(bot.self_id, params['message_id'])
        state = self.locks.setdefault(key, [asyncio.Lock(), 0])
        state[1] += 1
        lock = state[0]
        try:
            async with lock:
                entry = self.entries.get(key)
                if entry and self.clock() - entry[0] < (self.cooldown if entry[2] else self.ttl):
                    self.entries.move_to_end(key)
                    if entry[2]:
                        raise entry[2]
                    return deepcopy(entry[1])
                try:
                    data = await bot.call_api('get_msg', **params)
                except Exception as exc:
                    self._put(key, None, exc)
                    raise
                self.remember(bot.self_id, data)
                return deepcopy(data)
        finally:
            state[1] -= 1
            if not state[1]:
                self.locks.pop(key, None)
