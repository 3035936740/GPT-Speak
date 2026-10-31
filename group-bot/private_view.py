"""Authenticated dashboard views and an idempotent manual private-send queue."""
import re
from contextlib import closing
from pathlib import Path
import sqlite3
import time
import json

from console_config import Config, read_json, pid_alive
from member_impressions import impression
import private_profiles
import private_settings
import private_access
from private_events import emit
from debug_history import bounded


def contacts(root):
    root = Path(root)
    directory = read_json(root / 'runtime-private/directory.json')
    expected = str(Config(root).load().get('expected_self_id', '169901502'))
    if directory.get('self_id') and str(directory['self_id']) != expected:
        return {}
    cfg=Config(root).load()
    return {uid: row for uid, row in directory.get('contacts', {}).items()
            if re.fullmatch(r'[1-9][0-9]{4,14}', uid) and uid != expected
            and any(group in cfg['chat_group_whitelist'] and Config(root,group).load()['enabled']
                    for group in map(int,row.get('groups',[])))}


def view(root, peer='', kind='all'):
    root = Path(root)
    cfg = Config(root).load()
    if kind not in {'all', 'chat', 'flow', 'system'}:
        raise ValueError('未知私信记录筛选')
    allowed = contacts(root)
    if peer and peer not in allowed:
        raise ValueError('该联系人不在当前授权群好友列表中')
    folder = root / 'runtime-private'
    state = read_json(folder / 'state.json')
    state['online'] = bool(state.get('online') and pid_alive(state.get('pid')))
    result = {'contacts': [], 'selected': peer, 'messages': [], 'impression': {}, 'requests': [], 'system_instruction': '',
              'status': state, 'enabled': cfg['private_reply_enabled'], 'events': [], 'event_counts': {},
              'profile': {}, 'contact_settings': {}, 'knowledge': [], 'runtime': {}, 'default_persona': '',
              'settings_fields': private_settings.FIELDS, 'settings_categories': private_settings.CATEGORIES,
              'settings_base': private_settings.values(cfg), 'settings_defaults': {}, 'settings_default_overrides': {},
              'bot_name': cfg['bot_name'], 'can_chat': False, 'access_reason': '',
              'member_scope':'global' if cfg.get('member_labels_global_enabled') else 'private','scoped_marks':True}
    path = folder / 'private-chat.sqlite3'
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) if path.exists() else None
    try:
        if db:
            db.row_factory = sqlite3.Row
            saved_defaults = private_settings.defaults(db)
            result['settings_default_overrides'] = saved_defaults
            result['settings_defaults'] = private_settings.values(cfg, saved_defaults)
        else:
            result['settings_defaults'] = private_settings.values(cfg)
        naming_cfg=private_access.config(root,cfg,db)
        for uid, contact in allowed.items():
            last = db.execute('SELECT text,stamp FROM messages WHERE peer=? ORDER BY stamp DESC,rowid DESC LIMIT 1', (uid,)).fetchone() if db else None
            result['contacts'].append({'id': uid, 'name': contact.get('name') or uid,
                'last_text': last['text'][:60] if last else '还没有私信记录', 'stamp': last['stamp'] if last else 0})
        result['contacts'].sort(key=lambda row: (-row['stamp'], row['name']))
        if peer:
            result['name'] = allowed[peer].get('name') or peer
            if db:
                local = private_profiles.profile(db, peer)
                effective = private_profiles.effective(cfg, local)
                result['profile'] = local
                result['access_reason'] = private_access.reason(private_access.config(root, cfg, db), peer)
                if local['blocked']:
                    result['access_reason'] = '已在联系人标记中屏蔽私聊'
                result['can_chat'] = not result['access_reason']
                result['contact_settings'] = private_settings.values(cfg, local['defaults'], local['settings'])
                result['knowledge'] = private_profiles.knowledge(db, peer)
                result['default_persona'] = Config(root).prompt_path('persona').read_text(encoding='utf-8-sig')
                result['messages'] = [dict(row) for row in reversed(db.execute(
                    'SELECT id,role,text,stamp FROM messages WHERE peer=? ORDER BY stamp DESC,rowid DESC LIMIT 100', (peer,)).fetchall())]
                result['requests'] = [dict(row) for row in db.execute(
                    'SELECT id,status,error FROM outbox WHERE peer=? ORDER BY stamp DESC LIMIT 5', (peer,))]
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='private_instructions'").fetchone():
                    instruction = db.execute('SELECT text FROM private_instructions WHERE peer=?', (peer,)).fetchone()
                    result['system_instruction'] = instruction['text'] if instruction else ''
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='private_events'").fetchone():
                    events = []
                    for row in reversed(db.execute('SELECT * FROM private_events WHERE peer=? ORDER BY id DESC LIMIT 1200', (peer,)).fetchall()):
                        events.append({'id': row['id'], 'kind': row['kind'], 'text': row['text'],
                            'stamp': row['stamp'], 'time': row['stamp'], 'level': row['level'], 'details': json.loads(row['details'])})
                    def matches(event, mode):
                        return mode == 'all' or (event['kind'] in {'sent', 'received'} if mode == 'chat'
                            else event['kind'] == 'system' if mode == 'system' else event['kind'] not in {'sent', 'received', 'system'})
                    result['event_counts'] = {mode: sum(matches(event, mode) for event in events) for mode in ('all', 'chat', 'flow', 'system')}
                    result['events'] = bounded([e for e in events if matches(e, kind)], effective)
                result['runtime'] = {'model': effective['model'], 'reasoning_effort': effective['reasoning_effort'],
                    'max_replies_per_hour': effective['private_max_replies_per_hour'],
                    'max_model_calls_per_day': effective['private_max_model_calls_per_day'],
                    'stored_messages': db.execute('SELECT count(*) FROM messages WHERE peer=?', (peer,)).fetchone()[0],
                    'delivery_states': {r[0]: r[1] for r in db.execute('SELECT status,count(*) FROM deliveries WHERE peer=? GROUP BY status', (peer,))}}
            if result.get('contact_settings',{}).get('use_group_impressions',cfg['private_use_group_impressions']):
                result['impression'] = impression(root, peer, allowed[peer]['groups'])
        return result
    finally:
        if db:
            db.close()


