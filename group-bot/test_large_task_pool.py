import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from large_task_pool import LargeTaskPool


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_three_slots_no_waiting_and_ordinary_work_remains_responsive(self):
        with tempfile.TemporaryDirectory() as folder:
            a=LargeTaskPool(folder,Path(folder)/'a',1);b=LargeTaskPool(folder,Path(folder)/'b',2)
            gate=asyncio.Event();started=[]
            async def work():started.append(1);await gate.wait()
            cfg={'large_tasks_enabled':True,'large_task_max_concurrent':3}
            try:
                self.assertEqual(a.submit('1',cfg,work),'accepted')
                self.assertEqual(b.submit('2',cfg,work),'accepted')
                self.assertEqual(b.submit('3',cfg,work),'accepted')
                self.assertEqual(a.submit('4',cfg,work),'full')
                self.assertEqual(a.submit('1',cfg,work),'duplicate')
                async def ordinary():await asyncio.sleep(0);return 'ordinary reply'
                self.assertEqual(await asyncio.wait_for(ordinary(),.5),'ordinary reply')
                self.assertEqual(len(started),3)
            finally:gate.set();await a.close();await b.close()
            with closing(sqlite3.connect(a.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    async def test_disabled_and_cancelled_jobs_never_reserve_slots_or_leak_after_immediate_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=LargeTaskPool(folder,Path(folder)/'g',1)
            async def work():await asyncio.Event().wait()
            self.assertEqual(pool.submit('off',{'large_tasks_enabled':False},work),'disabled')
            self.assertEqual(pool.submit('on',{'large_tasks_enabled':True},work),'accepted')
            await pool.close()
            with closing(sqlite3.connect(pool.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    async def test_operator_permission_guard_cancels_running_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=LargeTaskPool(folder,Path(folder)/'g',1);live=True;cancelled=asyncio.Event()
            async def work():
                try:await asyncio.Event().wait()
                finally:cancelled.set()
            pool.submit('1',{'large_tasks_enabled':True},work,lambda:live)
            await asyncio.sleep(0.02);live=False
            await asyncio.wait_for(cancelled.wait(),1);await pool.close()
