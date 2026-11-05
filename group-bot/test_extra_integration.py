import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import httpx
import bot
from console_config import Config, atomic, runtime
from member_labels import MemberLabels
from napcat_connection import connection, request, ForwardWebSocket, NapCatHTTPClient
from owner_commands import handle, drain, parse


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        atomic(self.root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200],
            'enabled':True,'owner_commands_enabled':False,'member_labels_global_enabled':False})

    def test_global_marks_propagate_and_toggle_without_merging_rule_messages(self):
        first=MemberLabels(runtime(self.root,100));other=MemberLabels(runtime(self.root,200))
        first.mark('11111','bot','account note',['alias'])
        self.assertNotIn('11111',other.facts())
        Config(self.root).set('member_labels_global_enabled','on')
        self.assertEqual(other.facts()['11111']['label'],'bot')
        other.mark('11111','owner','updated',[],True)
        self.assertTrue(first.facts()['11111']['blocked'])
        self.assertEqual(MemberLabels(self.root/'runtime-private').facts()['11111']['manual_label'],'owner')
        Config(self.root).set('member_labels_global_enabled','off')
        self.assertEqual(first.facts()['11111']['label'],'bot')
        self.assertNotIn('11111',other.facts())

    def test_owner_commands_off_by_default_and_not_granted_by_model_labels(self):
        marks=MemberLabels(runtime(self.root,100));marks.mark('11111','owner')
        self.assertIsNone(handle(self.root,'11111','/stop','1','100'))
        Config(self.root).set('owner_commands_enabled','on')
        self.assertIsNone(handle(self.root,'22222','/stop','1','100'))
        self.assertIsNotNone(handle(self.root,'11111','/session clear','1','100'))
        self.assertIsNotNone(handle(self.root,'11111','/session clear','1','100'))
        from owner_commands import db_open
        from contextlib import closing
        with closing(db_open(self.root)) as db:self.assertEqual(db.execute('SELECT count(*) FROM commands').fetchone()[0],1)

    def test_commands_use_cli_dispatch_in_source_group_and_restore_selection(self):
        from debug_console import Console,Supervisor
        MemberLabels(runtime(self.root,200)).mark('11111','owner')
        Config(self.root).set('owner_commands_enabled','on')
        ui=Console(self.root,Supervisor(self.root,start=False));ui.group=100
        for index,command in enumerate(['/session stop','/session resume','/session clear']):
            handle(self.root,'11111',command,str(index),'200');drain(ui)
            from session_control import state
            self.assertEqual(state(runtime(self.root,200))['mode'],command.rsplit(' ',1)[1])
            self.assertEqual(state(runtime(self.root,100)),{})
            self.assertEqual(ui.group,100)
        handle(self.root,'11111','/stop','stop','200');drain(ui)
        self.assertFalse(ui.running)

    def test_custom_prefix_alias_json_values_and_revoked_permissions(self):
        cfg=Config(self.root).load();cfg.update(owner_command_prefix='y',owner_command_aliases={'概率':'config local set passive_probability'})
        self.assertEqual(parse(cfg,'y概率 0.05'),'/config local set passive_probability 0.05')
        self.assertIsNone(parse(cfg,'yes'))
        self.assertEqual(parse({**cfg,'owner_command_prefix':'！'},'！config set bot_aliases ["Ａ"]'),'/config set bot_aliases ["Ａ"]')
        from debug_console import Console,Supervisor
        MemberLabels(runtime(self.root,100)).mark('11111','owner');Config(self.root).set('owner_commands_enabled','on')
        handle(self.root,'11111','/session clear','revoked','100')
        MemberLabels(runtime(self.root,100)).mark('11111','human')
        ui=Console(self.root,Supervisor(self.root,start=False));drain(ui)
        self.assertFalse((runtime(self.root,100)/'session-control.json').exists())

    def test_connection_defaults_inherit_current_and_mcp_overrides(self):
        env={'NAPCAT_HOST':'http://old.test:3456/base','NAPCAT_TOKEN':'old'}
        self.assertEqual(connection({},env)[:2],(env['NAPCAT_HOST'],'old'))
        cfg={'napcat_connection_mode':'ws','napcat_host':'::1','napcat_port':6700,'napcat_path':'api','napcat_token':'new'}
        self.assertEqual(connection(cfg,env)[:2],('ws://[::1]:6700/api','new'))
        self.assertEqual(connection(cfg,env,mcp=True),connection(cfg,env))
        self.assertEqual(connection({**cfg,'napcat_mcp_url':'https://mcp.test:123','napcat_mcp_token':'mcp'},env,True)[:2],('https://mcp.test:123','mcp'))

    def test_cli_avatar_refresh_requests_only_current_group(self):
        from debug_console import Console,Supervisor
        ui=Console(self.root,Supervisor(self.root,start=False));ui.group=200
        Config(self.root,200).set('avatar_remote_enabled','on')
        with patch('debug_avatars.avatar',return_value=(b'png','image/png')) as avatar:
            ui.execute('/avatar refresh')
        self.assertEqual(avatar.call_count,2)
        self.assertTrue(all(call.args[1]==200 for call in avatar.call_args_list))
        self.assertTrue((runtime(self.root,200)/'avatar-refresh.json').exists())
        self.assertFalse((runtime(self.root,100)/'avatar-refresh.json').exists())


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_and_websocket_return_same_data_and_auth(self):
        def respond(req):
            self.assertEqual(req.headers['Authorization'],'Bearer secret')
            return httpx.Response(200,json={'retcode':0,'data':{'ok':True}})
        async with NapCatHTTPClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(await request(client,{'NAPCAT_HOST':'http://local.test','NAPCAT_TOKEN':'secret'},'get_login_info'),{'ok':True})
        from websockets.asyncio.server import serve
        calls=[]
        async def handler(ws):
            self.assertEqual(ws.request.headers['Authorization'],'Bearer secret')
            async for frame in ws:
                payload=json.loads(frame);calls.append(payload)
                await ws.send(json.dumps({'post_type':'message','data':'event'}))
                await ws.send(json.dumps({'retcode':0,'data':payload['params'],'echo':payload['echo']}))
        async with serve(handler,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1]
            async with NapCatHTTPClient() as client:
                env={'NAPCAT_HOST':'ws://127.0.0.1:'+str(port),'NAPCAT_TOKEN':'secret'}
                result=await asyncio.gather(request(client,env,'get_group_info',{'group_id':100}),request(client,env,'get_group_info',{'group_id':200}))
                self.assertEqual(result,[{'group_id':100},{'group_id':200}])
            self.assertEqual(len(calls),2)

    async def test_uncertain_websocket_send_is_not_replayed(self):
        socket=Mock();socket.send=AsyncMock();socket.recv=AsyncMock(side_effect=TimeoutError);socket.close=AsyncMock()
        transport=ForwardWebSocket('ws://local.test','',1);transport.socket=socket
        with self.assertRaises(TimeoutError):await transport.request('send_group_msg',{'text':'once'})
        socket.send.assert_awaited_once();socket.close.assert_not_awaited();self.assertIs(transport.socket,socket)
        await transport.close()

    async def test_timeout_keeps_socket_and_late_echo_cannot_answer_the_next_request(self):
        from websockets.asyncio.server import serve
        for external in (False,True):
            release=asyncio.Event()
            calls=[]
            async def handler(ws):
                async for frame in ws:
                    payload=json.loads(frame);calls.append(payload['action'])
                    if len(calls)==1:await release.wait()
                    await ws.send(json.dumps({'retcode':0,'data':payload['action'],'echo':payload['echo']}))
            async with serve(handler,'127.0.0.1',0) as server:
                port=server.sockets[0].getsockname()[1]
                transport=ForwardWebSocket('ws://127.0.0.1:'+str(port),'',1 if external else .05)
                try:
                    with self.assertRaises(asyncio.TimeoutError):
                        call=transport.request('send_group_msg',{'text':'only once'})
                        if external:await asyncio.wait_for(call,.05)
                        else:await call
                    socket=transport.socket
                    self.assertIsNotNone(socket)
                    release.set();transport.timeout=1
                    result=await transport.request('get_login_info',{})
                    self.assertEqual(result['data'],'get_login_info')
                    self.assertIs(transport.socket,socket)
                    self.assertEqual(calls,['send_group_msg','get_login_info'])
                finally:
                    release.set()
                    await transport.close()
