import asyncio,base64,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
import bot,large_tasks
import task_controls as controls
from task_history import begin,update,view
from console_config import atomic,Config


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.cfg={**bot.settings(),'enabled':True,'large_tasks_enabled':True,'group_id':100,'chat_group_whitelist':[100],
            'large_task_retention_hours':0,'large_task_executor':'text','_task_project_root':str(bot.ROOT)}
        atomic(self.root/'settings.json',self.cfg);self.folder=self.root/'runtime';self.folder.mkdir()
        for name in ('persona.txt','reply_protocol.txt'):(self.root/name).write_text((bot.ROOT/name).read_text(encoding='utf-8-sig'),encoding='utf-8')
        atomic(self.folder/'state.json',{'pid':os.getpid(),'online':True})
        self.target={'_id':'original','sender':'user','text':'make a file','_uid':'11111'}
        self.identity=begin(self.folder,self.target,self.cfg)
        update(self.folder,self.identity,status='failed',error='Task tool step limit reached')

    async def test_partial_binary_is_retained_and_manual_send_ignores_auto_switch(self):
        controls.save_partial(self.folder,self.identity,[('result.mid',b'MThd\0data')],self.cfg)
        row=view(self.root,100,self.identity)
        self.assertTrue(row['partial']);self.assertTrue(row['can_resume']);self.assertTrue(row['artifact_base64'])
        Config(self.root).set('large_task_send_incomplete','off')
        result=controls.enqueue_send(self.root,100,self.identity,True);self.assertTrue(result['ok'])
        with self.assertRaises(ValueError):controls.enqueue_send(self.root,100,self.identity,False)

    async def test_continuation_duplicate_queue_and_completed_rejection(self):
        self.assertTrue(controls.enqueue_resume(self.root,100,self.identity)['ok'])
        self.assertIn('重复',controls.enqueue_resume(self.root,100,self.identity)['message'])
        update(self.folder,self.identity,status='local_ready')
        with self.assertRaises(ValueError):controls.enqueue_resume(self.root,100,self.identity)
        with self.assertRaises(ValueError):controls.enqueue_resume(self.root,200,self.identity)

    async def test_text_continuation_preserves_draft_and_is_local_only(self):
        directory=self.folder/'generated-files'/self.identity;directory.mkdir(parents=True)
        (directory/'draft.txt').write_text('earlier\n',encoding='utf-8')
        update(self.folder,self.identity,filename='result.txt')
        async def model(cfg,prompt,schema):
            self.assertIn('earlier',prompt)
            return {'filename':'result.txt','content':'continued\n','complete':True,'summary':'done'}
        result=await controls.resume(self.root,100,self.identity,self.cfg,model)
        detail=view(self.root,100,result.task_view_id)
        self.assertEqual(detail['status'],'local_ready');self.assertEqual(detail['artifact'],'earlier\ncontinued\n')
        self.assertEqual(detail['resumed_from'],self.identity)
        self.assertEqual(view(self.root,100,self.identity)['resume_child'],result.task_view_id)

    async def test_resume_can_confirm_an_existing_complete_text_file(self):
        directory=self.folder/'generated-files'/self.identity;directory.mkdir(parents=True)
        (directory/'draft.txt').write_text('already written',encoding='utf-8')
        update(self.folder,self.identity,filename='result.txt')
        model=AsyncMock(return_value={'filename':'result.txt','content':'','complete':True,'summary':'done'})
        result=await controls.resume(self.root,100,self.identity,self.cfg,model)
        self.assertEqual(view(self.root,100,result.task_view_id)['artifact'],'already written')

    async def test_sandbox_failure_checkpoints_before_container_close(self):
        import sandbox_task
        events=[]
        class Box:
            work='/workspace/jobs/new';image='python:3';name='box'
            start=AsyncMock();inputs=AsyncMock();python=AsyncMock(return_value=(0,b'partial saved'))
            async def outputs(self):events.append('export');return [('draft.mid',b'MThd\0partial')]
            async def snapshot(self):return [('output/draft.mid',b'MThd\0partial'),('parsed.json',b'{}')]
            async def close(self):events.append('close')
        cfg={**self.cfg,'large_task_executor':'docker','large_task_max_steps':1,'large_task_container_mode':'per_task'}
        target={**self.target,'_task_trace_id':self.identity}
        with patch.object(sandbox_task,'Sandbox',return_value=Box()):
            with self.assertRaisesRegex(ValueError,'step limit'):
                await sandbox_task.generate(cfg,target,[],AsyncMock(return_value={'action':'run','code':'x','summary':''}),self.folder,[])
        self.assertEqual(events,['export','close'])
        directory=self.folder/'generated-files'/self.identity
        self.assertTrue((directory/'resume.json').exists());self.assertTrue((directory/'workspace-snapshot/parsed.json').exists())
        self.assertTrue(view(self.root,100,self.identity)['artifact_base64'])

    async def test_auto_partial_reply_marks_unfinished_and_not_completed(self):
        cfg={**self.cfg,'large_task_executor':'docker'}
        controls.save_partial(self.folder,self.identity,[('result.mid',b'MThd\0draft')],cfg)
        reply=controls.partial_reply(self.folder,self.identity,cfg)
        self.assertTrue(reply.unfinished_task);self.assertFalse(hasattr(reply,'task_view_id'))
        self.assertTrue(reply.segments[0]['data']['name'].startswith('草稿-'))
        self.assertTrue(large_tasks.DEFAULTS['large_task_send_incomplete'])

    async def test_manual_upload_uncertain_is_not_replayed(self):
        controls.save_partial(self.folder,self.identity,[('result.txt',b'partial')],self.cfg)
        from napcat_connection import NapCatHTTPClient
        calls=[]
        async def request(client,env,action,params,cfg):
            calls.append(action)
            if action=='get_login_info':return {'user_id':169901502}
            if action=='get_group_member_info':return {'user_id':169901502,'role':'admin'}
            if action=='get_group_file_system_info':return {}
            if action=='upload_group_file':raise TimeoutError('uncertain')
        with patch('napcat_connection.request',request):
            with self.assertRaises(TimeoutError):await controls.send(self.root,100,self.identity,True)
            with self.assertRaisesRegex(ValueError,'重复'):await controls.send(self.root,100,self.identity,True)
        self.assertEqual(calls.count('upload_group_file'),1)
