"""Container-only code execution. No model-controlled host command or mount."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import zipfile
import uuid


def safe_name(name):
    path=PurePosixPath(name)
    if (not isinstance(name,str) or len(name)>180 or not name or path.is_absolute()
            or any(p in {'..','.'} for p in name.split('/')) or re.search(r'[\\:\x00-\x1f]',name)
            or any(p.endswith((' ','.')) or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',p) for p in path.parts)):
        raise ValueError('Unsafe sandbox filename')
    return name


def preview(raw):
    if b'\0' in raw[:8192]:return None
    try:value=raw.decode('utf-8-sig')
    except UnicodeError:return None
    if any(ord(c)<32 and c not in '\r\n\t' for c in value):return None
    return value


def package(files,cap):
    if not files or len(files)>64:raise ValueError('No deliverables or too many files')
    total=0;names=set()
    for name,raw in files:
        safe_name(name)
        if name.casefold() in names:raise ValueError('Duplicate output filename')
        names.add(name.casefold());total+=len(raw)
        if not raw or total>cap:raise ValueError('Task output exceeds upload limit')
    if len(files)==1:
        name,raw=files[0];return name.rsplit('/',1)[-1],raw
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in files:archive.writestr(name,raw)
    if len(buffer.getvalue())>cap:raise ValueError('ZIP exceeds upload limit')
    return 'deliverables.zip',buffer.getvalue()


# Executed INSIDE the container. Never extract a user archive on the host.
EXPORT=r'''
import os,stat,json,base64
from pathlib import Path
root=Path('/workspace/output');files=[];total=0
for folder,dirs,names in os.walk(root,followlinks=False):
 for name in dirs+names:
  p=Path(folder)/name;s=p.lstat()
  if stat.S_ISLNK(s.st_mode) or not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode)):raise ValueError('Links/devices forbidden')
  if stat.S_ISREG(s.st_mode):
   total+=s.st_size
   if total>CAP or len(files)>=64:raise ValueError('Output limit exceeded')
   raw=p.read_bytes()
   if len(raw)!=s.st_size:raise ValueError('File changed during export')
   files.append([str(p.relative_to(root)),base64.b64encode(raw).decode()])
print(json.dumps(files))
'''


def unpack(data,cap):
    import tarfile
    rows=[];total=0
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as archive:
        for item in archive:
            if item.isdir():continue
            if not item.isfile():raise ValueError('Links and special files forbidden')
            name=item.name
            while name.startswith('./'):name=name[2:]
            safe_name(name);total+=item.size
            if item.size<0 or total>cap or len(rows)>=64:raise ValueError('Sandbox export limit exceeded')
            body=archive.extractfile(item).read(item.size+1)
            if len(body)!=item.size:raise ValueError('Incomplete sandbox export')
            rows.append((name,body))
    return rows


def signal_script(signal):
    return ("import os,signal\nme=os.getpid()\nfor p in os.listdir('/proc'):\n"
        " if not p.isdigit() or int(p) in (1,me):continue\n"
        " try:\n  if os.stat('/proc/'+p).st_uid==os.getuid():os.kill(int(p),signal."+signal+")\n"
        " except (ProcessLookupError,FileNotFoundError,PermissionError):pass\n")


class Sandbox:
    def __init__(self,cfg,identity):
        import shutil
        distro=cfg.get('large_task_docker_wsl','')
        if distro and (os.name!='nt' or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',distro)):raise ValueError('Invalid WSL distribution')
        backend=cfg.get('large_task_executor','text')
        if backend not in {'docker','podman'}:raise ValueError('未配置容器运行时')
        executable=shutil.which('wsl' if distro else backend)
        if not executable:raise ValueError('任务沙箱不可用：未安装Docker；不会在宿主执行代码')
        if not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('Invalid task identity')
        self.prefix=[executable,'-d',distro,'--',backend] if distro else [executable]
        self.backend=backend
        self.identity=identity
        self.name=cfg.get('large_task_container_name','gpt-speak-container')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}',self.name):raise ValueError('Invalid container name')
        self.mode=cfg.get('large_task_container_mode','shared')
        if self.mode not in {'shared','per_task'}:raise ValueError('Invalid container mode')
        if self.mode=='per_task':self.name+='-'+str(uuid.UUID(hex=identity))
        self.label='gpt-speak.shared=true' if self.mode=='shared' else 'gpt-speak.task='+identity
        self.uid=100000+int(identity[:7],16)
        self.work='/workspace/jobs/'+identity
        self.runtime=cfg.get('large_task_sandbox_runtime','python')
        if self.runtime not in {'python','shell'}:raise ValueError('Invalid sandbox runtime')
        self.cap=int(cfg.get('large_task_max_file_bytes',20971520))
        self.image=cfg.get('large_task_sandbox_image','gpt-speak-task:1')
        if not re.fullmatch(r'[a-z0-9][a-z0-9./_:@-]{0,199}',self.image):raise ValueError('Invalid sandbox image')

    async def command(self,*args,data=b'',timeout=20,limit=65536):
        from platform_runtime import process_options
        proc=await asyncio.create_subprocess_exec(*self.prefix,*args,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,**process_options())
        async def exchange():
            async def send():
                try:proc.stdin.write(data);await proc.stdin.drain()
                except (BrokenPipeError,ConnectionResetError):pass
                finally:proc.stdin.close()
            sender=asyncio.create_task(send());out=bytearray()
            try:
                while chunk:=await proc.stdout.read(16384):
                    out.extend(chunk)
                    if len(out)>limit:raise ValueError('Sandbox command output limit exceeded')
                await sender;await proc.wait();return proc.returncode,bytes(out)
            finally:
                sender.cancel();await asyncio.gather(sender,return_exceptions=True)
        try:return await asyncio.wait_for(exchange(),timeout)
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()

    async def ensure(self):
        code,data=await self.command('info','--format','json')
        if code:raise ValueError('任务沙箱不可用：无法连接容器服务；不会在宿主执行代码')
        info=json.loads(data)
        system=info.get('OSType') or info.get('host',{}).get('os')
        if system!='linux':raise ValueError('任务沙箱需要Linux容器服务')
        init=(['--entrypoint=python',self.image,'-I','-u','-c',"import os,time;os.makedirs('/workspace/jobs',exist_ok=True);time.sleep(2147483647)"] if self.runtime=='python' else
            ['--entrypoint=/bin/sh',self.image,'-c','mkdir -p /workspace/jobs && sleep 2147483647'])
        code,_=await self.command('inspect',self.name)
        if code:
            await self.command('run','--detach','--pull=never','--name',self.name,
            '--label',self.label,'--network=none','--read-only','--cap-drop=ALL','--cap-add=CHOWN',
            '--security-opt=no-new-privileges','--user=0:0','--pids-limit=128','--memory=512m',
            '--memory-swap=512m','--cpus=1','--ulimit','fsize=25165824:25165824',
            '--tmpfs','/workspace:rw,noexec,nosuid,nodev,size=268435456,mode=755',
            '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777',
            '--workdir=/workspace',*init)
        code,data=await self.command('inspect',self.name)
        if code:raise ValueError('共享容器启动失败')
        row=json.loads(data)[0];host=row.get('HostConfig',{});config=row.get('Config',{})
        mounts=row.get('Mounts',[])
        key,value=self.label.split('=',1)
        if (config.get('Labels',{}).get(key)!=value or host.get('Privileged')
                or host.get('NetworkMode')!='none' or not host.get('ReadonlyRootfs')
                or host.get('PidMode')=='host' or host.get('IpcMode')=='host'
                or (self.backend=='docker' and not any(str(c).upper()=='ALL' for c in host.get('CapDrop',[])))
                or (self.backend=='podman' and any(str(c).upper().removeprefix('CAP_')!='CHOWN' for c in row.get('EffectiveCaps',['UNKNOWN'])))
                or any(str(c).upper().removeprefix('CAP_')!='CHOWN' for c in host.get('CapAdd',[]))
                or any('unconfined' in s for s in host.get('SecurityOpt',[]))
                or any(m.get('Type')!='tmpfs' for m in mounts)
                or not any('no-new-privileges' in s for s in host.get('SecurityOpt',[]))):
            raise ValueError('容器隔离配置不符，拒绝执行；请检查或重建容器')
        code,image=await self.command('image','inspect',self.image)
        if code or row.get('Image','').removeprefix('sha256:')!=json.loads(image)[0].get('Id','').removeprefix('sha256:'):
            raise ValueError('共享容器镜像与配置不同，请重建容器')
        if not row.get('State',{}).get('Running'):raise ValueError('共享容器未运行，请重建容器')
        status,_=await self.command('exec','--user=0',self.name,'python' if self.runtime=='python' else '/bin/sh',
            *(['-I','-c',"from pathlib import Path;assert Path('/workspace/jobs').is_dir()"] if self.runtime=='python' else ['-c','test -d /workspace/jobs']))
        if status:raise ValueError('容器解释器或工作目录损坏，请重建容器')

    async def start(self):
        await self.ensure()
        if self.runtime=='python':
            script=("import os;from pathlib import Path;p=Path("+repr(self.work)+");uid="+str(self.uid)+";assert not any(x.stat().st_uid==uid for x in p.parent.iterdir());p.mkdir(mode=0o700);os.chown(p,uid,uid)")
            code,_=await self.command('exec','--user=0',self.name,'python','-I','-c',script)
        else:
            script='test ! -e '+self.work+' && mkdir -m 700 '+self.work+' && chown '+str(self.uid)+':'+str(self.uid)+' '+self.work
            code,_=await self.command('exec','--user=0',self.name,'/bin/sh','-c',script)
        if code:raise ValueError('任务目录创建失败或已存在')
        code,_=await (self.python("from pathlib import Path;[Path(n).mkdir() for n in ('input','output','tmp')]",10)
            if self.runtime=='python' else self.shell('mkdir input output tmp',10))
        if code:raise ValueError('任务目录初始化失败')

    def user_args(self):
        return ['--user='+str(self.uid)+':'+str(self.uid),'--workdir='+self.work,'--env','TMPDIR='+self.work+'/tmp','--env','HOME='+self.work]

    async def python(self,code,timeout):
        if not isinstance(code,str) or len(code)>20000:raise ValueError('Invalid sandbox code')
        return await self.command('exec','-i',*self.user_args(),self.name,'python','-I','-u','-',data=code.encode(),timeout=timeout)

    async def shell(self,code,timeout):
        if not isinstance(code,str) or len(code)>20000:raise ValueError('Invalid sandbox code')
        return await self.command('exec','-i',*self.user_args(),self.name,'/bin/sh','-s',data=code.encode(),timeout=timeout)

    async def inputs(self,files):
        for index,(name,raw) in enumerate(files,1):
            # Filename is serialized as data, never interpolated into shell code.
            target=str(index)+'-'+safe_name(name).rsplit('/',1)[-1]
            script='import sys;from pathlib import Path;Path('+repr(self.work+'/input/'+target)+').write_bytes(sys.stdin.buffer.read())'
            if self.runtime=='python':status,_=await self.command('exec','-i',*self.user_args(),self.name,'python','-I','-c',script,data=raw)
            else:
                import shlex
                status,_=await self.command('exec','-i',*self.user_args(),self.name,'/bin/sh','-c','cat > '+shlex.quote(self.work+'/input/'+target),data=raw)
            if status:raise ValueError('Sandbox input staging failed')

    async def outputs(self):
        # Freeze all untrusted processes before taking a snapshot. The trusted
        # export process is the sole runnable process until container removal.
        freeze=(['python','-I','-c',signal_script('SIGSTOP')] if self.runtime=='python' else
            ['/bin/sh','-c','for p in /proc/[0-9]*; do n=${p##*/}; if [ "$n" != 1 ] && [ "$n" != "$$" ]; then kill -STOP "$n" 2>/dev/null || :; fi; done'])
        status,_=await self.command('exec',*self.user_args(),self.name,*freeze)
        if status:raise ValueError('Sandbox freeze failed')
        status,data=await self.command('cp',self.name+':'+self.work+'/output/.','-',limit=self.cap*2+65536)
        if status:raise ValueError('Sandbox export failed')
        return unpack(data,self.cap)

    async def close(self):
        if self.mode=='per_task':
            await self.destroy()
            return
        script=signal_script('SIGKILL')
        if self.runtime=='python':await self.command('exec',*self.user_args(),self.name,'python','-I','-c',script)
        else:await self.command('exec',*self.user_args(),self.name,'/bin/sh','-c','for p in /proc/[0-9]*; do n=${p##*/}; if [ "$n" != 1 ] && [ "$n" != "$$" ]; then kill -KILL "$n" 2>/dev/null || :; fi; done')

    async def destroy(self):
        status,raw=await self.command('inspect',self.name)
        if status:
            status,_=await self.command('info')
            if status:raise ValueError('无法连接容器服务，临时容器删除待重试')
            return
        row=json.loads(raw)[0]
        if row.get('Config',{}).get('Labels',{}).get('gpt-speak.task')!=self.identity:
            raise ValueError('临时容器归属不符，拒绝删除')
        status,_=await self.command('rm','--force',self.name)
        if status:raise ValueError('临时容器删除失败')


async def probe(cfg):
    try:
        box=Sandbox(cfg,'0'*32)
        status,raw=await box.command('info','--format','json',timeout=8)
        if status:raise ValueError('无法连接容器服务')
        info=json.loads(raw);system=info.get('OSType') or info.get('host',{}).get('os')
        if system!='linux':raise ValueError('需要Linux容器服务')
        status,_=await box.command('image','inspect',box.image,timeout=8)
        return {'ok':True,'ready':not status,'message':'连接成功；'+('本地镜像可用，任务启动时校验解释器' if not status else '本地镜像未找到，请先拉取或构建镜像')}
    except (ValueError,OSError,TimeoutError) as exc:
        return {'ok':False,'ready':False,'message':str(exc)[:200]}


def remove(cfg,identity):
    """Expiry removes a single job directory, never the shared container."""
    async def clean():
        box=Sandbox(cfg,identity)
        if box.mode=='per_task':
            await box.destroy()
            return
        status,raw=await box.command('inspect',box.name)
        if status:
            status,_=await box.command('info')
            if status:raise ValueError('Container runtime unavailable')
            return
        row=json.loads(raw)[0]
        if row.get('Config',{}).get('Labels',{}).get('gpt-speak.shared')!='true':raise ValueError('Container ownership mismatch')
        if not row.get('State',{}).get('Running'):return
        await box.close()
        if box.runtime=='python':
            code,exists=await box.command('exec','--user=0',box.name,'python','-I','-c',"from pathlib import Path;print(int(Path("+repr(box.work)+").exists()))")
            if code:raise ValueError('Sandbox path check failed')
            if exists.strip()==b'0':return
            code,_=await box.python("import shutil;from pathlib import Path;p=Path("+repr(box.work)+");[(shutil.rmtree(x) if x.is_dir() and not x.is_symlink() else x.unlink()) for x in p.iterdir()] if p.exists() else None",15)
            if code:raise ValueError('Sandbox directory cleanup failed')
            script="from pathlib import Path;p=Path("+repr(box.work)+");p.rmdir() if p.exists() else None"
            code,_=await box.command('exec','--user=0',box.name,'python','-I','-c',script)
        else:
            code,_=await box.shell('rm -rf '+box.work+'/* '+box.work+'/.[!.]* '+box.work+'/..?*',15)
            if code:raise ValueError('Sandbox directory cleanup failed')
            code,_=await box.command('exec','--user=0',box.name,'/bin/sh','-c','test ! -d '+box.work+' || rmdir '+box.work)
        if code:raise ValueError('Sandbox directory removal failed')
    asyncio.run(clean())


async def reinstall(cfg):
    box=Sandbox(cfg,'0'*32)
    if box.mode=='per_task':
        try:await box.start()
        finally:await box.close()
        return {'ok':True,'message':'独立容器重建验证通过，检测容器已删除；后续任务均创建新容器'}
    status,raw=await box.command('inspect',box.name)
    if not status:
        row=json.loads(raw)[0]
        if row.get('Config',{}).get('Labels',{}).get('gpt-speak.shared')!='true':raise ValueError('同名容器不属于GPT Speak，不会覆盖')
        status,_=await box.command('rm','--force',box.name)
        if status:raise ValueError('Container rebuild failed')
    await box.ensure()
    return {'ok':True,'message':'共享容器已重建；原沙箱目录已清空，原任务不会重放'}


async def health(cfg):
    box=Sandbox(cfg,uuid.uuid4().hex)
    try:await box.start() if box.mode=='per_task' else await box.ensure()
    finally:
        if box.mode=='per_task':await box.close()
    return {'ok':True,'message':'容器健康：隔离配置、解释器和工作目录正常'}


async def build_image(cfg):
    """Build only operator-supplied Dockerfile; never send host context or secrets."""
    import tarfile
    box=Sandbox(cfg,uuid.uuid4().hex)
    dockerfile=cfg.get('large_task_dockerfile','')
    if not isinstance(dockerfile,str) or not dockerfile.strip() or len(dockerfile)>20000 or '\0' in dockerfile:
        raise ValueError('Dockerfile须为1～20000字文本')
    buffer=io.BytesIO();body=dockerfile.encode()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        item=tarfile.TarInfo('Dockerfile');item.size=len(body);archive.addfile(item,io.BytesIO(body))
    status,raw=await box.command('build','--pull=false','--tag',box.image,'-',data=buffer.getvalue(),timeout=600,limit=2*1024*1024)
    if status:raise ValueError('镜像构建失败：'+raw.decode('utf-8',errors='replace')[-600:])
    return {'ok':True,'message':'镜像已构建：'+box.image+'；新任务使用此镜像，共享模式需重建容器'}
