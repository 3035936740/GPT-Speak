import asyncio,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
import bot,sandbox_task,large_tasks
from task_execution_state import PROBE
from task_file_metrics import describe
from account_usage import reported_usage


class MetricsTests(unittest.TestCase):
    def test_probe_and_export_agree_on_utf8_bom_boundary_and_binary(self):
        files={'1.txt':'中文😀\n'.encode(),'bom.txt':b'\xef\xbb\xbfhello',
            'boundary.txt':b'a'*65535+'龙'.encode(),'salt.bin':bytes(range(67)),
            'bad.txt':b'hello\xe9','empty.txt':b''}
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'output';output.mkdir()
            for name,data in files.items():(output/name).write_bytes(data)
            process=subprocess.run([sys.executable,'-I','-c',PROBE.replace('CAP','1000000')],cwd=root,capture_output=True,check=True)
            rows=json.loads(process.stdout)
        for row in rows:
            self.assertEqual(row,describe(row['name'],files[row['name']],hashed=True))
        self.assertEqual(describe('1.txt',files['1.txt'])['characters'],4)
        self.assertNotIn('characters',describe('salt.bin',files['salt.bin']))

    def test_actual_usage_never_guesses_missing_tokens(self):
        def event(usage):return json.dumps({'type':'turn.completed','usage':usage}).encode()
        self.assertIsNone(reported_usage(b'no usage'))
        self.assertEqual(reported_usage(event({'input_tokens':20,'output_tokens':5})),
            {'input_tokens':20,'output_tokens':5,'cached_input_tokens':None})
        self.assertEqual(reported_usage(event({'input_tokens':20,'output_tokens':5,'input_tokens_details':{'cached_tokens':12}}))['cached_input_tokens'],12)
        for usage in ({'input_tokens':True,'output_tokens':5},{'input_tokens':1,'output_tokens':5,'cached_input_tokens':2}):
            self.assertIsNone(reported_usage(event(usage)))


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def box(self):
        return type('Box',(),{'work':'/workspace/jobs/test','image':'python:3','name':'box',
            'start':AsyncMock(),'inputs':AsyncMock(),'restore':AsyncMock(),'close':AsyncMock(),
            'python':AsyncMock(return_value=(0,b'verified')),
            'inventory':AsyncMock(side_effect=[[],[describe('result.mid',b'MThd\0actual',True)]]),
            'outputs':AsyncMock(return_value=[('result.mid',b'MThd\0actual')])})()

    async def test_single_call_executes_and_exports_without_done_and_saves_measured_stats(self):
        box=self.box();calls=0
        usage={'input_tokens':100,'cached_input_tokens':80,'output_tokens':20}
        async def model(cfg,prompt,schema):
            nonlocal calls
            calls+=1;self.assertIn('run_and_finish',cfg['_developer_instructions'])
            cfg['_task_usage_sink'](usage)
            return {'action':'run_and_finish','code':'write_and_verify()','summary':'完成'}
        with tempfile.TemporaryDirectory() as root,patch.object(sandbox_task,'Sandbox',return_value=box):
            cfg={**bot.settings(),'large_task_executor':'podman','large_task_max_steps':1,'_task_project_root':str(bot.ROOT)}
            reply,result=await sandbox_task.generate(cfg,{'_task_trace_id':'a'*32,'text':'编排midi'},[],model,root,[])
            row=json.loads((Path(root)/'task-history'/('a'*32+'.json')).read_text(encoding='utf-8'))
            self.assertEqual(calls,1);self.assertEqual(result['status'],'ready')
            self.assertEqual(row['execution'][0]['usage'],usage)
            self.assertEqual(row['execution'][0]['original_output_bytes'],8)
            self.assertEqual(row['execution'][0]['files'][0]['bytes'],11)
            self.assertEqual(row['model_calls'][0]['usage'],usage)
            self.assertEqual(row['files'],[describe('result.mid',b'MThd\0actual')])
            box.close.assert_awaited_once()

    async def test_finish_does_not_accept_failed_script_or_empty_deliverables(self):
        for exit_code,inventory in ((0,[]),(1,[describe('bad.mid',b'broken',True)]),(0,[describe('empty.mid',b'',True)])):
            with self.subTest(exit=exit_code,files=inventory),tempfile.TemporaryDirectory() as root:
                box=self.box();box.inventory=AsyncMock(side_effect=[[],inventory]);box.python=AsyncMock(return_value=(exit_code,b'log'));box.outputs=AsyncMock(return_value=[])
                cfg={**bot.settings(),'large_task_executor':'podman','large_task_max_steps':1,'_task_project_root':str(bot.ROOT)}
                with patch.object(sandbox_task,'Sandbox',return_value=box),self.assertRaisesRegex(ValueError,'step limit'):
                    await sandbox_task.generate(cfg,{'_task_trace_id':'a'*32,'text':'make midi'},[],AsyncMock(return_value={'action':'run_and_finish','code':'x','summary':''}),root,[])
                row=json.loads((Path(root)/'task-history'/('a'*32+'.json')).read_text(encoding='utf-8'))
                self.assertIn('未通过',row['execution'][0]['completion_issue'])
                self.assertNotIn('artifact_file',row)

    async def test_text_mode_also_records_usage_and_file_metrics(self):
        with tempfile.TemporaryDirectory() as root:
            cfg={**bot.settings(),'group_id':100,'chat_group_whitelist':[100],'enabled':True,'large_tasks_enabled':True,'_task_project_root':str(bot.ROOT)}
            async def model(cfg,prompt,schema):
                cfg['_task_usage_sink']({'input_tokens':10,'cached_input_tokens':None,'output_tokens':5})
                return {'filename':'1.txt','content':'你好😀','complete':True,'summary':''}
            async def api(action,params):return {'user_id':169901502,'role':'admin'}
            reply,_=await large_tasks.generate(cfg,{'_id':'x','_self_id':169901502,'text':'write file'},[],api,model,root)
            row=json.loads((Path(root)/'task-history'/(reply.task_view_id+'.json')).read_text(encoding='utf-8'))
            self.assertEqual(row['files'][0]['characters'],3)
            self.assertEqual(row['files'][0]['bytes'],10)
            self.assertEqual(row['model_calls'][0]['usage']['output_tokens'],5)
