import asyncio
import unittest
from unittest.mock import patch
from latest_messages import LatestMessages,bootstrap


class LatestMessageTests(unittest.TestCase):
    def test_failed_bootstrap_does_not_prevent_latest_poll(self):
        async def run():
            async def api(c,e,action,params):
                if params.get('count')==100:raise TimeoutError()
                return {'messages':[{'message_id':'new','time':20}]}
            self.assertEqual(await bootstrap(api,None,None,1),{'messages':[]})
            self.assertEqual((await LatestMessages().poll(api,None,None,1))['messages'][0]['message_id'],'new')
        asyncio.run(run())

    def test_stale_history_recovers_latest_one_without_backfill(self):
        async def run():
            calls=[]
            async def api(c,e,action,params):
                calls.append((action,params))
                if action=='get_group_msg_history':return {'messages':[{'message_id':'old','time':1}]}
                if action=='get_recent_contact':return [{'chatType':2,'peerUin':'1','msgTime':'20','msgId':'raw'}]
                return {'group_id':1,'message_id':'new','time':20}
            feed=LatestMessages();await feed.poll(api,None,None,1);feed.next_probe=0
            result=await feed.poll(api,None,None,1)
            self.assertEqual([row['message_id'] for row in result['messages']],['old','new'])
            self.assertEqual(calls[-1],('get_msg',{'message_id':'raw'}))
            self.assertTrue(calls[0][1]['reverseOrder']);self.assertEqual(calls[0][1]['message_seq'],0)
        asyncio.run(run())

    def test_history_failure_can_still_receive_latest(self):
        async def run():
            async def api(c,e,action,params):
                if action=='get_group_msg_history':raise TimeoutError()
                if action=='get_recent_contact':return [{'chatType':2,'peerUin':'1','msgTime':'20','msgId':'raw'}]
                return {'group_id':1,'message_id':'new','time':20}
            result=await LatestMessages().poll(api,None,None,1)
            self.assertEqual(result['messages'][0]['message_id'],'new')
        asyncio.run(run())

    def test_other_groups_and_invalid_message_ids_are_not_admitted(self):
        async def run():
            async def api(c,e,action,params):
                if action=='get_group_msg_history':return {'messages':[]}
                if action=='get_recent_contact':return [{'chatType':2,'peerUin':'2','msgTime':'20','msgId':'raw'}]
                self.fail('Must not fetch other groups')
            self.assertEqual((await LatestMessages().poll(api,None,None,1))['messages'],[])
        asyncio.run(run())

    def test_bootstrap_has_a_real_deadline(self):
        async def run():
            real_wait=asyncio.wait_for;cancelled=[]
            async def api(*args):
                try:await asyncio.Event().wait()
                finally:cancelled.append(True)
            async def short_wait(task,seconds):return await real_wait(task,.01)
            with patch('latest_messages.asyncio.wait_for',new=short_wait):
                self.assertEqual(await bootstrap(api,None,None,1),{'messages':[]})
            self.assertEqual(cancelled,[True])
        asyncio.run(run())


if __name__=='__main__':unittest.main()
