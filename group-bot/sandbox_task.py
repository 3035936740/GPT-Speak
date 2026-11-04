"""Bounded tool loop: model returns actions; only the container executes code."""
import asyncio
import base64
import json
from pathlib import Path
import time
from itertools import count
from task_sandbox import Sandbox,package,preview,safe_name
from task_history import update
from task_file_metrics import describe


async def generate(cfg,target,pages,model,runtime,context):
    from task_prompt import budget
    cfg={**cfg,'max_prompt_chars':budget(cfg)}
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
        state=target.get('_resume_state',{})
        if target.get('_resume_files'):
            await box.restore(target['_resume_files'])
        update(runtime,identity,input_files=[{**describe(name,raw),'status':'staged'} for name,raw in inputs],stage='附件已就绪，等待模型规划')
        from task_prompt import trusted,render,chat_window
        from task_execution_state import Progress
        progress=Progress(state.get('task_state'),cfg.get('large_task_stall_limit',6))
        if progress.state.get('last_action',{}).get('code_path'):
            progress.state['last_action']['code_path']=box.work+'/.task-actions/last.'+('sh' if cfg.get('large_task_sandbox_runtime')=='shell' else 'py')
        inventory=await box.inventory() if hasattr(box,'inventory') else None
        if inventory is not None:progress.baseline(inventory)
        rules=('在隔离的Python3容器内完成任务。只输出schema JSON动作。action=python时code为要执行的Python代码，程序执行后把stdout/stderr作为不可信观察返回。'
            '可读取/workspace/input，只能在当前任务工作目录内创建和修改文件，使用相对路径；不能写共享父目录/workspace。最终交付文件必须放在/workspace/output。'
            '不向用户交付源代码来代替其要求的MIDI等实际二进制成品。可以多次读文件、检查、修改；done仅在实际写完文件后选择。'
            '无网络、不可安装依赖、不能访问宿主文件、QQ接口或密钥，不申请提权。默认Python标准库；根据实际可用库工作。'
            '处理二进制要用代码解析，不猜测文件内容。禁止创建填充、随机、无意义或超限文件。')
        rules+='先形成简短计划，持续更新state中的plan、completed、phase和next_step。已有解析结果直接复用；stdout只打印简短结论，不反复打印全部音符。last_action是前一步实际执行的代码和结果，不要遗忘后重做同一分析。交付目录没有实际变化时不把分析文件改名当成任务进展。'
        rules+='对MIDI编排、格式转换等明确任务，优先action=run_and_finish：code为一个完整脚本，在同一步读取输入、完成修改、写入output并重新读取校验成品；校验失败必须抛错或非零退出。程序仅在脚本成功且交付目录存在非空文件时直接打包，不再调用模型确认done。不要只解析或打印音符，交付目标文件。确需观察再决策时才分步python/run，完成后done。run_and_finish的代码语言与当前执行环境一致。'
        if cfg.get('large_task_sandbox_runtime')=='shell':rules=rules.replace('Python3','基础Shell').replace('action=python时code为要执行的Python代码','action=run时code为/bin/sh脚本').replace('默认Python标准库；根据实际可用库工作','不假设存在Python或其他语言；先检查实际可用命令')
        work=box.work
        if cfg.get('large_task_executor')=='host':rules=rules.replace('隔离的Python3容器','已由管理员确认风险的本机Python工作目录（无容器隔离）').replace('无网络、不可安装依赖、不能访问宿主文件、QQ接口或密钥，不申请提权。','仅操作本任务工作目录，不读取密钥、不联网、不修改本机配置、不运行其他程序或申请提权。')
        rules=rules.replace('/workspace/input',work+'/input').replace('/workspace/output',work+'/output')
        instructions=trusted(cfg,runtime,rules)
        base={'request':target.get('_file_task_request') or target.get('text',''),
            'conversation':chat_window(cfg,target,context),'person_references':target.get('_person_context',[]),
            'sandbox_files':[{'path':work+'/input/'+str(i)+'-'+n.rsplit('/',1)[-1],'bytes':len(b)} for i,(n,b) in enumerate(inputs,1)]}
        if state.get('base'):
            old=state['base'];base.update(conversation=old.get('conversation',[]),person_references=old.get('person_references',[]))
            base['request']=old.get('request',base['request'])
        if target.get('_task_research') or state.get('base',{}).get('web_references'):
            base['web_references'] = target.get('_task_research') or state['base']['web_references']
        if target.get('_resume_files'):
            base['sandbox_files'].extend({'path':work+'/'+name,'bytes':len(raw),'restored':True} for name,raw in target['_resume_files'])
        schema={'type':'object','properties':{'action':{'type':'string','enum':['run','python','run_and_finish','done']},'code':{'type':'string','maxLength':20000},
            'summary':{'type':'string','maxLength':200},'state':{'type':'object','properties':{
                'plan':{'type':'array','maxItems':8,'items':{'type':'string','maxLength':120}},
                'completed':{'type':'array','maxItems':8,'items':{'type':'string','maxLength':120}},
                'phase':{'type':'string','maxLength':100},'next_step':{'type':'string','maxLength':200}},'required':['plan','completed','phase','next_step'],'additionalProperties':False}},'required':['action','code','summary','state'],'additionalProperties':False}
        path=folder/'tool.schema.json';path.write_text(json.dumps(schema),encoding='utf-8')
        deadline=time.monotonic()+cfg.get('large_task_timeout_seconds',600);observations=state.get('observations',[])[-2:]
        from console_config import atomic
        def checkpoint():atomic(folder/'resume.json',{'base':base,'observations':observations[-2:],'task_state':progress.state})
        checkpoint()
        limit=cfg.get('large_task_max_steps',4)
        model_calls=[]
        for step in range(limit) if limit else count():
            reserve=cfg.get('_reserve_extra_call')
            if step and callable(reserve) and not reserve():raise ValueError('Task call budget exhausted')
            left=deadline-time.monotonic()
            if left<=0:raise TimeoutError('Task sandbox timeout')
            update(runtime,identity,status='generating',step=step+1,stage='模型规划' if not observations else '检查执行结果')
            prompt=render(cfg,instructions,{**base,'tool_observations':observations[-2:],'task_state':progress.state},[])
            call={'step':step+1}
            model_calls.append(call)
            def capture_usage(usage):
                if usage is not None:call['usage']=usage
                update(runtime,identity,model_calls=model_calls)
            result=await asyncio.wait_for(model({**cfg,'model':cfg.get('large_task_model','gpt-6.1-sol'),
                'reasoning_effort':cfg.get('large_task_reasoning_effort','high'),'fast_mode':cfg.get('large_task_fast_mode',False),
                '_usage_category':'task','_web_search_mode':'disabled','_developer_instructions':instructions,
                '_task_identity':identity,'_task_usage_sink':capture_usage,
                '_request_timeout_seconds':left,'model_timeout_seconds':left},prompt,path),left)
            call['action']=result.get('action');update(runtime,identity,model_calls=model_calls)
            if result.get('action')=='done':break
            if result.get('action') not in {'run','python','run_and_finish'}:raise ValueError('Invalid task action')
            update(runtime,identity,stage='沙箱执行Python')
            execute=box.shell if cfg.get('large_task_sandbox_runtime')=='shell' else box.python
            action_path='.task-actions/last.'+('sh' if cfg.get('large_task_sandbox_runtime')=='shell' else 'py')
            if hasattr(box,'restore'):await box.restore([(action_path,str(result.get('code','')).encode())])
            status,out=await execute(result.get('code'),min(60,max(.1,deadline-time.monotonic())))
            text=out.decode('utf-8',errors='replace')
            clipped=len(text)>3000
            observations.append({'step':step+1,'exit_code':status,'output':text[:800]+'\n[输出已截断]\n'+text[-2200:] if clipped else text,
                'truncated':clipped,'original_output_chars':len(text),'original_output_bytes':len(out),
                'summary':result.get('summary',''),'usage':call.get('usage')})
            inventory=await box.inventory() if hasattr(box,'inventory') else None
            observations[-1]['files']=inventory
            finish=result.get('action')=='run_and_finish'
            complete=finish and status==0 and bool(inventory) and all(f.get('bytes',0)>0 for f in inventory)
            if finish and not complete:observations[-1]['completion_issue']='未通过交付检查：需要脚本成功且output中存在非空成品。修正脚本并写出文件，不能只分析输入。'
            stalled=progress.observe(result,status,text,inventory,work+'/'+action_path)
            checkpoint()
            update(runtime,identity,execution=observations,task_state={k:v for k,v in progress.state.items() if k!='last_action'},stage='纠正重复分析' if progress.state.get('correction') else '等待下一步')
            if complete:break
            if stalled:raise ValueError(stalled)
        else:raise ValueError('Task tool step limit reached; no upload')
        update(runtime,identity,stage='检查并打包成品')
        files=await box.outputs();name,raw=package(files,cfg.get('large_task_max_file_bytes',20971520))
        output=folder/'deliverables';output.mkdir(exist_ok=True)
        for filename,body in files:
            dest=output/safe_name(filename);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
        (folder/name).write_bytes(raw)
        text=preview(raw)
        update(runtime,identity,filename=name,bytes=len(raw),characters=len(text) if text is not None else 0,
            binary=text is None,artifact_file=name,files=[describe(n,b) for n,b in files],stage='生成完成')
        from emoji_library import ReplyText
        packet={'group_id':cfg['group_id'],'name':name,'file':'base64://'+base64.b64encode(raw).decode()}
        reply=ReplyText('',segments=[{'type':'_generated_group_file','data':packet},{'type':'text','data':{'text':str(result.get('summary',''))[:200]}}]);reply.task_view_id=identity
        return reply,{'allowed':True,'status':'ready','name':name,'size':len(raw)}
    except BaseException:
        # Export drafts and intermediates before a per-task container is deleted.
        try:
            files=await asyncio.shield(box.outputs())
            from task_controls import save_partial
            save_partial(runtime,identity,files,cfg)
            if hasattr(box,'snapshot'):
                rows=await asyncio.shield(box.snapshot())
                saved=folder/'workspace-snapshot';saved.mkdir(exist_ok=True)
                for name,body in rows:
                    if name.startswith(('input/','tmp/')):continue
                    path=saved/safe_name(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
        except Exception as error:
            update(runtime,identity,snapshot_error=type(error).__name__)
        raise
    finally:
        await asyncio.shield(box.close())
        if cfg.get('large_task_container_mode')=='per_task' and cfg.get('large_task_executor')!='host':
            update(runtime,identity,container_removed=True)
