"""Opt-in background jobs with atomic, cross-process global admission; no waiting queue."""
import asyncio
import logging
from pathlib import Path
import os
import sqlite3
import time
import uuid
from datetime import datetime
from contextlib import closing
from console_config import pid_alive,atomic

log=logging.getLogger('qq-group-bot')


class LargeTaskPool:
    def __init__(self,root,folder,group):
        self.root=Path(root);self.folder=Path(folder);self.group=str(group)
        self.path=self.root/'runtime-large-tasks.sqlite3';self.jobs={};self.closed=False
        self.root.mkdir(parents=True,exist_ok=True)
        self.folder.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, scope TEXT, message_id TEXT, pid INTEGER, stamp REAL, ttl REAL, UNIQUE(scope,message_id))')
            db.execute('CREATE TABLE IF NOT EXISTS admissions(id TEXT PRIMARY KEY,day TEXT,stamp REAL)')

    def connect(self):return sqlite3.connect(self.path,timeout=5)

    def claim(self,identity,cfg):
        token=uuid.uuid4().hex;now=time.time()
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            for key,pid,stamp,ttl in db.execute('SELECT id,pid,stamp,ttl FROM jobs').fetchall():
                if now-stamp>ttl or not pid_alive(pid):db.execute('DELETE FROM jobs WHERE id=?',(key,))
            if db.execute('SELECT 1 FROM jobs WHERE scope=? AND message_id=?',(self.group,str(identity))).fetchone():return None,'duplicate'
            if db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=cfg.get('large_task_max_concurrent',3):return None,'full'
            day=datetime.now().strftime('%Y-%m-%d');limit=cfg.get('large_task_daily_limit',0)
            used=db.execute('SELECT count(*) FROM admissions WHERE day=?',(day,)).fetchone()[0]
            if limit and used>=limit:return None,'daily_limit'
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(token,self.group,str(identity),os.getpid(),now,
                max(120,cfg.get('large_task_timeout_seconds',600)+2*cfg.get('napcat_timeout_seconds',30)+120)))
            db.execute('INSERT INTO admissions VALUES (?,?,?)',(token,day,now))
            db.execute('DELETE FROM admissions WHERE stamp<?',(now-32*86400,))
        return token,'accepted'

    def release(self,token):
        with closing(self.connect()) as db,db:db.execute('DELETE FROM jobs WHERE id=?',(token,))

    def snapshot(self,cfg):
        with closing(self.connect()) as db:
            total=db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        atomic(self.folder/'large-tasks.json',{'running':len(self.jobs),'global_running':total,
            'limit':cfg.get('large_task_max_concurrent',3),'waiting':0,'updated':time.time()})

    def submit(self,identity,cfg,work,active=lambda:True):
        if self.closed or not cfg.get('large_tasks_enabled') or not active():return 'disabled'
        token,status=self.claim(identity,cfg)
        if not token:return status
        async def runner():
            operation=asyncio.create_task(work())
            async def watch():
                while active() and not self.closed:await asyncio.sleep(.2)
            guard=asyncio.create_task(watch())
            try:
                done,_=await asyncio.wait([operation,guard],return_when=asyncio.FIRST_COMPLETED)
                if operation in done:await operation
                else:operation.cancel();await asyncio.gather(operation,return_exceptions=True)
            except asyncio.CancelledError:raise
            except Exception as error:
                from debug_events import safe_log
                log.warning('Background file task failed; message_id=%s; error_type=%s; reason=%s',identity,type(error).__name__,safe_log(str(error))[:200])
            finally:
                for task in (operation,guard):
                    if not task.done():task.cancel()
                await asyncio.gather(operation,guard,return_exceptions=True)
                self.release(token);self.jobs.pop(token,None);self.snapshot(cfg)
        self.jobs[token]=asyncio.create_task(runner());self.snapshot(cfg)
        return status

    async def cancel(self):
        tokens=list(self.jobs);tasks=list(self.jobs.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        for token in tokens:self.release(token);self.jobs.pop(token,None)

    async def close(self):
        self.closed=True;await self.cancel()


def daily_usage(root):
    path=Path(root)/'runtime-large-tasks.sqlite3'
    if not path.exists():return 0
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='admissions'").fetchone():return 0
        return db.execute('SELECT count(*) FROM admissions WHERE day=?',(datetime.now().strftime('%Y-%m-%d'),)).fetchone()[0]
