"""Operator-only continuation and explicit file delivery; never replay a chat request."""
import asyncio
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import time
from console_config import Config,runtime,read_json,pid_alive,atomic
from task_history import update,view,TERMINAL
from task_sandbox import safe_name,package,preview


def checked(root,group,identity):
    cfg=Config(root,int(group)).load()
    if not cfg.get('large_tasks_enabled'):raise ValueError('请先开启本群任务')
    if not re.fullmatch(r'[a-f0-9]{32}',str(identity)):raise ValueError('无效任务编号')
    row=view(root,int(group),identity)
    from member_policy import blocked_accounts
    if row.get('requester_qq') and row['requester_qq'] in blocked_accounts(runtime(root,int(group))):raise ValueError('该任务请求者已被屏蔽')
    return cfg,runtime(root,int(group)),row


def force_cancel(root,group,identity):
    Config(root,int(group)).load()
    from task_cancel import path as cancel_path
    folder=runtime(root,int(group));cancel_path(folder,identity)
    row=read_json(folder/'task-history'/(identity+'.json'))
    if not row:raise ValueError('任务不存在或已清理')
    if row.get('status') not in {'preparing','generating','continuing'}:raise ValueError('任务已停止或上传已开始，不能再中断生成')
    from task_cancel import request
    request(folder,identity)
    update(folder,identity,cancel_requested=True,stage='正在强制中断此任务')
    return {'ok':True,'message':'已请求强制中断，仅影响此任务；正在终止模型与任务执行，保留可恢复文件'}


def save_partial(folder,identity,files,cfg):
    from task_file_metrics import describe
    if not files:return
    name,raw=package(files,cfg.get('large_task_max_file_bytes',20971520))
    directory=Path(folder)/'generated-files'/identity;directory.mkdir(parents=True,exist_ok=True)
    path='partial-'+name
    (directory/path).write_bytes(raw)
    text=preview(raw)
    update(folder,identity,filename=name,artifact_file=path,partial=True,bytes=len(raw),
        characters=len(text) if text is not None else 0,binary=text is None,
        files=[describe(n,b) for n,b in files])


def artifact(folder,row):
    directory=Path(folder)/'generated-files'/row['id']
    name=row.get('artifact_file') or 'draft.txt';safe_name(name)
    path=directory/name
    if path.is_symlink() or not path.is_file() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('没有可下载或发送的文件')
    filename=row.get('filename') or 'draft.txt';safe_name(filename)
    return filename,path


