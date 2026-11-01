import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot
from console_config import Config, delete_local
from conversation_focus import ConversationFocus
from group_memory import GroupMemory
from image_memory import ImageMemory, source_key
from recent_chat_journal import RecentChatJournal
from social_state import SocialState, participation
from social_planner import apply_plan
from style_memory import StyleMemory

VISION={'description':'群聊机器人运行配置页面','visible_text':'水群概率 0.35', 'confidence':.9,'uncertainty':''}
PLAN={'action':'reply','topic':'音乐推荐','expects_response':False,'wait_seconds':0,
    'expression_ids':[],'memory_ids':[],'memory_query':''}


class SocialChainTests(unittest.TestCase):
    def test_waiting_for_a_person_survives_restart_and_human_thread_clears_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'social.json';state=SocialState(path)
            target={'_id':'1','_uid':'a','text':'猜我玩哪个音游','_accepted_plan':{**PLAN,'expects_response':True}}
            state.plan(target,{**PLAN,'topic':'音游猜谜','expects_response':True})
            state.begin('text',target)
            state.delivered({'_id':'2','_time':time.time(),'text':'你玩哪家'},target)
            restored=SocialState(path)
            self.assertTrue(restored.related({'_uid':'a','_time':time.time(),'_audience':'open'}))
            self.assertFalse(restored.related({'_uid':'b','_time':time.time(),'_audience':'open'}))
            self.assertEqual(restored.snapshot()['tasks'],[])  # interrupted work isn't completed
            restored.observe({'_uid':'a','_time':time.time(),'_audience':'others'})
            self.assertFalse(restored.related({'_uid':'a','_time':time.time(),'_audience':'open'}))

    def test_stale_topic_and_waiting_do_not_resurface_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'social.json'
            path.write_text(json.dumps({'revision':1,'exchanges':[],
                'topic':{'text':'旧帧合并','time':time.time()-601},
                'awaiting':{'user_id':'a','time':time.time()-601}}),encoding='utf-8')
            state=SocialState(path)
            self.assertFalse(state.snapshot()['active_topic'])
            self.assertFalse(state.related({'_uid':'a'}))

    def test_dynamic_participation_reduces_monopoly_and_zero_stays_off(self):
        now=time.time()
        quiet=[{'is_bot':False,'_time':now-5} for _ in range(10)]
        crowded=quiet+[{'is_bot':True,'_time':now-1} for _ in range(10)]
        cfg={'passive_probability':.35}
        self.assertLess(participation(cfg,crowded,[],now=now)['probability'],
            participation(cfg,quiet,[],now=now)['probability'])
        self.assertEqual(participation({'passive_probability':0},quiet,[],now=now)['probability'],0)
        self.assertEqual(participation({**cfg,'adaptive_participation_enabled':False},crowded,[],now=now)['probability'],.35)

    def test_planner_cannot_wait_forever_or_silence_an_explicit_call(self):
        for action in ('wait','silent'):
            target={'at_bot':True}
            result=apply_plan({},target,{'confidence':.9,'plan':{**PLAN,'action':action,'wait_seconds':20}})
            self.assertIsNone(result)
            self.assertEqual(target['_accepted_plan']['action'],'reply')
        target={'at_bot':False}
        result=apply_plan({},target,{'confidence':.9,'plan':{**PLAN,'action':'wait','wait_seconds':5}})
        self.assertEqual(result.route,'wait')
        target['_wait_attempts']=1
        self.assertIsNone(apply_plan({},target,{'confidence':.9,'plan':{**PLAN,'action':'wait','wait_seconds':5}}))

    def test_actual_reply_can_correct_the_planners_waiting_prediction(self):
        with tempfile.TemporaryDirectory() as folder:
            state=SocialState(Path(folder)/'s.json')
            target={'_id':'1','_uid':'a','text':'嗨','_accepted_plan':PLAN,'_expects_response':True}
            state.delivered({'_id':'2','_time':time.time(),'text':'你今天咋样'},target)
            self.assertTrue(state.related({'_uid':'a','_audience':'open'}))
            target['_expects_response']=False
            state.delivered({'_id':'3','_time':time.time(),'text':'好耶'},target)
            self.assertFalse(state.related({'_uid':'a','_audience':'open'}))

    def test_invented_candidate_ids_are_not_injected(self):
        target={'_memories':[{'content':'现有资料'}]}
        result=apply_plan({},target,{'confidence':.9,'plan':{**PLAN,'memory_ids':['invented']}})
        self.assertIsNone(result)
        self.assertEqual(target['_memories'],[{'content':'现有资料'}])

    def memory_row(self,memory,id,uid,text,stamp=None):
        memory.observe({'_id':id,'_uid':uid,'sender':'群友'+uid,'text':text,'_time':stamp or time.time()})
        return next(row for row in memory.learning_batch(force=True) if row['id']==memory.digest(id))

    def learn_preference(self,memory,row,content,supersedes=()):
        memory.apply_learning({'expressions':[],'memories':[{'kind':'preference','content':content,
            'subject':row['speaker'],'source_ids':[row['id']],'keywords':[],
            'fact_key':'音乐偏好','confidence':.95,'supersedes':list(supersedes)}]},[row])
        return memory.digest('preference'+row['speaker']+content)

    def test_corrected_personal_fact_replaces_old_one_without_cross_person_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            memory=GroupMemory(Path(folder)/'m.sqlite3')
            row=self.memory_row(memory,'1','a','平时只听轻音乐',time.time()-20)
            old=self.learn_preference(memory,row,'偏爱轻音乐')
            row=self.memory_row(memory,'2','b','我听金属')
            self.learn_preference(memory,row,'喜欢金属',[old])
            self.assertIn('轻音乐',json.dumps(memory.profile({'_uid':'a'}),ensure_ascii=False))
            row=self.memory_row(memory,'3','a','我现在改听金属了')
            new=self.learn_preference(memory,row,'改听金属',[old])
            memory.close();memory=GroupMemory(Path(folder)/'m.sqlite3')
            profile=memory.profile({'_uid':'a'})
            self.assertIn('金属',json.dumps(profile,ensure_ascii=False))
            self.assertNotIn('轻音乐',json.dumps(profile,ensure_ascii=False))
            self.assertEqual(profile['facts'][0]['id'],new)
            memory.close()

    def test_reverting_a_preference_does_not_create_a_supersession_cycle(self):
        with tempfile.TemporaryDirectory() as folder:
            memory=GroupMemory(Path(folder)/'m.sqlite3')
            a=self.memory_row(memory,'1','a','喜欢轻音乐',time.time()-30)
            first=self.learn_preference(memory,a,'喜欢轻音乐')
            b=self.memory_row(memory,'2','a','改听金属',time.time()-20)
            second=self.learn_preference(memory,b,'喜欢金属',[first])
            c=self.memory_row(memory,'3','a','还是更喜欢轻音乐',time.time()-10)
            self.learn_preference(memory,c,'喜欢轻音乐',[second])
            self.assertEqual(len(memory.profile({'_uid':'a'})['facts']),1)
            self.assertIn('轻音乐',memory.profile({'_uid':'a'})['facts'][0]['content'])
            memory.close()

    def test_episode_requires_real_sources_and_is_available_as_a_memory_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            memory=GroupMemory(Path(folder)/'m.sqlite3')
            a=self.memory_row(memory,'1','a','昨晚把那套谱打通了')
            b=self.memory_row(memory,'2','b','居然过了恭喜')
            batch=[a,b]
            result={'episode':{'summary':'群友昨晚打通一套谱，另一位群友道贺',
                'recall_cues':['通关经历'],'source_ids':[a['id'],b['id']]}}
            self.assertEqual(memory.apply_learning(result,batch),1)
            result['episode']['source_ids']=['invented',a['id']]
            self.assertEqual(memory.apply_learning(result,batch),0)
            self.assertIn('通一套谱',json.dumps(memory.candidates({'_uid':'a','text':'上回打得怎么样'}),ensure_ascii=False))
            memory.close()

    def test_expression_feedback_affects_candidates_and_is_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'m.sqlite3';memory=GroupMemory(path)
            row=self.memory_row(memory,'1','a','可以啊（')
            memory.apply_learning({'expressions':[{'situation':'接朋友的邀约','pattern':'随口接受','example':'可以啊（','source_ids':[row['id']]}]},[row])
            identity=memory.candidates({'text':'来一起玩吗'},expressions=True)[0]['id']
            memory.expression_used([identity]);memory.expression_feedback([identity],False)
            memory.close();memory=GroupMemory(path)
            row=memory.db.execute('SELECT uses,negative FROM note_metadata WHERE note_id=?',(identity,)).fetchone()
            self.assertEqual(tuple(row),(1,1))
            memory.close()

    def test_bracket_learning_keeps_the_abstract_interaction_pattern(self):
        with tempfile.TemporaryDirectory() as folder:
            memory=GroupMemory(Path(folder)/'m.sqlite3')
            row=self.memory_row(memory,'1','a','可以啊（')
            memory.apply_learning({'expressions':[{'situation':'朋友邀约','pattern':'轻松接下朋友邀约',
                'example':'可以啊（','source_ids':[row['id']]}]},[row])
            candidates=memory.candidates({'text':'一起玩吗'},expressions=True)
            self.assertIn('接下朋友邀约',candidates[0]['pattern'])
            memory.close()

    def test_group_voice_changes_gradually_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'style.json';style=StyleMemory(path)
            for i in range(25):style.observe({'_id':str(i),'_uid':str(i%3),'_time':time.time(),'text':'很短（'})
            first=style.voice_profile(.35)
            style.voice['updated_at']=time.time()-601
            for i in range(25,60):style.observe({'_id':str(i),'_uid':str(i%3),'_time':time.time(),'text':'这是一句比较长的普通说法，没有句尾括号'})
            second=style.voice_profile(.35)
            self.assertLess(second['bracket_fraction'],first['bracket_fraction'])
            self.assertGreater(second['bracket_fraction'],.5)  # no instant personality flip
            style.save()
            self.assertEqual(StyleMemory(path).voice_profile(.35),second)

    def test_semantic_selection_uses_existing_context_call_and_reaches_reply(self):
        async def run():
            calls=[]
            target={'_id':'q','_uid':'a','_time':time.time(),'at_bot':True,'text':'我想听点不吵的东西',
                '_expression_candidates':[{'id':'e','situation':'朋友轻松求推荐','pattern':'直接给个人化建议','example':'试试这个（'}],
                '_memory_candidates':[{'id':'m','kind':'preference','subject':'当前发言者','content':'平时偏爱轻音乐','historical':True}]}
            async def model(cfg,prompt,schema,images=None):
                role=schema.name.split('.')[0];calls.append(role)
                if role=='emotion':return {'mood':'neutral','reply_tone':'casual','confidence':.9,'note':'轻松求推荐'}
                if role=='context':
                    self.assertIn('平时偏爱轻音乐',prompt)
                    return {'addressed_to_bot':True,'intent':'request','needs_web_search':False,'search_query':'',
                        'confidence':.9,'reference_message_id':'q','note':'根据偏好推荐',
                        'plan':{**PLAN,'expression_ids':['e'],'memory_ids':['m']}}
                self.assertIn('平时偏爱轻音乐',prompt)
                self.assertIn('直接给个人化建议',prompt)
                self.assertLessEqual(len(prompt),6000)
                return {'reply_to':'q','should_reply':True,'conversation':'direct','text':'你不是偏爱轻音乐嘛，先挑点钢琴曲（','media_kind':'none'}
            with patch.object(bot,'generate_json',model),patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
                reply=await bot.generate_for_message(None,{**bot.settings(),'_reserve_extra_call':lambda:True},[], '被@',target)
                self.assertIn('轻音乐',str(reply));self.assertEqual(calls[-1],'reply');self.assertCountEqual(calls[:-1],['emotion','context'])
            self.assertEqual(target['_selected_expression_ids'],['e'])
        asyncio.run(run())

    def test_restored_quoted_image_recovers_summary_without_rereading_pixels(self):
        async def run():
            with tempfile.TemporaryDirectory() as folder:
                cache=ImageMemory(Path(folder)/'images.json')
                key=source_key({'file':'a'*32+'.png'})
                cache.observe('100',[key]);cache.store(key,'digest',VISION)
                journal=RecentChatJournal(Path(folder)/'recent.jsonl')
                journal.append({'_id':'100','_uid':'a','_time':time.time()-3600,'text':'[图片，未读取内容]',
                    '_image_keys':{'https://gchat.qpic.cn/secret?token=private':key}})
                restored=journal.load()[0]
                self.assertNotIn('token',journal.path.read_text(encoding='utf-8'))
                target={'_id':'200','_time':time.time(),'_uid':'a','at_bot':True,'text':'再看看这是什么东西','_reply_id':'100'}
                async def generate(cfg,context,trigger,adjusted,paths):
                    self.assertEqual(adjusted['_cached_vision'][0]['description'],VISION['description'])
                    self.assertFalse(paths)
                    return '这是bot的配置页'
                with patch.object(bot,'analyze_roles',return_value={}),patch.object(bot,'generate',generate), \
                        patch.object(bot,'download_image') as download,patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
                    result=await bot.generate_for_message(None,{**bot.settings(),'_image_memory':cache},[restored],'引用',target)
                download.assert_not_called();self.assertIn('配置页',str(result))
        asyncio.run(run())

    def test_quoted_image_fetches_missing_media_even_when_message_is_in_history(self):
        async def run():
            with tempfile.TemporaryDirectory() as folder:
                focus=ConversationFocus(Path(folder)/'focus.json')
                target={'_id':'2','_reply_id':'1','_group_id':'985569438','_uid':'3035936740','at_bot':True,'text':'这是什么图'}
                restored={'_id':'1','_uid':'3035936740','text':'[图片，未读取内容]'}
                async def api(client,env,action,params=None):
                    self.assertEqual(action,'get_msg')
                    return {'message_id':1,'group_id':985569438,'user_id':3035936740,
                        'message':[{'type':'image','data':{'url':'https://gchat.qpic.cn/fresh'}}]}
                with patch.object(bot,'api',api):await bot.classify_audience(None,{},target,[restored],'169901502',focus)
                self.assertEqual(target['_quoted_message']['_images'],['https://gchat.qpic.cn/fresh'])
        asyncio.run(run())

    def test_deleting_source_removes_derived_profile_episode_and_social_exchange(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'settings.json').write_text(json.dumps(bot.settings()),encoding='utf-8')
            runtime=root/'runtime';runtime.mkdir()
            memory=GroupMemory(runtime/'group-memory.sqlite3')
            row=self.memory_row(memory,'123','a','我爱轻音乐')
            self.learn_preference(memory,row,'偏爱轻音乐');memory.close()
            social=SocialState(runtime/'social-state.json')
            social.delivered({'_id':'456','_time':time.time(),'text':'给你推荐'}, {'_id':'123','_uid':'a','text':'我爱轻音乐'})
            delete_local(root,985569438,'123')
            memory=GroupMemory(runtime/'group-memory.sqlite3')
            self.assertFalse(memory.profile({'_uid':'a'}));memory.close()
            self.assertFalse(SocialState(runtime/'social-state.json').data['exchanges'])

    def test_settings_and_prompt_saved_by_console_are_read_after_recreation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'settings.json').write_text(json.dumps(bot.settings()),encoding='utf-8')
            (root/'persona.txt').write_text('原人格',encoding='utf-8')
            config=Config(root)
            catalog={'models':[{'slug':'gpt-6-luna','supported_reasoning_levels':[{'effort':'low'}], 'service_tiers':[{'id':'priority'}]}]}
            with patch('console_config.read_json',side_effect=lambda path,default=None:catalog if Path(path).name=='models_cache.json' else json.loads(Path(path).read_text(encoding='utf-8-sig'))):
                config.set('proactive_topic_probability','0.42')
                config.set('at_reply_probability','0.7')
                config.set('persona','我叫自定义的名字')
                config.set('protocol','自定义的回复系统规则')
            restored=Config(root)
            self.assertEqual(restored.load()['proactive_topic_probability'],.42)
            self.assertEqual(restored.load()['at_reply_probability'],.7)
            self.assertEqual((root/'persona.txt').read_text(encoding='utf-8'),'我叫自定义的名字')
            self.assertEqual((root/'reply_protocol.txt').read_text(encoding='utf-8'),'自定义的回复系统规则')
            self.assertTrue(list((root/'config-backups').glob('*.json')))

    def test_prompt_layers_preserve_identity_and_persona_while_fitting_media_and_memory(self):
        target={'_id':'1','_uid':'a','at_bot':True,'text':'这是什么东西','_time':time.time(),
            '_cached_vision':[VISION],'_conversation_state':{'active_topic':{'text':'配置页面'}},
            '_person_profile':{'facts':[{'content':'喜欢音游'}]},
            '_selected_expression_ids':['e'],'_expressions':[{'situation':'自然答疑','pattern':'直接说人话','example':'就是这个（'}]}
        prompt=bot.build_prompt(bot.settings(),[],'被@',target)
        self.assertIn('GPT 6 Luna',prompt)
        self.assertIn('成年女性的成熟心智',prompt)
        self.assertIn('白色大肥龙',prompt)
        self.assertIn('按当前目标、对话对象和真实证据',prompt)
        self.assertIn(VISION['description'],prompt)
        self.assertIn('直接说人话',prompt)
        self.assertLessEqual(len(prompt),6000)

if __name__=='__main__':unittest.main()
