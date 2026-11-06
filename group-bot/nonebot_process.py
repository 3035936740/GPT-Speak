"""Console-owned NoneBot gateway shared by group and private workers."""
import os
import subprocess
import sys
import time


class GatewayProcess:
    def __init__(self, root, job_factory):
        self.root = root
        self.job_factory = job_factory
        self.process = self.stream = self.job = None
        self.key = None
        self.retry_at = 0

    def sync(self, cfg):
        from nonebot_credentials import ensure_token
        ensure_token(self.root, cfg)
        key = tuple(cfg[k] for k in ('nonebot_listen_host', 'nonebot_port',
                                     'nonebot_access_token', 'expected_self_id', 'nonebot_api_timeout_seconds'))
        if self.process and self.key == key and self.process.poll() is None:
            return
        if self.process and self.process.poll() is not None and self.key == key:
            self.close()
            self.retry_at = time.monotonic() + 10
        if time.monotonic() < self.retry_at:
            return
        self.close()
        self.stream = (self.root / 'nonebot-gateway.log').open('ab', buffering=0)
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        self.process = subprocess.Popen([sys.executable, '-X', 'utf8',
            str(self.root / 'nonebot_gateway.py'), '--root', str(self.root)],
            cwd=self.root, stdin=subprocess.DEVNULL, stdout=self.stream, stderr=self.stream,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, **options)
        self.job = self.job_factory()
        try:
            self.job.add(self.process)
        except BaseException:
            self.close()
            raise
        self.key = key

    def restart(self, cfg):
        self.close()
        self.retry_at = 0
        self.sync(cfg)

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.job:
            self.job.close()
        if self.stream:
            self.stream.close()
        self.process = self.job = self.stream = None
