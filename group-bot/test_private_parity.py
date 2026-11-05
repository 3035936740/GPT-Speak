import asyncio
import base64
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch,AsyncMock
import httpx
from PIL import Image
import test_private_chat as fixtures
from console_config import atomic
import private_memory
import private_knowledge
import private_media
import private_profiles
from private_view import view,enqueue,control_session
from private_directory import load
from debug_avatars import private_avatar
from debug_webui import DebugWebUI


class PrivateParityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=fixtures.PrivateTests('test_reply_is_once_only_and_session_is_isolated')
        await self.fixture.asyncSetUp();self.root=self.fixture.root;self.service=self.fixture.service
    async def asyncTearDown(self):await self.fixture.asyncTearDown()
    def png(self):
        out=io.BytesIO();Image.new('RGB',(20,20),'#aabbcc').save(out,format='PNG');return out.getvalue()

    async def test_private_image_summary_depth_reaches_prompt_and_disabled_vision_stays_off(self):
        from console_config import Config
        profile={'observations':[]}
        Config(self.root).set('image_summary_level','balanced')
        prompt=self.service.prompt('11111',profile)
        self.assertIn('图片理解等级：平衡',prompt)
        self.assertIn('完整识别可见文字',prompt)
        Config(self.root).set('image_summary_level','detailed')
        self.assertIn('必须标为推测',self.service.prompt('11111',profile))
        Config(self.root).set('private_vision_enabled','false')
        self.assertNotIn('图片理解等级',self.service.prompt('11111',profile))

    async def test_memory_has_same_crud_backup_conflict_detection_and_contact_isolation_as_groups(self):
        private_memory.mutate(self.root,'11111','notes','create',{'kind':'preference','content':'喜欢蓝莓蛋糕','keywords':['蓝莓'],'evidence':[]})
        listing=private_memory.view(self.root,'11111');row=listing['rows'][0]
        self.assertTrue(row['id']);self.assertEqual(listing['total'],1)
        self.assertEqual(private_memory.view(self.root,'22222')['total'],0)
        self.assertIn('蓝莓蛋糕',str(private_memory.recall(self.root,'11111','蓝莓')))
        private_memory.mutate(self.root,'11111','notes','update',{'content':'喜欢草莓蛋糕'},row['_rowid'],row)
        with self.assertRaises(ValueError):private_memory.mutate(self.root,'11111','notes','delete',row_id=row['_rowid'],expected=row)
        fresh=private_memory.view(self.root,'11111')['rows'][0]
        private_memory.mutate(self.root,'11111','notes','delete',row_id=fresh['_rowid'],expected=fresh)
        self.assertEqual(private_memory.view(self.root,'11111')['total'],0)
        self.assertTrue(list((self.root/'runtime-private/contacts/11111').glob('**/*.sqlite3')))
        for peer in ('../11111','33333'):
            with self.assertRaises(ValueError):private_memory.view(self.root,peer)

    async def test_knowledge_large_text_pagination_disabled_retrieval_and_peer_guards(self):
        for i in range(23):private_profiles.save(self.root,'11111','knowledge',{'id':'k'+str(i),'title':'蓝莓 '+str(i),'content':'蓝莓资料'*1500,'source_file':'data.yaml','enabled':i!=0})
        data=private_knowledge.view(self.root,'11111');self.assertEqual(data['total'],23);self.assertEqual(len(data['rows']),20)
        self.assertEqual(len(private_knowledge.view(self.root,'11111',offset=20)['rows']),3)
        self.assertNotIn('content',data['rows'][0]);self.assertEqual(data['rows'][0]['source_file'],'data.yaml')
        detail=private_knowledge.view(self.root,'11111',identity='k0');self.assertGreater(len(detail['content']),4000)
        self.assertFalse(detail['enabled']);self.assertEqual(private_knowledge.view(self.root,'11111',state='disabled')['total'],1)
        records=private_profiles.knowledge(self.service.store.db,'11111','蓝莓',0)
        self.assertNotIn('k0',[r['id'] for r in records])
        with self.assertRaises(ValueError):private_knowledge.view(self.root,'22222',identity='k0')
        private_profiles.save(self.root,'11111','knowledge',{'id':'k0','delete':True})
        self.assertEqual(private_knowledge.view(self.root,'11111')['total'],22)

    async def test_media_metadata_does_not_leak_remote_urls_or_cross_contacts(self):
        row=fixtures.message('media');row['message']=[{'type':'image','data':{'url':'https://gchat.qpic.cn/a?secret=hidden'}},
            {'type':'reply','data':{'id':'previous'}},{'type':'file','data':{'file_id':'f','name':'a.log','size':20}}]
        self.service.store.add_message('11111','media','user','[图片]',self.fixture.now)
        private_media.store_metadata(self.service.store.db,'11111',row)
        public=private_media.metadata(self.service.store.db,'11111','media')
        self.assertNotIn('secret',json.dumps(public));self.assertEqual(public['reply_id'],'previous')
        self.assertEqual(public['files'][0]['name'],'a.log');self.assertTrue(public['images'][0]['url'].startswith('/api/private-image?'))
        self.assertFalse(private_media.metadata(self.service.store.db,'22222','media')['images'])
        data=view(self.root,'11111');event=next(e for e in data['events'] if e['details'].get('message_id')=='media')
        self.assertEqual(event['user_id'],'11111');self.assertEqual(len(event['media']['images']),1)
        with self.assertRaises(ValueError):private_media.image(self.root,'33333','media',0)

    async def test_manual_private_text_image_quote_is_idempotent_and_previewable(self):
        self.service.store.add_message('11111','quoted','user','引用的原话',self.fixture.now-10)
        atomic(self.root/'runtime-private/state.json',{'online':True,'pid':os.getpid()})
        image={'slot':1,'data':base64.b64encode(self.png()).decode()};identity='12345678-1234-1234-1234-123456789abc'
        for _ in range(2):enqueue(self.root,'11111','看看这张[图片1]',identity,[image],'quoted')
        await self.service.send_manual();await self.service.send_manual()
        self.assertEqual(len(self.fixture.sent),1);segments=self.fixture.sent[0]['message']
        self.assertEqual([s['type'] for s in segments],['reply','text','image'])
        self.assertEqual(base64.b64decode(segments[-1]['data']['file'][9:]),self.png())
        sent_id=str(501);data=private_media.metadata(self.service.store.db,'11111',sent_id)
        self.assertEqual(data['reply_id'],'quoted');raw,mime=private_media.image(self.root,'11111',sent_id,0)
        self.assertEqual(raw,self.png());self.assertEqual(mime,'image/png')
        with self.assertRaises(ValueError):enqueue(self.root,'22222','wrong quote','22345678-1234-1234-1234-123456789abc',[],'quoted')

    async def test_directory_refresh_is_independent_from_reply_queue_and_context(self):
        self.service.store.add_message('11111','existing','user','已有私聊',self.fixture.now)
        async def api(action,params=None):
            if action=='get_login_info':return {'user_id':99999}
            if action=='get_friend_list':return [{'user_id':11111,'nickname':'新昵称'},{'user_id':33333,'nickname':'新增好友'}]
            if action=='get_group_member_list':return [{'user_id':11111},{'user_id':33333}]
            raise AssertionError(action)
        await load(self.root,api,self.service.config())
        data=view(self.root,'11111')
        self.assertEqual({row['id'] for row in data['contacts'] if not row['unavailable_reason']},{'11111','33333'})
        archived=view(self.root,'22222')
        self.assertFalse(archived['can_chat'])
        self.assertIn('不在当前账号好友列表',archived['access_reason'])
        self.assertIn('33333',[row['id'] for row in data['contacts']]);self.assertEqual(data['name'],'新昵称')
        self.assertEqual(self.service.store.known('11111'),{'existing'});self.assertFalse(self.fixture.sent)

    async def test_avatars_require_header_and_known_private_accounts(self):
        folder=self.root/'runtime/avatar-cache';folder.mkdir(parents=True)
        (folder/'user-11111.bin').write_bytes(self.png())
        self.assertEqual(private_avatar(self.root,'11111','11111')[0],self.png())
        os.utime(folder/'user-11111.bin',(1,1))
        with patch('debug_avatars.httpx.Client') as remote:
            self.assertEqual(private_avatar(self.root,'11111','11111')[0],self.png())
            remote.assert_not_called()
        with self.assertRaises(ValueError):private_avatar(self.root,'11111','22222')
        server=DebugWebUI(self.root,lambda value:{'ok':True});server.start()
        try:
            def request():
                with httpx.Client(trust_env=False) as client:
                    url=server.base_url+'api/private-avatar?peer=11111&id=11111'
                    return client.get(url),client.get(url,headers={'X-QQ-Debug-Key':server.token})
            denied,allowed=await asyncio.to_thread(request)
            self.assertEqual(denied.status_code,401);self.assertEqual(allowed.status_code,200);self.assertEqual(allowed.content,self.png())
        finally:await asyncio.to_thread(server.close)

    async def test_session_controls_and_clear_do_not_erase_other_friends_or_long_memory(self):
        for peer in ('11111','22222'):self.service.store.add_message(peer,'same-id','user','当前上下文',self.fixture.now)
        private_memory.mutate(self.root,'11111','notes','create',{'kind':'fact','content':'应保留的长期记忆'})
        control_session(self.root,'11111','stop');self.assertTrue(view(self.root,'11111')['session']['paused'])
        self.assertFalse(view(self.root,'22222')['session'].get('paused',False))
        control_session(self.root,'11111','clear');self.assertFalse(self.service.store.history('11111'))
        self.assertTrue(self.service.store.history('22222'));self.assertEqual(private_memory.view(self.root,'11111')['total'],1)
        control_session(self.root,'11111','resume');self.assertFalse(view(self.root,'11111')['session']['paused'])

    async def test_learning_uses_only_current_friend_sources_and_applies_group_memory_schema(self):
        import bot
        for peer,text in [('11111','我喜欢蓝莓'),('22222','另一个好友的秘密')]:
            for i in range(2):private_memory.observe(self.root,peer,peer+'-'+str(i),text,self.fixture.now-i)
        sources=private_memory.view(self.root,'11111','sources')['rows']
        ids=[row['id'] for row in sources]
        async def generate(cfg,prompt,schema):
            self.assertIn('蓝莓',prompt);self.assertNotIn('秘密',prompt);self.assertTrue(cfg['_private_context']);self.assertEqual(cfg['group_id'],0)
            return {'expressions':[],'memories':[{'kind':'preference','content':'喜欢蓝莓','subject':sources[0]['speaker'],'keywords':['蓝莓'],
                'source_ids':ids,'confidence':.9,'fact_key':'food','supersedes':[]}],'episode':{'summary':'','recall_cues':[],'source_ids':[]}}
        cfg={**self.service.config(),'learning_min_messages':2,'learning_interval_seconds':30}
        with patch.object(bot,'generate_json',generate):await private_memory.prepare_learning(self.service,'11111',cfg)
        self.assertEqual(private_memory.view(self.root,'11111')['total'],1)
        self.assertEqual(private_memory.view(self.root,'22222')['total'],0)

    async def test_private_role_configuration_executes_the_existing_group_analyzer_with_private_scope(self):
        import private_worker,bot,role_analysis
        cfg={**self.service.config(),'_private_role_analysis_enabled':True,'_private_role_context':[
            {'_id':'p1','_uid':'11111','_time':self.fixture.now,'text':'你好','is_bot':False}]}
        analyze=AsyncMock(return_value={'emotion':{'mood':'neutral'}});generate=AsyncMock(return_value={'should_reply':True,'text':'你好'})
        with patch.object(private_worker,'ROOT',self.root),patch.object(bot,'RUNTIME',self.root/'runtime-private'),patch.object(bot,'generate_json',generate),patch.object(role_analysis,'analyze',analyze):
            result=await private_worker.generate('私聊提示词',cfg)
        self.assertTrue(result['should_reply']);self.assertEqual(analyze.call_args.args[0]['group_id'],0)
        self.assertTrue(analyze.call_args.args[0]['_private_context']);self.assertIn('neutral',generate.call_args.args[1])

    async def test_private_configuration_round_trips_mappings_display_and_unlimited_knowledge(self):
        from private_settings import save,values
        save(self.root,'11111','contact','role_models',{'emotion':{'model':'gpt-6-luna','reasoning_effort':'low'}})
        save(self.root,'11111','contact','show_avatars',False)
        save(self.root,'11111','contact','knowledge_max_chars',0)
        profile=private_profiles.profile(self.service.store.db,'11111');cfg=private_profiles.effective(self.service.config(),profile)
        self.assertFalse(cfg['_private_display_avatars']);self.assertEqual(cfg['knowledge_max_chars'],0)
        self.assertEqual(cfg['role_models']['emotion']['model'],'gpt-6-luna')
        with self.assertRaises(ValueError):save(self.root,'11111','contact','role_models',{'shell':{}})
