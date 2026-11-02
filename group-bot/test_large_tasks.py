import asyncio
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch
import httpx
import bot
import large_tasks as tasks
from message_delivery import delivery_plan,deliver_one,DeliveryAborted


class LargeTaskTests(unittest.IsolatedAsyncioTestCase):
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
        for text in ['写一个好看的html前端发给我','根据这个写一个html发给我','生成一份csv文件','编写Python代码文件']:self.assertTrue(tasks.requested(text))
        for text in ['这个html写得不错','不要生成文件','你昨天发的文件好看','你会写代码吗']:
            self.assertFalse(tasks.requested(text))

    async def test_huge_or_meaningless_requests_are_rejected_without_generation(self):
        for text in ['写一个55g的文件','生成55GB文件','给我生成无限大文件','生成空白文件','生成填满硬盘的文件','生成100MB的文件']:
            api=AsyncMock();model=AsyncMock()
            result,status=await tasks.generate(self.cfg(),self.target(text=text),[],api,model,Path('.'))
            self.assertIsNone(result);self.assertEqual(status['status'],'rejected',text);api.assert_not_awaited();model.assert_not_awaited()

    async def test_upload_permission_denial_unknown_and_positive_are_distinct(self):
        for api,expected in [(self.api('member'),False),(self.api('member',can_upload_file=False),False),(self.api('member',can_upload_file=True),True),(self.api('admin'),True)]:
            status=await tasks.permission(api,985569438,169901502);self.assertEqual(status['allowed'],expected)
        status=await tasks.permission(self.api('member'),985569438,169901502);self.assertEqual(status['status'],'unknown')
        status=await tasks.permission(AsyncMock(side_effect=TimeoutError),985569438,169901502);self.assertEqual(status['status'],'unknown')

    async def test_full_group_space_stops_generation_before_model(self):
        async def api(action,params):return {'user_id':169901502,'role':'owner'} if action=='get_group_member_info' else {'file_count':10000,'limit_count':10000}
        model=AsyncMock();result,status=await tasks.generate(self.cfg(),self.target(),[],api,model,Path('.'))
        self.assertIsNone(result);self.assertFalse(status['allowed']);model.assert_not_awaited()

    async def test_source_file_contents_reach_generation_and_only_owned_path_is_written(self):
        api=self.api();seen=[]
        async def model(cfg,prompt,schema):
            seen.append(prompt);self.assertEqual(cfg['model_timeout_seconds'],150)
            return {'filename':'report.html','content':'<!doctype html><title>日志分析</title>','summary':'已生成日志页面'}
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
                await tasks.generate(self.cfg(large_task_max_file_bytes=limit),self.target(),[],self.api(),AsyncMock(return_value={'filename':name,'content':content,'summary':''}),folder)
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
            return {'filename':'log.html','content':'<html>E42</html>','summary':'日志页面已生成'}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'RuntimeError E42'))) as client:
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),patch.object(bot,'generate_json',model),patch.object(bot,'analyze_roles',AsyncMock()) as roles:
                result=await bot.generate_for_message(client,self.cfg(),[],'被@',target)
                self.assertEqual(result.segments[0]['type'],'_generated_group_file');self.assertTrue(seen);roles.assert_not_awaited()
