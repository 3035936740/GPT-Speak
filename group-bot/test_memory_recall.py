import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
from console_config import atomic
from debug_store import database_mutate
from group_memory import GroupMemory
from memory_reference import current,INSTRUCTION
from role_analysis import analyze
from social_planner import apply_plan


class MemoryRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'runtime').mkdir()
        atomic(self.root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100]})
        self.memory=GroupMemory(self.root/'runtime/group-memory.sqlite3');self.addCleanup(self.memory.close)

    def add(self,identity='manual',content='月亮是本群对夜班同伴的昵称',evidence=None):
        database_mutate(self.root,100,'notes','create',{'id':identity,'kind':'jargon','content':content,
            'keywords':['月亮'],'evidence':evidence or []})

    def target(self):
        return {'_id':'now','_uid':'11111','_time':time.time(),'text':'羡慕月亮','at_bot':True,
            '_jargon':self.memory.jargon('羡慕月亮')}

    def test_manual_terms_still_work_when_an_unrelated_account_is_blocked(self):
        self.add();self.memory.set_blocked({'22222'})
        self.assertEqual(len(self.memory.jargon('羡慕月亮')),1)
        self.assertEqual(len(self.memory.retrieve(self.target())),1)
        self.assertTrue(self.memory.candidates(self.target()))

    def test_blocked_subject_or_evidence_still_excludes_operator_notes(self):
        self.add('source',evidence=[{'id':'s','speaker':self.memory.digest('22222'),'text':'月亮说明'}])
        self.add('subject');self.memory.db.execute('UPDATE notes SET subject=? WHERE id=?',(self.memory.digest('22222'),'subject'));self.memory.db.commit()
        self.memory.set_blocked({'22222'});self.assertFalse(self.memory.jargon('月亮'))

    def test_legacy_empty_source_manual_note_migrates_but_learned_orphan_does_not(self):
        for identity in ('legacy','learned'):
            self.memory.db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,1)',(identity,'jargon','月亮是夜班昵称','','["月亮"]','[]',time.time()))
        self.memory.db.execute('INSERT INTO note_metadata(note_id) VALUES (?)',('learned',));self.memory.db.commit()
        other=GroupMemory(self.memory.path)
        try:
            other.set_blocked({'22222'})
            rows={r['id']:r for r in other.db.execute('SELECT * FROM notes')}
            self.assertTrue(other.allowed(rows['legacy']));self.assertFalse(other.allowed(rows['learned']))
        finally:other.close()

    def test_superseded_term_is_not_supplied_and_removal_clears_origin(self):
        self.add('old','月亮是旧定义');self.add('new','月亮是新定义')
        self.memory.db.execute('INSERT INTO supersessions VALUES (?,?)',('old','new'));self.memory.db.commit()
        self.assertEqual([r['meaning'] for r in self.memory.jargon('月亮')],['月亮是新定义'])
        row=self.memory.db.execute('SELECT rowid FROM notes WHERE id=?',('new',)).fetchone()[0]
        database_mutate(self.root,100,'notes','delete',row_id=row)
        self.assertIsNone(self.memory.db.execute('SELECT 1 FROM note_origins WHERE note_id=?',('new',)).fetchone())

    def test_exact_term_survives_empty_semantic_plan_and_reply_compaction(self):
        self.add();target=self.target();target['_person_profile']={'optional':'x'*50000}
        target['_memory_candidates']=self.memory.candidates(target)
        plan={'action':'reply','topic':'月亮','expects_response':False,'wait_seconds':0,'expression_ids':[],'memory_ids':[],'memory_query':''}
        cfg={**bot.settings(),'max_prompt_chars':12800}
        apply_plan(cfg,target,{'confidence':.9,'plan':plan});self.assertEqual(target['_memories'],[])
        with patch.object(bot,'knowledge_for_message',return_value=[{'title':'其他知识','content':'x'*20000}]):
            prompt=bot.build_prompt(cfg,[],'被@',target)
        data=json.loads(prompt[prompt.rfind('{"target":'):])
        self.assertEqual(data['target']['local_term_reference'][0]['meaning'],'月亮是本群对夜班同伴的昵称')
        self.assertIn(INSTRUCTION,prompt);self.assertLessEqual(len(prompt),12800)

    def test_context_and_emotion_roles_receive_actual_matched_definition(self):
        self.add();target=self.target();target['_peer_scene']={'optional':'x'*50000};calls=[]
        async def generate(cfg,prompt,schema,images=None):
            role=schema.name.split('.')[0];calls.append(role)
            self.assertIn('月亮是本群对夜班同伴的昵称',prompt);self.assertIn('local_term_reference',prompt)
            self.assertLessEqual(len(prompt),cfg['max_prompt_chars'])
            if role=='emotion':return {'mood':'neutral','reply_tone':'casual','confidence':.9,'note':''}
            return {'addressed_to_bot':True,'intent':'play','needs_web_search':False,'search_query':'',
                'reference_message_id':'now','confidence':.9,'note':''}
        cfg={**bot.settings(),'multi_role_enabled':True,'max_prompt_chars':6000,'_reserve_extra_call':lambda:True}
        result=asyncio.run(analyze(cfg,[],target,[],generate,self.root/'runtime'))
        self.assertEqual(set(result),{'context','emotion'});self.assertEqual(len(calls),2)

    def test_unrelated_or_malformed_term_references_are_not_promoted(self):
        self.assertFalse(current({'text':'天气不错','_jargon':[{'meaning':'旧梗','matched_terms':['月亮']}]}))
        self.assertFalse(current({'text':'月亮','_jargon':['invalid',{'meaning':'旧梗','matched_terms':['']}]}))


if __name__=='__main__':unittest.main()