def enqueue_resume(root,group,identity):
    cfg,folder,row=checked(root,group,identity)
    if row['status'] not in {'failed','interrupted','cancelled','rejected'}:raise ValueError('仅未完成且已停止的任务可以继续')
    if not cfg.get('enabled') or cfg.get('receive_only'):raise ValueError('请先恢复本群AI处理')
    from session_control import state
    if state(folder).get('paused'):raise ValueError('请先恢复当前会话')
    if not pid_alive(read_json(folder/'state.json').get('pid')):raise ValueError('请先启动本群后台')
    from manual_task_requests import connect
    key='resume:'+identity
    with closing(connect(folder)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT state FROM requests WHERE message_id=?',(key,)).fetchone()
        if old and old[0] in {'queued','claimed'}:return {'ok':True,'message':'继续任务已提交，请勿重复点击'}
        if row.get('resume_child'):
            child=read_json(folder/'task-history'/(row['resume_child']+'.json'))
            if child and child.get('status') not in TERMINAL:raise ValueError('该任务已有继续中的任务')
        target={'_id':key,'sender':row.get('sender',''),'text':row.get('request',''),
            '_local_task_resume':identity,'_large_task_origin_id':key,'at_bot':True,'_time':time.time()}
        db.execute("INSERT OR REPLACE INTO requests VALUES (?,?,'queued',?)",(key,json.dumps(target,ensure_ascii=False),time.time()))
    return {'ok':True,'message':'已提交继续任务，完成后仅供本地下载，不自动发群'}


def read_files(directory,cap,limit=64):
    rows=[];total=0
    if not directory.is_dir() or directory.is_symlink():return rows
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('任务文件含链接，拒绝恢复')
        if path.is_dir():continue
        total+=path.stat().st_size
        if total>cap or len(rows)>=limit:raise ValueError('保留的任务文件超过上限')
        rows.append((safe_name(path.relative_to(directory).as_posix()),path.read_bytes()))
    return rows


def enqueue_send(root,group,identity,confirmed=False):
    if not confirmed:raise ValueError('请先确认发送到QQ群')
    cfg,folder,row=checked(root,group,identity)
    if row['status'] not in TERMINAL|{'ready'}:raise ValueError('任务仍在执行，请等待停止后再发送')
    if not cfg.get('enabled') or cfg.get('receive_only') or not pid_alive(read_json(folder/'state.json').get('pid')):raise ValueError('请先启动本群AI后台')
    from manual_task_requests import connect
    key='send-task:'+identity
    target={'_id':key,'sender':row.get('sender',''),'text':row.get('request',''),'_manual_file_send':identity,
        '_large_task_origin_id':key,'at_bot':True,'_time':time.time()}
    with closing(connect(folder)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT state FROM requests WHERE message_id=?',(key,)).fetchone()
        if old and old[0] in {'queued','claimed'}:return {'ok':True,'message':'文件发送已提交，请勿重复点击'}
        db.execute("INSERT OR REPLACE INTO requests VALUES (?,?,'queued',?)",(key,json.dumps(target,ensure_ascii=False),time.time()))
    return {'ok':True,'message':'已提交发送到QQ群；未完成文件会标为草稿'}


async def recover(folder,row,cfg):
    """Compatibility for older failed shared-container tasks, before checkpoints existed."""
    if not row.get('sandbox') or row.get('artifact_file'):return
    from task_sandbox import Sandbox
    saved=row['sandbox']
    old={**cfg,'large_task_executor':saved['executor'],'large_task_container_name':saved.get('container','gpt-speak-container'),
        'large_task_container_mode':saved.get('mode','shared'),'large_task_docker_wsl':saved.get('wsl','')}
    box=Sandbox(old,row['id'])
    status,raw=await box.command('inspect',box.name)
    if status:return
    labels=json.loads(raw)[0].get('Config',{}).get('Labels',{})
    if labels.get('gpt-speak.shared')!='true' and labels.get('gpt-speak.task')!=row['id']:raise ValueError('沙箱归属不符')
    files=await box.outputs();save_partial(folder,row['id'],files,cfg)
    rows=await box.snapshot();directory=Path(folder)/'generated-files'/row['id']/'workspace-snapshot'
    for name,raw in rows:
        if name.startswith(('input/','tmp/')):continue
        path=directory/safe_name(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)


async def resume(root,group,identity,cfg,model):
    _,folder,row=checked(root,group,identity)
    if row['status'] not in {'failed','interrupted','cancelled','rejected'}:raise ValueError('任务未停止或已经完成')
    try:await recover(folder,row,cfg)
    except Exception:pass # Original inputs can still rebuild missing intermediates.
    directory=folder/'generated-files'/identity
    inputs=read_files(directory/'inputs',cfg.get('model_max_file_bytes',5242880)*cfg.get('model_max_files',10),cfg.get('model_max_files',10))
    snapshots=read_files(directory/'workspace-snapshot',cfg.get('large_task_max_file_bytes',20971520))
    checkpoint=read_json(directory/'resume.json')
    people=(checkpoint or {}).get('base',{}).get('person_references',row.get('person_references',[]))
    from member_policy import blocked_accounts
    if {p.get('qq_id') for p in people}.intersection(blocked_accounts(folder)):raise ValueError('任务涉及已屏蔽账号，不能继续读取')
    target={'_id':'resume:'+identity,'sender':row.get('sender',''),'text':row.get('request',''),'_local_only':True,
        '_resume_parent':identity,'_resume_state':checkpoint or {'observations':row.get('execution',[])[-2:]},
        '_resume_files':snapshots,'_task_inputs':[{'name':re.sub(r'^\d+-','',name),'data':base64.b64encode(raw).decode(),
            'binary':preview(raw) is None} for name,raw in inputs]}
    target['_uid']=row.get('requester_qq','')
    target['_person_context']=people
    pages=[{'title':f['name'],'body':f.get('content',''),'source_type':'file'} for f in row.get('source_files',[])]
    if row.get('sandbox') or row.get('host_execution'):
        if cfg.get('large_task_executor','text')=='text':raise ValueError('请先配置容器或已确认风险的本机环境')
    else:
        target['_resume_content']=(directory/'draft.txt').read_text(encoding='utf-8') if (directory/'draft.txt').exists() else ''
        target['_resume_filename']=row.get('filename','')
        target['_person_context']=row.get('person_references',[])
    from large_tasks import generate
    try:
        result,status=await generate({**cfg,'_task_project_root':str(root)},target,pages,None,model,folder)
        if result is None:raise ValueError(status.get('reason','继续任务未执行'))
    except BaseException as error:
        update(folder,identity,resume_error=str(error)[:300]);raise
    return result


def partial_reply(folder,identity,cfg):
    from emoji_library import ReplyText
    row=read_json(Path(folder)/'task-history'/(identity+'.json'))
    name,path=artifact(folder,row)
    raw=path.read_bytes()
    packet={'group_id':cfg['group_id'],'name':'草稿-'+name,'file':'base64://'+base64.b64encode(raw).decode()}
    from large_tasks import validate_upload
    validate_upload(packet,cfg)
    reply=ReplyText('',segments=[{'type':'_generated_group_file','data':packet}]);reply.unfinished_task=True
    return reply


async def send(root,group,identity,confirmed=False):
    if not confirmed:raise ValueError('请确认发送文件到此QQ群')
    cfg,folder,row=checked(root,group,identity)
    if row['status'] not in TERMINAL|{'ready'}:raise ValueError('请等待本次执行停止后再发送')
    from session_control import state
    if not cfg.get('enabled') or cfg.get('receive_only') or state(folder).get('paused'):raise ValueError('本群已停用、仅接收或会话暂停，不能发送')
    try:await recover(folder,row,cfg)
    except Exception:pass
    row=read_json(folder/'task-history'/(identity+'.json'))
    name,path=artifact(folder,row)
    if path.stat().st_size>cfg.get('large_task_max_file_bytes',20971520):raise ValueError('文件超过发送上限')
    raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    from manual_outbox import ManualOutbox
    with ManualOutbox(root,group).lock():
        current=read_json(folder/'task-history'/(identity+'.json'))
        if current.get('manual_delivery',{}).get('digest')==digest and current['manual_delivery'].get('status') in {'sending','sent','uncertain'}:raise ValueError('此文件已发送或结果未确认，不自动重复发送')
        update(folder,identity,manual_delivery={'digest':digest,'status':'sending'})
    partial=row.get('partial') or row['status'] not in {'local_ready','ready','uploaded'}
    packet={'group_id':int(group),'name':('草稿-' if partial else '')+name,'file':'base64://'+base64.b64encode(raw).decode()}
    from large_tasks import validate_upload,permission
    from napcat_connection import NapCatHTTPClient,request
    from dotenv import dotenv_values
    started=False
    try:
        validate_upload(packet,cfg)
        async with NapCatHTTPClient() as client:
            env=dotenv_values(cfg['mcp_env_file'])
            async def api(action,params):return await request(client,env,action,params,Config(root,int(group)).load()) or {}
            access=await permission(api,int(group),cfg.get('expected_self_id'))
            if not access['allowed']:raise ValueError(access['reason'])
            latest=Config(root,int(group)).load()
            if latest.get('receive_only') or not latest.get('enabled') or state(folder).get('paused'):raise ValueError('发送权限已改变')
            started=True;result=await api('upload_group_file',packet)
            if not result or not (result.get('file_id') or result.get('message_id')):raise ValueError('上传结果未确认')
        update(folder,identity,manual_delivery={'digest':digest,'status':'sent','partial':bool(partial)})
        return {'ok':True,'partial':bool(partial),'failure':{'reason':row.get('error',''),'steps_used':row.get('step',0)},'message':'文件已发送到群'+('（未完成草稿）' if partial else '')}
    except Exception:
        update(folder,identity,manual_delivery={'digest':digest,'status':'uncertain' if started else 'failed'});raise
