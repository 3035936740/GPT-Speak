import asyncio
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from PIL import Image
import bot
from content_reference import resolve
from file_messages import metadata,prepare,text_content,allowed_url
from model_attachments import limits,select,local_inputs
from image_sources import ImageDownloadError
from recent_chat_journal import compact
from debug_references import message_metadata


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    def target(self,**kw):return {'_id':'101','_uid':'11111','_self_id':'169901502','_time':time.time(),'at_bot':True,'text':'娘化这个表情包',**kw}

    async def test_archived_avatar_drawing_uses_both_originals_without_public_profile_lookup(self):
        from image_archive import ImageArchive
        with tempfile.TemporaryDirectory() as folder:
            archive=ImageArchive(folder)
            originals=[]
            keys=['a'*64,'b'*64]
            for key,color in zip(keys,['blue','white']):
                output=io.BytesIO()
                Image.new('RGB',(2,2),color).save(output,format='PNG')
                originals.append(output.getvalue())
                archive.put(originals[-1],key)
            target=self.target(text='给你的形象生成一张图二的这个头像',
                _image_hashes=keys,_image_count=2,_attachment_order=[['image',0],['image',1]])
            async def image(cfg,runtime,request,paths):
                self.assertEqual([p.read_bytes() for p in paths],originals)
                self.assertIn('[图片1]、[图片2]',request)
                return 'generated'
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',AsyncMock()) as api, \
                    patch.object(bot,'download_image',AsyncMock()) as download, \
                    patch.object(bot,'create_image',image),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
                cfg={**bot.settings(),'_image_archive':archive}
                self.assertEqual(await bot.generate_for_message(None,cfg,[],'手动生图',target,as_image=True),'generated')
                api.assert_not_awaited()
                download.assert_not_awaited()

    async def test_two_quoted_images_are_bound_to_the_original_message_and_reach_generator(self):
        urls=['https://gchat.qpic.cn/a','https://gchat.qpic.cn/b']
        quote=self.target(_id='100',_images=urls);target=self.target(_reply_id='100',_quoted_message=quote)
        seen=[]
        async def download(client,url,path,cfg):path.write_bytes(url.encode());return 'hash'
        async def image(cfg,runtime,request,paths):seen.extend(p.read_text() for p in paths);return 'generated'
        reader=AsyncMock()
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'download_image',download),patch.object(bot,'create_image',image),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
            result=await bot.generate_for_message(None,{**bot.settings(),'_image_reader':reader},[self.target(_id='new',_images=['wrong'])],'被@',target,as_image=True)
        self.assertEqual(result,'generated');self.assertEqual(seen,urls);reader.read.assert_not_awaited()

    async def test_first_ten_images_survive_message_parsing_and_generation(self):
        urls=['https://gchat.qpic.cn/'+str(i) for i in range(12)]
        msg={'message_id':'123','user_id':11111,'group_id':985569438,'time':time.time(),'message':[{'type':'image','data':{'url':u}} for u in urls]}
        item=bot.chat_item(msg,169901502);self.assertEqual(item['_images'],urls)
        chosen,files,omitted=select(resolve(item,[]),{})
        self.assertEqual(chosen,urls[:10]);self.assertEqual(files,[]);self.assertEqual(omitted,2)
        seen=[]
        async def download(client,url,path,cfg):seen.append(url);path.write_bytes(b'image')
        async def image(cfg,runtime,request,paths):self.assertEqual(len(paths),10);self.assertEqual(cfg['_attachment_status'][0]['count'],2);return 'image'
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'download_image',download),patch.object(bot,'create_image',image),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
            await bot.generate_for_message(None,bot.settings(),[],'被@',{**item,'at_bot':True,'text':'娘化这些图片'},as_image=True)
        self.assertEqual(seen,urls[:10])

    async def test_image_limit_checks_original_size_and_equal_boundary_before_decoding(self):
        for declared in [True,False]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*10,headers={'content-length':'10'} if declared else {}))) as client:
                with tempfile.TemporaryDirectory() as folder,self.assertRaisesRegex(ImageDownloadError,'model_file_too_large'):
                    await bot.download_image(client,'https://gchat.qpic.cn/a',Path(folder)/'image.jpg',{'model_max_file_bytes':10})

    async def test_all_rejected_drawing_references_become_a_model_explanation_not_text_to_image(self):
        target=self.target(_images=['https://gchat.qpic.cn/a']);seen=[]
        async def explain(cfg,context,trigger,adjusted,paths):seen.extend(adjusted['_attachment_status']);self.assertFalse(cfg['image_generation_enabled']);return 'too big'
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'download_image',AsyncMock(side_effect=ImageDownloadError('model_file_too_large'))),patch.object(bot,'create_image',AsyncMock()) as image,patch.object(bot,'generate',explain),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
            result=await bot.generate_for_message(None,bot.settings(),[],'被@',target,as_image=True)
        self.assertEqual(result,'too big');self.assertEqual(seen[0]['status'],'too_large');image.assert_not_awaited()

    async def test_file_card_and_quote_persist_without_file_contents_or_local_paths(self):
        msg={'message_id':123,'message':[{'type':'file','data':{'file_id':'id','file_name':'抓取日志.log','file_size':126*1024,'url':'file:///secret'}}]}
        item=bot.chat_item(msg,169901502)
        self.assertIn('抓取日志.log',item['text']);self.assertEqual(message_metadata(item)['files'][0]['size'],126*1024)
        stored=compact({**item,'_time':time.time()});self.assertEqual(stored['_files'][0]['id'],'id');self.assertNotIn('file://',json.dumps(stored))
        self.assertEqual(resolve(self.target(_reply_id='123',_quoted_message={'text':'旧预览'}),[item])['files'],item['_files'])

    async def test_file_metadata_size_rejects_before_any_api_or_download(self):
        api=AsyncMock();client=AsyncMock()
        results,pages=await prepare([{'id':'x','name':'large.log','size':5242880}],{},api,client,123,'m')
        self.assertEqual(results[0]['status'],'too_large');self.assertEqual(pages,[]);api.assert_not_awaited()

    async def test_text_files_read_through_qq_url_not_local_paths(self):
        raw='日志：测试错误\n第二行'.encode('utf-8');api=AsyncMock(return_value={'url':'https://gzc-download.ftn.qq.com/file'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=raw))) as client:
            results,pages=await prepare([{'id':'x','name':'抓取.log','size':len(raw)}],{},api,client,123,'m')
        self.assertEqual(results[0]['status'],'read');self.assertEqual(pages[0]['body'],raw.decode());self.assertEqual(api.call_args.args[1]['group_id'],123)
        for url in ['file:///etc/passwd','http://127.0.0.1/a','https://qq.com.evil.test/a','https://u:p@qq.com/a']:self.assertFalse(allowed_url(url))

    async def test_file_stream_cannot_lie_about_size_or_follow_redirects(self):
        for response,expected in [(httpx.Response(200,content=b'x'*10),'too_large'),(httpx.Response(302,headers={'location':'http://127.0.0.1/secret'}),'unavailable')]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:response)) as client:
                results,pages=await prepare([{'id':'x','name':'test.log','size':1}],{'model_max_file_bytes':10},AsyncMock(return_value={'url':'https://qq.com/f'}),client,123,'m')
            self.assertEqual(results[0]['status'],expected);self.assertFalse(pages)

    async def test_group_file_switch_blocks_reading_but_tells_model(self):
        quote=self.target(_id='100',_files=[{'id':'x','name':'a.log','size':100}]);target=self.target(text='根据这个写一个html发给我',_reply_id='100',_quoted_message=quote)
        seen=[]
        async def model(cfg,context,trigger,adjusted,paths):seen.extend(adjusted['_attachment_status']);return 'disabled'
        with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',AsyncMock()) as api,patch.object(bot,'generate',model),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
            await bot.generate_for_message(None,{**bot.settings(),'group_file_read_enabled':False},[],'被@',target)
        api.assert_not_awaited();self.assertEqual(seen[0]['status'],'disabled')

    async def test_quote_based_file_task_reads_contents_and_reaches_reply_prompt(self):
        quote=self.target(_id='100',_files=[{'id':'x','name':'a.log','size':100}]);target=self.target(text='根据这个写一个html发给我',_reply_id='100',_quoted_message=quote)
        async def model(cfg,context,trigger,adjusted,paths):
            self.assertEqual(adjusted['_attachment_status'][0]['status'],'read')
            prompt=bot.build_prompt(cfg,context,trigger,adjusted);self.assertIn('source fact',prompt);self.assertIn('a.log',prompt);self.assertLessEqual(len(prompt),cfg['max_prompt_chars']);return 'read'
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'source fact'))) as client:
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',AsyncMock(return_value={'url':'https://qq.com/f'})),patch.object(bot,'generate',model),patch.object(bot,'analyze_roles',AsyncMock(return_value={})):
                await bot.generate_for_message(client,bot.settings(),[],'被@',target)

    async def test_text_decoders_and_binary_rejection(self):
        for encoding in ['utf-8-sig','utf-16','gb18030']:self.assertEqual(text_content('中文日志'.encode(encoding)),'中文日志')
        with self.assertRaises(ValueError):text_content(b'\x00binary')

    async def test_mixed_attachments_share_first_n_limit_and_local_limit_is_independent_of_manual_upload(self):
        refs={'images':['a','b'],'files':[{'name':'a.log'},{'name':'b.log'}],'attachment_order':[('image',0),('file',0),('image',1),('file',1)]}
        self.assertEqual(select(refs,{'model_max_files':2}),(['a'],[{'name':'a.log'}],2))
        self.assertEqual(limits({}),(10,5242880))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'image';path.write_bytes(b'x'*10)
            selected,rejected=local_inputs([path],{'model_max_file_bytes':10});self.assertFalse(selected);self.assertEqual(rejected[0]['status'],'too_large')

    async def test_edit_commands_are_recognized_without_turning_discussion_into_drawing(self):
        for text in ['娘化这个表情包','娘化这个图片','把这张图片娘化','拟人化这些图片']:self.assertTrue(bot.wants_image(self.target(text=text)),text)
        for text in ['娘化是怎么回事','你会娘化吗','不要娘化这个图片']:self.assertFalse(bot.wants_image(self.target(text=text)),text)

    async def test_drawing_combines_quoted_and_current_images_in_source_order(self):
        target=self.target(_images=['current'],_quoted_message={'_id':'99','_images':['quoted']})
        self.assertEqual(resolve(target,[],for_drawing=True)['images'],['quoted','current'])
