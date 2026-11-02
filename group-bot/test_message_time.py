import asyncio
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
import message_time as mt
from recent_chat_journal import RecentChatJournal,compact
from role_analysis import evidence
from conversation_batch import attach


class MessageTimeTests(unittest.TestCase):
    def test_local_time_has_seconds_and_explicit_offset(self):
        stamp=1793594017
        expected=datetime.fromtimestamp(stamp).astimezone().isoformat(sep=' ',timespec='seconds')
        self.assertEqual(mt.formatted(stamp),expected)
        self.assertEqual(mt.formatted(stamp*1000),expected)
        for invalid in [None,0,True,'not-time',float('nan'),float('inf')]:self.assertIsNone(mt.formatted(invalid))

    def test_linux_date_output_is_preserved_including_japanese_and_timezone(self):
        for text in ['Mon Nov  2 12:33:37 PM CST 2026','2026年 11月  2日 月曜日 13:34:12 JST']:
            with patch.object(mt.os,'name','posix'),patch.object(mt.subprocess,'run',return_value=SimpleNamespace(stdout=text+'\n')) as run:
                self.assertEqual(mt.clock(),text)
                self.assertEqual(run.call_args.args[0],['date'])

    def test_clock_replacement_does_not_touch_escaped_user_content_or_accumulate(self):
        user='literal "generation_clock":"old user text"'
        payload={'target':{'text':user},'generation_clock':'old turn'}
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        with patch.object(mt,'clock',return_value='Mon Nov  2 12:33:37 PM CST 2026'):
            first=mt.refresh(raw)
        with patch.object(mt,'clock',return_value='2026年 11月  2日 月曜日 13:34:12 JST'):
            second=mt.refresh(first)
        data=json.loads(second)
        self.assertEqual(data['target']['text'],user)
        self.assertEqual(data['generation_clock'],'2026年 11月  2日 月曜日 13:34:12 JST')
        self.assertNotIn('12:33:37',second)

    def test_group_and_role_prompts_supply_message_times_and_one_turn_clock(self):
        stamp=time.time();source={'_id':'source','_uid':'11111','sender':'甲','text':'上一条','_time':stamp-60}
        own={'_id':'reply','_uid':'99999','sender':'Bot','text':'好的','_time':stamp-30,'is_bot':True}
        target={'_id':'current','_uid':'22222','sender':'乙','_time':stamp,'text':'我这条消息是什么时候发的','at_bot':True}
        prompt=bot.build_prompt(bot.settings(),[source,own],'被@',target)
        payload=json.loads(prompt[prompt.index('{"target":'):])
        self.assertEqual(payload['target']['sent_at'],mt.formatted(stamp))
        self.assertEqual({r['message_id']:r['sent_at'] for r in payload['recent_chat']},
            {'source':mt.formatted(stamp-60),'reply':mt.formatted(stamp-30)})
        self.assertEqual(list(payload).count('generation_clock'),1)
        role=evidence([source,own],target,bot.settings())
        self.assertEqual(role['target']['sent_at'],mt.formatted(stamp))
        self.assertTrue(role['generation_clock'])

    def test_quoted_time_survives_storage_and_old_mini_quote_recovers_from_history(self):
        stamp=time.time();quote={'_id':'q','_uid':'11111','sender':'甲','text':'原消息','_time':stamp-30}
        target={'_id':'t','_time':stamp,'_reply_id':'q','_quoted_message':quote,'text':'回复'}
        row=compact(target)
        self.assertEqual(row['_quoted_message']['_time'],quote['_time'])
        self.assertEqual(row['_quoted_message']['_id'],'q')
        old={**target,'_quoted_message':{'sender':'甲','text':'原消息'}}
        self.assertEqual(mt.quoted(old,[quote])['_time'],quote['_time'])
        self.assertNotIn('_time',old['_quoted_message'])

    def test_clock_is_never_persisted_and_napcat_echo_corrects_without_duplicate(self):
        stamp=time.time()
        with tempfile.TemporaryDirectory() as folder:
            journal=RecentChatJournal(Path(folder)/'recent.jsonl')
            journal.append({'_id':'sent','_uid':'99999','_time':stamp,'is_bot':True,'text':'好的','generation_clock':'temporary'})
            self.assertTrue(journal.confirm_time('sent',stamp-1))
            self.assertFalse(journal.confirm_time('sent',stamp-1))
            rows=journal.load();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['_time'],stamp-1)
            self.assertEqual(rows[0]['_time_source'],'napcat');self.assertNotIn('generation_clock',journal.path.read_text(encoding='utf-8'))

    def test_sent_api_time_is_preferred_and_missing_uses_local_send_time(self):
        self.assertEqual(mt.sent_timestamp({'time':1793594017},1793594019),1793594017)
        self.assertEqual(mt.sent_timestamp({'timestamp':1234000000000},2000),1234000000)
        self.assertEqual(mt.sent_timestamp({'message_id':1},2000),2000)
        self.assertEqual(mt.sent_timestamp({'time':0},2000),2000)

    def test_batch_messages_each_include_their_own_time(self):
        stamp=time.time();rows=[{'_id':str(n),'_uid':str(n),'text':'聊天','_time':stamp+n} for n in range(2)]
        target=attach(dict(rows[-1]),rows,bot.settings())
        self.assertEqual([r['sent_at'] for r in target['_conversation_batch']['messages']],[mt.formatted(stamp),mt.formatted(stamp+1)])

    def test_transport_refreshes_clock_immediately_before_model_call(self):
        async def run(folder):
            schema=Path(folder)/'reply.json';schema.write_text(json.dumps({'type':'object','properties':{'ok':{'type':'boolean'}}}),encoding='utf-8')
            output=None;captured=[]
            class Process:
                returncode=0
                async def communicate(self,body):
                    captured.append(json.loads(body));output.write_text('{"ok":true}',encoding='utf-8');return b'',b''
            async def launch(*args,**kwargs):
                nonlocal output
                output=Path(args[args.index('-o')+1]);return Process()
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'resolve_codex_executable',return_value='fake'), \
                    patch.object(bot.asyncio,'create_subprocess_exec',launch),patch.object(mt,'clock',return_value='Mon Nov  2 12:33:37 PM CST 2026'):
                await bot.generate_json(bot.settings(),'{"generation_clock":"old turn"}',schema)
            self.assertEqual(captured[0]['generation_clock'],'Mon Nov  2 12:33:37 PM CST 2026')
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))


if __name__=='__main__':unittest.main()
