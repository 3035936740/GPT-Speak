"""Explicitly acknowledged UNSAFE local Python execution; never a fallback."""
import asyncio,os,sys
from pathlib import Path
from task_sandbox import safe_name


class Host:
    image='本机Python（危险：无容器隔离）'
    name='host'
    def __init__(self,cfg,identity,runtime):
        if cfg.get('large_task_executor')!='host' or cfg.get('large_task_host_acknowledged') is not True:raise ValueError('本机执行尚未确认风险')
        if cfg.get('large_task_sandbox_runtime','python')!='python':raise ValueError('本机执行只支持Python模式')
        self.root=Path(runtime).resolve()/'generated-files'/identity/'host-workspace'
        self.work=self.root.as_posix();self.cap=cfg.get('large_task_max_file_bytes',20971520)
    async def start(self):
        for name in ('input','output','tmp'):(self.root/name).mkdir(parents=True,exist_ok=False)
    async def inputs(self,files):
        for i,(name,raw) in enumerate(files,1):(self.root/'input'/(str(i)+'-'+safe_name(name).rsplit('/',1)[-1])).write_bytes(raw)
    async def python(self,code,timeout):
        if not isinstance(code,str) or len(code)>20000:raise ValueError('Invalid task code')
        from platform_runtime import process_options
        env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','LANG','LC_ALL'}}
        env.update(TEMP=str(self.root/'tmp'),TMP=str(self.root/'tmp'),TMPDIR=str(self.root/'tmp'),PYTHONIOENCODING='utf-8')
        proc=await asyncio.create_subprocess_exec(sys.executable,'-I','-u','-',cwd=self.root,env=env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,**process_options())
        async def run():
            proc.stdin.write(code.encode());await proc.stdin.drain();proc.stdin.close();out=bytearray()
            while block:=await proc.stdout.read(8192):
                out.extend(block)
                if len(out)>65536:raise ValueError('Task log limit exceeded')
            await proc.wait();return proc.returncode,bytes(out)
        try:return await asyncio.wait_for(run(),timeout)
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()
    async def outputs(self):
        rows=[];total=0
        for p in (self.root/'output').rglob('*'):
            if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:raise ValueError('Links forbidden')
            if p.is_dir():continue
            total+=p.stat().st_size
            if total>self.cap or len(rows)>=64:raise ValueError('Output limit exceeded')
            rows.append((safe_name(p.relative_to(self.root/'output').as_posix()),p.read_bytes()))
        return rows
    async def restore(self,files):
        for name,raw in files:
            path=self.root/safe_name(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    async def snapshot(self):
        from task_controls import read_files
        return read_files(self.root,self.cap)
    async def inventory(self):
        from task_file_metrics import describe
        return [describe(name,raw,hashed=True) for name,raw in await self.outputs()]
    async def close(self):pass
