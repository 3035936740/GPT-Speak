"""Durable per-task cancellation, scoped to one runtime and trace ID."""
import asyncio
from pathlib import Path
import re
import time
from console_config import atomic


def path(folder,identity):
    if not re.fullmatch(r'[a-f0-9]{32}',str(identity)):raise ValueError('无效任务编号')
    return Path(folder)/'task-cancel'/(identity+'.json')


def requested(folder,identity):return path(folder,identity).exists()


def request(folder,identity):
    atomic_path=path(folder,identity);atomic_path.parent.mkdir(parents=True,exist_ok=True)
    atomic(atomic_path,{'requested':time.time()})


async def guarded(awaitable,folder,identity):
    job=asyncio.create_task(awaitable)
    async def watch():
        while not requested(folder,identity):await asyncio.sleep(.2)
    watcher=asyncio.create_task(watch())
    try:
        await asyncio.wait([job,watcher],return_when=asyncio.FIRST_COMPLETED)
        if requested(folder,identity):
            job.cancel();await asyncio.gather(job,return_exceptions=True)
            raise asyncio.CancelledError('operator cancelled task')
        return await job
    finally:
        for task in (job,watcher):
            if not task.done():task.cancel()
        await asyncio.gather(job,watcher,return_exceptions=True)
