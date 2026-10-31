from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from console_config import atomic,runtime
from session_control import request,state,allows


class SessionControlTests(unittest.TestCase):
    def test_stop_boundary_clear_boundary_and_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);atomic(root/'settings.json',{'group_id':1,'chat_group_whitelist':[1,2]})
            first=runtime(root,1);second=runtime(root,2)
            with patch('session_control.time.time',return_value=100):request(root,1,'stop')
            self.assertFalse(allows(first,{'_time':99}));self.assertFalse(allows(first,{'_time':101}))
            self.assertTrue(allows(second,{'_time':99}))
            with patch('session_control.time.time',return_value=200):request(root,1,'clear')
            with patch('session_control.time.time',return_value=300):request(root,1,'stop')
            self.assertEqual(state(first)['clear_before'],200)
            self.assertFalse(allows(first,{'_time':250}));self.assertFalse(allows(first,{'_time':301}))
            with patch('session_control.time.time',return_value=400):request(root,1,'resume')
            self.assertFalse(allows(first,{'_time':301}));self.assertTrue(allows(first,{'_time':401}))
            self.assertFalse(state(first)['paused']);self.assertTrue(allows(second,{'_time':99}))
            with self.assertRaises(ValueError):request(root,3,'stop')


if __name__=='__main__':unittest.main()
