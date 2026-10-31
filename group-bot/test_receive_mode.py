from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
import asyncio
from console_config import Config,atomic
from receive_mode import set_mode
from session_control import state


class ReceiveModeTests(unittest.TestCase):
    def test_read_only_mode_blocks_model_and_send_at_entry(self):
        import bot
        async def run():
            client=AsyncMock()
            with patch.object(bot,'settings',return_value={'receive_only':True}):
                with self.assertRaises(ValueError):await bot.api(client,{},'send_group_msg',{})
                with self.assertRaises(ValueError):await bot.generate_json({},'ignored',Path('missing.schema.json'))
            client.post.assert_not_called()
        asyncio.run(run())
    def test_persistent_group_mode_does_not_disable_other_groups(self):
        import bot
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);atomic(root/'settings.json',{**bot.settings(),'group_id':1,'chat_group_whitelist':[1,2]})
            with patch('console_config.validate_model'):
                set_mode(root,1,True)
                self.assertTrue(Config(root,1).load()['receive_only'])
                self.assertFalse(Config(root,2).load()['receive_only'])
                self.assertTrue(Config(root,1).load()['enabled'])
                self.assertEqual(Config(root).load()['chat_group_whitelist'],[1,2])
                self.assertEqual(state(root/'runtime')['mode'],'interrupt')
                self.assertFalse(state(root/'runtime')['paused'])
                set_mode(root,1,False);self.assertFalse(Config(root,1).load()['receive_only'])


if __name__=='__main__':unittest.main()
