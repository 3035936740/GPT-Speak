"""Local task progress and partial artifacts; never execute generated files."""
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from console_config import atomic,read_json,pid_alive

TERMINAL={'uploaded','failed','cancelled','rejected','upload_uncertain','interrupted','local_ready'}

def update(runtime,identity,**values):
    if not re.fullmatch(r'[a-f0-9]{32}',str(identity)):raise ValueError('无效任务编号')
    path=Path(runtime)/'task-history'/(identity+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    row=read_json(path,{})
    row.update(values,id=identity,updated=time.time())
    if row.get('status') in TERMINAL:row.setdefault('finished',time.time())
    atomic(path,row);return row

def begin(runtime,target,cfg):
    identity=uuid.uuid4().hex
    update(runtime,identity,status='preparing',pid=os.getpid(),created=time.time(),message_id=str(target.get('_id','')),
        sender=str(target.get('sender',''))[:80],requester_qq=str(target.get('_uid','')),request=target.get('_file_task_request') or target.get('text',''),
        model=cfg.get('large_task_model'),step=0,characters=0)
    return identity

def cleanup(runtime,cfg):
    runtime=Path(runtime);hours=cfg.get('large_task_retention_hours',2)
    for path in (runtime/'task-history').glob('*.json'):
        row=read_json(path,{})
        if not row or not re.fullmatch(r'[a-f0-9]{32}',path.stem):continue
        if row.get('status') not in TERMINAL and not pid_alive(row.get('pid')):
            row=update(runtime,path.stem,status='interrupted',error='任务进程已退出，未自动重放')
        sandbox=row.get('sandbox',{})
        if sandbox.get('mode')=='per_task' and row.get('status') in TERMINAL and not row.get('container_removed'):
            from task_sandbox import remove
            try:
                remove({'large_task_executor':sandbox['executor'],'large_task_docker_wsl':sandbox.get('wsl',''),
                    'large_task_sandbox_image':sandbox['image'],'large_task_container_name':sandbox['container'],
                    'large_task_container_mode':'per_task','large_task_sandbox_runtime':sandbox.get('runtime','python')},path.stem)
                row=update(runtime,path.stem,container_removed=True)
            except Exception:continue
        if hours and row.get('status') in TERMINAL and time.time()-row.get('finished',row['updated'])>=hours*3600:
            if row.get('sandbox') and not row.get('container_removed'):
                from task_sandbox import remove
                sandbox=row['sandbox']
                try:remove({'large_task_executor':sandbox['executor'],'large_task_docker_wsl':sandbox.get('wsl',''),
                    'large_task_sandbox_image':sandbox['image'],'large_task_container_name':sandbox.get('container','gpt-speak-container'),
                    'large_task_sandbox_runtime':sandbox.get('runtime','python')},path.stem)
                except Exception:continue
            folder=(runtime/'generated-files'/path.stem).resolve();base=(runtime/'generated-files').resolve()
            if folder.parent==base and folder.is_dir():shutil.rmtree(folder)
            path.unlink(missing_ok=True)
            (runtime/'task-cancel'/(path.stem+'.json')).unlink(missing_ok=True)

def view(root,group,identity='',scope='group'):
    from console_config import Config,runtime
    cfg=Config(root,int(group)).load()
    if scope not in {'group','all'}:raise ValueError('Invalid task scope')
    if scope=='all':
        if identity:raise ValueError('Task detail requires its source group')
        data={'enabled':False,'rows':[],'daily_used':__import__('large_task_pool').daily_usage(root),'daily_limit':cfg.get('large_task_daily_limit',0)}
        for gid in Config(root).load()['chat_group_whitelist']:
            result=view(root,gid)
            data['enabled']=data['enabled'] or result['enabled'];data['rows'].extend(result['rows'])
        data['rows'].sort(key=lambda r:r.get('created',0),reverse=True);return data
    if identity and not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('无效任务编号')
    if not cfg.get('large_tasks_enabled'):return {'enabled':False,'rows':[]}
    folder=runtime(root,int(group));cleanup(folder,cfg)
    rows=[read_json(p,{}) for p in (folder/'task-history').glob('*.json')]
    name=read_json(folder/'debug-group.json',{}).get('name',str(group))
    rows=sorted(({**r,'group_id':int(group),'group_name':name} for r in rows if r),key=lambda r:r.get('created',0),reverse=True)
    if not identity:
        from large_task_pool import daily_usage
        return {'enabled':True,'daily_used':daily_usage(root),'daily_limit':cfg.get('large_task_daily_limit',0),'rows':[{k:v for k,v in r.items() if k not in {'person_references','source_files','artifact','request'}} for r in rows]}
    row=next((r for r in rows if r['id']==identity),None)
    if row is None:raise ValueError('任务不存在或已过保存期限')
    row['can_resume']=row.get('status') in {'failed','cancelled','interrupted','rejected'}
    row['can_cancel']=row.get('status') in {'preparing','generating','continuing'}
    row['can_send']=row.get('status') in TERMINAL|{'ready'}
    row['partial']=row.get('partial',row.get('status') in {'failed','cancelled','interrupted','rejected'})
    # Older tasks staged bytes without recording their visible manifest.
    # Recover metadata only; never expose local paths or binary file contents.
    if 'input_files' not in row:
        inputs=folder/'generated-files'/identity/'inputs'
        row['input_files']=[]
        if inputs.is_dir() and not inputs.is_symlink():
            for path in sorted(inputs.iterdir())[:cfg.get('model_max_files',10)]:
                if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400 or not path.is_file():continue
                row['input_files'].append({'name':re.sub(r'^\d+-','',path.name),'bytes':path.stat().st_size,'status':'saved'})
    artifact=folder/'generated-files'/identity/'draft.txt'
    if row.get('artifact_file'):
        from task_sandbox import preview,safe_name
        artifact=folder/'generated-files'/identity/safe_name(row['artifact_file'])
        raw=artifact.read_bytes() if artifact.exists() else b''
        return {**row,'artifact':preview(raw) if not row.get('binary') else '',
            'artifact_base64':__import__('base64').b64encode(raw).decode()}
    return {**row,'artifact':artifact.read_text(encoding='utf-8') if artifact.exists() else ''}
