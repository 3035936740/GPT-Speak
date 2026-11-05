import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bot
from image_memory import ImageMemory, source_key
from emoji_library import EmojiLibrary

FACTS = {'description': '小猫挥手', 'visible_text': '你好', 'confidence': .9, 'uncertainty': ''}


class ImageMemoryTests(unittest.TestCase):
    def test_every_occurrence_renews_the_2000_message_lifetime(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ImageMemory(Path(folder)/'memory.json')
            with patch.object(memory, 'save'):
                memory.observe('start', ['key'])
                memory.store('key', 'hash', FACTS)
                for index in range(1200): memory.observe(str(index), [])
                memory.observe('repeat', ['key'])
                for index in range(1999): memory.observe('next'+str(index), [])
                self.assertEqual(memory.lookup(key='key'), FACTS)
                memory.observe('expired', [])
                self.assertIsNone(memory.lookup(key='key'))
                self.assertFalse(memory.data['entries'])

    def test_restart_and_repoll_do_not_age_or_renew_the_same_message(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'memory.json'
            memory = ImageMemory(path, 10)
            memory.observe('a', ['key']); memory.store('key', 'hash', FACTS)
            memory.observe('b', [])
            restored = ImageMemory(path, 10)
            restored.observe('a', ['key'])
            self.assertEqual(restored.data['sequence'], 2)
            self.assertEqual(restored.data['entries']['hash']['last_seen'], 1)
            self.assertEqual(restored.lookup(key='key'), FACTS)
            self.assertNotIn('https:', path.read_text(encoding='utf-8'))

    def test_equal_content_from_new_source_renews_same_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ImageMemory(Path(folder)/'memory.json', 10)
            memory.observe('a', ['one']); memory.store('one', 'hash', FACTS)
            for index in range(8): memory.observe(str(index), [])
            memory.observe('repeat', ['two']); memory.bind('two', 'hash')
            self.assertEqual(memory.lookup(key='two'), FACTS)
            self.assertEqual(memory.data['entries']['hash']['last_seen'], 10)
            self.assertEqual(len(memory.data['entries']), 1)

    def test_repetition_is_per_message_and_rolls_over_including_text(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ImageMemory(Path(folder)/'memory.json')
            for index in range(3):
                result = memory.observe(str(index), ['key', 'key'])
                memory.observe('gap'+str(index), [])
            self.assertTrue(result['is_repetition'])
            self.assertEqual(result['same_image_messages'], 3)
            self.assertGreater(result['repetition_confidence'], 0)
            self.assertLess(result['repetition_confidence'], 1)
            self.assertFalse(result['consecutive_same_image'])
            for index in range(5): memory.observe('text'+str(index), [])
            self.assertFalse(memory.repetition(['key'])['is_repetition'])

    def test_two_consecutive_identical_images_are_repetition_but_separated_two_are_not(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ImageMemory(Path(folder)/'memory.json')
            self.assertFalse(memory.observe('first', ['key', 'key'])['is_repetition'])
            second = memory.observe('second', ['key'])
            self.assertTrue(second['is_repetition'])
            self.assertTrue(second['consecutive_same_image'])
            self.assertEqual(second['repetition_confidence'], .5)
            memory.observe('text', [])
            self.assertFalse(memory.repetition(['key'])['is_repetition'])
            self.assertFalse(memory.observe('other-image', ['different'])['is_repetition'])

    def test_three_consecutive_identical_images_have_full_repetition_confidence(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ImageMemory(Path(folder)/'memory.json')
            memory.observe('first', ['key'])
            self.assertEqual(memory.observe('second', ['key'])['repetition_confidence'], .5)
            third = memory.observe('third', ['key'])
            self.assertTrue(third['is_repetition'])
            self.assertEqual(third['repetition_confidence'], 1)

    def test_continuity_increases_confidence_for_three_images_in_ten_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            scores = []
            for name, positions in [('sparse', {0, 4, 9}), ('dense', {6, 7, 9}), ('consecutive', {7, 8, 9})]:
                memory = ImageMemory(Path(folder)/(name+'.json'))
                for index in range(10):
                    result = memory.observe(str(index), ['key'] if index in positions else [])
                self.assertTrue(result['is_repetition'])
                self.assertEqual(result['same_image_messages'], 3)
                scores.append(result['repetition_confidence'])
            self.assertLess(scores[0], scores[1])
            self.assertLess(scores[1], scores[2])
            self.assertEqual(scores[2], 1)

    def test_two_consecutive_sources_with_equal_bytes_also_match(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ImageMemory(Path(folder)/'memory.json')
            memory.observe('first', ['one']); memory.bind('one', 'hash')
            memory.observe('second', ['two']); memory.bind('two', 'hash')
            self.assertTrue(memory.repetition(['two'])['is_repetition'])

    def test_invalid_or_uncertain_vision_and_group_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            one, two = [ImageMemory(Path(folder)/(name+'.json')) for name in ('one','two')]
            one.observe('a', ['key'])
            self.assertFalse(one.store('key', 'hash', {**FACTS, 'confidence': .4}))
            self.assertFalse(one.store('key', 'hash', {'description': 'made-up'}))
            one.store('key', 'hash', FACTS)
            self.assertIsNone(two.lookup(key='key'))

    def test_stable_qq_hash_ignores_rotating_url_but_plain_filename_does_not(self):
        self.assertEqual(source_key({'file': 'A'*32+'.jpg','url': 'one'}),
            source_key({'file': 'a'*32+'.jpg','url': 'two'}))
        self.assertNotEqual(source_key({'file': 'image.png','url': 'one'}),
            source_key({'file': 'image.png','url': 'two'}))

    def test_repeated_ordinary_picture_is_only_an_unapproved_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ImageMemory(Path(folder)/'memory.json')
            library = EmojiLibrary(Path(folder)/'emoji', Path(folder)/'missing')
            for index in range(2):
                msg = {'message_id': index+1, 'message': [{'type':'image', 'data':
                    {'file':'photo.png','url':'https://gchat.qpic.cn/photo','sub_type':0}}]}
                item = bot.chat_item(msg, 'bot')
                item['_image_repetition'] = memory.observe(index+1, item['_image_keys'].values())
                library.observe(msg)
                bot.offer_repeated_images(memory,library,msg,item)
            self.assertEqual(library.queue.qsize(),1)
            self.assertFalse(library.items)
            self.assertFalse(library.choices('你好'))

    def test_repeated_vision_skips_download_and_raw_attachment_but_drawing_keeps_reference(self):
        async def scenario():
            downloads, visions, generations = [], [], []
            async def download(client,url,path,cfg):
                downloads.append(url); path.write_bytes(b'raw'); return hashlib.sha256(b'raw').hexdigest()
            async def roles(cfg,context,target,paths,*args,**kwargs):
                if paths: visions.append(list(paths)); return {'vision':FACTS}
                return {}
            async def generate(cfg,context,trigger,target,paths):
                generations.append((list(paths),target['_cached_vision']))
                return '你好('
            async def draw(cfg,runtime,request,paths):
                self.assertEqual(len(paths),1)
                self.assertTrue(paths[0].exists());return 'drawn'
            with tempfile.TemporaryDirectory() as folder, patch.object(bot,'RUNTIME',Path(folder)), \
                    patch.object(bot,'download_image',download), patch.object(bot,'analyze_roles',roles), \
                    patch.object(bot,'generate',generate), patch.object(bot,'create_image',draw), \
                    patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
                memory = ImageMemory(Path(folder)/'memory.json')
                cfg = {**bot.settings(),'_image_memory':memory}
                def target(id, url='https://gchat.qpic.cn/one', key='key'):
                    memory.observe(id,[key])
                    return {'_id':id,'text':'看图','at_bot':True,'_images':[url], '_image_keys':{url:key}}
                await bot.generate_for_message(None,cfg,[],'被@',target('1'))
                await bot.generate_for_message(None,cfg,[],'被@',target('2'))
                self.assertEqual(len(downloads),1)
                self.assertEqual(len(visions),1)
                self.assertFalse(generations[-1][0])
                self.assertEqual(generations[-1][1][0]['description'],FACTS['description'])
                await bot.generate_for_message(None,cfg,[],'被@',target('3','https://gchat.qpic.cn/two','newkey'))
                self.assertEqual(len(downloads),2)
                self.assertEqual(len(visions),1)
                result = await bot.generate_for_message(None,cfg,[],'画图',target('4'),as_image=True)
                self.assertEqual(result,'drawn')
                self.assertEqual(len(downloads),3)
                self.assertEqual(len(visions),1)
        asyncio.run(scenario())

    def test_background_summary_finishing_during_preparation_is_visible_to_routing(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);memory=ImageMemory(root/'memory.json')
                memory.observe('1',['key'])
                digest=hashlib.sha256(b'raw').hexdigest()
                async def download(client,url,path,cfg,**kwargs):
                    path.write_bytes(b'raw');return (digest,path) if kwargs.get('original') else digest
                def knowledge(*args):
                    memory.store('key',digest,FACTS);return []
                async def roles(cfg,context,target,paths,*args,**kwargs):
                    self.assertEqual(target['_cached_vision'][0]['description'],FACTS['description'])
                    return {}
                async def answer(cfg,context,trigger,target,images=None):return bot.ReplyText('图片已读')
                cfg={**bot.settings(),'image_direct_original_enabled':True,'_image_memory':memory}
                target={'_id':'1','text':'看图','at_bot':True,'_images':['https://gchat.qpic.cn/one'],
                        '_image_keys':{'https://gchat.qpic.cn/one':'key'}}
                with patch.object(bot,'RUNTIME',root),patch.object(bot,'download_image',download), \
                        patch.object(bot,'knowledge_for_message',knowledge),patch.object(bot,'analyze_roles',roles), \
                        patch.object(bot,'generate',answer):
                    await bot.generate_for_message(None,cfg,[],'被@',target)
        asyncio.run(scenario())

    def test_two_images_are_cached_with_separate_facts(self):
        async def scenario():
            async def download(client,url,path,cfg):path.write_bytes(url.encode()); return hashlib.sha256(url.encode()).hexdigest()
            async def roles(cfg,context,target,paths,*args,**kwargs):
                if not paths:return {}
                self.assertEqual(len(paths),1)
                return {'vision':{**FACTS,'description':paths[0].read_bytes().decode()}}
            async def generate(*args):return '好('
            with tempfile.TemporaryDirectory() as folder,patch.object(bot,'RUNTIME',Path(folder)), \
                    patch.object(bot,'download_image',download),patch.object(bot,'analyze_roles',roles), \
                    patch.object(bot,'generate',generate),patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
                memory = ImageMemory(Path(folder)/'cache.json')
                urls=['https://gchat.qpic.cn/one','https://gchat.qpic.cn/two']
                memory.observe('1',['one','two'])
                await bot.generate_for_message(None,{**bot.settings(),'_image_memory':memory},[],'图',
                    {'_id':'1','text':'看图','at_bot':True,'_images':urls,'_image_keys':dict(zip(urls,['one','two']))})
                self.assertEqual(memory.lookup(key='one')['description'],urls[0])
                self.assertEqual(memory.lookup(key='two')['description'],urls[1])
        asyncio.run(scenario())
