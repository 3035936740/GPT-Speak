import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from napcat_retry import retry_delay,wait_retry


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def test_unavailable_group_does_not_poll_login_every_ten_seconds(self):
        self.assertEqual(retry_delay(1,absent=True),60)
        self.assertEqual([retry_delay(i) for i in range(1,7)],[10,20,40,60,60,60])

    async def test_stop_wakes_within_one_second(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);cfg={'enabled':True}
            async def stop(seconds):
                self.assertEqual(seconds,1)
                (folder/'STOP').touch()
            with patch('napcat_retry.asyncio.sleep',new=AsyncMock(side_effect=stop)) as sleep:
                await wait_retry(folder,cfg,lambda:cfg,60)
            self.assertEqual(sleep.await_count,1)

    async def test_config_change_wakes_and_refresh_marker_is_consumed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);cfg={'expected_self_id':'123'}
            with patch('napcat_retry.asyncio.sleep',new=AsyncMock()) as sleep:
                await wait_retry(folder,cfg,lambda:{'expected_self_id':'456'},60)
                sleep.assert_not_awaited()
                marker=folder/'group-directory-refresh';marker.touch()
                self.assertEqual(await wait_retry(folder,cfg,lambda:cfg,60),'refresh')
                self.assertFalse(marker.exists())
                sleep.assert_not_awaited()
                await wait_retry(folder,cfg,lambda:cfg,10)
                self.assertEqual(sleep.await_count,10)
