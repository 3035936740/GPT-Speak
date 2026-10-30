"""Transport recovery must resume @ replies without a global timed silence."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock
import httpx
import bot
from PIL import Image
import uuid
from emoji_library import ReplyText


class RecoveryTests(unittest.TestCase):
    def test_operator_stop_cancels_generation_and_waits_for_new_message(self):
        asyncio.run(self.scenario('session'))
    def test_operator_clear_removes_old_short_term_context(self):
        asyncio.run(self.scenario('session_clear'))
    def test_restart_archives_recent_at_and_only_answers_new_message(self):
        asyncio.run(self.scenario('archive'))
    def test_failed_bootstrap_does_not_replay_old_poll_messages(self):
        asyncio.run(self.scenario('archive_failed'))
    def test_recovered_poll_starts_waiting_mention_immediately(self):
        asyncio.run(self.scenario('poll'))

    def test_failed_delivery_retries_only_unsent_part_without_regeneration(self):
        asyncio.run(self.scenario('delivery'))

    def test_local_processing_failure_does_not_mark_qq_offline(self):
        asyncio.run(self.scenario('local'))

    def test_successful_generated_image_delivery_keeps_temp_file(self):
        asyncio.run(self.scenario('image'))

    def test_busy_status_file_does_not_interrupt_reply_or_error_handling(self):
        asyncio.run(self.scenario('state'))

    async def scenario(self, failure):
        cfg = bot.settings()
        real_sleep, real_save, real_style_save = asyncio.sleep, bot.save_state, bot.StyleMemory.save
        polls, attempts, generations, style_saves = 0, 0, 0, 0
        sent, states, delays, start_polls = [], [], [], []
        stamp = time.time()
        incoming = {'message_id': '1', 'time': stamp, 'user_id': 3035936740,
            'sender': {'nickname': 'human'}, 'message': [
                {'type': 'at', 'data': {'qq': '169901502'}}, {'type': 'text', 'data': {'text': '画一张蓝色圆的图片' if failure=='image' else '你在吗'}}]}
        async def api(client, env, action, params=None):
            nonlocal polls, attempts
            if action == 'get_login_info':
                return {'user_id': 169901502}
            if action == 'get_group_info':
                return {}
            if action == 'get_group_msg_history':
                if params.get('count') == 100:
                    if failure=='archive_failed':raise TimeoutError()
                    if failure=='archive':return {'messages':[incoming]}
                    return {'messages': []}
                polls += 1
                if polls > 10:
                    (bot.RUNTIME / 'STOP').touch()
                    raise AssertionError('recovery stalled despite healthy API')
                if failure == 'poll' and polls == 1:
                    raise httpx.ConnectError('NapCat temporarily unavailable')
                if failure in {'session','session_clear'}:
                    if polls==2:
                        cutoff=time.time()
                        bot.atomic_write_text(bot.RUNTIME/'session-control.json',json.dumps({'id':'stop','mode':'clear' if failure=='session_clear' else 'stop','created':cutoff,'clear_before':cutoff if failure=='session_clear' else 0}))
                    if polls<4 and polls>1:return {'messages':[incoming]}
                    if polls>=4:incoming['message_id']='new-after-stop'
                if failure in {'archive','archive_failed'}:
                    if polls==1:
                        if failure=='archive_failed':incoming['time']=stamp-10
                        return {'messages':[incoming]}
                    incoming['message_id']='new-live'
                incoming['time']=time.time()
                return {'messages': [incoming]}
            if action == 'send_group_msg':
                attempts += 1
                if failure == 'delivery' and attempts == 2:
                    raise httpx.ConnectError('connection interrupted between parts')
                text = ''.join(s.get('data', {}).get('text', '') for s in params['message'] if s['type'] == 'text')
                sent.append(text)
                if failure=='image':
                    image=next(segment for segment in params['message'] if segment['type']=='image')
                    self.assertTrue(image['data']['file'].startswith('base64://'))
                    (bot.RUNTIME/'STOP').touch()
                if text == ('第二句' if failure == 'delivery' else '在（'):
                    (bot.RUNTIME / 'STOP').touch()
                return {'message_id': 100 + len(sent)}
            raise AssertionError(action)
        async def generate(*args, **kwargs):
            nonlocal generations
            generations += 1
            start_polls.append(polls)
            if failure in {'session','session_clear'} and generations==1:await asyncio.Event().wait()
            if failure=='session_clear' and generations==2:self.assertFalse(any(row.get('_id')=='1' for row in args[2]))
            if failure in {'archive','archive_failed'}:
                self.assertGreaterEqual(polls,2)
                self.assertTrue(any(row.get('_historical') for row in args[2]))
            if failure=='image':return image_path
            if failure == 'delivery':
                return ReplyText('第一句\n第二句', chunks=['第一句', '第二句'])
            return '在（'
        async def tick(seconds):
            delays.append(seconds)
            await real_sleep(0)
            await real_sleep(0)
        def save(state):
            states.append(dict(state))
            if failure == 'state' and len(states) <= 2:
                error = PermissionError('another reader temporarily holds state.json')
                error.winerror = 5
                with patch.object(bot, 'atomic_write_text', side_effect=error):
                    self.assertFalse(real_save(state))
            else:
                real_save(state)
        def style_save(language):
            nonlocal style_saves
            style_saves += 1
            if failure == 'local' and style_saves == 2:
                raise OSError('temporary style cache write failure')
            real_style_save(language)
        with tempfile.TemporaryDirectory() as folder:
            image_path=Path(folder)/'temp/ai-images'/(uuid.uuid4().hex+'.png');image_path.parent.mkdir(parents=True)
            Image.new('RGB',(8,8),'blue').save(image_path)
            with patch.object(bot, 'RUNTIME', Path(folder)), patch.object(bot, 'settings', lambda: {
                    **cfg, 'poll_seconds': 6, 'mention_cooldown_seconds': 0,
                    'passive_probability': 0, 'memory_enabled': False, 'style_learning_enabled': False}), \
                    patch.object(bot, 'api', api), patch.object(bot, 'generate_for_message', generate), \
                    patch.object(bot, 'save_state', save), patch.object(bot.StyleMemory, 'save', style_save), \
                    patch.object(bot,'maintain_images',AsyncMock()), \
                    patch.object(asyncio, 'sleep', tick):
                await bot.run()
                self.assertEqual(generations, 2 if failure in {'session','session_clear'} else 1)
                self.assertLessEqual(polls, 6 if failure in {'session','session_clear'} else 3)
                self.assertLessEqual(max(delays), 6)
                final = json.loads((bot.RUNTIME / 'state.json').read_text(encoding='utf-8'))
                if failure == 'delivery':
                    self.assertEqual(sent, ['第一句', '第二句'])
                    self.assertEqual(len(final['reply_times']), 2)
                elif failure=='image':
                    self.assertEqual(sent,['']);self.assertTrue(image_path.exists())
                else:
                    self.assertEqual(sent, ['在（'])
                if failure == 'poll':
                    self.assertEqual(start_polls, [2])
                    self.assertTrue(any(s.get('online') is False and 'error' in s for s in states))
                if failure == 'local':
                    errors = [s for s in states if 'error' in s]
                    self.assertEqual(len(errors), 1)
                    self.assertTrue(errors[0]['online'])
                if failure == 'state':
                    self.assertFalse(any('error' in state for state in states))
                    self.assertGreaterEqual(len(states), 3)


if __name__ == '__main__':
    unittest.main()
