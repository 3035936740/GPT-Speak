import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock,Mock
from large_task_notices import write


class NoticeTests(unittest.IsolatedAsyncioTestCase):
    def setup_files(self,folder):
        root=Path(folder);(root/'persona.txt').write_text('全局龙娘设定',encoding='utf-8')
        (root/'reply_protocol.txt').write_text('全局简短回复规则',encoding='utf-8')
        group=root/'group-config'/'123';group.mkdir(parents=True)
        (group/'persona.txt').write_text('当前群专属龙娘设定',encoding='utf-8')
        (group/'reply_protocol.txt').write_text('本群回复规则：自然随意',encoding='utf-8')
        return root
    def cfg(self,**more):return {'group_id':123,'model':'chat-model','max_prompt_chars':12800,
        'model_timeout_seconds':30,'_reserve_extra_call':Mock(return_value=True),**more}

    async def test_start_duplicate_and_disabled_remain_silent_without_model_calls(self):
        model=AsyncMock()
        for status in ('accepted','duplicate','disabled'):
            self.assertFalse(await write(self.cfg(),{},status,{},model,Path('unused')))
        model.assert_not_awaited()

    async def test_completion_uses_current_group_persona_rules_and_chat_model(self):
        model=AsyncMock(return_value={'text':'做好啦，看看合不合胃口'})
        cfg=self.cfg()
        with tempfile.TemporaryDirectory() as folder:
            root=self.setup_files(folder)
            result=await write(cfg,{'text':'做一个页面'},'uploaded',{'name':'page.html','summary':'技术清单'},model,root)
        self.assertEqual(result,'做好啦，看看合不合胃口');self.assertTrue(result.model_called)
        used,prompt,schema=model.call_args.args
        self.assertEqual(used['model'],'chat-model');self.assertEqual(used['_web_search_mode'],'disabled')
        self.assertEqual(used['_usage_category'],'task')
        self.assertIn('当前群专属龙娘设定',prompt);self.assertIn('本群回复规则：自然随意',prompt)
        self.assertNotIn('全局龙娘设定',prompt);self.assertIn('QQ已确认',prompt)
        cfg['_reserve_extra_call'].assert_called_once()

    async def test_no_canned_fallback_on_failed_model_invalid_text_or_exhausted_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.setup_files(folder)
            for model in (AsyncMock(side_effect=ValueError('bad format')),AsyncMock(return_value={'text':''}),
                    AsyncMock(return_value={'text':'a'*61})):
                result=await write(self.cfg(),{},'uploaded',{},model,root)
                self.assertFalse(result);self.assertTrue(result.model_called)
            model=AsyncMock()
            result=await write(self.cfg(_reserve_extra_call=lambda:False),{},'uploaded',{},model,root)
            self.assertFalse(result);model.assert_not_awaited()

    async def test_full_uses_reserved_call_and_failure_honors_log_only(self):
        model=AsyncMock(return_value={'text':'现在有点忙，晚点再找我吧'})
        cfg=self.cfg()
        with tempfile.TemporaryDirectory() as folder:
            root=self.setup_files(folder)
            self.assertTrue(await write(cfg,{},'full',{},model,root));cfg['_reserve_extra_call'].assert_not_called()
            self.assertFalse(await write(cfg,{},'failed',{},model,root));self.assertEqual(model.await_count,1)
            model.return_value={'text':'已经上传成功'}
            self.assertFalse(await write(cfg,{},'full',{},model,root))
