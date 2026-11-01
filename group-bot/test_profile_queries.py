import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

import bot
import profile_queries as profiles
import tool_workflow


class ProfileQueries(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        profiles._cache.clear()
        self.calls=[]
        self.target={'_uid':'11111','_self_id':'99999','text':'看看我的个人简介','at_bot':True,
            '_audience':'direct','_id':'profile-test','_time':1000,'sender':'甲'}

    async def api(self,action,params):
        self.calls.append((action,params))
        return {'user_id':params['user_id'],'nickname':'昵称','card':'群名片','personal_note':'公开的签名',
            'phone':'不应传入','age':99,'operator_note':'不是公开简介'}

    async def lookup(self,cfg=None,target=None,**kw):
        return await profiles.lookup(cfg or {},target or self.target,[],self.api,
            scope='group:1',permitted=kw.pop('permitted',lambda uid:True),**kw)

    async def test_ordinary_chat_at_and_non_personal_bio_do_not_query(self):
        for text in ['早上好','@99999 今天吃什么','介绍这款游戏的简介','你帮我画个头像','我昨天改了我的头像','我的签名挺有意思','我的接口签名错误了','头像开关怎么配置']:
            self.assertEqual(await self.lookup(target={**self.target,'text':text}),([],[]))
        self.assertEqual(self.calls,[])

    async def test_public_fields_are_allowlisted_and_missing_is_not_nonexistent(self):
        rows,avatars=await self.lookup()
        self.assertEqual(rows[0]['description'],'公开的签名');self.assertEqual(avatars,[])
        self.assertEqual([call[0] for call in self.calls],['verify_member','get_stranger_info'])
        self.assertNotIn('phone',rows[0]);self.assertNotIn('operator_note',rows[0]);self.assertNotIn('age',rows[0])
        self.assertEqual(profiles.public_fields({'nickname':'小明'})['description'],'')
        self.assertIn('接口未提供',rows[0]['note'])

    async def test_off_never_reads_cached_data_or_calls_api(self):
        await self.lookup();self.calls.clear()
        rows,_=await self.lookup({'model_profile_query_enabled':False})
        self.assertEqual(rows[0]['profile_status'],'disabled');self.assertNotIn('description',rows[0]);self.assertEqual(self.calls,[])
        rows,avatars=await self.lookup({'model_avatar_query_enabled':False},target={**self.target,'text':'看看我的头像'})
        self.assertEqual(rows[0]['avatar_status'],'disabled');self.assertEqual(avatars,[]);self.assertEqual(self.calls,[])

    async def test_cache_is_scoped_and_membership_rechecked(self):
        await self.lookup();self.calls.clear()
        rows,_=await self.lookup();self.assertEqual(rows[0]['profile_status'],'cached')
        self.assertEqual([c[0] for c in self.calls],['verify_member'])
        await profiles.lookup({},self.target,[],self.api,scope='group:2',permitted=lambda uid:True)
        self.assertEqual([c[0] for c in self.calls],['verify_member','verify_member','get_stranger_info'])
        self.calls.clear();await self.lookup({'profile_query_cache_seconds':0})
        self.assertIn('get_stranger_info',[c[0] for c in self.calls])

    async def test_blocked_or_inactive_have_no_side_effects(self):
        self.assertEqual(await self.lookup(permitted=lambda uid:False),([],[]))
        self.assertEqual(await self.lookup(active=lambda:False),([],[]));self.assertEqual(self.calls,[])

    async def test_toggle_change_during_fetch_discards_data_and_avatar(self):
        live=True
        async def call(action,params):
            nonlocal live
            row=await self.api(action,params)
            if action=='get_stranger_info':live=False
            return row
        result=await profiles.lookup({}, {**self.target,'text':'我的头像和个人简介'},[],call,
            scope='1',permitted=lambda uid:True,active=lambda:live)
        self.assertEqual(result,([],[]))

    async def test_member_or_profile_account_mismatch_is_not_used(self):
        async def wrong(action,params):return {'user_id':22222,'personal_note':'错误账号'}
        rows,avatars=await profiles.lookup({},self.target,[],wrong,scope='1',permitted=lambda uid:True)
        self.assertEqual(rows[0]['status'],'unavailable');self.assertNotIn('description',rows[0]);self.assertEqual(avatars,[])

    async def test_api_failure_does_not_claim_read_success(self):
        async def fail(action,params):raise OSError('offline')
        rows,_=await profiles.lookup({},self.target,[],fail,scope='1',permitted=lambda uid:True)
        self.assertEqual(rows[0]['status'],'unavailable');self.assertNotIn('description',rows[0])

    async def test_explicit_mentions_quotes_unique_names_and_ambiguous_names(self):
        target={**self.target,'text':'看看他的头像','_at_ids':['99999','22222']}
        self.assertEqual(profiles.targets(target),['22222'])
        target.pop('_at_ids');target['_quoted_message']={'_uid':'33333'}
        self.assertEqual(profiles.targets(target),['33333'])
        target={'text':'看看小猫的简介','_identity_snapshot':{'mentioned_members':[{'qq_id':'1','nickname':'小猫'}]}}
        self.assertEqual(profiles.targets(target),['1'])
        target['_identity_snapshot']['mentioned_members'].append({'qq_id':'2','nickname':'小猫'})
        rows,avatars=await self.lookup(target=target)
        self.assertEqual(rows[0]['status'],'ambiguous_subject');self.assertEqual(self.calls,[]);self.assertEqual(avatars,[])

    async def test_recent_followup_inherits_kind_not_subject(self):
        before={**self.target,'text':'我的头像好看吗','_time':950}
        target={**self.target,'text':'那他的呢','_quoted_message':{'_uid':'22222'}}
        resolved=profiles.request_target(target,[before]);self.assertEqual(profiles.targets(resolved),['22222'])
        self.assertEqual(profiles.intent(resolved['text']),(True,False))
        for old in [{**before,'_uid':'3'},{**before,'_time':800},{**before,'_historical':True}]:
            self.assertEqual(profiles.request_target(target,[old]),target)

    async def test_group_profile_reaches_actual_reply_prompt_and_is_ephemeral(self):
        cfg={**bot.settings(),'multi_role_enabled':False}
        captured={}
        async def api(client,env,action,params):return await self.api(action,params)
        async def reply(cfg,context,trigger,target,paths):
            captured.update(json.loads(json.dumps(target['_profile_context'][0])));return bot.ReplyText('看到了你的签名')
        with patch.object(bot,'api',api),patch.object(bot,'analyze_roles',AsyncMock(return_value={})), \
                patch.object(bot,'generate',reply),patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
            result=await bot.generate_for_message(None,cfg,[],'at',self.target)
        self.assertEqual(captured['description'],'公开的签名');self.assertIn('签名',str(result))
        self.assertNotIn('_profile_context',self.target)

    async def test_group_avatar_is_attached_only_after_successful_download(self):
        captured={};cfg=bot.settings();target={**self.target,'text':'我的头像好看吗'}
        async def api(client,env,action,params):return await self.api(action,params)
        async def download(client,url,path,cfg):path.write_bytes(b'fake-test-image')
        async def reply(cfg,context,trigger,target,paths):
            captured['rows']=target['_profile_context'];captured['paths']=len(paths);return bot.ReplyText('头像很可爱')
        with patch.object(bot,'api',api),patch.object(bot,'download_image',download),patch.object(bot,'analyze_roles',AsyncMock(return_value={})), \
                patch.object(bot,'generate',reply),patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
            await bot.generate_for_message(None,cfg,[],'at',target)
        self.assertEqual(captured['rows'][0]['avatar_status'],'attached');self.assertEqual(captured['paths'],1)


class ToolWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_verified_research_is_carried_to_image_generation(self):
        cfg={**bot.settings(),'_reserve_extra_call':lambda:True}
        target={'_id':'draw','_uid':'11111','_self_id':'99999','text':'先查官方设定，再画这个角色',
            'at_bot':True,'_audience':'direct','_time':1000,'sender':'甲'}
        calls=[]
        async def research(cfg,prompt,schema,images=None):
            calls.append('research');self.assertEqual(cfg['_web_search_mode'],'live')
            return {'facts':'银发、蓝色外套','source_url':'https://example.com/character','published_date':''}
        async def draw(cfg,runtime,request,references):
            calls.append('draw');self.assertEqual(cfg['_image_research_evidence']['facts'],'银发、蓝色外套')
            self.assertIn('先查官方设定',request);return 'image-result'
        with patch.object(bot,'generate_json',research),patch.object(bot,'create_image',draw),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
            self.assertEqual(await bot.generate_for_message(None,cfg,[],'draw',target,as_image=True),'image-result')
        self.assertEqual(calls,['research','draw'])

    async def test_research_failure_prevents_generation(self):
        cfg={**bot.settings(),'_reserve_extra_call':lambda:True}
        target={'_id':'draw','_uid':'11111','_self_id':'99999','text':'先查资料再画', 'at_bot':True,'_audience':'direct'}
        draw=AsyncMock()
        with patch.object(bot,'generate_json',AsyncMock(side_effect=ValueError('no web tool evidence'))), \
                patch.object(bot,'create_image',draw),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
            with self.assertRaisesRegex(ValueError,'no web tool evidence'):
                await bot.generate_for_message(None,cfg,[],'draw',target,as_image=True)
        draw.assert_not_awaited()

    async def test_disabled_budget_stop_and_failed_evidence_do_not_continue(self):
        with tempfile.TemporaryDirectory() as folder:
            pages=AsyncMock(return_value=[]);gen=AsyncMock(return_value={'facts':'red','source_url':'','published_date':''})
            for overrides,guard in [({'tool_workflow_enabled':False},lambda:True),({'tool_workflow_max_steps':1},lambda:True),({},lambda:False),({'_reserve_extra_call':lambda:False},lambda:True)]:
                with self.assertRaises(ValueError):
                    await tool_workflow.research({'_reserve_extra_call':lambda:True,**overrides},'画',[],pages,gen,folder,folder,guard)
            gen.assert_not_awaited();pages.assert_not_awaited()
            with self.assertRaisesRegex(ValueError,'no grounded facts'):
                await tool_workflow.research({'_reserve_extra_call':lambda:True},'画',[],pages,gen,folder,folder)

    async def test_links_are_read_before_research_with_reserved_final_step(self):
        calls=[]
        async def read(urls):calls.append('read');return [{'status':'read','url':urls[0],'body':'official red coat'}]
        async def gen(cfg,prompt,schema):
            calls.append('research');self.assertIn('official red coat',prompt)
            return {'facts':'red coat','source_url':'https://example.com/source','published_date':''}
        with tempfile.TemporaryDirectory() as folder:
            evidence=await tool_workflow.research({'_reserve_extra_call':lambda:True},'依照官网绘图',['https://example.com/source'],read,gen,folder,folder)
        self.assertEqual(calls,['read','research']);self.assertEqual(len(evidence['steps']),2)

    async def test_web_answer_limit_is_configurable_in_prompt_schema_and_validator(self):
        answer='有来源的解释，'*30
        async def gen(cfg,prompt,schema,images=None):
            self.assertIn('最多300字',prompt)
            self.assertEqual(json.loads(Path(schema).read_text())['properties']['answer']['maxLength'],300)
            return {'answer':answer,'source_url':'https://example.com/source','source_title':'source','published_date':''}
        with patch.object(bot,'generate_json',gen):result=await bot.researched_reply({**bot.settings(),'web_answer_max_chars':300},'解释差异')
        self.assertEqual(str(result),answer)


if __name__=='__main__':unittest.main()
