"""Reverse bridge cursors are private to each worker and reset after failure."""
import unittest
import httpx
from napcat_connection import NapCatHTTPClient
from napcat_events import poll
from nonebot_bridge import DEFAULTS


class EventsTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_reconnect_invalidates_inflight_work_without_replaying_events(self):
        cfg = {**DEFAULTS, 'nonebot_access_token': 'test'}
        replies = iter([
            {'epoch':'old','cursor':1,'messages':[]},
            {'epoch':'new','cursor':8,'messages':[]},
            {'epoch':'new','cursor':9,'messages':[{'message_id':9}]},
        ])
        async with NapCatHTTPClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=next(replies)))) as client:
            self.assertEqual(await poll(client,cfg,{}),[])
            with self.assertRaises(ConnectionError):await poll(client,cfg,{})
            self.assertEqual(await poll(client,cfg,{}),[{'message_id':9}])

    async def test_cursor_and_failure_baseline(self):
        cfg = {**DEFAULTS, 'nonebot_access_token': 'test'}
        calls = []
        def handler(request):
            self.assertEqual(request.headers['authorization'], 'Bearer test')
            calls.append(dict(request.url.params))
            if len(calls) == 3:
                return httpx.Response(503)
            return httpx.Response(200, json={'epoch': 'session', 'cursor': len(calls), 'messages': []})
        async with NapCatHTTPClient(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(await poll(client, cfg, {}), [])
            self.assertEqual(await poll(client, cfg, {}), [])
            with self.assertRaises(httpx.HTTPStatusError):
                await poll(client, cfg, {})
            self.assertEqual(await poll(client, cfg, {}), [])
        self.assertEqual(calls[0]['epoch'], '')
        self.assertEqual(calls[1]['cursor'], '1')
        self.assertEqual(calls[3]['epoch'], '')
