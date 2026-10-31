"""Own the private worker's process lifetime without touching group workers."""
import os
from pathlib import Path
import subprocess
import sys
import time

from platform_runtime import process_options
from worker_instance import instance_locked


class PrivateSupervisor:
    def __init__(self, root, job_factory):
        self.folder = Path(root) / 'runtime-private'
        self.root, self.job_factory = Path(root), job_factory
        self.process = self.stream = self.job = None
        self.retry_at = 0

    def sync(self, cfg):
        if not cfg['enabled'] or not cfg.get('private_reply_enabled', False):
            self.stop()
            return
        if self.process and self.process.poll() is not None:
            self.stop()
            self.retry_at = time.monotonic() + 10
        if self.process or time.monotonic() < self.retry_at:
            return
        if instance_locked(self.folder):
            return
        self.folder.mkdir(exist_ok=True)
        (self.folder / 'STOP').unlink(missing_ok=True)
        self.stream = (self.folder / 'console-worker.log').open('ab', buffering=0)
        self.process = subprocess.Popen([sys.executable, '-X', 'utf8', str(self.root / 'private_worker.py')],
            cwd=self.root, stdin=subprocess.DEVNULL, stdout=self.stream, stderr=self.stream,
            **process_options(), env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        self.job = self.job_factory()
        try:
            self.job.add(self.process)
        except Exception:
            self.process.terminate()
            self.stream.close()
            self.job.close()
            self.process = self.stream = self.job = None
            raise

    def stop(self):
        if self.process:
            (self.folder / 'STOP').touch()
            try:
                self.process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        if self.job:
            self.job.close()
        if self.stream:
            self.stream.close()
        self.process = self.stream = self.job = None
