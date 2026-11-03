import copy
import json
from pathlib import Path
import tempfile
import unittest
from image_context import compress
from debug_history import bounded
from task_prompt import chat_window
from topic_context import select

DESCRIPTION='[图片摘要：'+('人物拿着杯子，画面背景为浅蓝色；'*30)+']'


def row(identity,key='a'*64,text=None):
    return {'_id':str(identity),'message_id':str(identity),'_uid':'u'+str(identity),'sender':'sender'+str(identity),
        '_time':float(identity),'time':float(identity),'kind':'received','_image_hashes':[key],
        'text':text or DESCRIPTION}


class ImageContextTests(unittest.TestCase):
    def test_repeats_keep_one_full_summary_each_sender_time_and_mixed_text(self):
        rows=[row(i,text='消息'+str(i)+DESCRIPTION+'尾部') for i in range(1,10)]
        before=copy.deepcopy(rows);result=compress(rows)
        self.assertEqual(rows,before)
        self.assertEqual(sum(r['text'].count('[图片摘要：') for r in result),1)
        self.assertEqual(sum('[同图：消息9第1张]' in r['text'] for r in result),8)
        for original,short in zip(rows,result):
            self.assertEqual(original['_time'],short['_time']);self.assertEqual(original['sender'],short['sender'])
            self.assertTrue(short['text'].startswith('消息'));self.assertTrue(short['text'].endswith('尾部'))
        self.assertLess(sum(len(r['text']) for r in result),sum(len(r['text']) for r in rows)//3)

    def test_identical_descriptions_without_matching_hashes_are_not_merged(self):
        rows=[row(1,'a'*64),row(2,'b'*64),{**row(3),'_image_hashes':[]}]
        self.assertEqual(compress(rows),rows)

    def test_url_aliases_share_digest_only_after_content_binding_and_mixed_slots_stay_ordered(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'image-memory.json').write_text(json.dumps({'sources':{'a'*64:{'digest':'c'*64},'b'*64:{'digest':'c'*64}}}))
            result=compress([row(1),row(2,'b'*64)],folder)
            self.assertIn('[同图：消息2第1张]',result[0]['text'])
        mixed={**row(1),'_image_hashes':['a'*64,'b'*64],'text':'前'+DESCRIPTION+'中'+DESCRIPTION+'后'}
        result=compress([mixed,row(2,'a'*64)])
        self.assertEqual(result[0]['text'],'前[同图：消息2第1张]中'+DESCRIPTION+'后')

    def test_view_recomputes_after_anchor_deleted_or_older_view_selected(self):
        compressed=compress([row(1),row(2),row(3)])
        self.assertEqual(compress(compressed[:1])[0]['text'],DESCRIPTION)
        reduced=compress(compressed[:2])
        self.assertEqual(reduced[-1]['text'],DESCRIPTION)
        self.assertIn('消息2',reduced[0]['text'])

    def test_bounded_window_counts_compressed_text_and_keeps_anchor_on_roll(self):
        rows=[row(i) for i in range(1,18)]
        cfg={'console_max_text_chars':1600,'console_max_records':100}
        result=bounded(rows,cfg)
        self.assertEqual(len(result),17)
        self.assertEqual(result[-1]['text'],DESCRIPTION)
        self.assertLessEqual(sum(len(r['text']) for r in result),1600)
        restored=bounded(result[:5],cfg)
        self.assertEqual(restored[-1]['text'],DESCRIPTION)
        self.assertNotIn('消息17',str([r['text'] for r in restored]))

    def test_task_and_role_selection_only_reference_images_in_their_selected_window(self):
        rows=[row(i) for i in range(1,10)]
        cfg={'group_id':100,'console_max_text_chars':1600,'context_messages':3}
        task=chat_window(cfg,row(10),rows)
        self.assertEqual(len(task),9)
        self.assertEqual(task[-1]['text'],DESCRIPTION)
        self.assertNotIn('_image_full_text',json.dumps(task))
        required,recent,_=select(rows,row(10),cfg)
        self.assertEqual(required,[]);self.assertEqual(len(recent),3)
        self.assertEqual(recent[-1]['text'],DESCRIPTION)
        self.assertIn('消息9',recent[0]['text'])


if __name__=='__main__':unittest.main()