def enqueue(root, peer, text, request_id):
    root = Path(root)
    cfg = Config(root).load()
    if not cfg['enabled'] or not cfg['private_reply_enabled']:
        raise ValueError('请先启用私信服务')
    if str(peer) not in contacts(root):
        raise ValueError('该联系人不在当前授权群好友列表中')
    reason = private_access.reason(private_access.config(root, cfg), peer)
    if reason:
        raise ValueError(reason)
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('私信请输入1～2000字')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}', request_id):
        raise ValueError('无效的私信请求ID')
    state = read_json(root / 'runtime-private/state.json')
    if not pid_alive(state.get('pid')):
        raise ValueError('私信服务尚未启动，请稍后重试')
    with closing(sqlite3.connect(root / 'runtime-private/private-chat.sqlite3', timeout=5)) as db, db:
        if private_profiles.profile(db, peer)['blocked']:
            raise ValueError('此联系人已屏蔽私聊')
        old = db.execute('SELECT peer,text FROM outbox WHERE id=?', (request_id,)).fetchone()
        if old and old != (str(peer), text.strip()):
            raise ValueError('同一请求ID不能用于不同的消息')
        cursor = db.execute("INSERT OR IGNORE INTO outbox(id,peer,text,status,stamp) VALUES (?,?,?,'queued',?)",
                   (request_id, str(peer), text.strip(), time.time()))
        if cursor.rowcount:
            emit(db, peer, 'trigger', '手动私信已排队', details={'request_id': request_id}, key='manual:' + request_id)
    return {'ok': True, 'message': '私信已排队', 'request_id': request_id}


def set_instruction(root, peer, text):
    root = Path(root)
    if str(peer) not in contacts(root):
        raise ValueError('该联系人不在当前授权群好友列表中')
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('私聊系统提示词最多2000字')
    with closing(sqlite3.connect(root / 'runtime-private/private-chat.sqlite3', timeout=5)) as db, db:
        old = db.execute('SELECT text FROM private_instructions WHERE peer=?', (str(peer),)).fetchone()
        if old:
            db.execute('INSERT INTO instruction_history VALUES (?,?,?)', (str(peer), old[0], time.time()))
        stamp = time.time()
        db.execute('INSERT OR REPLACE INTO private_instructions VALUES (?,?,?)', (str(peer), text.strip(), stamp))
        emit(db, peer, 'system', text.strip() or '已清除当前私聊系统提示词', stamp=stamp,
            details={'action': 'set' if text.strip() else 'clear', 'local_only': True},
            key='system:' + str(peer) + ':' + format(stamp, '.6f'))
    return {'ok': True, 'message': '当前私聊系统提示词已保存，下次回复生效' if text.strip() else '当前私聊系统提示词已清除'}


def control_session(root,peer,mode):
    if mode not in {'stop','resume','clear'}:raise ValueError('未知私聊会话操作')
    if str(peer) not in contacts(root):raise ValueError('联系人不在授权好友列表')
    path=Path(root)/'runtime-private/private-chat.sqlite3'
    with closing(sqlite3.connect(path,timeout=5)) as db,db:
        row=db.execute('SELECT value FROM meta WHERE key=?',('session:'+str(peer),)).fetchone()
        previous=json.loads(row[0]) if row else {}
        value={'stamp':time.time(),'paused':True if mode=='stop' else False if mode=='resume' else previous.get('paused',False)}
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('session:'+str(peer),json.dumps(value)))
        if mode=='clear':db.execute('DELETE FROM messages WHERE peer=?',(str(peer),))
    return {'ok':True,'message':'当前私聊会话已'+{'stop':'暂停','resume':'恢复','clear':'清空上下文'}[mode]}
