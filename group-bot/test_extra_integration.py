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
from napcat_connection import request, NapCatHTTPClient
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


