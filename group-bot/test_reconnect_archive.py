"""Exercise the real group loop with offline NapCat/model doubles."""
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
import bot
from image_recognition import ImageRecognition
from recent_chat_journal import RecentChatJournal


class ReconnectArchiveTests(unittest.TestCase):
    def test_chat_checkpoint_retries_transient_windows_file_occupation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recent-chat.jsonl'
            original=Path.replace;attempts=[]
            def replace(source,target):
                if Path(target)==path:
                    attempts.append(str(source))
                    if len(attempts)<3:
                        error=PermissionError('temporary occupation');error.winerror=5;raise error
                return original(source,target)
            journal=RecentChatJournal(path)
            with patch.object(Path,'replace',replace),patch('storage_io.time.sleep'):
                journal.checkpoint([{'_id':'1','text':'存档','_time':time.time(),'_historical':True}])
            self.assertEqual(len(attempts),3)
            self.assertEqual(journal.load()[0]['text'],'存档')
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_recovery_archives_backlog_and_late_arrivals_and_cancels_old_jobs(self):
        self.scenario(False)

    def test_empty_recovery_snapshot_still_fences_late_backlog(self):
        self.scenario(True)

    def scenario(self, empty_recovery):
        async def run(folder):
            clock=[int(time.time())+10]
            started=asyncio.Event();cancelled=asyncio.Event()
            generated=[];sent=[];polls=0;original_sleep=asyncio.sleep
            cfg={**bot.settings(),'expected_self_id':'169901502','poll_seconds':0,'mention_cooldown_seconds':0,
                'memory_enabled':False,'style_learning_enabled':False,'passive_probability':0,
                'proactive_topics_enabled':False}
            def raw(key, stamp, text, image=False):
                segments=[{'type':'at','data':{'qq':'169901502'}}, {'type':'text','data':{'text':text}}]
                if image:segments.append({'type':'image','data':{'url':'https://invalid.test/old.png'}})
                return {'message_id':key,'time':stamp,'user_id':123,'sender':{'nickname':'测试'},'message':segments}
            async def api(client,env,action,params=None):
                if action=='get_login_info':return {'user_id':169901502}
                if action=='get_group_info':return {}
                if action=='get_group_member_list':return []
                if action=='get_group_msg_history':return {'messages':[]}
                if action=='send_group_msg':
                    sent.append(params);(folder/'STOP').touch();return {'message_id':999}
                raise AssertionError(action)
            async def poll(feed,*args):
                nonlocal polls
                polls+=1;clock[0]+=2
                if polls==1:return {'messages':[raw('old-active',clock[0],'旧请求')]}
                if polls==2:
                    await started.wait()
                    raise httpx.ConnectError('offline')
                if polls==3:raise httpx.ConnectError('still offline')
                if polls==4:
                    return {'messages':[] if empty_recovery else [raw('backlog',clock[0]-1,'积压请求',True)]}
                if polls==5:
                    return {'messages':[raw('late-old',clock[0]-4,'延迟的旧消息',True),raw('new',clock[0],'新请求')]}
                if polls>25:(folder/'STOP').touch()
                return {'messages':[]}
            async def generate(client,cfg,history,trigger,target,as_image=False):
                generated.append(target['_id'])
                if target['_id']=='old-active':
                    started.set()
                    try:await asyncio.Event().wait()
                    finally:cancelled.set()
                self.assertTrue(all(row.get('_historical') for row in history if row['_id']!='new'))
                return '收到'
            async def tick(seconds):
                await original_sleep(0);await original_sleep(0)
            with patch.object(bot,'RUNTIME',folder),patch.object(bot,'settings',return_value=cfg), \
                    patch.object(bot,'api',api),patch.object(bot,'generate_for_message',generate), \
                    patch('latest_messages.LatestMessages.poll',poll),patch.object(bot.time,'time',lambda:clock[0]), \
                    patch.object(bot.asyncio,'sleep',tick),patch.object(ImageRecognition,'offer') as offer:
                await bot.run()
            self.assertTrue(cancelled.is_set())
            self.assertEqual(generated,['old-active','new'])
            self.assertEqual(len(sent),1)
            self.assertEqual([call.args[0]['_id'] for call in offer.call_args_list], ['old-active','new'])
            rows={row['_id']:row for row in RecentChatJournal(folder/'recent-chat.jsonl').load()}
            self.assertTrue(rows['late-old']['_historical'])
            if not empty_recovery:self.assertTrue(rows['backlog']['_historical'])
            self.assertFalse(rows['new'].get('_historical',False))
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(Path(folder)))


if __name__=='__main__':unittest.main()
