from copy import deepcopy
import asyncio
import json
import time
import unittest
from unittest.mock import patch

import bot
from knowledge_budget import fit_references,attach
from role_budget import fit_role_prompt


class KnowledgeBudgetTests(unittest.TestCase):
    def test_group_large_knowledge_cannot_displace_recent_chat_or_quote(self):
        now=time.time();cfg={**bot.settings(),'max_prompt_chars':12800}
        quote={'_id':'quote','_uid':'22222','sender':'甲','text':'QUOTE_REQUIRED','_time':now-20}
        history=[{'_id':str(n),'_uid':'33333','sender':'乙','text':'LIVE_'+str(n)+' 最近讨论的内容'*10,'_time':now-18+n} for n in range(12)]
        target={'_id':'current','_uid':'11111','_self_id':'99999','sender':'丙','text':'CURRENT_REQUIRED',
            '_time':now,'at_bot':True,'_reply_id':'quote','_quoted_message':quote}
        refs=[{'id':str(n),'title':'相关资料','source':'data.json','content':'KNOWLEDGE_'+str(n)+' '+('相关知识'*200)} for n in range(200)]
        original=deepcopy(refs)
        with patch.object(bot,'knowledge_for_message',return_value=[]):
            baseline=bot.build_prompt(cfg,history+[quote],'at',deepcopy(target))
        with patch.object(bot,'knowledge_for_message',return_value=refs):
            actual=bot.build_prompt(cfg,history+[quote],'at',deepcopy(target))
        self.assertLessEqual(len(actual),12800)
        for marker in ['CURRENT_REQUIRED','QUOTE_REQUIRED']+['LIVE_'+str(n)+' ' for n in range(12)]:
            self.assertIn(marker,baseline);self.assertIn(marker,actual)
        self.assertIn('KNOWLEDGE_0',actual);self.assertNotIn('KNOWLEDGE_199',actual)
        self.assertEqual(refs,original)

    def test_role_analysis_retains_all_fitting_chat_before_knowledge(self):
        data={'target':{'id':'current','text':'CURRENT'},'quoted_message':{'id':'quote','text':'QUOTE'},
            'recent_chat':[{'id':str(n),'text':'LIVE_'+str(n)+' '+('讨论内容'*40)} for n in range(24)],
            'knowledge_references':[{'id':'doc','source':'data.yaml','content':'FACT '+('知识内容'*30000)}]}
        before=deepcopy(data)
        prompt=fit_role_prompt('rules\n',data,12800,'short rules\n')
        result=json.loads(prompt.split('\n',1)[1])
        self.assertLessEqual(len(prompt),12800);self.assertEqual(len(result['recent_chat']),24)
        self.assertIn('FACT',prompt);self.assertTrue(result['knowledge_references'][0]['truncated']);self.assertEqual(data,before)

    def test_escaped_json_counts_actual_size_and_never_slices_source_metadata(self):
        rows=[{'id':'doc','source':'quoted.json','title':'source','content':'"\\\n知识'*1000}]
        result=fit_references(rows,800)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False,separators=(',',':'))),800)
        self.assertEqual(result[0]['source'],'quoted.json');self.assertTrue(result[0]['truncated'])
        self.assertEqual(fit_references(rows,30),[])
        payload={'target':'x'*90}
        base='rules'+json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        self.assertEqual(attach('rules',payload,'knowledge',rows,len(base)),base)

    def test_actual_context_role_does_not_pretrim_chat_because_of_large_knowledge(self):
        from role_analysis import analyze
        import tempfile
        async def run():
            stamp=time.time();captured=[]
            cfg={**bot.settings(),'max_prompt_chars':12800,'multi_role_enabled':True,'_reserve_extra_call':lambda:True}
            history=[{'_id':str(n),'_uid':'33333','sender':'乙','text':'LIVE_'+str(n)+' '+('讨论内容'*12),'_time':stamp-15+n} for n in range(10)]
            target={'_id':'current','_uid':'11111','sender':'甲','text':'CURRENT','_time':stamp,'at_bot':True}
            async def gen(cfg,prompt,path,images=None):
                self.assertLessEqual(len(prompt),12800)
                if path.name.startswith('context'):
                    captured.append(prompt)
                    return {'addressed_to_bot':True,'intent':'question','needs_web_search':False,'search_query':'','confidence':1,'reference_message_id':'current','note':''}
                return {'mood':'neutral','reply_tone':'casual','confidence':1,'note':''}
            with tempfile.TemporaryDirectory() as folder:
                await analyze(cfg,history,deepcopy(target),[],gen,folder)
                refs=[{'id':str(n),'source':'data.json','content':'FACT '+('知识内容'*1000)} for n in range(100)]
                await analyze(cfg,history,{**deepcopy(target),'_knowledge_references':refs},[],gen,folder)
            self.assertEqual(len(captured),2)
            for n in range(10):
                self.assertIn('LIVE_'+str(n)+' ',captured[0]);self.assertIn('LIVE_'+str(n)+' ',captured[1])
            self.assertIn('FACT',captured[1])
        asyncio.run(run())


if __name__=='__main__':unittest.main()
