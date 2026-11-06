"""Integration checks using real NoneBot and a synthetic reverse OneBot peer."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

import httpx
from websockets.asyncio.client import connect
from nonebot_gateway import Inbox
from nonebot_bridge import poll, request


class InboxTests(unittest.TestCase):
    def test_baseline_overflow_and_reconnect(self):
        inbox = Inbox(2)
        inbox.reset(True)
        epoch = inbox.epoch
        inbox.append({'message_id': 1})
        self.assertEqual(inbox.read('', 0)['messages'], [])
        for value in (2, 3):
            inbox.append({'message_id': value})
        with self.assertRaises(OverflowError):
            inbox.read(epoch, 0)
        inbox.reset(False)
        with self.assertRaises(ConnectionError):
            inbox.read(epoch, 1)
        inbox.reset(True)
        self.assertEqual(inbox.read(epoch, 1)['messages'], [])


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_reverse_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            cfg.update(nonebot_port=port, nonebot_listen_host='127.0.0.1',
                       nonebot_bridge_url=f'http://127.0.0.1:{port}',
                       nonebot_access_token='integration-secret', expected_self_id='3755716291')
            (root / 'settings.json').write_text(json.dumps(cfg), encoding='utf-8')
            with (root / 'gateway.log').open('wb') as log:
                from debug_console import WindowsJob, PosixJob
                job = WindowsJob() if os.name == 'nt' else PosixJob()
                process = subprocess.Popen([sys.executable, '-X', 'utf8',
                    str(Path(__file__).with_name('nonebot_gateway.py')), '--root', str(root)],
                    stdout=log, stderr=log, **({} if os.name == 'nt' else {'start_new_session': True}))
                job.add(process)
                try:
                    async with httpx.AsyncClient(trust_env=False) as client:
                        url = cfg['nonebot_bridge_url']
                        headers = {'Authorization': 'Bearer integration-secret'}
                        for _ in range(100):
                            if process.poll() is not None:
                                self.fail((root / 'gateway.log').read_text(encoding='utf-8'))
                            try:
                                result = await client.get(url + '/gpt-speak/health', headers=headers)
                                if result.status_code == 200:
                                    self.assertEqual(result.json()['adapter'],'nonebot.adapters.onebot.v11.adapter.Adapter')
                                    self.assertEqual(result.json()['plugin'],'nonebot_plugin_gpt_speak')
                                    break
                            except httpx.ConnectError:
                                pass
                            await asyncio.sleep(.1)
                        else:
                            self.fail('Gateway did not start')
                        self.assertEqual((await client.get(url + '/gpt-speak/health')).status_code, 401)
                        with self.assertRaises(httpx.HTTPStatusError):
                            await poll(client, cfg)
                        async with connect(f'ws://127.0.0.1:{port}/onebot/v11/ws',
                            additional_headers={**headers, 'X-Self-ID': '3755716291', 'X-Client-Role': 'Universal'}) as ws:
                            await ws.send(json.dumps({'time': 1, 'self_id': 3755716291,
                                'post_type': 'meta_event', 'meta_event_type': 'lifecycle', 'sub_type': 'connect'}))
                            await asyncio.sleep(.1)
                            self.assertEqual(await poll(client, cfg), [])
                            async with httpx.AsyncClient(trust_env=False) as private:
                                self.assertEqual(await poll(private, cfg), [])
                                event = {'time': 2, 'self_id': 3755716291, 'post_type': 'message',
                                    'message_type': 'group', 'sub_type': 'normal', 'group_id': 12345,
                                    'message_id': 71, 'user_id': 123456, 'font': 0, 'raw_message': 'hello',
                                    'sender': {'user_id': 123456, 'nickname': 'test'},
                                    'message': [{'type': 'text', 'data': {'text': 'hello'}}]}
                                await ws.send(json.dumps(event))
                                await asyncio.sleep(.1)
                                self.assertEqual((await poll(client, cfg))[0]['message_id'], 71)
                                self.assertEqual((await poll(private, cfg))[0]['message_id'], 71)
                                self.assertEqual(await poll(client, cfg), [])
                            # The official adapter strips @/reply from its working
                            # message. The plugin must preserve original segments.
                            rich = {**event, 'message_id':73, 'raw_message':'reply with media', 'message':[
                                {'type':'reply','data':{'id':'70'}},
                                {'type':'at','data':{'qq':'3755716291'}},
                                {'type':'text','data':{'text':'look at these'}},
                                {'type':'image','data':{'file':'first.png'}},
                                {'type':'image','data':{'file':'second.png'}},
                                {'type':'file','data':{'file_id':'document','name':'notes.txt'}}]}
                            await ws.send(json.dumps(rich))
                            frame=json.loads(await asyncio.wait_for(ws.recv(),2))
                            self.assertEqual(frame['action'],'get_msg')
                            await ws.send(json.dumps({'status':'ok','retcode':0,'echo':frame['echo'], 'data':{
                                'time':1,'message_type':'group','message_id':70,'real_id':70,
                                'sender':{'user_id':3755716291},'message':[{'type':'text','data':{'text':'previous'}}]}}))
                            await asyncio.sleep(.1)
                            received=(await poll(client,cfg))[0]
                            self.assertEqual(received['message'],rich['message'])
                            self.assertTrue(received['to_me'])
                            # Self-sent reports use the official fallback Event model.
                            await ws.send(json.dumps({**event,'message_id':74,'post_type':'message_sent','user_id':3755716291}))
                            await asyncio.sleep(.1)
                            self.assertEqual((await poll(client,cfg))[0]['post_type'],'message_sent')
                            await ws.send(json.dumps({**event,'message_id':75,'self_id':111111}))
                            await asyncio.sleep(.1)
                            self.assertEqual(await poll(client,cfg),[])
                            async def reply_apis():
                                frames = [json.loads(await ws.recv()) for _ in range(2)]
                                for frame in reversed(frames):
                                    await ws.send(json.dumps({'status': 'ok', 'retcode': 0,
                                        'echo': frame['echo'], 'data': {'action': frame['action']}}))
                            peer = asyncio.create_task(reply_apis())
                            values = await asyncio.gather(request(client, cfg, 'get_login_info', {}),
                                                          request(client, cfg, 'send_group_msg', {'group_id': 12345, 'message': event['message']}))
                            await peer
                            self.assertEqual([v['action'] for v in values], ['get_login_info', 'send_group_msg'])
                            self.assertEqual(await request(client, cfg, 'get_group_msg_history', {}), {'messages': []})
                            async def reject_api():
                                frame = json.loads(await ws.recv())
                                await ws.send(json.dumps({'status': 'failed', 'retcode': 200,
                                    'echo': frame['echo'], 'message': 'busy'}))
                            rejected = asyncio.create_task(reject_api())
                            from napcat_errors import NapCatAPIError
                            with self.assertRaises(NapCatAPIError):
                                await request(client, cfg, 'send_group_msg', {})
                            await rejected
                            heartbeat={'time':3,'self_id':3755716291,'post_type':'meta_event',
                                'meta_event_type':'heartbeat','interval':30000,'status':{'online':False,'good':True}}
                            await ws.send(json.dumps(heartbeat))
                            await asyncio.sleep(.1)
                            with self.assertRaises(httpx.HTTPStatusError):await poll(client,cfg)
                            heartbeat['status']['online']=True
                            await ws.send(json.dumps(heartbeat))
                            await asyncio.sleep(.1)
                            self.assertEqual(await poll(client,cfg),[])
                        await asyncio.sleep(.1)
                        with self.assertRaises(httpx.HTTPStatusError):
                            await poll(client, cfg)
                        async with connect(f'ws://127.0.0.1:{port}/onebot/v11/ws',
                            additional_headers={**headers, 'X-Self-ID': '3755716291'}) as ws:
                            await asyncio.sleep(.1)
                            # Events accumulated before a consumer returns are a baseline.
                            await ws.send(json.dumps(event))
                            await asyncio.sleep(.1)
                            self.assertEqual(await poll(client, cfg), [])
                            event.update(message_type='private', sub_type='friend', message_id=72)
                            await ws.send(json.dumps(event))
                            await asyncio.sleep(.1)
                            self.assertEqual((await poll(client, cfg))[0]['message_id'], 72)
                finally:
                    process.terminate()
                    process.wait(timeout=10)
                    job.close()
                    await asyncio.sleep(.1)


if __name__ == '__main__':
    unittest.main()
