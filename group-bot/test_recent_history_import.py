import unittest
from recent_history_import import merge


class Tests(unittest.TestCase):
    def test_history_only_dedup_order_and_group(self):
        def convert(row, self_id):
            return {'_id':str(row['message_id']), '_time':row['time'], 'text':'x'}
        rows=[{'message_id':i,'time':i,'group_id':g} for i,g in [(4,1),(3,2),(2,1),(2,1),(1,1),(5,1)]]
        added=merge(rows,[{'_id':'1'}],'9',1,convert,deleted=['5'])
        self.assertEqual([item['_id'] for item in added],['2','4'])
        self.assertTrue(all(item['_historical'] for item in added))
        self.assertEqual(merge(rows,[],'9',1,convert,clear_before=5),[])
