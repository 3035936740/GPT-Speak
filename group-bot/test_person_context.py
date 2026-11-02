import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch
import bot
import profile_queries as profiles
from person_context import collect
from group_memory import GroupMemory


class PersonContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):profiles._cache.clear()
    def target(self,**kw):return {'_id':'task','_uid':'11111','_self_id':'99999','_time':time.time(),'at_bot':True,
        '_at_ids':['99999','22222'],'text':'以你对@22222的印象做一个html页面',**kw}
    async def api(self,client,env,action,params):
        if action=='get_login_info':return {'user_id':99999}
        if action=='get_group_member_info':return {'user_id':params['user_id'],'role':'member','card':'笔刷刷'}
        if action=='get_group_file_system_info':return {'file_count':1,'limit_count':10000}
        if action=='get_stranger_info':return {'user_id':params['user_id'],'nickname':'笔刷刷','personal_note':'喜欢画画和像素艺术','phone':'不要带入'}
        raise AssertionError(action)

    async def test_named_person_profile_and_exact_account_context_reach_artifact_model(self):
        target=self.target();history=[{'_id':'b','_uid':'22222','_time':time.time()-20,'text':'我最近在画像素小动物'},
            {'_id':'c','_uid':'33333','_time':time.time()-10,'text':'不要混进另一个人的资料'}]
        async def model(cfg,prompt,schema,images=None):
            data=json.loads(prompt);subject=data['person_references'][0]
            self.assertEqual(subject['qq_id'],'22222');self.assertEqual(data['requester_qq_id'],'11111')
            self.assertIn('喜欢画画',subject['public_profile']['description']);self.assertIn('像素小动物',json.dumps(subject,ensure_ascii=False))
            self.assertNotIn('不要混进',json.dumps(subject,ensure_ascii=False));self.assertNotIn('不要带入',prompt)
            return {'filename':'brush.html','content':'<html><body>像素艺术</body></html>','summary':'已生成像素风页面','complete':True}
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',self.api),patch.object(bot,'generate_json',model):
            reply=await bot.generate_for_message(None,{**bot.settings(),'large_tasks_enabled':True},history,'被@',target)
            self.assertEqual(reply.segments[0]['type'],'_generated_group_file')
        self.assertNotIn('_person_context',target);self.assertNotIn('_profile_context',target)

    async def test_user_portrait_means_requester_unless_somebody_else_is_explicitly_mentioned(self):
        mine=self.target(_at_ids=['99999'],text='给我写一份用户画像')
        self.assertEqual(profiles.targets(mine),['11111']);self.assertTrue(profiles.person_request(mine))
        other=self.target(text='给我写一份@22222的用户画像')
        self.assertEqual(profiles.targets(other),['22222']);self.assertTrue(profiles.person_request(other))

    async def test_bare_mention_is_not_profile_lookup_but_asking_to_look_at_person_is(self):
        for text in ['@99999 @22222 早上好','看看@22222的代码','根据这个游戏的印象做页面']:
            self.assertFalse(profiles.person_request(self.target(text=text,_at_ids=['99999'] if '游戏' in text else ['99999','22222'])),text)
        self.assertTrue(profiles.person_request(self.target(text='@99999 看看@22222')))

    async def test_disabled_profile_still_uses_group_chat_without_api_or_cached_bio(self):
        cfg={**bot.settings(),'model_profile_query_enabled':False};target=self.target()
        call=AsyncMock()
        records,_=await profiles.lookup(cfg,target,[],call,scope='g',permitted=lambda uid:True)
        self.assertEqual(records[0]['profile_status'],'disabled');call.assert_not_awaited()
        result=collect(cfg,target,[{'_id':'old','_uid':'22222','text':'喜欢像素画','_time':time.time()-20}],records)
        self.assertEqual(result[0]['status'],'grounded');self.assertNotIn('description',result[0]['public_profile'])

    async def test_blocked_person_and_cross_group_records_are_not_read(self):
        target=self.target();rows=[{'_id':'other','_uid':'22222','text':'别的群的消息','_time':time.time()-20,'_group_id':'3'}]
        result=collect({'group_id':1},target,rows,[],permitted=lambda uid:False)
        self.assertEqual(result,[{'status':'blocked','reason':'指定账号已屏蔽，未读取资料或聊天'}])
        result=collect({'group_id':1},target,rows,[])
        self.assertEqual(result[0]['status'],'insufficient');self.assertFalse(result[0]['recent_messages'])

    async def test_current_request_does_not_count_as_evidence_about_person(self):
        target=self.target(_uid='22222',_at_ids=['99999'],text='给我写一份用户画像')
        self.assertEqual(collect(bot.settings(),target,[target],[])[0]['status'],'insufficient')

    async def test_group_memory_sources_are_looked_up_by_subject_not_requester(self):
        with tempfile.TemporaryDirectory() as folder:
            memory=GroupMemory(Path(folder)/'memory.db')
            try:
                memory.observe({'_id':'old-b','_uid':'22222','text':'我喜欢像素画','_time':time.time()})
                memory.observe({'_id':'old-a','_uid':'11111','text':'我喜欢网页开发','_time':time.time()})
                result=collect({**bot.settings(),'_group_memory':memory},self.target(),[],[])
                text=json.dumps(result,ensure_ascii=False);self.assertIn('像素画',text);self.assertNotIn('网页开发',text)
            finally:memory.close()
