import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from console_config import runtime
from gpt_speak_mcp import SpeakBridge,create_server


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100],enabled=True)
        (self.root/'settings.json').write_text(json.dumps(cfg),encoding='utf-8')
        self.folder=runtime(self.root,100);self.folder.mkdir()
        self.bridge=SpeakBridge(self.root)

    def tearDown(self):self.temp.cleanup()

    def test_reads_local_chat_with_ids_time_dedup_and_flow_filter(self):
        (self.folder/'recent-chat.jsonl').write_text(json.dumps({'_id':'1','_time':10,'text':'old','sender':'A'})+'\n',encoding='utf-8')
        rows=[{'message_id':'1','time':10,'kind':'received','sender':'A','text':'new'},
              {'time':11,'kind':'stage','text':'Authorization: supersecret'}]
        (self.folder/'debug-events.jsonl').write_text('\n'.join(map(json.dumps,rows))+'\n{incomplete',encoding='utf-8')
        with patch('httpx.AsyncClient.post',side_effect=AssertionError('No NapCat calls')):
            data=self.bridge.messages('100')['messages']
            self.assertEqual(len(data),1);self.assertEqual(data[0]['text'],'new')
            self.assertEqual(data[0]['time'],10);self.assertEqual(data[0]['sender'],'A')
            flow=self.bridge.messages('100',kind='flow')['messages']
            self.assertNotIn('supersecret',str(flow))

    def test_permissions_and_input_limits_are_checked_on_each_call(self):
        for operation in [lambda:self.bridge.status(200),lambda:self.bridge.messages('200'),
                          lambda:self.bridge.tasks(200),lambda:self.bridge.send_group(200,'x','a'*32)]:
            with self.assertRaises(ValueError):operation()
        for n in [0,201]:
            with self.assertRaises(ValueError):self.bridge.messages('100',limit=n)
        with self.assertRaises(ValueError):self.bridge.send_group(100,'hello','')
        with self.assertRaises(ValueError):self.bridge.messages('../settings.json',scope='private')

    def test_send_only_queues_once_and_reuses_existing_worker_permissions(self):
        pid=os.getpid()
        (self.folder/'state.json').write_text(json.dumps({'pid':pid,'online':True}),encoding='utf-8')
        (self.folder/'manual-worker.json').write_text(json.dumps({'pid':pid,'version':3}),encoding='utf-8')
        with patch('httpx.AsyncClient.post',side_effect=AssertionError('No direct send')):
            first=self.bridge.send_group(100,'hello','a'*32)
            second=self.bridge.send_group(100,'hello','a'*32)
            self.assertEqual(first,second);self.assertEqual(first['status'],'queued')
            self.assertEqual(len(list((self.folder/'manual-outbox').glob('*.json'))),1)
            with self.assertRaises(ValueError):self.bridge.send_group(100,'different','a'*32)
        (self.folder/'state.json').write_text('{"online":false}')
        with self.assertRaises(ValueError):self.bridge.send_group(100,'hello','b'*32)

    def test_status_never_returns_connection_credentials(self):
        data=self.bridge.status(100)
        self.assertNotIn('napcat_token',data);self.assertNotIn('webui_key',data)
        self.assertFalse(data['worker_running'])


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_check_uses_gpt_speak_stdio_with_legacy_url_ignored(self):
        from napcat_reconnect import probe_mcp
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
            cfg.update(group_id=100,chat_group_whitelist=[100],napcat_mcp_url='http://invalid.example:9',napcat_mcp_token='unused')
            (root/'settings.json').write_text(json.dumps(cfg),encoding='utf-8')
            self.assertTrue(await probe_mcp(root,100))
            self.assertTrue(await probe_mcp(root,0,private=True))

    async def test_tool_catalog_has_read_write_annotations_without_napcat_tools(self):
        tools=await create_server(Path('.')).list_tools()
        names={tool.name for tool in tools}
        self.assertIn('read_messages',names);self.assertIn('queue_group_message',names)
        self.assertNotIn('get_group_msg_history',names)
        for tool in tools:
            self.assertEqual(tool.annotations.readOnlyHint,not tool.name.startswith('queue_'))


if __name__=='__main__':unittest.main()
