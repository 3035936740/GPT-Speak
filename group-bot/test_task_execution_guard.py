import asyncio,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
import bot,large_tasks,sandbox_task
from console_config import atomic
from task_controls import force_cancel
from task_execution_state import Progress
from task_history import view


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def test_content_progress_ignores_renames_and_corrects_then_stops(self):
        progress=Progress(limit=6);progress.baseline([])
        for index in range(6):
            issue=progress.observe({'code':'print(notes)','summary':'analysis','state':{'plan':['parse','write','verify'],'completed':['parse'],'next_step':'write'}},0,'notes '*1000,[])
            if index==2:self.assertIn('correction',progress.state)
        self.assertIn('stalled',issue)
        progress.observe({'code':'write midi','summary':'write'},0,'saved',[{'name':'x.mid','sha256':'A','bytes':4}])
        self.assertEqual(progress.stale,0)
        progress.observe({'code':'rename','summary':'rename'},0,'',[{'name':'renamed.mid','sha256':'A','bytes':4}])
        self.assertEqual(progress.stale,1)
        self.assertEqual(progress.state['plan'],['parse','write','verify'])

    async def test_force_cancel_interrupts_waiting_model_and_not_other_task(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'runtime';runtime.mkdir()
            cfg={**bot.settings(),'enabled':True,'large_tasks_enabled':True,'group_id':100,'chat_group_whitelist':[100],
                '_task_project_root':str(bot.ROOT)}
            atomic(root/'settings.json',{k:v for k,v in cfg.items() if not k.startswith('_')})
            started=asyncio.Event();model_cancelled=asyncio.Event()
            async def slow(cfg,prompt,schema):
                started.set()
                try:await asyncio.Event().wait()
                finally:model_cancelled.set()
            async def api(action,params):return {'user_id':169901502,'role':'admin'}
            first=asyncio.create_task(large_tasks.generate(cfg,{'_id':'a','_self_id':169901502,'text':'write a file'},[],api,slow,runtime))
            await asyncio.wait_for(started.wait(),2)
            row=view(root,100)['rows'][0]
            good=AsyncMock(return_value={'filename':'b.txt','content':'done','complete':True,'summary':'done'})
            second=await large_tasks.generate(cfg,{'_id':'b','_self_id':169901502,'text':'write a file'},[],api,good,runtime)
            force_cancel(root,100,row['id'])
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(first,2)
            self.assertTrue(model_cancelled.is_set())
            self.assertEqual(view(root,100,row['id'])['status'],'cancelled')
            self.assertIn('强制',view(root,100,row['id'])['error'])
            self.assertEqual(view(root,100,second[0].task_view_id)['status'],'ready')
            with self.assertRaises(ValueError):force_cancel(root,200,row['id'])

    async def test_repeated_analysis_stops_and_persists_code_plan_and_truncation(self):
        class Box:
            work='/workspace/jobs/abc';image='python:3';name='box'
            start=AsyncMock();inputs=AsyncMock();restore=AsyncMock();close=AsyncMock()
            python=AsyncMock(return_value=(0,b'HEAD '+b'x'*5000+b' TAIL'))
            inventory=AsyncMock(return_value=[]);outputs=AsyncMock(return_value=[])
        with tempfile.TemporaryDirectory() as folder:
            cfg={**bot.settings(),'large_task_executor':'docker','large_task_stall_limit':6,'large_task_max_steps':0,'_task_project_root':str(bot.ROOT)}
            calls=0
            async def model(config,prompt,schema):
                nonlocal calls
                calls+=1;data=json.loads(prompt)
                if calls==2:
                    self.assertEqual(data['task_state']['last_action']['code'],'print(notes)')
                    self.assertTrue(data['tool_observations'][-1]['truncated'])
                if calls==4:self.assertIn('correction',data['task_state'])
                return {'action':'run','code':'print(notes)','summary':'inspect',
                    'state':{'plan':['inspect','write','verify'],'completed':['inspect'],'phase':'inspect','next_step':'write'}}
            box=Box()
            with patch.object(sandbox_task,'Sandbox',return_value=box):
                with self.assertRaisesRegex(ValueError,'stalled'):
                    await sandbox_task.generate(cfg,{'_task_trace_id':'a'*32,'text':'make midi'},[],model,folder,[])
            self.assertEqual(calls,6);box.close.assert_awaited_once()
            state=json.loads((Path(folder)/'generated-files'/('a'*32)/'resume.json').read_text(encoding='utf-8'))
            self.assertEqual(state['task_state']['last_action']['code'],'print(notes)')
            self.assertEqual(state['task_state']['no_output_change_rounds'],6)
