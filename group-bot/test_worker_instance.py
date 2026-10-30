import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from console_config import atomic,runtime
from debug_console import Supervisor
from platform_runtime import msvcrt
from worker_instance import instance_locked


class WorkerInstanceTests(unittest.TestCase):
    def test_missing_and_unlocked_stale_files_are_not_running(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(instance_locked(folder))
            path=Path(folder)/'instance.lock';path.write_bytes(b'0')
            self.assertFalse(instance_locked(folder));self.assertEqual(path.read_bytes(),b'0')

    def test_real_owned_lock_is_detected_and_released(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'instance.lock';path.write_bytes(b'0')
            with path.open('r+b') as owner:
                msvcrt.locking(owner.fileno(),msvcrt.LK_NBLCK,1)
                try:self.assertTrue(instance_locked(folder))
                finally:owner.seek(0);msvcrt.locking(owner.fileno(),msvcrt.LK_UNLCK,1)
            self.assertFalse(instance_locked(folder))

    def test_recycled_live_pid_does_not_block_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);atomic(root/'settings.json',{'group_id':100,'chat_group_whitelist':[100],'enabled':True})
            target=runtime(root,100);target.mkdir(exist_ok=True)
            atomic(target/'state.json',{'pid':os.getpid(),'online':True,'last_poll':'2026-10-25T03:08:16'})
            (target/'instance.lock').write_bytes(b'0')
            supervisor=Supervisor(root);child=MagicMock();child.pid=32100
            with patch('debug_console.subprocess.Popen',return_value=child) as launch,patch('debug_console.WindowsJob'),patch('debug_console.PosixJob'):
                supervisor.sync();launch.assert_called_once()
                self.assertEqual(supervisor.children[100],child)
            for stream in supervisor.streams.values():stream.close()

    def test_stop_does_not_wait_on_an_unrelated_live_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);atomic(root/'settings.json',{'group_id':100,'chat_group_whitelist':[100]})
            target=runtime(root,100);target.mkdir(exist_ok=True);atomic(target/'state.json',{'pid':os.getpid()})
            with patch('debug_console.time.sleep') as sleep:
                Supervisor(root).stop_group(100);sleep.assert_not_called()


if __name__=='__main__':unittest.main()
