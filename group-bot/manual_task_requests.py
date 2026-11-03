"""Explicit operator task requests, durably claimed once by the current group worker."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from console_config import Config,runtime,pid_alive,read_json

def connect(folder):
    db=sqlite3.connect(Path(folder)/'manual-tasks.sqlite3',timeout=5)
    db.execute('CREATE TABLE IF NOT EXISTS requests(message_id TEXT PRIMARY KEY,target TEXT,state TEXT,stamp REAL)')
    db.commit();return db

def enqueue(root,group,message_id):
    cfg=Config(root,int(group)).load();folder=runtime(root,int(group))
    if not cfg.get('large_tasks_enabled') or not cfg['enabled'] or cfg.get('receive_only'):raise ValueError('请先开启本群任务和AI处理')
    from session_control import state
    if state(folder).get('paused'):raise ValueError('请先恢复当前会话')
    if not pid_alive(read_json(folder/'state.json').get('pid')):raise ValueError('本群后台尚未运行')
    path=folder/'recent-chat.jsonl';target=None
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
        try:row=json.loads(line)
        except ValueError:continue
        if str(row.get('_id'))==str(message_id):target=row
    if not target or target.get('is_bot'):raise ValueError('仅可处理近期存档中群友发送的消息')
    from member_policy import blocked_accounts,is_blocked
    if is_blocked(target,blocked_accounts(folder)):raise ValueError('该账号已屏蔽，不能交给模型处理')
    target={**target,'_force_file_task':True,'_manual_task_started':time.time(),'at_bot':True,'_audience':'direct'}
    with closing(connect(folder)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT state FROM requests WHERE message_id=?',(str(message_id),)).fetchone()
        if old and old[0] in {'queued','claimed','accepted'}:return {'ok':True,'message':'该消息已提交任务，请查看任务列表'}
        db.execute("INSERT OR REPLACE INTO requests VALUES (?,?,'queued',?)",(str(message_id),json.dumps(target,ensure_ascii=False),time.time()))
    return {'ok':True,'message':'已提交手动任务，仍受并发和每日上限限制'}

def claim(folder):
    if not (Path(folder)/'manual-tasks.sqlite3').exists():return None
    with closing(connect(folder)) as db,db:
        db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT message_id,target,stamp FROM requests WHERE state='queued' ORDER BY stamp LIMIT 1").fetchone()
        if not row:return None
        db.execute("UPDATE requests SET state='claimed' WHERE message_id=?",(row[0],))
        if time.time()-row[2]>120:
            db.execute("UPDATE requests SET state='expired' WHERE message_id=?",(row[0],));return None
        return json.loads(row[1])

def finish(folder,target,status):
    with closing(connect(folder)) as db,db:db.execute('UPDATE requests SET state=? WHERE message_id=?',(status,str(target['_id'])))
    if status!='accepted':
        from task_history import begin,update
        identity=begin(folder,target,{})
        update(folder,identity,status='rejected',error={'full':'当前并发任务已满','daily_limit':'已达每日任务上限','disabled':'任务已关闭或权限已改变','duplicate':'同一消息已有运行中的任务'}.get(status,status))
