import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from artifact_integrity import problem
import large_tasks
import bot


class IntegrityTests(unittest.IsolatedAsyncioTestCase):
    def cfg(self,**more):return {**bot.settings(),**large_tasks.DEFAULTS,'large_tasks_enabled':True,**more}
    async def api(self,action,params):return {'user_id':169901502,'role':'member'} if action=='get_group_member_info' else {}
    def target(self):return {'text':'写一个完整HTML文件','_self_id':'169901502','_uid':'11111'}

    async def test_continuation_is_assembled_then_validated_before_upload(self):
        outputs=[{'filename':'page.html','content':'<html><body><h1>标题</h1>','summary':'','complete':False},
            {'filename':'page.html','content':'</body></html>','summary':'完整网页','complete':True}]
        prompts=[]
        async def model(cfg,prompt,schema):prompts.append(prompt);return outputs.pop(0)
        with tempfile.TemporaryDirectory() as folder:
            reply,_=await large_tasks.generate(self.cfg(),self.target(),[],self.api,model,folder)
            files=list(Path(folder).rglob('page.html'));self.assertEqual(files[0].read_text(encoding='utf-8'),'<html><body><h1>标题</h1></body></html>')
            self.assertIn('model_reports_unfinished',prompts[1]);self.assertEqual(reply.segments[0]['type'],'_generated_group_file')

    async def test_claimed_complete_but_cut_off_html_is_not_uploaded(self):
        model=AsyncMock(return_value={'filename':'page.html','content':'<html><body>cut','summary':'完成','complete':True})
        with tempfile.TemporaryDirectory() as folder,self.assertRaisesRegex(ValueError,'still incomplete'):
            await large_tasks.generate(self.cfg(large_task_max_steps=2),self.target(),[],self.api,model,folder)
        self.assertEqual(model.await_count,2)

    async def test_repetition_requests_rejected_before_model_and_multiline_filler_rejected(self):
        for text in ('生成55GB文件','把你好重复100000遍写入文件','生成一个无意义文件','重复一百万次你好生成文本文件'):
            model=AsyncMock()
            with tempfile.TemporaryDirectory() as folder:
                reply,status=await large_tasks.generate(self.cfg(),{**self.target(),'text':text},[],self.api,model,folder)
                self.assertIsNone(reply);self.assertEqual(status['status'],'rejected');model.assert_not_awaited()
        self.assertEqual(problem('x.txt','same text\n'*200),'meaningless_repetition')

    async def test_continuation_prompt_obeys_input_cap_and_no_partial_output_file(self):
        prompts=[]
        async def model(cfg,prompt,schema):
            prompts.append(prompt);self.assertLessEqual(len(prompt)+len(cfg['_developer_instructions']),7000)
            return {'filename':'page.html','content':'<html><body>'+'detail '*1000,'summary':'','complete':False}
        target={**self.target(),'_id':'task','_time':1000}
        history=[{'_id':str(i),'_uid':'11111','_time':999,'text':'old '*200} for i in range(8)]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                await large_tasks.generate(self.cfg(large_task_max_prompt_chars=7000,large_task_max_steps=2),target,[],self.api,model,folder,history)
            self.assertFalse(list(Path(folder).rglob('page.html')))
        self.assertEqual(len(prompts),2)

    async def test_missing_completion_flag_oversized_chunk_and_meaningless_filler_are_rejected(self):
        for result in [{'filename':'page.html','content':'<html></html>','summary':'done'},
            {'filename':'x.txt','content':'x'*20001,'summary':'done','complete':True},
            {'filename':'x.txt','content':'0'*2000,'summary':'done','complete':True}]:
            with tempfile.TemporaryDirectory() as folder,self.assertRaises(ValueError):
                await large_tasks.generate(self.cfg(),self.target(),[],self.api,AsyncMock(return_value=result),folder)

    async def test_continuation_obeys_model_call_budget_and_timeout(self):
        partial={'filename':'page.html','content':'<html>','summary':'','complete':False}
        with tempfile.TemporaryDirectory() as folder,self.assertRaisesRegex(ValueError,'budget'):
            await large_tasks.generate(self.cfg(_reserve_extra_call=lambda:False),self.target(),[],self.api,AsyncMock(return_value=partial),folder)
        async def slow(*args):await asyncio.sleep(.2)
        with tempfile.TemporaryDirectory() as folder,self.assertRaises(asyncio.TimeoutError):
            await large_tasks.generate(self.cfg(large_task_timeout_seconds=.02),self.target(),[],self.api,slow,folder)

    async def test_format_checks_parse_without_executing_code(self):
        for name,text in [('a.json','{"a":'),('a.py','def bad('),('a.xml','<a>'),('a.md','```python\nprint(1)')]:self.assertTrue(problem(name,text))
        for name,text in [('a.json','{"a":1}'),('a.py','raise RuntimeError("not executed")'),('a.html','<html><body>done</body></html>')]:self.assertFalse(problem(name,text))
