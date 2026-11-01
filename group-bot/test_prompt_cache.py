import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bot
from prompt_cache import stable_schema,check_constraints,transport_schema,ConstraintViolation


def spec(identity):
    return {'type':'object','properties':{'reply_to':{'type':'string','const':identity},'media_ids':{'type':'array','maxItems':2,'items':{'type':'string','enum':[identity+'-sticker']}},
        'action':{'type':'string','enum':['chat']},'member_updates':{'type':'array','items':{'type':'object','properties':{'qq_id':{'type':'string','enum':[identity]}}}}}}


class PromptCacheTests(unittest.TestCase):
    def test_schema_stable_across_message_and_candidate_ids_keeps_policy(self):
        first=spec('a');original=deepcopy(first)
        self.assertEqual(stable_schema(first),stable_schema(spec('b')));self.assertEqual(first,original)
        self.assertEqual(stable_schema(first)['properties']['action']['enum'],['chat'])
        self.assertEqual(stable_schema(first)['properties']['media_ids']['maxItems'],2)
        check_constraints(first,{'reply_to':'a','media_ids':['a-sticker'],'action':'chat'})
        for bad in [{'reply_to':'b'},{'media_ids':['invented']},{'action':'draw'},{'member_updates':[{'qq_id':'wrong'}]}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):check_constraints(first,bad)

    def test_identical_schema_gets_same_path_across_temp_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);one=root/'one.json';two=root/'two.json';one.write_text(json.dumps(spec('a')));two.write_text(json.dumps(spec('b')))
            a,_=transport_schema(one,root);b,_=transport_schema(two,root)
            self.assertEqual(a,b);self.assertEqual(len(list((root/'prompt-cache-schemas').glob('*.json'))),1)

    def test_constraint_diagnostics_identify_field_without_logging_content(self):
        with self.assertRaises(ConstraintViolation) as caught:
            check_constraints(spec('a'),{'media_ids':['secret-untrusted-id']})
        self.assertEqual(caught.exception.path,'$.media_ids[0]')
        self.assertNotIn('secret',str(caught.exception))

    def test_constraint_failure_is_reported_as_invalid_answer(self):
        from failure_response import failure_reason
        self.assertEqual(failure_reason('text',ConstraintViolation('const','$.reply_to')),'invalid_answer')

    def test_reply_binding_failure_retries_once_with_exact_schema_and_charged_budget(self):
        async def run(root):
            schemas=[];prompts=[];reserved=[]
            class Process:
                returncode=0
                async def communicate(self,content):
                    prompts.append(content)
                    output.write_text(json.dumps({'reply_to':'wrong' if len(schemas)==1 else '1667291484',
                        'should_reply':True,'text':'','conversation':'direct','action':'chat'}))
                    return b'',b''
            async def launch(*command,**kw):
                nonlocal output
                output=Path(command[command.index('-o')+1])
                schemas.append(json.loads(Path(command[command.index('--output-schema')+1]).read_text(encoding='utf-8')))
                return Process()
            cfg={**bot.settings(),'_reserve_extra_call':lambda:reserved.append(True) or True}
            output=None
            with patch.object(bot,'RUNTIME',root),patch.object(bot,'resolve_codex_executable',return_value='mock'),patch.object(bot.asyncio,'create_subprocess_exec',launch):
                result=await bot.generate_reply_json(cfg,'same original request',{'_id':'1667291484','at_bot':True})
            self.assertEqual(result['reply_to'],'1667291484');self.assertEqual(reserved,[True]);self.assertEqual(prompts[0],prompts[1])
            self.assertNotIn('const',schemas[0]['properties']['reply_to']);self.assertEqual(schemas[1]['properties']['reply_to']['const'],'1667291484')
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(Path(folder)))

    def test_constraint_retry_is_bounded_and_not_permitted_without_budget(self):
        async def run(root):
            async def invalid(*args,**kw):raise ConstraintViolation('const','$.reply_to')
            for budget,count in [(False,1),(True,2)]:
                with patch.object(bot,'generate_json',side_effect=invalid) as call,patch.object(bot,'RUNTIME',root):
                    with self.assertRaises(ConstraintViolation):
                        await bot.generate_reply_json({**bot.settings(),'_reserve_extra_call':lambda:budget},'request',{'_id':'x','at_bot':True})
                    self.assertEqual(call.call_count,count)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(Path(folder)))

    def test_actual_model_transport_stabilizes_schema_and_rejects_invalid_id(self):
        async def run(root):
            schema=root/'reply.json';schema.write_text(json.dumps(spec('a')));paths=[];answer={'reply_to':'a','media_ids':['a-sticker'],'action':'chat'}
            class Process:
                returncode=0
                async def communicate(self,content):
                    output.write_text(json.dumps(answer));return b'',b''
            async def launch(*command,**kwargs):
                nonlocal output
                output=Path(command[command.index('-o')+1]);path=Path(command[command.index('--output-schema')+1]);paths.append(path)
                self.assertNotIn('const',json.loads(path.read_text())['properties']['reply_to']);return Process()
            output=None
            with patch.object(bot,'RUNTIME',root),patch.object(bot,'resolve_codex_executable',return_value='mock'),patch.object(bot.asyncio,'create_subprocess_exec',launch):
                self.assertEqual(await bot.generate_json(bot.settings(),'target a',schema),answer)
                answer={'reply_to':'wrong'}
                with self.assertRaises(ValueError):await bot.generate_json(bot.settings(),'target a',schema)
            self.assertEqual(paths[0],paths[1])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(Path(folder)))


if __name__=='__main__':unittest.main()
