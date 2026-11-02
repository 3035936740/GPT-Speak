"""Per-contact behavior, prompts, local knowledge and operator notes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import uuid

from style_memory import grams
import private_settings


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS private_profiles(
        peer TEXT PRIMARY KEY, settings TEXT DEFAULT '{}', persona TEXT DEFAULT '',
        protocol TEXT DEFAULT '', note TEXT DEFAULT '', aliases TEXT DEFAULT '[]', blocked INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS private_knowledge(
        id TEXT PRIMARY KEY, peer TEXT, title TEXT, content TEXT, stamp REAL);
        CREATE TABLE IF NOT EXISTS private_profile_backups(peer TEXT, section TEXT, data TEXT, stamp REAL);''')
    db.execute("CREATE TABLE IF NOT EXISTS private_knowledge_options(id TEXT PRIMARY KEY,enabled INTEGER DEFAULT 1,source_file TEXT DEFAULT '')")


def profile(db, peer):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_profiles'").fetchone():
        return {'settings': {}, 'defaults': {}, 'persona': '', 'protocol': '', 'note': '', 'aliases': [], 'blocked': False}
    row = db.execute('SELECT settings,persona,protocol,note,aliases,blocked FROM private_profiles WHERE peer=?', (str(peer),)).fetchone()
    row = list(row) if row else ['{}', '', '', '', '[]', 0]
    result={'settings': json.loads(row[0]), 'persona': row[1], 'protocol': row[2],
            'note': row[3], 'aliases': json.loads(row[4]), 'blocked': bool(row[5]), 'defaults': private_settings.defaults(db), 'account_type':'','mark_scope':'global'}
    from member_labels import MemberLabels
    database=db.execute('PRAGMA database_list').fetchone()[2]
    mark=MemberLabels(Path(database).parent).facts().get(str(peer),{}) if database else {}
    if mark:
        result.update(account_type=mark['label'],blocked=mark['blocked'],mark_scope=mark['mark_scope'])
        if mark['note_source']!='unmarked': result['note']=mark['note']
        if mark['aliases_source']!='unmarked': result['aliases']=mark['aliases']
    return result


def effective(cfg, row):
    return private_settings.apply(cfg, row)


def knowledge(db, peer, query=None, budget=0):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_knowledge'").fetchone():
        return []
    rows = [{'id': r[0], 'title': r[1], 'content': r[2], 'stamp': r[3]} for r in db.execute(
        'SELECT id,title,content,stamp FROM private_knowledge WHERE peer=? ORDER BY stamp DESC'+(' LIMIT 50' if query is None or budget else ''), (str(peer),))]
    if query is None:
        return rows
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='private_knowledge_options'").fetchone():
        disabled={r[0] for r in db.execute('SELECT id FROM private_knowledge_options WHERE enabled=0')}
        rows=[row for row in rows if row['id'] not in disabled]
    words = grams(query)
    ranked = [(len(words & grams(row['title'] + row['content'])), row) for row in rows]
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    result = []
    for score, row in (ranked[:3] if budget else ranked):
        if not score:
            continue
        if not budget:result.append(row);continue
        available = budget - len(json.dumps(result + [{**row, 'content': ''}], ensure_ascii=False))
        if available <= 0:
            break
        # A long saved entry should still contribute a bounded relevant excerpt.
        content = row['content']
        if len(content) > available:
            position = next((index for index in range(len(content) - 1)
                             if content[index:index + 2] in words), 0)
            start = max(0, position - min(100, available // 4))
            content = content[start:start + available]
        row = {**row, 'content': content}
        while content and len(json.dumps(result + [row], ensure_ascii=False)) > budget:
            content = content[:-1]
            row['content'] = content
        if content:
            result.append(row)
    return result


def save(root, peer, section, values):
    from private_view import contacts
    from private_events import emit
    if str(peer) not in contacts(root):
        raise ValueError('该联系人不在当前授权群好友列表中')
    if not isinstance(values, dict):
        raise ValueError('配置内容必须是对象')
    path = Path(root) / 'runtime-private/private-chat.sqlite3'
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        old = profile(db, peer)
        if section == 'behavior':
            if set(values) - {'reply_enabled', 'use_group_impressions', 'max_reply_chars', 'reply_cooldown_seconds', 'knowledge_enabled', 'vision_enabled'}:
                raise ValueError('未知私聊行为设置')
            for key in ('reply_enabled', 'use_group_impressions', 'knowledge_enabled', 'vision_enabled'):
                if key in values and type(values[key]) is not bool:
                    raise ValueError('开关必须为布尔值')
            for key, low, high in [('max_reply_chars', 20, 2000), ('reply_cooldown_seconds', 0, 300)]:
                if key in values and (type(values[key]) is not int or not low <= values[key] <= high):
                    raise ValueError(key + '超出范围')
            new = {**old, 'settings': {**old['settings'], **values}}
        elif section == 'prompts':
            if set(values) != {'persona', 'protocol'} or any(not isinstance(v, str) or len(v) > 6000 for v in values.values()):
                raise ValueError('私聊人设和规则各最多6000字')
            if sum(map(len, values.values())) > 8000:
                raise ValueError('私聊人设与规则合计最多8000字，为对话留出上下文空间')
            new = {**old, **values}
        elif section == 'member':
            if not {'note','aliases','blocked'}<=set(values) or set(values)-{'note','aliases','blocked','account_type','mark_scope'} or not isinstance(values['note'], str) or len(values['note']) > 200:
                raise ValueError('联系人说明最多200字')
            aliases = values['aliases']
            if not isinstance(aliases, list) or len(aliases) > 10 or any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 40 for x in aliases):
                raise ValueError('额外昵称最多10个，每个1～40字')
            if type(values['blocked']) is not bool:
                raise ValueError('屏蔽开关必须为布尔值')
            new = {**old, **values}
            from member_labels import MemberLabels
            MemberLabels(Path(root)/'runtime-private').mark(peer,values.get('account_type',old.get('account_type')) or 'auto',values['note'],values['aliases'],values['blocked'],values.get('mark_scope'))
        elif section == 'knowledge':
            if 'enabled' in values and type(values['enabled']) is not bool:raise ValueError('知识启用状态必须为开关')
            identity = str(values.get('id') or uuid.uuid4().hex)
            if len(identity) > 80:
                raise ValueError('知识ID无效')
            existing = db.execute('SELECT peer,title,content FROM private_knowledge WHERE id=?', (identity,)).fetchone()
            if existing and existing[0] != str(peer):
                raise ValueError('不能修改其他私聊的知识')
            if values.get('delete'):
                if existing:
                    db.execute('INSERT INTO private_profile_backups VALUES (?,?,?,?)', (str(peer), 'knowledge', json.dumps(list(existing), ensure_ascii=False), time.time()))
                db.execute('DELETE FROM private_knowledge WHERE id=? AND peer=?', (identity, str(peer)))
                db.execute('DELETE FROM private_knowledge_options WHERE id=?',(identity,))
            else:
                title, content = values.get('title'), values.get('content')
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80 or not isinstance(content, str) or not content.strip():
                    raise ValueError('标题1～80字，知识正文不能为空')
                if existing:
                    db.execute('INSERT INTO private_profile_backups VALUES (?,?,?,?)', (str(peer), 'knowledge', json.dumps(list(existing), ensure_ascii=False), time.time()))
                db.execute('INSERT OR REPLACE INTO private_knowledge VALUES (?,?,?,?,?)', (identity,str(peer),title.strip(),content.strip(),time.time()))
                db.execute('INSERT OR REPLACE INTO private_knowledge_options VALUES (?,?,?)',
                    (identity,int(values.get('enabled',True)),str(values.get('source_file',''))[:240]))
            emit(db, peer, 'stage', '当前私聊知识库已更新；下次相关回复检索')
            return {'ok': True, 'message': '私聊知识库已保存'}
        else:
            raise ValueError('未知私聊配置分类')
        db.execute('INSERT INTO private_profile_backups VALUES (?,?,?,?)', (str(peer), section, json.dumps(old, ensure_ascii=False), time.time()))
        db.execute('INSERT OR REPLACE INTO private_profiles VALUES (?,?,?,?,?,?,?)',
            (str(peer), json.dumps(new['settings'], ensure_ascii=False), new['persona'], new['protocol'],
             new['note'], json.dumps(new['aliases'], ensure_ascii=False), int(new['blocked'])))
        emit(db, peer, 'system' if section == 'prompts' else 'stage',
            '当前私聊人设与规则已更新' if section == 'prompts' else '当前私聊设置已更新：' + section,
            details={'section': section, 'local_only': True})
    return {'ok': True, 'message': '当前私聊已保存，下次回复生效'}
