"""Explicit operator requests, durably claimed by the current group worker."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from console_config import Config,runtime,pid_alive,read_json,deleted_ids


def find_target(folder, group, message_id):
    from storage_io import read_text
    target = None
    for filename in ('debug-events.jsonl', 'recent-chat.jsonl'):
        try:
            lines = read_text(Path(folder) / filename).splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if filename == 'recent-chat.jsonl':
                if str(row.get('_id')) == str(message_id) and str(row.get('_group_id', group)) == str(group):
                    target = {**(target or {}), **row}
                continue
            if (row.get('kind') != 'received' or str(row.get('message_id')) != str(message_id)
                    or str(row.get('group_id', group)) != str(group)):
                continue
            quote = row.get('quote') or {}
            target = {'_id': str(message_id), '_uid': str(row.get('user_id') or ''),
                '_time': row.get('time', 0), 'sender': row.get('sender', ''),
                'text': row.get('text', ''), 'is_bot': False, '_group_id': group,
                '_reply_id': row.get('reply_id', ''), '_image_count': row.get('image_count', 0),
                '_image_hashes': row.get('image_hashes', []), '_files': row.get('files', []),
                '_record_only': row.get('record_only', False),
                '_quoted_message': {'_id': quote.get('message_id', ''), 'sender': quote.get('sender', ''),
                                    'text': quote.get('text', '')} if quote.get('found') else {}}
    if target and target.get('_image_count') and not target.get('_image_hashes'):
        from image_archive import message_image_keys
        memory = read_json(Path(folder) / 'image-memory.json')
        target['_image_hashes'] = memory.get('messages', {}).get(str(message_id), []) or message_image_keys(
            group, message_id, target['_image_count'])
    return target

def queue_path(folder, reply):
    return Path(folder)/('manual-ai-replies.sqlite3' if reply else 'manual-tasks.sqlite3')

def connect(folder, reply=False):
    db=sqlite3.connect(queue_path(folder,reply),timeout=5)
    db.execute('CREATE TABLE IF NOT EXISTS requests(message_id TEXT PRIMARY KEY,target TEXT,state TEXT,stamp REAL)')
    db.commit();return db

def enqueue(root,group,message_id,*,reply=False,image=False):
    reply=reply or image
    group=int(group)
    cfg=Config(root,group).load();folder=runtime(root,group)
    if group not in cfg['chat_group_whitelist']:raise ValueError('该群不在白名单内')
    if not cfg['enabled'] or cfg.get('receive_only'):raise ValueError('请先开启本群 AI 处理')
    if image and not cfg.get('image_generation_enabled'):raise ValueError('请先开启本群图片生成')
    if not reply and not cfg.get('large_tasks_enabled'):raise ValueError('请先开启本群任务')
    from session_control import state
    if state(folder).get('paused'):raise ValueError('请先恢复当前会话')
    if not pid_alive(read_json(folder/'state.json').get('pid')):raise ValueError('本群后台尚未运行')
    target = find_target(folder, group, message_id)
    if not target or target.get('is_bot') or str(message_id) in deleted_ids(folder):
        raise ValueError('当前群本地记录中找不到可处理的群友消息，请刷新聊天记录')
    if reply and target.get('_record_only'):raise ValueError('此消息类型仅供查看')
    from member_policy import blocked_accounts,is_blocked
    if is_blocked(target,blocked_accounts(folder)):raise ValueError('该账号已屏蔽，不能交给模型处理')
    now=time.time()
    target={**target,'at_bot':True,'_audience':'direct'}
    if reply:
        target['_manual_reply_started']=now
        target.pop('_historical',None)
        if image:target.update(_image_request=True,_manual_image_request=True,_delivery_quote=True)
    else:
        target.update(_force_file_task=True,_manual_task_started=now)
    with closing(connect(folder,reply)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT state,stamp FROM requests WHERE message_id=?',(str(message_id),)).fetchone()
        if reply:
            db.execute("DELETE FROM requests WHERE stamp<?",(now-86400,))
            if old and (old[0]=='queued' and now-old[1]<=120 or old[0]=='claimed' and now-old[1]<=600):
                return {'ok':True,'message':'该消息的 AI 回复正在等待或处理中'}
            old=None
        if old and old[0] in {'queued','claimed','accepted'}:return {'ok':True,'message':'该消息已提交任务，请查看任务列表'}
        db.execute("INSERT OR REPLACE INTO requests VALUES (?,?,'queued',?)",(str(message_id),json.dumps(target,ensure_ascii=False),now))
    return {'ok':True,'message':'已提交图片生成任务' if image else '已提交 AI 回复，将在群里引用原消息' if reply else '已提交手动任务，仍受并发和每日上限限制'}

def claim(folder,*,reply=False):
    if not queue_path(folder,reply).exists():return None
    with closing(connect(folder,reply)) as db,db:
        db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT message_id,target,stamp FROM requests WHERE state='queued' ORDER BY stamp LIMIT 1").fetchone()
        if not row:return None
        db.execute("UPDATE requests SET state='claimed' WHERE message_id=?",(row[0],))
        if time.time()-row[2]>120:
            db.execute("UPDATE requests SET state='expired' WHERE message_id=?",(row[0],));return None
        return json.loads(row[1])

def finish(folder,target,status,*,reply=False):
    with closing(connect(folder,reply)) as db,db:
        if reply:
            db.execute("UPDATE requests SET state=? WHERE message_id=? AND state='claimed' AND stamp=?",
                (status,str(target['_id']),target['_manual_reply_started']))
        else:
            db.execute('UPDATE requests SET state=? WHERE message_id=?',(status,str(target['_id'])))
    if reply:return
    if status!='accepted':
        from task_history import begin,update
        identity=begin(folder,target,{})
        update(folder,identity,status='rejected',error={'full':'当前并发任务已满','daily_limit':'已达每日任务上限','disabled':'任务已关闭或权限已改变','duplicate':'同一消息已有运行中的任务'}.get(status,status))
