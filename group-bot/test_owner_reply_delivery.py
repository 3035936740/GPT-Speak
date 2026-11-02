from contextlib import closing
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch

import bot
from console_config import Config,atomic,runtime
from debug_console import Console,Supervisor
from member_labels import MemberLabels
from owner_commands import handle,drain,db_open
from owner_reply_delivery import deliver,dispatch,finish,redact


class OwnerResultTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        atomic(self.root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200],
            'enabled':True,'owner_commands_enabled':True,'private_reply_enabled':True})
        MemberLabels(runtime(self.root,200)).mark('11111','owner')
        self.ui=Console(self.root,Supervisor(self.root,start=False));self.ui.group=100

    def queue(self,command='/usage',identity='request'):
        text='5 hour token -\n1周 已用74%，剩余26%\n输入113006（缓存20776），输出18526 token'
        self.assertIn('bot指令',handle(self.root,'11111',command,identity,'200'))
        with patch('account_usage.snapshot',return_value={}) as snapshot,patch('account_usage.format_text',return_value=text):
            drain(self.ui)
            if command=='/usage':snapshot.assert_called_once_with(self.root,200,True)
        return '200:11111:'+identity,text

    def send(self,identity,request=None):
        request=request or AsyncMock(return_value={'message_id':500})
        with closing(db_open(self.root)) as db,db:db.execute("UPDATE replies SET status='sending' WHERE id=?",(identity,))
        with patch('napcat_connection.request',request):asyncio.run(deliver(self.root,identity))
        return request

    def status(self,identity):
        with closing(db_open(self.root)) as db:return db.execute('SELECT status,sent_ids,error FROM replies WHERE id=?',(identity,)).fetchone()

    def test_usage_result_is_actual_cli_output_scoped_to_origin(self):
        identity,text=self.queue();request=self.send(identity)
        args=request.call_args.args
        self.assertEqual(args[2],'send_group_msg');self.assertEqual(args[3]['group_id'],200)
        body=args[3]['message'][0]['data']['text'];self.assertIn(text,body);self.assertIn('bot指令 /usage 已执行',body)
        self.assertEqual(self.ui.group,100);self.assertEqual(self.status(identity)[0],'sent')

    def test_dispatch_once_and_restart_does_not_resend_sent_result(self):
        identity,_=self.queue();request=AsyncMock(return_value={'message_id':501})
        with patch('napcat_connection.request',request):
            dispatch(self.ui);self.ui._bot_reply_future.result(timeout=3);dispatch(self.ui);finish(self.ui)
        self.assertEqual(request.call_count,1);self.assertEqual(self.status(identity)[0],'sent')

    def test_uncertain_result_is_not_retried_or_replayed(self):
        identity,_=self.queue();request=AsyncMock(side_effect=TimeoutError())
        self.send(identity,request)
        self.assertEqual(self.status(identity)[0],'uncertain')
        with patch('napcat_connection.request',request):dispatch(self.ui)
        self.assertEqual(request.call_count,1)

    def test_revoke_block_disable_and_receive_only_cancel_delivery(self):
        for number,change in enumerate(['revoke','disabled','receive_only']):
            Config(self.root).set('owner_commands_enabled','on');Config(self.root).set('receive_only','off')
            MemberLabels(runtime(self.root,200)).mark('11111','owner')
            identity,_=self.queue(identity=str(number))
            if change=='revoke':MemberLabels(runtime(self.root,200)).mark('11111','human')
            elif change=='disabled':Config(self.root).set('owner_commands_enabled','off')
            else:Config(self.root,200).set('receive_only','on')
            request=self.send(identity);request.assert_not_called();self.assertEqual(self.status(identity)[0],'cancelled')
            Config(self.root,200).set('receive_only','off')

    def test_session_stop_and_console_stop_can_return_results(self):
        for index,command in enumerate(['/session stop','/stop']):
            identity,_=self.queue(command,str(index))
            request=self.send(identity);self.assertEqual(request.call_count,1)
        self.assertFalse(self.ui.running)

    def test_console_shutdown_flushes_its_own_stop_result(self):
        identity,_=self.queue('/stop')
        request=AsyncMock(return_value={'message_id':502})
        with patch('napcat_connection.request',request):finish(self.ui)
        self.assertEqual(request.call_count,1);self.assertEqual(self.status(identity)[0],'sent')

    def test_partial_multi_message_delivery_is_never_restarted(self):
        identity,_=self.queue()
        with closing(db_open(self.root)) as db,db:db.execute('UPDATE replies SET text=? WHERE id=?',('长'*7000,identity))
        request=AsyncMock(side_effect=[{'message_id':503},TimeoutError()])
        self.send(identity,request);status,ids,error=self.status(identity)
        self.assertEqual(status,'uncertain');self.assertEqual(json.loads(ids),['503'])
        with patch('napcat_connection.request',request):dispatch(self.ui)
        self.assertEqual(request.call_count,2)

    def test_failed_cli_command_returns_error_to_same_group(self):
        identity,_=self.queue('/session invalid')
        request=self.send(identity)
        self.assertIn('执行失败',request.call_args.args[3]['message'][0]['data']['text'])

    def test_secret_redaction_preserves_usage_token_numbers(self):
        cfg={'webui_key':'old-private-key','napcat_token':'another-private-token'}
        cleaned=redact('webui_key=old-private-key napcat_token: another-private-token\n缓存20776，合计113006 token\n5 hour token -',self.root,cfg)
        self.assertNotIn('old-private-key',cleaned);self.assertNotIn('another-private-token',cleaned)
        self.assertIn('20776',cleaned);self.assertIn('113006 token',cleaned);self.assertIn('5 hour token -',cleaned)

    def test_stale_queued_result_is_not_delivered(self):
        identity,_=self.queue()
        with closing(db_open(self.root)) as db,db:db.execute('UPDATE replies SET stamp=? WHERE id=?',(time.time()-130,identity))
        request=AsyncMock()
        with patch('napcat_connection.request',request):dispatch(self.ui)
        request.assert_not_called();self.assertEqual(self.status(identity)[0],'cancelled')

    def test_private_result_is_only_sent_to_requesting_contact(self):
        from owner_reply_delivery import enqueue
        from private_profiles import initialize
        import sqlite3
        folder=self.root/'runtime-private';folder.mkdir(exist_ok=True)
        atomic(folder/'directory.json',{'contacts':{'11111':{'groups':[200]}}})
        MemberLabels(folder).mark('11111','owner')
        with sqlite3.connect(folder/'private-chat.sqlite3') as db:initialize(db)
        handle(self.root,'11111','/help','private-request','private');drain(self.ui)
        identity='private:11111:private-request';request=self.send(identity)
        self.assertEqual(request.call_args.args[2],'send_private_msg');self.assertEqual(request.call_args.args[3]['user_id'],11111)
        self.assertNotIn('group_id',request.call_args.args[3])


if __name__=='__main__':unittest.main()
