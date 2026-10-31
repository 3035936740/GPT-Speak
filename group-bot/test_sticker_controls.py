import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
from console_config import Config,atomic
from debug_store import library,approve_image,sticker_mutate,sticker_view
from emoji_library import EmojiLibrary,sticker_source_key
from message_delivery import deliver_burst
from reply_behavior import preferences


class StickerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        config=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        config.update(group_id=100,chat_group_whitelist=[100,200],stickers_enabled=True)
        atomic(self.root/'settings.json',config)
        stream=io.BytesIO();Image.new('RGB',(12,12),'red').save(stream,format='PNG');self.raw=stream.getvalue()
        self.path=self.root/'source.png';self.path.write_bytes(self.raw)
        self.digest=hashlib.sha256(self.raw).hexdigest();self.values={'label':'红色贴纸','emotion':'开心'}

    def toggle(self,value):
        cfg=json.loads((self.root/'settings.json').read_text(encoding='utf-8'));cfg['stickers_enabled']=value;atomic(self.root/'settings.json',cfg)

    def test_master_switch_blocks_collection_candidates_and_preserves_library(self):
        lib=library(self.root);key=approve_image(lib,self.path,self.values)['id']
        self.toggle(False)
        self.assertFalse(library(self.root).enabled())
        self.assertEqual(lib.choices('开心'),[])
        legacy={'action':'send_group_msg','params':{'message':[{'type':'image','data':{'file':'base64://'+base64.b64encode(self.raw).decode()}}]}}
        self.assertFalse(lib.filter_delivery(legacy))
        lib.observe({'message':[{'type':'image','data':{'file':'new.png','sub_type':1}}]})
        lib.offer_candidate({'file':'another.png'}, {'seen_count':1000});self.assertEqual(lib.queue.qsize(),0)
        lib.observe({'message':[{'type':'face','data':{'id':'307'}}]});self.assertNotIn('307',lib.faces)
        with self.assertRaisesRegex(ValueError,'总开关'):approve_image(lib,self.path,self.values)
        with self.assertRaisesRegex(ValueError,'总开关'):sticker_mutate(self.root,'create',values={'image_base64':base64.b64encode(self.raw).decode(),**self.values})
        self.assertIn(key,library(self.root).items)
        style=preferences({'stickers_enabled':False},True,{'text':'哈哈'})
        self.assertFalse(style['allow_no_words_reaction']);self.assertFalse(style['allow_followup_sticker'])
        self.toggle(True);self.assertTrue(lib.choices('开心'))
        with self.assertRaises(ValueError):Config(self.root,200).set('stickers_enabled','off')

    def test_exclusion_deletes_assets_survives_restart_and_blocks_manual_upload(self):
        lib=library(self.root);key=approve_image(lib,self.path,self.values)['id']
        paths=[lib.root/lib.items[key][field] for field in ('path','preview')]
        sticker_mutate(self.root,'exclude',key)
        self.assertTrue(all(not p.exists() for p in paths))
        fresh=library(self.root);self.assertNotIn(key,fresh.items);self.assertTrue(fresh.is_excluded(digest=self.digest))
        self.assertEqual(sticker_view(self.root,state='excluded')['total'],1)
        # Ordinary rejected-entry pruning does not remove a permanent exclusion.
        fresh.rejected={str(n):{'time':n} for n in range(600)};fresh.reject('unrelated','photo')
        self.assertTrue(library(self.root).is_excluded(digest=self.digest))
        with self.assertRaisesRegex(ValueError,'永久排除'):approve_image(library(self.root),self.path,self.values)
        with self.assertRaisesRegex(ValueError,'永久排除'):sticker_mutate(self.root,'create',values={'image_base64':base64.b64encode(self.raw).decode(),**self.values})
        self.assertFalse(lib.filter_delivery({'action':'send_group_msg','params':{'message':[{'type':'image','data':{'file':'base64://'+base64.b64encode(self.raw).decode()}}]},'_media_ids':[key]}))

    def test_same_hash_with_new_source_is_skipped_before_classification(self):
        async def run():
            lib=library(self.root);key=approve_image(lib,self.path,self.values)['id'];sticker_mutate(self.root,'exclude',key)
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=self.raw)))
            calls=[]
            async def classify(*args):calls.append(args);raise AssertionError('Excluded hash must not reach model')
            other=EmojiLibrary(self.root/'runtime-200/emoji-library',self.root/'missing.json',classifier=classify,policy_root=self.root)
            other.offer_candidate({'file':'renamed.png','url':'https://gchat.qpic.cn/renamed'})
            worker=asyncio.create_task(other.collector(client))
            try:await asyncio.wait_for(other.queue.join(),2)
            finally:worker.cancel();await asyncio.gather(worker,return_exceptions=True);await client.aclose()
            self.assertFalse(calls);self.assertEqual(other.items,{})
        asyncio.run(run())

    def test_inflight_review_cannot_admit_after_exclusion_or_switch_off(self):
        async def run(exclude):
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=self.raw)))
            lib=library(self.root);reached=asyncio.Event();release=asyncio.Event()
            async def classify(path,key):
                reached.set();await release.wait();return {'id':key,'asset_type':'sticker','confidence':.99,'emotion':'开心','description':'红色贴纸','use_when':'开心时'}
            lib.classifier=classify;lib.offer_candidate({'file':'fresh.png','url':'https://gchat.qpic.cn/fresh'})
            worker=asyncio.create_task(lib.collector(client))
            try:
                await asyncio.wait_for(reached.wait(),2)
                if exclude:
                    key=approve_image(library(self.root),self.path,self.values)['id'];sticker_mutate(self.root,'exclude',key)
                else:self.toggle(False)
                release.set();await asyncio.wait_for(lib.queue.join(),2)
                self.assertEqual(library(self.root).items,{})
            finally:release.set();worker.cancel();await asyncio.gather(worker,return_exceptions=True);await client.aclose()
        for exclude in (False,True):
            self.toggle(True);asyncio.run(run(exclude))

    def test_pending_upload_and_native_market_expression_can_be_excluded(self):
        created=sticker_mutate(self.root,'create',values={'label':'候选','image_base64':base64.b64encode(self.raw).decode()})
        pending=sticker_view(self.root,state='pending')['rows'][0]
        sticker_mutate(self.root,'exclude',pending['id'])
        self.assertEqual(list((self.root/'shared-emoji-library/incoming').glob('*')),[])
        lib=library(self.root);msg={'group_id':985569438,'message_id':12,'message':[{'type':'mface','data':{'emoji_package_id':1,'emoji_id':'native-test','key':'key','summary':'表情'}}]}
        lib.observe(msg);key=next(iter(lib.items));sticker_mutate(self.root,'exclude',key)
        lib.observe(msg);self.assertNotIn(key,library(self.root).items)

    def test_switch_off_between_parts_keeps_text_and_generated_images(self):
        async def run():
            lib=library(self.root);key=approve_image(lib,self.path,self.values)['id']
            def part(message,ids=None):return {'action':'send_group_msg','params':{'group_id':985569438,'message':message},**({'_media_ids':ids} if ids else {})}
            text=part([{'type':'text','data':{'text':'你好'}}])
            sticker=part([{'type':'image','data':{'file':'base64://'+base64.b64encode(self.raw).decode()}}],[key])
            drawn=part([{'type':'image','data':{'file':'file:///drawn.png'}}])
            plan=[text,sticker,drawn];sent=[]
            async def api(c,e,action,params):sent.append(params);return {'message_id':len(sent)}
            def done(result,part):self.toggle(False)
            await deliver_burst(api,None,None,plan,lambda:True,done,delay=(0,0),filter_part=lib.filter_delivery)
            self.assertEqual(len(sent),2);self.assertEqual(sent[1]['message'][0]['data']['file'],'file:///drawn.png')
            mixed=part([{'type':'text','data':{'text':'保留文字'}},{'type':'face','data':{'id':'307'}}],['f307'])
            self.assertTrue(lib.filter_delivery(mixed));self.assertEqual([s['type'] for s in mixed['params']['message']],['text'])
        asyncio.run(run())
