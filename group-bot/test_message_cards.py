import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
import bot
from content_reference import assets
from forward_messages import plain
from message_cards import card, render
from private_chat import private_text
from image_recognition import ImageRecognition
from recent_chat_journal import RecentChatJournal


class MessageCardTests(unittest.TestCase):
    def segment(self):
        return {'type':'json','data':{'data':json.dumps({'app':'com.tencent.miniapp_01','meta':{'detail_1':{
            'title':'哔哩哔哩','desc':'队友我摸出国王号了','qqdocurl':'https://www.bilibili.com/video/BV123',
            'preview':'https://invalid.test/cover.jpg'}}},ensure_ascii=False)}}

    def test_json_link_card_is_plain_forwarded_text_not_an_image(self):
        msg={'message':[self.segment()]}
        value=bot.message_text(msg)
        for expected in ('转发文本：链接卡片','哔哩哔哩','队友我摸出国王号了','https://www.bilibili.com/video/BV123'):
            self.assertIn(expected,value)
        self.assertEqual(bot.image_urls(msg),[])
        self.assertNotIn('cover.jpg',value)
        self.assertEqual(private_text(msg),value)
        self.assertEqual(plain(msg['message']),value)

    def test_structured_share_and_xml_are_read_as_text(self):
        row={'type':'json','data':{'data':json.dumps({'meta':{'news':{'title':'标题','desc':'摘要','jumpUrl':'https://example.test/a'}}})}}
        self.assertIn('摘要',render(row))
        xml={'type':'xml','data':{'data':'<msg url="https://example.test/a?x=1&amp;y=2"><item><title>标题</title><summary>简介</summary></item><source name="来源"/></msg>'}}
        self.assertEqual(card(xml)['url'],'https://example.test/a?x=1&y=2')
        self.assertEqual(card(xml)['source'],'来源')
        share={'type':'share','data':{'title':'标题','content':'摘要','url':'https://example.test/a'}}
        self.assertIn('链接：https://example.test/a',bot.message_text({'message':[share]}))

    def test_malformed_cards_do_not_leak_raw_payload_or_create_fake_links(self):
        for raw in ('bad SECRET', '[]', '{"meta":{"news":{"jumpUrl":"javascript:SECRET"}}}'):
            value=render({'type':'json','data':{'data':raw}})
            self.assertIn('卡片消息',value)
            self.assertNotIn('SECRET',value)
        self.assertEqual(card({'type':'xml','data':{'data':'<!DOCTYPE x [<!ENTITY y "z">]><msg/>'}}),{})

    def test_video_is_record_only_and_cannot_be_used_as_visual_source(self):
        msg={'message_id':1,'message':[{'type':'at','data':{'qq':99}},
            {'type':'video','data':{'file':'x.mp4','url':'https://invalid.test/x.mp4','thumb':'https://invalid.test/cover.jpg'}}]}
        item=bot.chat_item(msg,99)
        self.assertIn('[视频]',item['text'])
        self.assertTrue(item['_record_only'])
        self.assertEqual(assets(item)['images'],[])
        self.assertEqual(assets(item)['links'],[])
        with patch.object(bot,'analyze_roles',new=AsyncMock()) as roles:
            reply=asyncio.run(bot.generate_for_message(None,bot.settings(),[],'被@',item))
        self.assertEqual(str(reply),'');roles.assert_not_awaited()

    def test_group_loop_records_video_without_ai_then_answers_normal_new_message(self):
        async def run(folder):
            polls=0;generated=[];sent=[];sleep=asyncio.sleep
            cfg={**bot.settings(),'poll_seconds':0,'mention_cooldown_seconds':0,'passive_probability':0,
                'memory_enabled':False,'style_learning_enabled':False,'proactive_topics_enabled':False}
            def raw(identity,segment):
                return {'message_id':identity,'time':time.time(),'user_id':123,'message':[
                    {'type':'at','data':{'qq':169901502}},segment]}
            async def api(client,env,action,params=None):
                nonlocal polls
                if action=='get_login_info':return {'user_id':169901502}
                if action=='get_group_info':return {}
                if action=='get_group_member_list':return []
                if action=='get_group_msg_history':
                    if params['count']==100:return {'messages':[]}
                    polls+=1
                    if polls>15:(folder/'STOP').touch()
                    return {'messages':[raw('video',{'type':'video','data':{'url':'https://invalid.test/v.mp4'}})] if polls==1 else
                        [raw('new',{'type':'text','data':{'text':'在吗'}})]}
                if action=='send_group_msg':sent.append(params);(folder/'STOP').touch();return {'message_id':99}
                if action=='get_recent_contact':return []
                raise AssertionError(action)
            async def generate(client,cfg,history,trigger,target,as_image=False):
                generated.append(target['_id']);return '在呢'
            async def tick(seconds):await sleep(0);await sleep(0)
            with patch.object(bot,'RUNTIME',folder),patch.object(bot,'settings',return_value=cfg),patch.object(bot,'api',api), \
                    patch.object(bot,'generate_for_message',generate),patch.object(bot.asyncio,'sleep',tick), \
                    patch.object(ImageRecognition,'offer') as offer:
                await bot.run()
            self.assertEqual(generated,['new']);self.assertEqual(len(sent),1)
            self.assertEqual([call.args[0]['_id'] for call in offer.call_args_list],['new'])
            rows={row['_id']:row for row in RecentChatJournal(folder/'recent-chat.jsonl').load()}
            self.assertIn('[视频]',rows['video']['text']);self.assertTrue(rows['video']['_record_only'])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(Path(folder)))


if __name__=='__main__':unittest.main()
