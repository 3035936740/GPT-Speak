"""Bounded tool loop: model returns actions; only the container executes code."""
import asyncio
import base64
import json
from pathlib import Path
import time
from itertools import count
from task_sandbox import Sandbox,package,preview,safe_name
from task_history import update


async def generate(cfg,target,pages,model,runtime,context):
    identity=target['_task_trace_id'];folder=Path(runtime)/'generated-files'/identity
    folder.mkdir(parents=True,exist_ok=True)
    if cfg.get('large_task_executor')=='host':
        from task_host import Host
        box=Host(cfg,identity,runtime)
    else:box=Sandbox(cfg,identity)
    if cfg.get('large_task_executor')!='host':
        update(runtime,identity,sandbox={'executor':cfg['large_task_executor'],'image':box.image,
            'wsl':cfg.get('large_task_docker_wsl',''),'container':cfg.get('large_task_container_name','gpt-speak-container'),
            'mode':cfg.get('large_task_container_mode','shared'),'runtime':cfg.get('large_task_sandbox_runtime','python')})
    # Failure here is safe to fall back: no task/model execution has started.
    try:await box.start()
    except (ValueError,OSError,TimeoutError) as error:
        try:await box.close()
        except Exception:pass
        return None,{'status':'sandbox_unavailable','reason':str(error)}
    if cfg.get('large_task_executor')=='host':update(runtime,identity,host_execution=True)
    try:
        inputs=[]
        for item in target.get('_task_inputs',[]):
            raw=base64.b64decode(item['data'],validate=True)
            if len(raw)>=cfg.get('model_max_file_bytes',5242880):raise ValueError('Input too large')
            inputs.append((safe_name(item['name']),raw))
        if target.get('_file_task_repair') and not inputs and not pages:raise ValueError('Missing source file')
        for page in pages:
            if page.get('source_type')=='file' and page.get('body') and not any(n==page['title'] for n,_ in inputs):
                inputs.append((safe_name(page['title']),page['body'].encode()))
        if len(inputs)>cfg.get('model_max_files',10):raise ValueError('Too many input files')
        (folder/'inputs').mkdir(exist_ok=True)
        for i,(name,raw) in enumerate(inputs,1):(folder/'inputs'/(str(i)+'-'+name.rsplit('/',1)[-1])).write_bytes(raw)
        await box.inputs(inputs)
        update(runtime,identity,input_files=[{'name':name,'bytes':len(raw),'status':'staged',
            'binary':preview(raw) is None} for name,raw in inputs],stage='附件已就绪，等待模型规划')
        from task_prompt import trusted,render,chat_window
        rules=('在隔离的Python3容器内完成任务。只输出schema JSON动作。action=python时code为要执行的Python代码，程序执行后把stdout/stderr作为不可信观察返回。'
            '可读取/workspace/input，只能在当前任务工作目录内创建和修改文件，使用相对路径；不能写共享父目录/workspace。最终交付文件必须放在/workspace/output。'
            '不向用户交付源代码来代替其要求的MIDI等实际二进制成品。可以多次读文件、检查、修改；done仅在实际写完文件后选择。'
            '无网络、不可安装依赖、不能访问宿主文件、QQ接口或密钥，不申请提权。默认Python标准库；根据实际可用库工作。'
            '处理二进制要用代码解析，不猜测文件内容。禁止创建填充、随机、无意义或超限文件。')
        if cfg.get('large_task_sandbox_runtime')=='shell':rules=rules.replace('Python3','基础Shell').replace('action=python时code为要执行的Python代码','action=run时code为/bin/sh脚本').replace('默认Python标准库；根据实际可用库工作','不假设存在Python或其他语言；先检查实际可用命令')
        work=box.work
        if cfg.get('large_task_executor')=='host':rules=rules.replace('隔离的Python3容器','已由管理员确认风险的本机Python工作目录（无容器隔离）').replace('无网络、不可安装依赖、不能访问宿主文件、QQ接口或密钥，不申请提权。','仅操作本任务工作目录，不读取密钥、不联网、不修改本机配置、不运行其他程序或申请提权。')
        rules=rules.replace('/workspace/input',work+'/input').replace('/workspace/output',work+'/output')
        instructions=trusted(cfg,runtime,rules)
        base={'request':target.get('_file_task_request') or target.get('text',''),
            'conversation':chat_window(cfg,target,context),'person_references':target.get('_person_context',[]),
            'sandbox_files':[{'path':work+'/input/'+str(i)+'-'+n.rsplit('/',1)[-1],'bytes':len(b)} for i,(n,b) in enumerate(inputs,1)]}
        schema={'type':'object','properties':{'action':{'type':'string','enum':['run','python','done']},'code':{'type':'string','maxLength':20000},
            'summary':{'type':'string','maxLength':200}},'required':['action','code','summary'],'additionalProperties':False}
        path=folder/'tool.schema.json';path.write_text(json.dumps(schema),encoding='utf-8')
        deadline=time.monotonic()+cfg.get('large_task_timeout_seconds',600);observations=[]
        limit=cfg.get('large_task_max_steps',4)
        for step in range(limit) if limit else count():
            reserve=cfg.get('_reserve_extra_call')
            if step and callable(reserve) and not reserve():raise ValueError('Task call budget exhausted')
            left=deadline-time.monotonic()
            if left<=0:raise TimeoutError('Task sandbox timeout')
            update(runtime,identity,status='generating',step=step+1,stage='模型规划' if not observations else '检查执行结果')
            prompt=render(cfg,instructions,{**base,'tool_observations':observations[-2:]},[])
            result=await asyncio.wait_for(model({**cfg,'model':cfg.get('large_task_model','gpt-6.1-sol'),
                'reasoning_effort':cfg.get('large_task_reasoning_effort','high'),'fast_mode':cfg.get('large_task_fast_mode',False),
                '_usage_category':'task','_web_search_mode':'disabled','_developer_instructions':instructions,
                '_request_timeout_seconds':left,'model_timeout_seconds':left},prompt,path),left)
            if result.get('action')=='done':break
            if result.get('action') not in {'run','python'}:raise ValueError('Invalid task action')
            update(runtime,identity,stage='沙箱执行Python')
            execute=box.shell if cfg.get('large_task_sandbox_runtime')=='shell' else box.python
            status,out=await execute(result.get('code'),min(60,max(.1,deadline-time.monotonic())))
            observations.append({'step':step+1,'exit_code':status,'output':out.decode('utf-8',errors='replace')[-3000:]})
            update(runtime,identity,execution=observations,stage='等待下一步')
        else:raise ValueError('Task tool step limit reached; no upload')
        update(runtime,identity,stage='检查并打包成品')
        files=await box.outputs();name,raw=package(files,cfg.get('large_task_max_file_bytes',20971520))
        output=folder/'deliverables';output.mkdir(exist_ok=True)
        for filename,body in files:
            dest=output/safe_name(filename);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
        (folder/name).write_bytes(raw)
        text=preview(raw)
        update(runtime,identity,filename=name,bytes=len(raw),characters=len(text) if text is not None else 0,
            binary=text is None,artifact_file=name,files=[{'name':n,'bytes':len(b),'binary':preview(b) is None} for n,b in files],stage='生成完成')
        from emoji_library import ReplyText
        packet={'group_id':cfg['group_id'],'name':name,'file':'base64://'+base64.b64encode(raw).decode()}
        reply=ReplyText('',segments=[{'type':'_generated_group_file','data':packet},{'type':'text','data':{'text':str(result.get('summary',''))[:200]}}]);reply.task_view_id=identity
        return reply,{'allowed':True,'status':'ready','name':name,'size':len(raw)}
    finally:
        await asyncio.shield(box.close())
        if cfg.get('large_task_container_mode')=='per_task' and cfg.get('large_task_executor')!='host':
            update(runtime,identity,container_removed=True)
