import asyncio
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,Mock,patch
import httpx
import bot
import large_tasks as tasks
from message_delivery import delivery_plan,deliver_one,DeliveryAborted


class LargeTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_step_limit_allows_more_than_twelve_text_calls(self):
        calls=0
        async def model(cfg,prompt,schema):
            nonlocal calls
            calls+=1
            return {'filename':'result.txt','content':'part '+str(calls)+'\n','complete':calls==14,'summary':''}
        with tempfile.TemporaryDirectory() as folder:
            reply,status=await tasks.generate(self.cfg(large_task_max_steps=0),self.target(),[],self.api(),model,Path(folder))
            self.assertEqual(calls,14);self.assertTrue(status['allowed'])

    async def test_step_configuration_accepts_64_zero_and_int32_max(self):
        from console_config import Config,atomic
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);atomic(root/'settings.json',bot.settings());config=Config(root)
            for value in ('0','64','2147483647'):
                config.set('large_task_max_steps',value)
                self.assertEqual(config.load()['large_task_max_steps'],int(value))
            for value in ('-1','2147483648','1.5','true'):
                with self.assertRaises(ValueError):config.set('large_task_max_steps',value)
    def cfg(self,**kw):return {**bot.settings(),**tasks.DEFAULTS,'large_tasks_enabled':True,**kw}
    def target(self,**kw):return {'_id':'100','_uid':'11111','_self_id':'169901502','_time':time.time(),'at_bot':True,'text':'写一个好看的html前端发给我',**kw}
    def api(self,role='admin',**extra):
        async def call(action,params):
            if action=='get_group_member_info':return {'user_id':169901502,'role':role,**extra}
            if action=='get_group_file_system_info':return {'file_count':1,'limit_count':10000,'used_space':1,'total_space':10000000}
            raise AssertionError(action)
        return AsyncMock(side_effect=call)

    async def test_defaults_are_experimental_disabled_and_twenty_mib(self):
        self.assertFalse(tasks.DEFAULTS['large_tasks_enabled']);self.assertEqual(tasks.DEFAULTS['large_task_max_file_bytes'],20971520)
        api=AsyncMock();model=AsyncMock()
        result,status=await tasks.generate(tasks.DEFAULTS,self.target(),[],api,model,Path('.'))
        self.assertIsNone(result);self.assertEqual(status['status'],'disabled');api.assert_not_awaited();model.assert_not_awaited()

    async def test_explicit_file_tasks_not_everyday_discussions_or_negative_requests(self):
        for text in ['写一个好看的html前端发给我','根据这个写一个html发给我','生成一份csv文件','编写Python代码文件','你随便做一个html页面发给我','你随手帮我写一个网页','直接做个HTML给我']:self.assertTrue(tasks.requested(text),text)
        for text in ['这个html写得不错','不要生成文件','你昨天发的文件好看','你会写代码吗','写一个html有多难','怎么写html']:
            self.assertFalse(tasks.requested(text))

    async def test_followup_freedom_continues_only_same_users_unfinished_file_request(self):
        target=self.target(text='你想做什么就做什么')
        source=self.target(_id='99',_time=target['_time']-120,text='你随便做一个html页面发给我')
        bot_reply={'_id':'reply','_uid':'169901502','_time':target['_time']-80,'is_bot':True,'_reply_to_uid':target['_uid'],'text':'行，我做个单页HTML给你，想放什么内容？'}
        route=tasks.resolve_request(target,[source,bot_reply]);self.assertIn('html页面',route['request']);self.assertIn('你想做什么',route['request'])
        self.assertIsNone(tasks.resolve_request(target,[]))
        self.assertIsNone(tasks.resolve_request(target,[{**source,'_uid':'other'},bot_reply]))
        self.assertIsNone(tasks.resolve_request(target,[{**source,'_time':target['_time']-601},bot_reply]))
        self.assertIsNone(tasks.resolve_request(target,[source,{**bot_reply,'_large_task_completed':True}]))
        cancel={**source,'_id':'cancel','_time':target['_time']-20,'text':'不用做了'}
        self.assertIsNone(tasks.resolve_request(target,[source,bot_reply,cancel]))
        self.assertIsNone(tasks.resolve_request(self.target(text='不要做了'),[source,bot_reply]))

    async def test_disabled_switch_skips_task_detection_execution_and_model_actions(self):
        cfg=self.cfg(large_tasks_enabled=False);target=self.target(text='你随便做一个html页面发给我')
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch('large_tasks.resolve_request',side_effect=AssertionError('disabled detector called')),patch('large_tasks.generate',side_effect=AssertionError('disabled task executed')),patch.object(bot,'analyze_roles',AsyncMock(return_value={})),patch.object(bot,'generate',AsyncMock(return_value='ordinary reply')):
            self.assertEqual(await bot.generate_for_message(None,cfg,[],'被@',target),'ordinary reply')
        ordinary={'reply_to':target['_id'],'conversation':'direct','should_reply':True,'action':'chat','text':'我就做个HTML页面给你','text_parts':[]}
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch('large_tasks.unfulfilled_promise',side_effect=AssertionError('disabled promise detector called')),patch.object(bot,'generate_json',AsyncMock(return_value=ordinary)):
            result=await bot.generate(cfg,[],'被@',target)
            self.assertNotEqual(getattr(result,'action',None),'generate_file')
        for enabled in [False,True]:
            async def model(config,prompt,schema,images=None):
                actions=json.loads(Path(schema).read_text(encoding='utf-8'))['properties']['action']['enum']
                self.assertEqual('generate_file' in actions,enabled);return {}
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                await bot.generate_reply_json(self.cfg(large_tasks_enabled=enabled),'test',target)
            prompt=bot.build_prompt(self.cfg(large_tasks_enabled=enabled),[],'被@',target)
            self.assertEqual('generate_file' in prompt,enabled)

    async def test_natural_request_and_followup_reach_file_generation_without_a_chat_promise(self):
        for current,context in [(self.target(text='你随便做一个html页面发给我'),[]),
                (self.target(text='你想做什么就做什么'),[self.target(_id='99',_time=time.time()-120,text='你随便做一个html页面发给我')])]:
            async def api(client,env,action,params):return await self.api('member')(action,params)
            async def model(cfg,prompt,schema,images=None):
                self.assertIn('html页面',prompt);self.assertIn('自行决定',cfg['_developer_instructions'])
                return {'complete':True,'filename':'page.html','content':'<!doctype html><html><title>完成</title></html>','summary':'页面已上传'}
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),patch.object(bot,'generate_json',model),patch.object(bot,'generate',AsyncMock(side_effect=AssertionError('chat promise instead of file'))):
                reply=await bot.generate_for_message(None,self.cfg(),context,'被@',current)
                self.assertEqual(reply.segments[0]['type'],'_generated_group_file');self.assertEqual(str(reply),'')

    async def test_context_planner_file_action_executes_even_without_a_keyword_match(self):
        target=self.target(text='整一页能点的，给我个文件')
        self.assertFalse(tasks.requested(target['text']))
        decision={'addressed_to_bot':True,'confidence':.99,'plan':{'action':'create_file'}}
        async def api(client,env,action,params):return await self.api('member')(action,params)
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),patch.object(bot,'analyze_roles',AsyncMock(return_value={'context':decision})),patch.object(bot,'generate',AsyncMock(side_effect=AssertionError('file plan became chat'))),patch.object(bot,'generate_json',AsyncMock(return_value={'complete':True,'filename':'page.html','content':'<html></html>','summary':'页面完成'})):
            reply=await bot.generate_for_message(None,self.cfg(),[],'被@',target)
            self.assertEqual(reply.segments[0]['type'],'_generated_group_file')
        self.assertFalse(tasks.followup_only('随便整一个HTML文件'))

    async def test_reply_model_can_route_a_file_action_to_actual_generation(self):
        target=self.target(text='整一页能点的，给我个文件');calls=[];reserve=Mock(return_value=True)
        async def api(client,env,action,params):return await self.api('member')(action,params)
        async def model(cfg,prompt,schema,images=None):
            calls.append(Path(schema).name)
            if len(calls)==1:return {'reply_to':target['_id'],'conversation':'direct','should_reply':True,'action':'generate_file','text':''}
            return {'complete':True,'filename':'page.html','content':'<html></html>','summary':'页面完成'}
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),patch.object(bot,'analyze_roles',AsyncMock(return_value={})),patch.object(bot,'generate_json',model):
            reply=await bot.generate_for_message(None,self.cfg(_reserve_extra_call=reserve),[],'被@',target)
            self.assertEqual(reply.segments[0]['type'],'_generated_group_file');self.assertEqual(len(calls),2);reserve.assert_called_once()

    async def test_completed_task_marker_is_only_on_post_upload_message_and_survives_journal(self):
        from recent_chat_journal import compact,RecentChatJournal
        packet={'group_id':985569438,'name':'test.html','file':'base64://eA=='}
        plan=delivery_plan([{'type':'_generated_group_file','data':packet},{'type':'text','data':{'text':'完成'}}],985569438)
        self.assertNotIn('_large_task_completed',plan[0]);self.assertTrue(plan[1]['_large_task_completed'])
        row=compact({'_id':'sent','_time':time.time(),'text':'完成','_large_task_completed':True})
        self.assertTrue(row['_large_task_completed'])
        source=self.target(_id='99',_time=time.time()-20)
        with tempfile.TemporaryDirectory() as folder:
            journal=RecentChatJournal(Path(folder)/'recent.jsonl');journal.append(source)
            self.assertTrue(journal.mark_file_completed('99'))
            rows=journal.load();self.assertTrue(rows[0]['_large_task_completed'])
            self.assertIsNone(tasks.resolve_request(self.target(text='继续'),rows))

    async def test_semantic_action_cannot_revive_completed_or_cancelled_tasks_from_bare_followup(self):
        for text in ['继续','不用做了']:
            target=self.target(text=text);source=self.target(_id='99',_time=target['_time']-20,_large_task_completed=True)
            decision={'addressed_to_bot':True,'confidence':.99,'plan':{'action':'create_file'}}
            async def ordinary(cfg,context,trigger,adjusted,paths):
                self.assertFalse(cfg['large_tasks_enabled']);self.assertEqual(adjusted['_large_task_status']['status'],'no_pending_task');return '普通对话'
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'analyze_roles',AsyncMock(return_value={'context':decision})),patch('large_tasks.generate',AsyncMock(side_effect=AssertionError('completed task executed again'))),patch.object(bot,'generate',ordinary):
                self.assertEqual(await bot.generate_for_message(None,self.cfg(),[source],'被@',target),'普通对话')

    async def test_empty_file_promise_is_repaired_into_an_executable_action(self):
        target=self.target(text='整一页能点的，给我个文件');calls=[];reserve=Mock(return_value=True)
        async def api(client,env,action,params):return await self.api('member')(action,params)
        async def model(cfg,prompt,schema,images=None):
            calls.append(prompt)
            if len(calls)==1:return {'reply_to':target['_id'],'conversation':'direct','should_reply':True,'action':'chat','text':'好，那我就给你做个HTML页面','text_parts':[]}
            if len(calls)==2:
                self.assertIn('file_task_not_executed',prompt)
                return {'reply_to':target['_id'],'conversation':'direct','should_reply':True,'action':'generate_file','text':''}
            return {'complete':True,'filename':'page.html','content':'<html></html>','summary':'页面完成'}
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),patch.object(bot,'analyze_roles',AsyncMock(return_value={})),patch.object(bot,'generate_json',model):
            reply=await bot.generate_for_message(None,self.cfg(_reserve_extra_call=reserve),[],'被@',target)
            self.assertEqual(reply.segments[0]['type'],'_generated_group_file');self.assertEqual(len(calls),3);self.assertEqual(reserve.call_count,2)

    async def test_semantic_file_generation_respects_remaining_model_budget(self):
        target=self.target(text='整一页能点的，给我个文件')
        model=AsyncMock(return_value={'reply_to':target['_id'],'conversation':'direct','should_reply':True,'action':'generate_file','text':''})
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'analyze_roles',AsyncMock(return_value={})),patch.object(bot,'generate_json',model),patch('large_tasks.generate',AsyncMock()) as generator:
            result=await bot.generate_for_message(None,self.cfg(_reserve_extra_call=lambda:False),[],'被@',target)
            self.assertFalse(result);generator.assert_not_awaited();model.assert_awaited_once()

    async def test_context_model_file_option_is_offered_only_when_enabled(self):
        from role_analysis import analyze
        for enabled in [False,True]:
            seen=[]
            async def model(cfg,prompt,schema,images=None):
                role=Path(schema).name.split('.')[0]
                if role=='context':
                    spec=json.loads(Path(schema).read_text(encoding='utf-8'))
                    self.assertEqual('create_file' in spec['properties']['plan']['properties']['action']['enum'],enabled)
                    self.assertEqual('任务已开启' in prompt,enabled);seen.append(role)
                    return {'addressed_to_bot':True,'intent':'question','needs_web_search':False,'search_query':'','confidence':.9,'reference_message_id':'100','note':''}
                return {'mood':'neutral','reply_tone':'casual','confidence':.9,'note':''}
            with tempfile.TemporaryDirectory() as folder:
                await analyze(self.cfg(large_tasks_enabled=enabled,multi_role_enabled=True,_reserve_extra_call=lambda:True),[],self.target(),[],model,folder)
            self.assertEqual(seen,['context'])

    async def test_huge_or_meaningless_requests_are_rejected_without_generation(self):
        for text in ['写一个55g的文件','生成55GB文件','给我生成无限大文件','生成空白文件','生成填满硬盘的文件','生成100MB的文件']:
            api=AsyncMock();model=AsyncMock()
            result,status=await tasks.generate(self.cfg(),self.target(text=text),[],api,model,Path('.'))
            self.assertIsNone(result);self.assertEqual(status['status'],'rejected',text);api.assert_not_awaited();model.assert_not_awaited()

    async def test_upload_permission_denial_unknown_and_positive_are_distinct(self):
        for api,expected in [(self.api('member'),True),(self.api('member',can_upload_file=False),False),(self.api('member',can_upload_file=True),True),(self.api('admin'),True)]:
            status=await tasks.permission(api,985569438,169901502);self.assertEqual(status['allowed'],expected)
        status=await tasks.permission(self.api('member'),985569438,169901502);self.assertEqual(status['status'],'not_reported')
        status=await tasks.permission(AsyncMock(side_effect=TimeoutError),985569438,169901502);self.assertEqual(status['status'],'unknown')

    async def test_optional_file_preflight_failure_does_not_invent_a_permission_denial(self):
        async def api(action,params):
            if action=='get_group_member_info':return {'user_id':169901502,'role':'member'}
            raise RuntimeError('optional API unsupported')
        status=await tasks.permission(api,985569438,169901502)
        self.assertTrue(status['allowed']);self.assertEqual(status['status'],'not_reported')
        wrong=AsyncMock(return_value={'user_id':11111,'role':'member'})
        self.assertFalse((await tasks.permission(wrong,985569438,169901502))['allowed'])

    async def test_explicit_denial_wins_over_other_positive_flags(self):
        async def api(action,params):
            return {'user_id':169901502,'role':'admin','can_upload':True} if action=='get_group_member_info' else {'can_upload':False}
        status=await tasks.permission(api,985569438,169901502)
        self.assertFalse(status['allowed']);self.assertEqual(status['status'],'denied')

    async def test_full_group_space_stops_generation_before_model(self):
        async def api(action,params):return {'user_id':169901502,'role':'owner'} if action=='get_group_member_info' else {'file_count':10000,'limit_count':10000}
        model=AsyncMock();result,status=await tasks.generate(self.cfg(),self.target(),[],api,model,Path('.'))
        self.assertIsNone(result);self.assertFalse(status['allowed']);model.assert_not_awaited()

    async def test_source_file_contents_reach_generation_and_only_owned_path_is_written(self):
        api=self.api();seen=[]
        async def model(cfg,prompt,schema):
            self.assertEqual(cfg['_usage_category'],'task')
            seen.append(prompt);self.assertGreater(cfg['model_timeout_seconds'],599);self.assertLessEqual(cfg['model_timeout_seconds'],600);self.assertEqual(cfg['model'],'gpt-6.1-sol');self.assertEqual(cfg['reasoning_effort'],'high');self.assertFalse(cfg['fast_mode'])
            return {'complete':True,'filename':'report.html','content':'<!doctype html><html><title>日志分析</title></html>','summary':'已生成日志页面'}
        with tempfile.TemporaryDirectory() as folder:
            reply,status=await tasks.generate(self.cfg(),self.target(text='根据这个写一个html发给我'),[{'source_type':'file','title':'抓取.log','body':'Error E42 source fact'}],api,model,folder)
            files=list(Path(folder).rglob('report.html'));self.assertEqual(len(files),1);self.assertIn('日志分析',files[0].read_text(encoding='utf-8'));self.assertTrue(status['allowed'])
            self.assertIn('Error E42 source fact',seen[0]);self.assertEqual(reply.segments[0]['type'],'_generated_group_file')
            plan=delivery_plan(bot.outgoing_segments(reply,self.target(_delivery_preferences={'auto_target':True,'allow_at':True,'allow_quote':True}),[]),985569438);self.assertEqual([p['action'] for p in plan],['upload_group_file','send_group_msg'])
            self.assertEqual(sum(s['type']=='text' for part in plan for s in part['params'].get('message',[])),1)
            self.assertFalse(list(Path(folder).rglob('*.schema.json')))

    async def test_invalid_filename_and_actual_utf8_size_are_rejected_before_file_write(self):
        for name,content,limit in [('../escape.html','x',100),('C:/secret.txt','x',100),('bad.exe','x',100),('CON.txt','x',100),('safe.html','中文'*10,59)]:
            with tempfile.TemporaryDirectory() as folder,self.assertRaises(ValueError):
                await tasks.generate(self.cfg(large_task_max_file_bytes=limit),self.target(),[],self.api(),AsyncMock(return_value={'complete':True,'filename':name,'content':content,'summary':''}),folder)
                self.assertFalse(list(Path(folder).rglob('safe.html')))

    async def test_missing_or_oversized_reference_never_produces_fake_file_based_result(self):
        model=AsyncMock()
        result,status=await tasks.generate(self.cfg(),self.target(_attachment_status=[{'status':'too_large','name':'a.log'}]),[],self.api(),model,Path('.'))
        self.assertIsNone(result);self.assertEqual(status['status'],'missing_source');model.assert_not_awaited()

    async def test_upload_success_is_once_and_ambiguous_failure_is_never_replayed(self):
        packet={'group_id':985569438,'name':'result.html','file':'base64://'+base64.b64encode(b'<html></html>').decode()}
        for error in [httpx.ReadTimeout('unknown'),httpx.ConnectError('failed'),ValueError('permissions changed')]:
            plan=delivery_plan([{'type':'_generated_group_file','data':packet}],985569438);api=AsyncMock(side_effect=error)
            with self.assertRaises(DeliveryAborted):await deliver_one(api,None,{},plan)
            with self.assertRaises(DeliveryAborted):await deliver_one(api,None,{},plan)
            self.assertEqual(api.await_count,1)
        plan=delivery_plan([{'type':'_generated_group_file','data':packet}],985569438);api=AsyncMock(return_value={'file_id':'qq-file'})
        await deliver_one(api,None,{},plan);self.assertFalse(plan);api.assert_awaited_once()

    async def test_unconfirmed_upload_does_not_send_completion_text(self):
        packet={'group_id':985569438,'name':'result.html','file':'base64://eA=='}
        plan=delivery_plan([{'type':'_generated_group_file','data':packet},{'type':'text','data':{'text':'done'}}],985569438)
        with self.assertRaises(DeliveryAborted):await deliver_one(AsyncMock(return_value={}),None,{},plan)
        self.assertEqual(len(plan),2)

    async def test_actual_api_rechecks_config_and_permission_before_upload(self):
        packet={'group_id':985569438,'name':'result.html','file':'base64://eA=='}
        api=self.api('member',can_upload_file=False)
        async def lookup(client,env,action,params,cfg):return await api(action,params)
        with patch.object(bot,'settings',return_value=self.cfg()),patch('napcat_connection.request',side_effect=lookup) as request:
            with self.assertRaises(ValueError):await bot.api(None,{},'upload_group_file',packet)
            self.assertNotIn('upload_group_file',[call.args[2] for call in request.call_args_list])
        with patch.object(bot,'settings',return_value=self.cfg(large_tasks_enabled=False)),patch('napcat_connection.request',AsyncMock()) as request:
            with self.assertRaises(ValueError):await bot.api(None,{},'upload_group_file',packet)
            request.assert_not_awaited()

    async def test_real_pipeline_reads_quoted_log_before_generating_html(self):
        target=self.target(text='根据这个写一个html发给我',_reply_id='99',_quoted_message={'_id':'99','_files':[{'id':'log-id','name':'抓取日志.log','size':100}]})
        permissions=self.api();seen=[]
        async def api(client,env,action,params):
            if action=='get_group_file_url':return {'url':'https://qq.com/log'}
            return await permissions(action,params)
        async def model(cfg,prompt,schema,images=None):
            self.assertIn('RuntimeError E42',prompt);seen.append(prompt)
            return {'complete':True,'filename':'log.html','content':'<html>E42</html>','summary':'日志页面已生成'}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'RuntimeError E42'))) as client:
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),patch.object(bot,'generate_json',model),patch.object(bot,'analyze_roles',AsyncMock()) as roles:
                result=await bot.generate_for_message(client,self.cfg(),[],'被@',target)
                self.assertEqual(result.segments[0]['type'],'_generated_group_file');self.assertTrue(seen);roles.assert_not_awaited()

    async def test_member_upload_uses_utf8_bytes_over_http_without_windows_path(self):
        raw='<!doctype html><title>中文页面</title>'.encode('utf-8')
        packet={'group_id':985569438,'name':'页面.html','file':'base64://'+base64.b64encode(raw).decode('ascii')}
        actions=[]
        def respond(request):
            action=request.url.path.rsplit('/',1)[-1];actions.append(action)
            params=json.loads(request.content)
            if action=='get_login_info':data={'user_id':169901502}
            elif action=='get_group_member_info':data={'user_id':169901502,'role':'member'}
            elif action=='get_group_file_system_info':data={'file_count':1,'limit_count':10000,'used_space':1,'total_space':10000000}
            elif action=='upload_group_file':
                self.assertTrue(params['file'].startswith('base64://'))
                self.assertEqual(base64.b64decode(params['file'][9:],validate=True),raw)
                self.assertNotIn('file://',params['file']);self.assertNotIn('C:\\',params['file'])
                data={'file_id':'uploaded-by-wsl'}
            else:raise AssertionError(action)
            return httpx.Response(200,json={'retcode':0,'status':'ok','data':data})
        cfg=self.cfg(napcat_connection_mode='http',napcat_host='napcat-wsl',napcat_port=3000)
        cfg.pop('expected_self_id',None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'settings',return_value=cfg):
                plan=delivery_plan([{'type':'_generated_group_file','data':packet}],985569438)
                result=await deliver_one(bot.api,client,{},plan)
                self.assertEqual(result['file_id'],'uploaded-by-wsl');self.assertFalse(plan)
        self.assertEqual(actions,['get_login_info','get_group_member_info','get_group_file_system_info','upload_group_file'])

    async def test_upload_rejection_logs_actual_code_and_reason_without_base64_payload(self):
        from napcat_errors import NapCatAPIError
        packet={'group_id':985569438,'name':'test.html','file':'base64://eA=='}
        api=AsyncMock(side_effect=NapCatAPIError('upload_group_file',{'retcode':1200,'message':'permission denied; base64://eA=='}))
        plan=delivery_plan([{'type':'_generated_group_file','data':packet}],985569438)
        with self.assertRaises(DeliveryAborted) as caught:await deliver_one(api,None,{},plan)
        self.assertIn('1200',str(caught.exception));self.assertIn('permission denied',str(caught.exception));self.assertNotIn('eA==',str(caught.exception))
