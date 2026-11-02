import asyncio
import json
import logging
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from console_config import Config, atomic, runtime
from debug_webui import DebugWebUI
from group_memory import GroupMemory
from member_impressions import impression, digest
from private_chat import PrivateChat, PrivateStore, incoming_messages
from private_view import enqueue, view, set_instruction
from private_profiles import save as save_private_profile, knowledge as private_knowledge
from private_settings import save as save_private_setting
from PIL import Image


def message(identity, uid='11111', stamp=None, text='今天在玩音游'):
    return {'message_id': identity, 'user_id': int(uid), 'message_type': 'private',
            'time': time.time() - 2 if stamp is None else stamp, 'real_seq': identity,
            'message': [{'type': 'text', 'data': {'text': text}}]}


class PrivateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cfg = json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100, chat_group_whitelist=[100, 200], enabled=True,
                   expected_self_id='99999', private_reply_enabled=True)
        atomic(self.root / 'settings.json', cfg)
        (self.root / 'persona.txt').write_text('你是yonagi，喜欢自然接话', encoding='utf-8')
        self.now = time.time()
        self.sent = []
        self.messages = [message('1', stamp=self.now - 2)]
        self.prompts = []

        async def api(action, params=None):
            if action == 'get_login_info':
                return {'user_id': 99999}
            if action == 'get_friend_list':
                return [{'user_id': 11111}, {'user_id': 22222}, {'user_id': 44444}]
            if action == 'get_group_member_list':
                return [{'user_id': 11111, 'nickname': '同名群友'}, {'user_id': 22222, 'nickname': '另一个群友'}, {'user_id': 33333, 'nickname': '未加好友'}]
            if action == 'get_recent_contact':
                return [{'peerUin': '11111', 'chatType': 1, 'msgId': 'marker1', 'msgTime': str(self.now)}]
            if action == 'get_friend_msg_history':
                return {'messages': self.messages}
            if action == 'send_private_msg':
                self.sent.append(params)
                return {'message_id': 500 + len(self.sent)}
            raise AssertionError(action)

        self.api = api

        async def reply(prompt, cfg):
            self.prompts.append(prompt)
            return {'should_reply': True, 'text': '今天打得怎么样'}

        self.reply = reply
        self.service = PrivateChat(self.root, api, reply, clock=lambda: self.now - 20)
        self.service.clock = lambda: self.now
        await self.service.refresh(self.service.config())

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_reply_is_once_only_and_session_is_isolated(self):
        await self.service.handle('11111', self.messages, 'marker1')
        await self.service.handle('11111', self.messages, 'marker1')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['user_id'], 11111)
        self.assertEqual(self.service.store.history('22222'), [])
        self.assertEqual(len(self.service.store.history('11111')), 2)

    async def test_public_profile_on_demand_is_not_an_operator_note(self):
        import profile_queries
        profile_queries._cache.clear()
        self.messages=[message('profile',stamp=self.now-1,text='看看我的个人简介')]
        calls=[]
        async def api(action,params=None):
            calls.append(action)
            if action=='get_stranger_info':return {'user_id':11111,'nickname':'本人','personal_note':'QQ公开签名','phone':'隐藏'}
            return await self.api(action,params)
        self.service.api=api
        await self.service.handle('11111',self.messages,'profile-marker')
        self.assertEqual(len(self.sent),1);self.assertIn('QQ公开签名',self.prompts[0])
        self.assertIn('public_profile_results',self.prompts[0]);self.assertNotIn('隐藏',self.prompts[0])
        self.assertNotIn('QQ公开签名',json.dumps(self.service.store.history('11111'),ensure_ascii=False))
        self.assertIn('get_stranger_info',calls)

    async def test_public_profile_toggle_change_during_query_cancels_reply(self):
        import profile_queries
        profile_queries._cache.clear()
        self.messages=[message('profile',stamp=self.now-1,text='看看我的个人简介')]
        async def api(action,params=None):
            if action=='get_stranger_info':
                Config(self.root).set('model_profile_query_enabled','off')
                return {'user_id':11111,'personal_note':'不要传入'}
            return await self.api(action,params)
        self.service.api=api
        await self.service.handle('11111',self.messages,'profile-marker')
        self.assertFalse(self.prompts);self.assertFalse(self.sent)

    async def test_large_private_knowledge_does_not_evict_fitting_conversation(self):
        for n in range(8):self.service.store.add_message('11111',str(n),'user','LIVE_'+str(n)+' '+('近期聊天'*20),self.now-10+n)
        profile={'observations':[],'familiarity':'暂不熟悉'}
        with patch('private_profiles.knowledge',return_value=[]):baseline=self.service.prompt('11111',profile)
        refs=[{'id':str(n),'title':'知识','content':'FACT_'+str(n)+' '+('相关资料'*1000)} for n in range(30)]
        with patch('private_profiles.knowledge',return_value=refs):actual=self.service.prompt('11111',profile)
        self.assertLessEqual(len(actual),self.service.config()['max_prompt_chars'])
        for n in range(8):self.assertIn('LIVE_'+str(n)+' ',baseline);self.assertIn('LIVE_'+str(n)+' ',actual)
        self.assertIn('FACT_0',actual);self.assertNotIn('FACT_29',actual)

    async def test_private_avatar_is_attached_and_identified_separately_from_chat_images(self):
        self.messages=[message('avatar',stamp=self.now-1,text='我的头像好看吗')]
        async def download(client,url,path,cfg):path.write_bytes(b'fake-test-image')
        with patch('bot.download_image',download):await self.service.handle('11111',self.messages,'avatar-marker')
        self.assertEqual(len(self.sent),1);self.assertIn('"avatar_qq_id":"11111"',self.prompts[0])
        self.assertIn('"avatar_status":"attached"',self.prompts[0])

    async def test_pinyin_guess_switch_controls_private_prompt(self):
        self.service.store.add_message('11111','shorthand','user','csncsn',self.now-1)
        profile={'observations':[],'familiarity':'暂不熟悉'}
        prompt=self.service.prompt('11111',profile)
        self.assertIn('shorthand_context',prompt);self.assertIn('不是释义词典',prompt)
        Config(self.root).set('pinyin_abbreviation_guess_enabled','off')
        prompt=self.service.prompt('11111',profile)
        self.assertNotIn('shorthand_context',prompt);self.assertNotIn('拼音缩写：',prompt)

    async def test_private_message_times_and_ephemeral_clock(self):
        self.service.store.add_message('11111','one','user','第一条',self.now-10)
        self.service.store.add_message('11111','sent','assistant','好的',self.now-5)
        with patch('message_time.clock',return_value='Mon Nov  2 12:33:37 PM CST 2026'):
            prompt=self.service.prompt('11111',{'observations':[]})
        self.assertIn('sent_at',prompt);self.assertIn('12:33:37',prompt)
        rows=self.service.store.history('11111');self.assertNotIn('generation_clock',str(rows))
        self.service.store.confirm_times('11111',[{'user_id':int(self.service.self_id),'message_id':'sent','time':self.now-6}],self.service.self_id)
        self.assertEqual(self.service.store.history('11111')[-1]['stamp'],self.now-6)

    async def test_video_is_recorded_without_a_model_call_or_reply(self):
        row=message('video',stamp=self.now-1)
        row['message']=[{'type':'video','data':{'url':'https://invalid.test/video.mp4'}}]
        await self.service.handle('11111',[row],'video-marker')
        await self.service.handle('11111',[row],'video-marker')
        self.assertFalse(self.sent);self.assertFalse(self.prompts)
        history=self.service.store.history('11111')
        self.assertEqual(len(history),1);self.assertEqual(history[0]['text'],'[视频]')

    async def test_default_only_admits_friends_in_authorized_groups(self):
        await self.service.handle('22222', [message('2', '22222')], 'otherfriend')
        await self.service.handle('33333', [message('3', '33333')], 'notfriend')
        await self.service.handle('44444', [message('4', '44444')], 'friend-outside-groups')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['user_id'], 22222)
        self.assertEqual({row['id'] for row in view(self.root)['contacts']}, {'11111', '22222'})

    async def test_old_messages_and_self_messages_are_ignored(self):
        await self.service.handle('11111', [message('1', stamp=self.now - 100)], 'old')
        await self.service.handle('11111', [message('2', '99999', stamp=self.now)], 'own')
        self.assertFalse(self.sent)
        self.assertFalse(self.prompts)

    async def test_manual_reply_already_in_qq_suppresses_automatic_reply(self):
        self.messages.append(message('2', '99999', stamp=self.now - 1, text='人工回复'))
        await self.service.handle('11111', self.messages, 'manual')
        self.assertFalse(self.sent)

    async def test_group_removal_during_generation_cancels_delivery(self):
        async def change_membership(prompt, cfg):
            atomic(self.root / 'settings.json', {**cfg, 'chat_group_whitelist': [300]})
            return {'should_reply': True, 'text': '不应发送'}
        self.service.responder = change_membership
        await self.service.handle('11111', self.messages, 'removed')
        self.assertFalse(self.sent)

    async def test_stopping_during_generation_cancels_delivery(self):
        async def stop(prompt, cfg):
            (self.service.folder / 'STOP').touch()
            return {'should_reply': True, 'text': '不应发送'}
        self.service.responder = stop
        await self.service.handle('11111', self.messages, 'stopped')
        self.assertFalse(self.sent)

    async def test_uncertain_send_is_not_retried(self):
        original = self.service.api
        attempts = []
        async def uncertain(action, params=None):
            if action == 'send_private_msg':
                attempts.append(params)
                raise TimeoutError()
            return await original(action, params)
        self.service.api = uncertain
        await self.service.handle('11111', self.messages, 'marker1')
        await self.service.handle('11111', self.messages, 'marker1')
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.service.store.db.execute('SELECT status FROM deliveries').fetchone()[0], 'uncertain')

    async def test_impressions_contain_only_same_accounts_supported_facts(self):
        folder = runtime(self.root, 100)
        folder.mkdir()
        memory = GroupMemory(folder / 'group-memory.sqlite3')
        try:
            memory.observe({'_id': 'mine', '_uid': '11111', 'sender': '同名群友', 'text': '我喜欢音游', '_time': self.now})
            own = memory.digest('11111')
            other = memory.digest('22222')
            def note(key, subject, content, speaker):
                memory.db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,1)',
                    (key, 'preference', content, subject, '[]', json.dumps([{'speaker': speaker}]), self.now))
            note('mine', own, '喜欢音游', own)
            note('other', other, '另一个人的秘密', other)
            note('mixed', own, '不应带出的混合证据', other)
            note('superseded', own, '已经过时的兴趣', own)
            memory.db.execute('INSERT INTO supersessions VALUES (?,?)', ('superseded', 'mine'))
            memory.db.commit()
        finally:
            memory.close()
        value = json.dumps(impression(self.root, '11111', [100]), ensure_ascii=False)
        self.assertIn('喜欢音游', value)
        self.assertNotIn('另一个人的秘密', value)
        self.assertNotIn('混合证据', value)
        self.assertNotIn('已经过时', value)

    async def test_group_block_only_disables_group_impressions_for_private_friend(self):
        folder = runtime(self.root, 100)
        folder.mkdir()
        atomic(folder / 'member-labels.json', {'members': {'11111': {'manual_blocked': True}}})
        await self.service.handle('11111', self.messages, 'blocked')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.service.impression_groups('11111'), [])
        self.assertEqual(impression(self.root, '11111', [100])['observations'], [])

    async def test_disable_impression_keeps_group_details_out_of_prompt(self):
        cfg = self.service.config()
        atomic(self.root / 'settings.json', {**cfg, 'private_use_group_impressions': False})
        await self.service.handle('11111', self.messages, 'noprof')
        self.assertNotIn('source_group', self.prompts[0])

    async def test_manual_outbox_is_idempotent(self):
        self.service.status(True)
        identity = 'test-request-123456789'
        enqueue(self.root, '11111', '手动私信', identity)
        enqueue(self.root, '11111', '手动私信', identity)
        with self.assertRaises(ValueError):
            enqueue(self.root, '11111', '另一条消息', identity)
        await self.service.send_manual()
        await self.service.send_manual()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(view(self.root, '11111')['requests'][0]['status'], 'sent')

    async def test_private_endpoint_requires_auth_and_rejects_unknown_contact(self):
        server = DebugWebUI(self.root, lambda value: {})
        server.start()
        try:
            with httpx.Client(base_url=server.base_url, trust_env=False) as client:
                self.assertEqual(client.get('/api/private').status_code, 401)
                headers = {'X-QQ-Debug-Key': server.token}
                self.assertEqual(client.get('/api/private', headers=headers).status_code, 200)
                self.assertEqual(client.get('/api/private?peer=44444', headers=headers).status_code, 400)
                self.assertEqual(client.get('/private-ui.js').status_code, 200)
                response = client.get('/private.css')
                self.assertEqual(response.status_code, 200)
                self.assertIn('text/css', response.headers['content-type'])
        finally:
            server.close()

    async def test_reply_budget_prevents_generation(self):
        cfg = self.service.config()
        atomic(self.root / 'settings.json', {**cfg, 'private_max_model_calls_per_day': 1})
        self.service.store.call_started(self.now)
        await self.service.handle('11111', self.messages, 'budget')
        self.assertFalse(self.prompts)

    async def test_crash_marker_does_not_requeue_uncertain_manual_send(self):
        store = self.service.store
        store.db.execute("INSERT INTO outbox VALUES ('interrupted','11111','不要重复','sending',?,'','')", (self.now,))
        store.db.commit()
        store.close()
        self.service.store = PrivateStore(self.service.folder, self.now)
        await self.service.send_manual()
        self.assertFalse(self.sent)
        self.assertEqual(self.service.store.db.execute("SELECT status FROM outbox WHERE id='interrupted'").fetchone()[0], 'uncertain')

    async def test_system_instruction_is_local_and_used_only_on_next_reply(self):
        set_instruction(self.root, '11111', '回复短一点，顺着音游话题聊')
        self.assertFalse(self.sent)
        self.assertFalse(self.prompts)
        received = []
        async def reply(prompt, cfg):
            received.append(cfg)
            return {'should_reply': True, 'text': '今天怎么样'}
        self.service.responder = reply
        await self.service.handle('11111', self.messages, 'instruction')
        self.assertIn('回复短一点', received[0]['_developer_instructions'])
        self.assertEqual(view(self.root, '11111')['system_instruction'], '回复短一点，顺着音游话题聊')

    async def test_private_instruction_does_not_cross_contacts(self):
        set_instruction(self.root, '11111', '只给这位好友的提示词')
        self.service.friends.add('33333')
        received = []
        async def reply(prompt, cfg):
            received.append(cfg)
            return {'should_reply': True, 'text': '你好呀'}
        self.service.responder = reply
        self.messages = [message('4', '33333', self.now - 1)]
        await self.service.handle('33333', self.messages, 'separate')
        self.assertNotIn('_developer_instructions', received[0])

    async def test_instruction_clear_backs_up_without_sending(self):
        set_instruction(self.root, '11111', '旧提示词')
        set_instruction(self.root, '11111', '')
        self.assertEqual(self.service.store.instruction('11111'), '')
        self.assertEqual(self.service.store.db.execute('SELECT text FROM instruction_history').fetchone()[0], '旧提示词')
        with self.assertRaises(ValueError):
            set_instruction(self.root, '44444', '不应保存')
        self.assertFalse(self.sent)

    async def test_changing_instruction_during_generation_cancels_old_reply(self):
        set_instruction(self.root, '11111', '原提示词')
        async def change(prompt, cfg):
            set_instruction(self.root, '11111', '新提示词')
            return {'should_reply': True, 'text': '旧配置回复'}
        self.service.responder = change
        await self.service.handle('11111', self.messages, 'change-instruction')
        self.assertFalse(self.sent)

    async def test_four_filters_separate_chat_flow_and_system(self):
        set_instruction(self.root, '11111', '自然接话，简短回复')
        await self.service.handle('11111', self.messages, 'filter')
        all_rows = view(self.root, '11111')['events']
        self.assertTrue({'received', 'sent', 'system', 'stage', 'trigger', 'decision'} <= {row['kind'] for row in all_rows})
        self.assertEqual({row['kind'] for row in view(self.root, '11111', 'chat')['events']}, {'received', 'sent'})
        self.assertTrue(all(row['kind'] not in {'received', 'sent', 'system'} for row in view(self.root, '11111', 'flow')['events']))
        self.assertEqual({row['kind'] for row in view(self.root, '11111', 'system')['events']}, {'system'})
        with self.assertRaises(ValueError):
            view(self.root, '11111', 'unknown')

    async def test_concurrent_model_logs_are_scoped_and_credentials_redacted(self):
        self.service.friends.add('33333')
        logger = logging.getLogger('qq-group-bot')
        old_level = logger.level
        logger.setLevel(logging.INFO)
        async def log_reply(prompt, cfg):
            peer = '11111' if '"user_id":"11111"' in prompt else '33333'
            logger.info('Model input for contact %s', peer)
            await asyncio.sleep(0)
            logger.info('Model transport Authorization: Bearer do-not-show-secret')
            return {'should_reply': False, 'text': ''}
        self.service.responder = log_reply
        try:
            await asyncio.gather(self.service.handle('11111', self.messages, 'first'),
                self.service.handle('33333', [message('3', '33333', self.now - 1)], 'second'))
        finally:
            logger.setLevel(old_level)
        first = '\n'.join(row[0] for row in self.service.store.db.execute("SELECT text FROM private_events WHERE peer='11111'"))
        second = '\n'.join(row[0] for row in self.service.store.db.execute("SELECT text FROM private_events WHERE peer='33333'"))
        self.assertIn('contact 11111', first)
        self.assertNotIn('contact 33333', first)
        self.assertIn('contact 33333', second)
        self.assertNotIn('contact 11111', second)
        self.assertNotIn('do-not-show-secret', first + second)

    async def test_event_migration_and_polling_do_not_duplicate_history(self):
        set_instruction(self.root, '11111', '已保存的系统提示词')
        await self.service.handle('11111', self.messages, 'existing')
        counts = self.service.store.db.execute('SELECT kind,count(*) FROM private_events GROUP BY kind').fetchall()
        self.service.store.close()
        self.service.store = PrivateStore(self.service.folder, self.now)
        after = self.service.store.db.execute('SELECT kind,count(*) FROM private_events GROUP BY kind').fetchall()
        self.assertEqual([tuple(r) for r in counts], [tuple(r) for r in after])

    async def test_contact_behavior_controls_only_its_reply(self):
        save_private_profile(self.root, '11111', 'behavior', {'reply_enabled': False, 'max_reply_chars': 90})
        await self.service.handle('11111', self.messages, 'paused-contact')
        self.assertFalse(self.sent)
        self.assertFalse(self.prompts)
        self.assertEqual(len(self.service.store.history('11111')), 1)
        self.assertEqual(Config(self.root).load()['private_max_reply_chars'], 600)
        data = view(self.root, '11111')
        self.assertEqual(data['contact_settings']['max_reply_chars'], 90)
        self.assertTrue(data['runtime'])

    async def test_persona_and_rule_overrides_are_used_only_for_contact(self):
        save_private_profile(self.root, '11111', 'prompts', {'persona': '此私聊专用人设', 'protocol': '此私聊专用规则'})
        await self.service.handle('11111', self.messages, 'custom-prompt')
        self.assertIn('此私聊专用人设', self.prompts[0])
        self.assertIn('此私聊专用规则', self.prompts[0])
        self.assertNotIn('此私聊专用人设', (self.root / 'persona.txt').read_text(encoding='utf-8'))

    async def test_polling_uses_contact_cooldown_override(self):
        save_private_profile(self.root, '11111', 'behavior', {'reply_cooldown_seconds': 0})
        await self.service.handle('11111', self.messages, 'original-marker')
        self.now += 1
        self.messages = [message('2', stamp=self.now)]
        await self.service.tick()
        await asyncio.gather(*self.service.pending.values())
        self.assertEqual(len(self.sent), 2)

    async def test_private_knowledge_has_contact_scope_and_real_retrieval(self):
        directory = self.root / 'runtime-private/directory.json'
        data = json.loads(directory.read_text(encoding='utf-8'))
        data['contacts']['33333'] = {'groups': [100], 'name': '另一位好友'}
        atomic(directory, data)
        save_private_profile(self.root, '11111', 'knowledge', {'id': 'music-note', 'title': '音游练习', 'content': '这个私聊的音游练习建议'})
        self.assertTrue(private_knowledge(self.service.store.db, '11111', '音游练习'))
        self.assertFalse(private_knowledge(self.service.store.db, '33333', '音游练习'))
        with self.assertRaises(ValueError):
            save_private_profile(self.root, '33333', 'knowledge', {'id': 'music-note', 'delete': True})
        await self.service.handle('11111', self.messages, 'knowledge')
        self.assertIn('这个私聊的音游练习建议', self.prompts[0])
        save_private_profile(self.root, '11111', 'knowledge',
            {'id': 'long-note', 'title': '练习说明', 'content': '背景。' * 1000 + '节奏练习每天十分钟'})
        excerpts = private_knowledge(self.service.store.db, '11111', '节奏练习')
        self.assertIn('节奏练习', excerpts[0]['content'])
        self.assertGreater(len(json.dumps(excerpts, ensure_ascii=False)), 1800)
        bounded = private_knowledge(self.service.store.db, '11111', '节奏练习', budget=1800)
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False)), 1800)

    async def test_private_block_and_note_are_separate_from_group_labels(self):
        save_private_profile(self.root, '11111', 'member', {'note': '喜欢音游', 'aliases': ['小音'], 'blocked': True})
        self.service.status(True)
        await self.service.handle('11111', self.messages, 'private-block')
        self.assertFalse(self.sent)
        with self.assertRaises(ValueError):
            enqueue(self.root, '11111', '不应发送', 'request-blocked-123456')
        self.assertTrue(view(self.root, '11111')['profile']['blocked'])
        self.assertFalse((runtime(self.root, 100) / 'member-labels.json').exists())

    async def test_contact_event_lanes_keep_chat_when_execution_logs_roll(self):
        self.service.store.add_message('11111', 'chat-keep', 'user', '聊天不能被日志挤掉', self.now)
        for index in range(1020):
            self.service.store.event('11111', 'stage', '执行阶段 ' + str(index))
        data = view(self.root, '11111', 'chat')
        self.assertIn('聊天不能被日志挤掉', [row['text'] for row in data['events']])

    async def test_private_default_overrides_and_reset_do_not_change_group_settings(self):
        original = Config(self.root).load()
        save_private_setting(self.root, '', 'default', 'max_reply_chars', 90)
        self.assertEqual(view(self.root, '11111')['contact_settings']['max_reply_chars'], 90)
        save_private_setting(self.root, '11111', 'contact', 'max_reply_chars', 75)
        self.assertEqual(view(self.root, '11111')['contact_settings']['max_reply_chars'], 75)
        save_private_setting(self.root, '11111', 'contact', 'max_reply_chars', inherit=True)
        self.assertEqual(view(self.root, '11111')['contact_settings']['max_reply_chars'], 90)
        self.assertEqual(Config(self.root).load(), {**original,'private_max_reply_chars':90})
        with self.assertRaises(ValueError):
            save_private_setting(self.root, '11111', 'contact', 'max_model_calls_per_day', 100)
        with self.assertRaises(ValueError):
            save_private_setting(self.root, '11111', 'contact', 'max_images_per_call', 999)
        with self.assertRaises(ValueError):
            save_private_setting(self.root, '44444', 'contact', 'vision_enabled', True)

    async def test_whitelist_and_blacklist_precedence_and_manual_send(self):
        save_private_setting(self.root, '', 'default', 'access_mode', 'whitelist')
        save_private_setting(self.root, '', 'default', 'user_whitelist', [11111, '22222', 11111])
        save_private_setting(self.root, '', 'default', 'user_blacklist', ['11111'])
        self.assertFalse(self.service.can_chat('11111'))
        self.assertTrue(self.service.can_chat('22222'))
        self.assertEqual(view(self.root)['settings_defaults']['user_whitelist'], ['11111', '22222'])
        await self.service.handle('11111', self.messages, 'blocked')
        await self.service.handle('22222', [message('3', '22222', self.now - 1)], 'allowed')
        self.assertEqual([row['user_id'] for row in self.sent], [22222])
        self.assertFalse(view(self.root, '11111')['can_chat'])
        self.assertIn('黑名单', view(self.root, '11111')['access_reason'])
        with self.assertRaisesRegex(ValueError, '黑名单'):
            enqueue(self.root, '11111', '不应发送', 'blacklist-manual-1234')

    async def test_empty_whitelist_denies_every_friend_and_all_mode_restores_access(self):
        save_private_setting(self.root, '', 'default', 'access_mode', 'whitelist')
        self.assertFalse(self.service.can_chat('11111'))
        self.assertFalse(self.service.can_chat('22222'))
        save_private_setting(self.root, '', 'default', 'access_mode', 'all')
        self.assertTrue(self.service.can_chat('22222'))
        with self.assertRaises(ValueError):
            save_private_setting(self.root, '', 'default', 'user_blacklist', ['not-a-qq'])
        with self.assertRaises(ValueError):
            save_private_setting(self.root, '11111', 'contact', 'access_mode', 'all')

    async def test_policy_changed_during_generation_cancels_reply(self):
        async def reply(prompt, cfg):
            save_private_setting(self.root, '', 'default', 'user_blacklist', ['11111'])
            return {'should_reply': True, 'text': '不应发出'}
        self.service.responder = reply
        await self.service.handle('11111', self.messages, 'policy-change')
        self.assertFalse(self.sent)

    async def test_queued_manual_send_rechecks_blacklist(self):
        self.service.status(True)
        enqueue(self.root, '11111', '排队后屏蔽', 'blacklist-queued-1234')
        save_private_setting(self.root, '', 'default', 'user_blacklist', ['11111'])
        await self.service.send_manual()
        self.assertFalse(self.sent)
        self.assertEqual(view(self.root, '11111')['requests'][0]['status'], 'cancelled')

    async def test_group_membership_failure_does_not_admit_unverified_friends(self):
        original = self.service.api
        async def api(action, params=None):
            if action == 'get_group_member_list':
                raise TimeoutError()
            return await original(action, params)
        self.service.api = api
        await self.service.refresh(self.service.config())
        await self.service.handle('22222', [message('3', '22222', self.now - 1)], 'no-groups')
        self.assertFalse(self.sent)
        self.assertFalse(self.service.can_chat('22222'))
        self.assertEqual(self.service.impression_groups('22222'), [])

    async def test_owner_is_contact_scoped_and_cannot_bypass_blacklist(self):
        from member_labels import MemberLabels
        MemberLabels(self.root/'runtime-private').mark('11111','owner')
        Config(self.root).set('member_prompt_owner','主人专用聊天指令')
        await self.service.handle('11111', self.messages, 'owner')
        self.assertIn('主人专用聊天指令', self.prompts[0])
        self.assertIn('"account_type":"owner"', self.prompts[0])
        self.assertNotIn('主人专用聊天指令', self.service.prompt('22222', {'observations': []}))
        save_private_setting(self.root, '', 'default', 'user_blacklist', ['11111'])
        self.assertFalse(self.service.can_chat('11111'))

    async def test_globally_blocked_friend_is_recorded_but_never_read_by_model(self):
        from member_labels import MemberLabels
        Config(self.root).set('member_labels_global_enabled','on')
        MemberLabels(runtime(self.root,100)).mark('11111','human',blocked=True)
        await self.service.handle('11111',self.messages,'blocked')
        self.assertFalse(self.prompts);self.assertFalse(self.sent)
        self.assertEqual(view(self.root,'11111')['messages'][0]['role'],'user')
        self.assertTrue(view(self.root,'11111')['profile']['blocked'])

    async def test_private_model_context_and_reference_settings_reach_generation(self):
        self.service.store.add_message('11111', 'old-msg', 'user', '不该进入上下文的旧消息', self.now - 10)
        for key, value in [('memory_enabled', False), ('reference_aliases', False), ('reference_note', False),
                           ('model_timeout_seconds', 75), ('max_prompt_chars', 5000)]:
            save_private_setting(self.root, '11111', 'contact', key, value)
        save_private_profile(self.root, '11111', 'member', {'aliases': ['小音'], 'note': '此备注不应入模', 'blocked': False})
        captured = []
        async def reply(prompt, cfg):
            captured.append(cfg)
            self.prompts.append(prompt)
            return {'should_reply': False, 'text': ''}
        self.service.responder = reply
        await self.service.handle('11111', self.messages, 'settings')
        self.assertNotIn('不该进入上下文', self.prompts[0])
        self.assertNotIn('此备注不应入模', self.prompts[0])
        self.assertNotIn('小音', self.prompts[0])
        self.assertEqual(captured[0]['model_timeout_seconds'], 75)
        self.assertLessEqual(len(self.prompts[0]), 5000)

    async def test_private_images_are_attached_only_to_current_contact_then_removed(self):
        raw = io.BytesIO()
        Image.new('RGB', (12, 15), 'red').save(raw, 'PNG')
        urls = []
        def respond(request):
            urls.append(str(request.url))
            return httpx.Response(200, content=raw.getvalue())
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.messages[0]['message'] += [{'type': 'image', 'data': {'url': 'https://gchat.qpic.cn/private?secret=do-not-log'}}]
        captured = []
        async def reply(prompt, cfg):
            captured.extend(cfg['_private_image_paths'])
            self.assertEqual(len(captured), 1)
            with Image.open(captured[0]) as prepared:
                self.assertEqual(prepared.format, 'JPEG')
                self.assertEqual(prepared.size, (12, 15))
            self.assertIn('"attached_images":[{"image_index":1', prompt)
            self.assertNotIn('do-not-log', prompt)
            return {'should_reply': True, 'text': '图里是红色'}
        self.service.responder = reply
        with patch('private_vision.httpx.AsyncClient', return_value=client):
            await self.service.handle('11111', self.messages, 'image')
        self.assertEqual(len(urls), 1)
        self.assertTrue(captured)
        self.assertTrue(all(not path.exists() for path in captured))
        self.assertIn('图里是红色', self.service.store.history('11111')[-1]['text'])
        self.assertFalse(self.service.store.history('33333'))

    async def test_private_vision_off_never_downloads_and_reports_unread_image(self):
        save_private_setting(self.root, '11111', 'contact', 'vision_enabled', False)
        self.messages[0]['message'] += [{'type': 'image', 'data': {'url': 'https://gchat.qpic.cn/a'}}]
        async def reply(prompt, cfg):
            self.assertFalse(cfg['_private_image_paths'])
            self.assertIn('"enabled":false', prompt)
            self.assertIn('"unread_images":1', prompt)
            return {'should_reply': False, 'text': ''}
        self.service.responder = reply
        with patch('private_vision.httpx.AsyncClient') as factory:
            await self.service.handle('11111', self.messages, 'vision-off')
        factory.assert_not_called()
        self.assertFalse(view(self.root, '11111')['contact_settings']['vision_enabled'])

    async def test_private_image_errors_do_not_leak_urls_or_claim_images_were_read(self):
        self.messages[0]['message'] += [{'type': 'image', 'data': {'url': 'http://127.0.0.1/secret?token=hidden'}}]
        async def reply(prompt, cfg):
            self.assertFalse(cfg['_private_image_paths'])
            self.assertIn('"unread_images":1', prompt)
            return {'should_reply': False, 'text': ''}
        self.service.responder = reply
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.fail('Untrusted URL fetched'))) as client:
            with patch('private_vision.httpx.AsyncClient', return_value=client):
                await self.service.handle('11111', self.messages, 'bad-image')
        logs = json.dumps(view(self.root, '11111')['events'], ensure_ascii=False)
        self.assertIn('image_source_not_allowed', logs)
        self.assertNotIn('token=hidden', logs)

    async def test_changing_vision_switch_during_generation_cancels_reply(self):
        async def reply(prompt, cfg):
            save_private_setting(self.root, '11111', 'contact', 'vision_enabled', False)
            return {'should_reply': True, 'text': '不应发送'}
        self.service.responder = reply
        await self.service.handle('11111', self.messages, 'vision-change')
        self.assertFalse(self.sent)


class PrivateSupervisorTests(unittest.TestCase):
    def test_console_owns_exactly_one_private_worker_and_can_disable_it(self):
        from debug_console import WindowsJob
        from platform_runtime import PosixJob
        from private_supervisor import PrivateSupervisor
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'private_worker.py').write_text(
                "import time,os,json\nfrom pathlib import Path\n"
                "folder=Path(__file__).parent/'runtime-private'\n"
                "(folder/'state.json').write_text(json.dumps({'pid':os.getpid()}))\n"
                "while not (folder/'STOP').exists():time.sleep(.02)\n", encoding='utf-8')
            sup = PrivateSupervisor(root, WindowsJob if os.name == 'nt' else PosixJob)
            try:
                cfg = {'enabled': True, 'private_reply_enabled': True}
                sup.sync(cfg)
                process = sup.process
                sup.sync(cfg)
                self.assertIs(process, sup.process)
                sup.sync({**cfg, 'private_reply_enabled': False})
                self.assertIsNotNone(process.poll())
                self.assertIsNone(sup.process)
            finally:
                sup.stop()


if __name__ == '__main__':
    unittest.main()
