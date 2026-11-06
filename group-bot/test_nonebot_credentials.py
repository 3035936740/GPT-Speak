import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nonebot_credentials import ensure_token


class CredentialsTests(unittest.TestCase):
    def test_restart_closes_gateway_and_clears_restart_backoff(self):
        from nonebot_process import GatewayProcess
        gateway = GatewayProcess(Path('.'),None)
        gateway.retry_at=999999999
        calls=[]
        with patch.object(gateway,'close',side_effect=lambda:calls.append('close')), \
                patch.object(gateway,'sync',side_effect=lambda cfg:calls.append(('sync',cfg))):
            gateway.restart({'key':'value'})
        self.assertEqual(gateway.retry_at,0)
        self.assertEqual(calls,['close',('sync',{'key':'value'})])

    def test_only_first_start_allocates_and_empty_settings_reuse_saved_token(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('nonebot_credentials.secrets.token_urlsafe',return_value='first-token') as random, \
                    patch('console_config.Config.save'):
                cfg = {'nonebot_access_token': ''}
                self.assertEqual(ensure_token(root,cfg),'first-token')
                self.assertEqual(ensure_token(root,cfg),'first-token')
                self.assertEqual(ensure_token(root,{'nonebot_access_token':''}),'first-token')
                self.assertEqual(ensure_token(root,{'nonebot_access_token':'manual'}),'manual')
                self.assertEqual(ensure_token(root,{'nonebot_access_token':''}),'manual')
                random.assert_called_once()

    def test_existing_credential_is_adopted_without_random_allocation(self):
        with tempfile.TemporaryDirectory() as folder, patch('nonebot_credentials.secrets.token_urlsafe') as random:
            self.assertEqual(ensure_token(folder,{'nonebot_access_token':'existing'}),'existing')
            random.assert_not_called()
            self.assertEqual(json.loads((Path(folder)/'nonebot-credentials.json').read_text())['access_token'],'existing')
