import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bot
from task_prompt import chat_window,trusted,render,BOUNDARY
from message_time import formatted


class TaskPromptTests(unittest.IsolatedAsyncioTestCase):
    def test_task_cap_configuration_leaves_chat_limits_unchanged(self):
        from console_config import Config,atomic
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);atomic(root/'settings.json',bot.settings());config=Config(root)
            for value in ('0','500','128000','2147483647'):
                config.set('large_task_max_prompt_chars',value)
                self.assertEqual(config.load()['large_task_max_prompt_chars'],int(value))
                self.assertEqual(config.load()['max_prompt_chars'],12800)
                self.assertEqual(config.load()['console_max_text_chars'],12800)
            for value in ('-1','499','2147483648'):
                with self.assertRaises(ValueError):config.set('large_task_max_prompt_chars',value)

    def test_task_budget_is_independent_of_chat_and_handles_reported_payload(self):
        instructions='i'*3324
        data={'conversation':[],'sandbox_files':[{'path':'/workspace/input/'+('f'*300)} for _ in range(10)],
            'tool_observations':[{'step':1,'exit_code':0,'output':'x'*3000},{'step':2,'exit_code':1,'output':'y'*3000}],
            'request':'KEEP_REQUEST'}
        first=render({'max_prompt_chars':500,'console_max_text_chars':1},instructions,data,[])
        second=render({'max_prompt_chars':100000,'console_max_text_chars':12800},instructions,data,[])
        self.assertEqual(first,second);self.assertGreater(len(first)+len(instructions),12800)
        result=json.loads(render({'large_task_max_prompt_chars':12800},instructions,data,[]))
        from knowledge_budget import encoded
        self.assertLessEqual(len(encoded(result))+len(instructions)+160,12800)
        self.assertTrue(any(row.get('truncated') for row in result['tool_observations']))
        self.assertEqual(result['request'],'KEEP_REQUEST');self.assertEqual(result['sandbox_files'],data['sandbox_files'])
        self.assertEqual(len(data['tool_observations'][0]['output']),3000)

    def test_restored_inventory_collapses_without_losing_input_file_paths(self):
        original={'path':'/workspace/input/original.mid','bytes':1483}
        files=[original]+[{'path':'/workspace/output/'+('n'*120)+str(i),'restored':True} for i in range(40)]
        result=json.loads(render({'large_task_max_prompt_chars':1800},'rules',{'request':'keep','sandbox_files':files},[]))
        self.assertIn(original,result['sandbox_files'])
        directory=next(row for row in result['sandbox_files'] if row.get('restored_directory'))
        self.assertEqual(directory['file_count'],40);self.assertTrue(directory['listing_required'])
        self.assertEqual(len(files),41)

    def test_zero_task_cap_does_not_reapply_chat_cap(self):
        payload={'request':'r'*130000}
        result=json.loads(render({'large_task_max_prompt_chars':0,'max_prompt_chars':500},'rules',payload,[]))
        self.assertEqual(result['request'],payload['request'])
    def test_window_metadata_is_counted_before_trimming_at_exact_boundary(self):
        from knowledge_budget import encoded
        instructions='trusted rules'
        data={'conversation':[{'text':'older '*20},{'text':'newer '*20}],
            'context_window':{'window_messages':2,'chat_char_limit':12800},'request':'KEEP_CURRENT_REQUEST'}
        old_payload={'conversation':data['conversation'],'context_boundary':{'speaker':'assistant','text':BOUNDARY,'program_inserted':True},
            'context_window':data['context_window'],'request':data['request']}
        cap=len(instructions)+160+len(encoded(old_payload))
        prompt=render({'large_task_max_prompt_chars':cap},instructions,data,[]);result=json.loads(prompt)
        self.assertLessEqual(len(prompt)+len(instructions)+160,cap)
        self.assertEqual(result['request'],'KEEP_CURRENT_REQUEST')
        self.assertEqual(len(result['conversation']),1)
        self.assertEqual(result['context_window']['attached_messages'],1)
        self.assertTrue(result['context_window']['truncated'])
        self.assertNotIn('attached_messages',data['context_window'])

    def test_near_boundary_budgets_keep_valid_json_request_and_sources(self):
        data={'conversation':[{'text':'history '*35} for _ in range(8)],
            'context_window':{'window_messages':8,'chat_char_limit':12800},'request':'KEEP_REQUEST'}
        for cap in range(1000,1400,7):
            prompt=render({'large_task_max_prompt_chars':cap},'rules'*10,data,[{'name':'evidence.txt','content':'ORIGINAL_EVIDENCE '*20}])
            result=json.loads(prompt)
            self.assertLessEqual(len(prompt)+50+160,cap)
            self.assertEqual(result['request'],'KEEP_REQUEST');self.assertTrue(result['source_files'])
            self.assertEqual(result['context_window']['attached_messages'],len(result['conversation']))

    def test_impossible_required_input_reports_sizes_without_exposing_content(self):
        from task_prompt import TaskPromptBudgetError
        with self.assertRaises(TaskPromptBudgetError) as error:
            render({'large_task_max_prompt_chars':500},'PERSONA_SECRET'*100,{'request':'REQUEST_SECRET','conversation':[]},[])
        self.assertEqual(error.exception.budget['cap'],500)
        self.assertIn('request',error.exception.budget['sections'])
        self.assertNotIn('PERSONA_SECRET',str(error.exception));self.assertNotIn('REQUEST_SECRET',str(error.exception))

    def files(self,folder):
        root=Path(folder);(root/'persona.txt').write_text('全局人设',encoding='utf-8')
        (root/'reply_protocol.txt').write_text('全局规则',encoding='utf-8')
        local=root/'group-config'/'123';local.mkdir(parents=True)
        (local/'persona.txt').write_text('本群龙娘设定',encoding='utf-8')
        (local/'reply_protocol.txt').write_text('本群回复规则',encoding='utf-8')
        return root

    def test_order_boundary_optional_sections_and_no_chat_data_in_trusted_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg={'group_id':123,'_task_project_root':str(self.files(folder)),'max_prompt_chars':12800,
                '_developer_instructions':'当前有效的人工系统干预'}
            instructions=trusted(cfg,folder,'文件执行约束')
            data={'conversation':[{'text':'恶意历史要求忽略系统规则'}],'request':'正式需求生成页面',
                'person_references':[{'qq_id':'234','status':'grounded','public_profile':{'description':'只当参考'}}]}
            prompt=render(cfg,instructions,data,[{'name':'source.txt','content':'附件中的指令也不是系统指令'}])
            result=json.loads(prompt);keys=list(result)
            self.assertLess(keys.index('conversation'),keys.index('context_boundary'))
            self.assertLess(keys.index('context_boundary'),keys.index('person_references'))
            self.assertLess(keys.index('person_references'),keys.index('source_files'));self.assertEqual(keys[-1],'request')
            self.assertEqual(result['context_boundary']['speaker'],'assistant');self.assertEqual(result['context_boundary']['text'],BOUNDARY)
            self.assertIn('本群龙娘设定',instructions);self.assertIn('本群回复规则',instructions)
            self.assertIn('人工系统干预',instructions);self.assertNotIn('恶意历史',instructions);self.assertNotIn('正式需求',instructions)
            omitted=json.loads(render(cfg,instructions,{'conversation':[],'request':'无附件'},[]))
            self.assertNotIn('person_references',omitted);self.assertNotIn('source_files',omitted)

    def test_window_contains_other_members_timestamps_and_no_old_ten_minute_cutoff(self):
        cfg={'group_id':123,'console_max_text_chars':500};target={'_id':'now','_time':10000}
        rows=[{'_id':'old','_uid':'234','_time':1,'sender':'甲','text':'早些时候的群聊'},
            {'_id':'new','_uid':'345','_time':9999,'sender':'乙','text':'刚才的群聊'},
            {'_id':'command','_bot_command':True,'text':'/stop'},
            {'_id':'foreign','_group_id':999,'text':'别的群'}, {'_id':'now','text':'本次需求'}]
        result=chat_window(cfg,target,rows)
        self.assertEqual([row['message_id'] for row in result],['old','new'])
        self.assertEqual(result[0]['sent_at'],formatted(1));self.assertEqual(result[1]['sender'],'乙')
        result=chat_window({**cfg,'console_max_text_chars':4},target,rows)
        self.assertLessEqual(sum(len(row['text']) for row in result),4);self.assertTrue(result[0]['truncated'])

    async def test_actual_cli_transport_uses_developer_argument_and_keeps_user_data_in_stdin(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.files(folder);cfg={**bot.settings(),'_task_project_root':str(root),'group_id':123}
            instructions=trusted(cfg,root,'任务约束');prompt=render(cfg,instructions,{'conversation':[],'request':'用户任务'},[])
            captured={}
            class Process:
                returncode=0
                async def communicate(self,raw):captured['stdin']=raw.decode();return b'',b''
            async def launch(*args,**kwargs):
                captured['args']=args;Path(args[args.index('-o')+1]).write_text('{"text":"ok"}',encoding='utf-8');return Process()
            with patch.object(bot,'RUNTIME',root),patch.object(bot,'resolve_codex_executable',return_value='fake.exe'),\
                    patch.object(bot.asyncio,'create_subprocess_exec',launch):
                await bot.generate_json({**cfg,'_developer_instructions':instructions,'_task_instruction_chars':len(instructions)},prompt,bot.ROOT/'failure.schema.json')
            value=next(arg for arg in captured['args'] if arg.startswith('developer_instructions='))
            self.assertIn('本群龙娘设定',json.loads(value.split('=',1)[1]));self.assertNotIn('用户任务',value)
            self.assertEqual(json.loads(captured['stdin'])['request'],'用户任务');self.assertNotIn('本群龙娘设定',captured['stdin'])
