import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
import bot
from file_messages import prepare
from pdf_text import extract
from large_tasks import requested,unfulfilled_promise


def pdf_bytes(text='Solve 2 + 2',blank=False,encrypted=False,pages=1):
    writer=PdfWriter()
    for _ in range(pages):
        page=writer.add_blank_page(300,300)
        if not blank:
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
            stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 20 200 Td ('+text+') Tj ET').encode('ascii'))
            page[NameObject('/Contents')]=writer._add_object(stream)
    if encrypted:writer.encrypt('test-password')
    output=io.BytesIO();writer.write(output);return output.getvalue()


class PdfTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_pdf_is_extracted_by_worker_and_preserves_page_markers(self):
        result=await extract(pdf_bytes())
        self.assertIn('Solve 2 + 2',result['text']);self.assertIn('[PDF page 1]',result['text'])
        self.assertEqual(result['pages'],1);self.assertFalse(result['truncated'])
        self.assertIn('公式',result['extraction_note'])

    async def test_unreadable_scanned_encrypted_pdf_never_becomes_text_or_empty_success(self):
        for raw,reason in [(pdf_bytes(blank=True),'pdf_no_text'),(pdf_bytes(encrypted=True),'pdf_encrypted'),(b'%PDF-broken','pdf_unreadable')]:
            with self.assertRaisesRegex(ValueError,reason):await extract(raw)
        result=await extract(pdf_bytes(pages=51))
        self.assertTrue(result['truncated']);self.assertEqual(result['pages_read'],50)

    async def test_http_file_reader_reports_pdf_no_text_and_keeps_five_mib_limit(self):
        raw=pdf_bytes(blank=True);api=AsyncMock(return_value={'url':'https://qq.com/a'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=raw))) as client:
            status,pages=await prepare([{'id':'x','name':'题目.pdf','size':len(raw)}],{},api,client,123,'m')
            self.assertEqual(status[0]['status'],'unsupported');self.assertIn('扫描件',status[0]['reason']);self.assertFalse(pages)
            api.reset_mock()
            status,pages=await prepare([{'id':'x','name':'large.pdf','size':5242880}],{},api,client,123,'m')
            self.assertEqual(status[0]['status'],'too_large');api.assert_not_awaited()

    async def test_original_request_routes_quoted_pdf_to_file_model_instead_of_short_reply(self):
        raw=pdf_bytes();now=time.time();target={'_id':'task','_uid':'3035936740','_self_id':'169901502',
            '_time':now,'at_bot':True,'text':'[引用回复]@169901502 算了把这道题做完,做完后给我结题步骤文件','_reply_id':'pdf'}
        quote={'_id':'pdf','_uid':'3035936740','_time':now-10,'text':'[文件：题目.pdf]',
            '_files':[{'id':'file-id','name':'题目.pdf','size':len(raw)}]}
        async def api(client,env,action,params):
            if action=='get_group_file_url':return {'url':'https://qq.com/a'}
            if action=='get_group_member_info':return {'user_id':169901502,'role':'member'}
            if action=='get_group_file_system_info':return {}
            raise AssertionError(action)
        async def model(cfg,prompt,schema,images=None):
            data=json.loads(prompt);self.assertIn('Solve 2 + 2',data['source_files'][0]['content'])
            self.assertIn('公式',data['source_files'][0]['extraction_note']);self.assertEqual(cfg['model'],'gpt-6.1-sol')
            self.assertIn('结题步骤文件',data['request'])
            return {'filename':'solution.md','content':'2 + 2 = 4','complete':True,'summary':'答案和步骤'}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=raw))) as client:
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),\
                    patch.object(bot,'generate_json',model),patch.object(bot,'analyze_roles',AsyncMock()) as roles:
                reply=await bot.generate_for_message(client,{**bot.settings(),'large_tasks_enabled':True},[quote],'被@',target)
                self.assertEqual(reply.segments[0]['data']['name'],'solution.md');roles.assert_not_awaited()

    def test_honest_source_failure_is_not_misclassified_as_an_unfulfilled_file_promise(self):
        for text in ['我现在无法读取这份PDF，先把题目贴出来，我再做步骤文件','我没读到题目正文，发一下题目文字再给你做文件','我不能直接生成这个文件']:
            self.assertFalse(unfulfilled_promise(text),text)
        self.assertTrue(unfulfilled_promise('那我就做个HTML页面给你'))
        for text in ['算了把这道题做完,做完后给我结题步骤文件','把这份日志分析完后给我报告文件']:
            self.assertTrue(requested(text),text)
        for text in ['他把题做完后给我步骤文件了','不用写文件了','这个PDF怎么打开','你能生成文件吗']:
            self.assertFalse(requested(text),text)

    def test_pdf_delivery_word_order_and_other_formats_are_explicit_tasks(self):
        for text in ['[引用回复]@169901502 做这道题,做完把你的解题pdf发群里',
                '帮我解这道题，答案做成PDF发到群里','把这个附件整理成docx给我',
                '分析这个表格，整理成xlsx发群','做一份pdf报告','根据这个日志生成报告pdf']:
            self.assertTrue(requested(text),text)
        for text in ['他说做这道题做完把解题pdf发群里','你会做PDF吗','怎么做PDF发群里',
                '不用做题生成PDF了','这个pdf挺好看的','他昨天把pdf发群里了']:
            self.assertFalse(requested(text),text)

    async def test_exact_reported_request_stages_binary_pdf_and_bypasses_chat_roles(self):
        raw=pdf_bytes()+b'\0';now=time.time()
        for quoted in (True,False):
            target={'_id':'294754329','_uid':'3035936740','_self_id':'169901502','_time':now,
                'at_bot':True,'text':'[引用回复]@169901502 做这道题,做完把你的解题pdf发群里'}
            source={'_id':'993023434','_uid':'3035936740','_time':now-20,
                'text':'[文件：测试考题.pdf]','_files':[{'id':'file-id','name':'测试考题.pdf','size':len(raw)}]}
            if quoted:target['_reply_id']=source['_id']
            else:target['_files']=source['_files']
            async def api(client,env,action,params):
                self.assertEqual(action,'get_group_file_url');return {'url':'https://qq.com/test.pdf'}
            async def execute(cfg,item,pages,api,model,runtime,context=()):
                import base64
                from emoji_library import ReplyText
                self.assertIn('解题pdf发群里',item['_file_task_request'])
                self.assertEqual(base64.b64decode(item['_task_inputs'][0]['data']),raw)
                self.assertTrue(item['_task_inputs'][0]['binary'])
                self.assertEqual(item['_attachment_status'][0]['status'],'sandbox_pending')
                return ReplyText('task-routed'),{'status':'ready'}
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=raw))) as client:
                with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'api',api),\
                        patch('large_tasks.generate',side_effect=execute) as generate,\
                        patch.object(bot,'analyze_roles',AsyncMock(side_effect=AssertionError('must not become chat'))),\
                        patch.object(bot,'generate',AsyncMock(side_effect=AssertionError('must not become chat'))):
                    reply=await bot.generate_for_message(client,{**bot.settings(),'large_tasks_enabled':True,'large_task_executor':'podman'},[source] if quoted else [],'被@',target)
                    self.assertEqual(str(reply),'task-routed');generate.assert_awaited_once()
