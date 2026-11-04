from pathlib import Path
import tempfile
import unittest

from console_config import Config, prompt_path


class DefaultPersonaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'persona-default.txt').write_text('你是yonagi，保持核心人设。',encoding='utf-8')

    def test_first_global_read_creates_persona_from_versioned_default(self):
        path=Config(self.root).prompt_path('persona')
        self.assertEqual(path,self.root/'persona.txt')
        self.assertEqual(path.read_bytes(),(self.root/'persona-default.txt').read_bytes())

    def test_bot_group_inheritance_also_initializes_absent_global_persona(self):
        path=prompt_path(self.root,{'group_id':100},'persona')
        self.assertEqual(path.read_text(encoding='utf-8'),'你是yonagi，保持核心人设。')
        self.assertEqual(Config(self.root,200).prompt_path('persona'),path)

    def test_template_updates_do_not_replace_a_custom_persona(self):
        custom=self.root/'persona.txt';custom.write_text('自定义人设',encoding='utf-8')
        self.assertEqual(Config(self.root).prompt_path('persona').read_bytes(),custom.read_bytes())
        self.assertEqual(prompt_path(self.root,{'group_id':100},'persona').read_text(encoding='utf-8'),'自定义人设')

    def test_local_override_is_preserved_without_creating_a_global_file(self):
        local=self.root/'group-config/100/persona.txt';local.parent.mkdir(parents=True)
        local.write_text('局部人设',encoding='utf-8')
        self.assertEqual(Config(self.root,100).prompt_path('persona'),local)
        self.assertEqual(prompt_path(self.root,{'group_id':100},'persona'),local)
        self.assertFalse((self.root/'persona.txt').exists())

    def test_empty_existing_persona_is_not_silently_replaced(self):
        (self.root/'persona.txt').write_bytes(b'')
        self.assertEqual(Config(self.root).prompt_path('persona').read_bytes(),b'')

    def test_reply_protocol_never_uses_persona_template(self):
        path=Config(self.root).prompt_path('reply_protocol')
        self.assertEqual(path,self.root/'reply_protocol.txt')
        self.assertFalse(path.exists())

    def test_protocol_default_initializes_only_when_missing(self):
        (self.root/'reply_protocol-default.txt').write_text('GPT回复规则',encoding='utf-8')
        path=Config(self.root).prompt_path('reply_protocol')
        self.assertEqual(path.read_text(encoding='utf-8'),'GPT回复规则')
        path.write_text('已有自定义规则',encoding='utf-8')
        self.assertEqual(Config(self.root).prompt_path('reply_protocol').read_text(encoding='utf-8'),'已有自定义规则')

    def test_shipped_default_matches_gpt_character_and_names(self):
        from console_config import DEFAULTS
        import json
        root=Path(__file__).parent
        persona=(root/'persona-default.txt').read_text(encoding='utf-8')
        protocol=(root/'reply_protocol-default.txt').read_text(encoding='utf-8')
        example=json.loads((root/'settings.example.json').read_text(encoding='utf-8'))
        self.assertIn('GPT',persona)
        self.assertIn('白龙',persona)
        self.assertIn('句尾',persona)
        self.assertLessEqual(len(persona)+len(protocol),3000)
        self.assertEqual(example['bot_name'],DEFAULTS['bot_name'])
        self.assertEqual(example['bot_aliases'],DEFAULTS['bot_aliases'])

    def test_empty_template_reports_an_actionable_error(self):
        (self.root/'persona-default.txt').write_bytes(b'')
        with self.assertRaisesRegex(ValueError,'默认提示词不能为空'):
            Config(self.root).prompt_path('persona')
        self.assertFalse((self.root/'persona.txt').exists())


if __name__=='__main__':unittest.main()
