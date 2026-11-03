import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from large_task_pool import LargeTaskPool


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_delivery_does_not_consume_generation_daily_admission(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=LargeTaskPool(folder,Path(folder)/'a',1)
            token,status=pool.claim('send',{'large_task_daily_limit':1,'_task_delivery_only':True})
            self.assertEqual(status,'accepted');pool.release(token)
            token,status=pool.claim('generate',{'large_task_daily_limit':1})
            self.assertEqual(status,'accepted');pool.release(token)
    async def test_zero_concurrent_limit_accepts_more_than_32_and_keeps_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=LargeTaskPool(folder,Path(folder)/'a',1);cfg={'large_task_max_concurrent':0}
            tokens=[]
            for i in range(35):
                token,status=pool.claim(str(i),cfg);self.assertEqual(status,'accepted');tokens.append(token)
            self.assertEqual(pool.claim('1',cfg)[1],'duplicate')
            self.assertEqual(pool.claim('extra',{'large_task_max_concurrent':35})[1],'full')
            for token in tokens:pool.release(token)

    async def test_config_concurrency_range_includes_zero_and_int32_max(self):
        import bot
        from console_config import Config,atomic
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);atomic(root/'settings.json',bot.settings())
            config=Config(root)
            for value in ('0','33','2147483647'):
                config.set('large_task_max_concurrent',value)
                self.assertEqual(config.load()['large_task_max_concurrent'],int(value))
            for value in ('-1','2147483648','1.5','true'):
                with self.assertRaises(ValueError):config.set('large_task_max_concurrent',value)
    async def test_daily_limit_is_global_durable_and_reset_by_date(self):
        with tempfile.TemporaryDirectory() as folder:
            a=LargeTaskPool(folder,Path(folder)/'a',1);b=LargeTaskPool(folder,Path(folder)/'b',2)
            cfg={'large_tasks_enabled':True,'large_task_daily_limit':1}
            token,status=a.claim('1',cfg);self.assertEqual(status,'accepted')
            self.assertEqual(a.claim('1',cfg)[1],'duplicate');a.release(token)
            self.assertEqual(b.claim('2',cfg)[1],'daily_limit')
            with closing(sqlite3.connect(a.path)) as db,db:db.execute("UPDATE admissions SET day='2000-01-01'")
            token,status=b.claim('2',cfg);self.assertEqual(status,'accepted');b.release(token)

    async def test_zero_daily_limit_is_unlimited(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=LargeTaskPool(folder,Path(folder)/'a',1)
            for i in range(6):
                token,status=pool.claim(str(i),{'large_task_daily_limit':0});self.assertEqual(status,'accepted');pool.release(token)
            with closing(sqlite3.connect(pool.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM admissions').fetchone()[0],6)

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
