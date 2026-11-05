import asyncio
import json
import unittest
from unittest.mock import patch
from websockets.asyncio.server import serve
from napcat_connection import NapCatHTTPClient
from napcat_events import poll


class EventsTests(unittest.IsolatedAsyncioTestCase):
    async def test_events_are_received_while_no_api_request_is_in_flight(self):
        connected=asyncio.Event()
        emit=asyncio.Event()
        sent=asyncio.Event()
        received_paths=[]
        async def handler(socket):
            received_paths.append(socket.request.path)
            self.assertEqual(socket.request.headers['Authorization'],'Bearer test')
            connected.set()
            await emit.wait()
            for message in ({'message_id':1,'message_type':'group','group_id':10},
                            {'message_id':2,'message_type':'private','user_id':20}):
                await socket.send(json.dumps({'post_type':'message','self_id':123,**message}))
            sent.set()
            await socket.wait_closed()
        async with serve(handler,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1]
            cfg={'expected_self_id':123,'napcat_connection_mode':'ws','napcat_host':'127.0.0.1',
                 'napcat_port':port,'napcat_path':'/api','napcat_token':'test'}
            async with NapCatHTTPClient() as client:
                self.assertEqual(await poll(client,cfg,{}),[])
                await connected.wait()
                emit.set()
                await sent.wait()
                for _ in range(100):
                    if len(client._onebot_event_inbox.rows)==2:break
                    await asyncio.sleep(.001)
                messages=await poll(client,cfg,{})
                self.assertEqual([row['message_id'] for row in messages],[1,2])
                self.assertEqual(await poll(client,cfg,{}),[])
                self.assertEqual(received_paths,['/'])
            self.assertIsNone(client._onebot_event_inbox.reader)

    async def test_http_keeps_existing_polling_behavior(self):
        async with NapCatHTTPClient() as client:
            self.assertIsNone(await poll(client,{'napcat_connection_mode':'http'},{}))

    async def test_connected_but_silent_stream_periodically_allows_history_fallback(self):
        async def handler(socket):
            await socket.wait_closed()
        async with serve(handler,'127.0.0.1',0) as server:
            cfg={'expected_self_id':123,'napcat_connection_mode':'ws','napcat_host':'127.0.0.1',
                 'napcat_port':server.sockets[0].getsockname()[1]}
            async with NapCatHTTPClient() as client:
                await poll(client,cfg,{})
                inbox=client._onebot_event_inbox
                start=inbox.last_probe_at
                with patch('napcat_events.time.monotonic',return_value=start+61):
                    self.assertIsNone(await poll(client,cfg,{}))
                    self.assertEqual(await poll(client,cfg,{}),[])
                with patch('napcat_events.time.monotonic',return_value=start+122):
                    self.assertIsNone(await poll(client,cfg,{}))
                    inbox.rows.append({'message_id':99})
                    self.assertEqual(await poll(client,cfg,{}),[{'message_id':99}])

    async def test_disconnect_discards_pending_events_and_reports_failure(self):
        disconnected=asyncio.Event()
        async def handler(socket):
            await socket.send(json.dumps({'post_type':'message','self_id':123,'message_id':1}))
            await socket.close()
            disconnected.set()
        async with serve(handler,'127.0.0.1',0) as server:
            cfg={'expected_self_id':123,'napcat_connection_mode':'ws','napcat_host':'127.0.0.1',
                 'napcat_port':server.sockets[0].getsockname()[1]}
            async with NapCatHTTPClient() as client:
                await poll(client,cfg,{})
                await disconnected.wait()
                await client._onebot_event_inbox.reader
                with self.assertRaises(ConnectionError):await poll(client,cfg,{})
                self.assertEqual(len(client._onebot_event_inbox.rows),0)
