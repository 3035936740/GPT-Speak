import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import bot
from reply_validation import prepare, diagnostic


class ReplyValidationTests(unittest.TestCase):
    def result(self, **values):
        return {'reply_to':'101', 'conversation':'direct', 'should_reply':True,
            'action':'chat', 'text':'', 'text_parts':[], 'media_ids':[],
            'media_kind':'none', 'media_id':'', 'reply_mode':'text',
            'answer_complete':True, **values}

    def test_harmless_empty_chunks_and_paragraph_spacing_are_normalized(self):
        result, text, reason = prepare(self.result(text_parts=[' 第一段。 ', '', '\r\n', '第二段\n\n第三段。']), 40)
        self.assertEqual(text, '第一段\n第二段\n第三段')
        self.assertEqual(result['text_parts'], ['第一段', '第二段\n第三段'])
        self.assertEqual(reason, '')

    def test_blank_chunks_fall_back_to_real_text_without_coercing_other_types(self):
        self.assertEqual(prepare(self.result(text='正常回答', text_parts=[' ', '。']), 40)[1], '正常回答')
        self.assertEqual(prepare(self.result(text=None), 40)[2], 'invalid_text_type')
        self.assertEqual(prepare(self.result(text='正常回答', text_parts=[None]), 40)[2], 'invalid_text_parts_type')

    def test_unsafe_or_long_parts_cannot_be_bypassed_by_alternate_text(self):
        for part, reason in [('长'*41, 'text_over_limit'), ('[CQ:at,qq=1]', 'raw_cq_code'), ('```code```', 'code_fence')]:
            with self.subTest(reason=reason):
                _, text, actual = prepare(self.result(text='unused', text_parts=[part]), 40)
                self.assertEqual(text, '')
                self.assertEqual(actual, reason)
        self.assertEqual(prepare(self.result(text_parts=['长'*20, '长'*20]), 40)[2], 'text_over_limit')
        self.assertEqual(prepare(self.result(should_reply=False, text='不发送'), 40)[2], 'model_silence')

    def test_diagnostics_never_include_generated_content(self):
        result = self.result(text='PRIVATE-TEXT', text_parts=['SECRET-PART'], extra='PRIVATE-EXTRA')
        detail = diagnostic(result, 'code_fence', 40)
        self.assertIn('validation=code_fence', detail)
        self.assertIn('part_chars=[11]', detail)
        self.assertNotIn('PRIVATE', detail)
        self.assertNotIn('SECRET', detail)

    def run_reply(self, results, reserve):
        async def scenario(folder):
            target = {'_id':'101', '_uid':'11', 'sender':'甲', 'text':'在吗', 'at_bot':True,
                '_audience':'direct', '_reply_style':{'allow_burst':True}}
            cfg = {**bot.settings(), 'max_reply_chars':40, '_reserve_extra_call':reserve}
            model = AsyncMock(side_effect=results)
            failure = AsyncMock(return_value=bot.ReplyText('', route='silent'))
            with patch.object(bot, 'RUNTIME', Path(folder)), patch.object(bot, 'generate_reply_json', model), \
                    patch.object(bot, 'generated_failure_reply', failure):
                reply = await bot.generate(cfg, [], '被@', target)
            return reply, model, failure
        with tempfile.TemporaryDirectory() as folder:
            return asyncio.run(scenario(folder))

    def test_ordinary_overlong_reply_gets_one_bounded_repair(self):
        reserve = unittest.mock.Mock(return_value=True)
        reply, model, failure = self.run_reply([self.result(text='长'*41), self.result(text='在呢')], reserve)
        self.assertEqual(str(reply), '在呢')
        self.assertEqual(model.await_count, 2)
        reserve.assert_called_once()
        failure.assert_not_awaited()

    def test_failed_repair_keeps_specific_reason_and_never_sends_invalid_parts(self):
        reserve = unittest.mock.Mock(return_value=True)
        reply, model, failure = self.run_reply([self.result(text='长'*41), self.result(text_parts=['```SECRET```'])], reserve)
        self.assertEqual(str(reply), '')
        self.assertEqual(model.await_count, 2)
        self.assertIn('validation=code_fence', str(failure.call_args.args[-1]))
        self.assertNotIn('SECRET', str(failure.call_args.args[-1]))
        reserve.assert_called_once()

    def test_exhausted_budget_does_not_call_another_model(self):
        reserve = unittest.mock.Mock(return_value=False)
        reply, model, failure = self.run_reply([self.result(text='长'*41)], reserve)
        self.assertEqual(str(reply), '')
        self.assertEqual(model.await_count, 1)
        self.assertIn('validation=text_over_limit', str(failure.call_args.args[-1]))

    def test_normalized_content_is_exactly_what_delivery_uses(self):
        reply, model, failure = self.run_reply([self.result(text_parts=[' 第一段。 ', '', '第二段\n\n第三段'])], lambda:False)
        self.assertEqual(str(reply), '第一段\n第二段\n第三段')
        self.assertEqual(reply.chunks, ['第一段', '第二段\n第三段'])
        self.assertEqual(model.await_count, 1)
        failure.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
