"""Durable CLI result delivery to the originating QQ conversation only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FutureTimeout
from contextlib import closing
import json
from pathlib import Path
import re
import time

from console_config import Config
from owner_commands import db_open,authorized


def redact(text,root,*configs):
    from dotenv import dotenv_values
    from runtime_paths import project_path
    secrets=set()
    for cfg in configs:
        values=dict(cfg)
        try:values.update(dotenv_values(project_path(cfg.get('mcp_env_file','napcat.env'),root)))
        except (OSError,ValueError):pass
        for key,value in values.items():
            if re.search(r'key|token|secret|password|authorization',str(key),re.I) and isinstance(value,str) and value:
                secrets.add(value)
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',str(text))
    for secret in sorted(secrets,key=len,reverse=True):text=text.replace(secret,'[已隐藏]')
    text=re.sub(r'(?i)(bearer\s+|\bsk-)[A-Za-z0-9._-]+',r'\1[已隐藏]',text)
    text=re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|napcat[_-]?(?:mcp[_-]?)?token|webui[_-]?key|password|secret)[\s"\x27:=]+)[^\s,;"\x27}]+',r'\1[已隐藏]',text)
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]','',text)


def enqueue(root,identity,text,before):
    try:after=Config(root).load()
    except (OSError,ValueError):after={}
    text=redact(text,root,before,after)
    if len(text)>12000:text=text[:11950]+'\n[输出过长，余下内容请在CLI查看]'
    with closing(db_open(root)) as db,db:
        db.execute("INSERT OR IGNORE INTO replies VALUES (?,?,'queued',?,'','')",(identity,text,time.time()))


async def deliver(root,identity):
    from dotenv import dotenv_values
    from runtime_paths import project_path
    from napcat_connection import NapCatHTTPClient,request
    with closing(db_open(root)) as db,db:
        row=db.execute('SELECT c.actor,c.scope,r.text FROM replies r JOIN commands c ON c.id=r.id WHERE r.id=? AND r.status=\'sending\'',(identity,)).fetchone()
    if not row:return
    actor,scope,text=row;sent=[];status='sent';error=''
    try:
        async with NapCatHTTPClient(trust_env=False,timeout=8) as client:
            for start in range(0,len(text),3000):
                cfg=Config(root,int(scope) if scope.isdecimal() else None).load()
                permitted=cfg.get('owner_commands_enabled') and authorized(root,actor,scope)
                if scope=='private':
                    from private_access import allowed
                    permitted=permitted and cfg.get('private_reply_enabled') and allowed(cfg,actor)
                    path=Path(root)/'runtime-private/private-chat.sqlite3'
                    if path.exists():
                        import sqlite3
                        from private_profiles import profile
                        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as private_db:
                            permitted=permitted and not profile(private_db,actor)['blocked']
                else:permitted=permitted and not cfg.get('receive_only')
                if not permitted:status='cancelled';break
                env=dotenv_values(project_path(cfg['mcp_env_file'],root))
                body=redact(text[start:start+3000],root,cfg)
                action='send_private_msg' if scope=='private' else 'send_group_msg'
                params={('user_id' if scope=='private' else 'group_id'):int(actor if scope=='private' else scope),
                    'message':[{'type':'text','data':{'text':body}}],'auto_escape':True}
                response=await asyncio.wait_for(request(client,env,action,params,cfg),8)
                mid=str((response or {}).get('message_id') or '')
                if not mid:raise ValueError('missing message ID')
                sent.append(mid)
    except Exception as exc:status='uncertain';error=type(exc).__name__
    with closing(db_open(root)) as db,db:
        db.execute('UPDATE replies SET status=?,sent_ids=?,error=? WHERE id=?',(status,json.dumps(sent),error,identity))


def dispatch(console):
    future=getattr(console,'_bot_reply_future',None)
    if future and not future.done():return
    if future:
        try:future.result()
        except Exception:pass
    with closing(db_open(console.root)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE replies SET status='cancelled' WHERE status='queued' AND stamp<?",(time.time()-120,))
        db.execute("UPDATE replies SET status='uncertain' WHERE status='sending' AND stamp<?",(time.time()-120,))
        row=db.execute("SELECT id FROM replies WHERE status='queued' ORDER BY stamp LIMIT 1").fetchone()
        if not row:return
        identity=row[0];db.execute("UPDATE replies SET status='sending' WHERE id=? AND status='queued'",(identity,))
    executor=getattr(console,'_bot_reply_executor',None)
    if executor is None:executor=console._bot_reply_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='bot-command-result')
    console._bot_reply_future=executor.submit(asyncio.run,deliver(console.root,identity))


def finish(console):
    # /stop can close all workers; finish its explicit control response first.
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        dispatch(console);future=getattr(console,'_bot_reply_future',None)
        if not future:break
        try:future.result(timeout=max(.01,deadline-time.monotonic()))
        except FutureTimeout:break
        except Exception:pass
        with closing(db_open(console.root)) as db:
            if not db.execute("SELECT 1 FROM replies WHERE status='queued' LIMIT 1").fetchone():break
    executor=getattr(console,'_bot_reply_executor',None)
    if executor:executor.shutdown(wait=False)
