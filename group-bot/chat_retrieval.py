"""Retrieve retained group chat as timestamped dialogue passages."""
from array import array
from contextlib import closing
from datetime import date, datetime, time
from hashlib import sha256
from heapq import nlargest
from pathlib import Path
import re
import sqlite3
from threading import Lock, Thread
from time import monotonic

import httpx

from group_identity import account_mentioned, name_mentioned
from knowledge_base import terms
from knowledge_ranking import RECALL_LIMIT, RESULT_LIMIT, rerank
from knowledge_vectors import BATCH, THRESHOLD, query_vector


DATE = re.compile(r'(?<!\d)(\d{4})\s*(?:年\s*|[-/])(\d{1,2})\s*(?:月\s*|[-/])(\d{1,2})(?:\s*[日号])?(?!\d)')
HISTORY = re.compile(r'说过什么|说了什么|讲了什么|发了什么|聊了什么|说的什么|之前|上次|先前|查.{0,5}(?:聊天|发言)|历史|\bsaid\b', re.I)
_jobs = set()
_lock = Lock()
_build_lock = Lock()


def snapshot(memory, target, *, include_chat=True):
    """Resolve speaker filters before reading a snapshot on the bot thread."""
    query = str(target.get('text', ''))
    match = DATE.search(query)
    historical = bool(match or HISTORY.search(query))
    if not historical and len(re.sub(r'@\d+|\W', '', query)) < 4:
        return None
    names = {row['id']: row['name'] for row in memory.db.execute('SELECT id,name FROM people')}
    members = target.get('_identity_snapshot', {}).get('mentioned_members', [])
    own = str(target.get('_self_id', ''))
    speakers = {memory.digest(account) for account in map(str, target.get('_at_ids', []))
                if account.isdecimal() and account != own} if historical else set()
    spellings = set()
    if historical and not speakers:
        for account in re.findall(r'(?<!\d)\d{5,15}(?!\d)', DATE.sub('', query)):
            if account != own and account_mentioned(query, account):
                speakers.add(memory.digest(account))
                spellings.add(account)
        if not speakers:
            for member in members:
                account = str(member.get('qq_id', ''))
                if not account or account == own:
                    continue
                for name in [member.get('card'), member.get('nickname'), *member.get('aliases', [])]:
                    if name and name_mentioned(query, name):
                        spellings.add(name)
                        speakers.add(memory.digest(account))
            for identity, name in names.items():
                if name_mentioned(query, name):
                    spellings.add(name)
                    speakers.add(identity)
    if include_chat and len(speakers) > 1:
        return {'notice': '历史检索命中多个可能的群友，请用户提供准确 QQ 号。'}
    now = datetime.now().timestamp()
    start, end = 0, float(target.get('_time') or now)
    if not historical:
        # Recent turns already appear in the live context. Wait until a burst
        # settles so that each new reply does not regenerate the same vector.
        end -= 150
    if match and include_chat:
        try:
            day = date(*map(int, match.groups()))
        except ValueError:
            return {'notice': '历史检索中的日期无效，请用户核对。'}
        start = datetime.combine(day, time.min).timestamp()
        end = min(end, datetime.combine(day, time.max).timestamp() + .000001)
    words = DATE.sub(' ', re.sub(r'@\d+', ' ', query))
    for spelling in sorted(spellings, key=len, reverse=True):
        words = re.sub(re.escape(spelling), ' ', words, flags=re.I)
    words = HISTORY.sub(' ', words).strip(' \t，。？！,.!?')
    words = re.sub(r'^(?:请|麻烦|帮我|一下|帮忙|你|那天|当时)+', '', words).strip()
    direct = bool(re.search(r'说(?:过|了|的)|讲了|发了|聊了|\bsaid\b', query, re.I) and HISTORY.search(query))
    if include_chat and direct and not speakers and words and not re.search(r'群里|群聊|大家', words):
        return {'notice': '历史检索无法确认所指群友，请用户提供准确 QQ 号。'}
    if direct and words in {'群里','群聊','大家'}:
        words = ''
    seen = memory.digest(target.get('_id', ''))
    rows = memory.db.execute('SELECT id,speaker,text,stamp FROM sources WHERE stamp>=? AND stamp<? ORDER BY stamp DESC,id DESC LIMIT 2000',
        (max(start, now - 30 * 86400), end)).fetchall() if include_chat else []
    rows = [dict(row) for row in reversed(rows) if row['id'] != seen and memory.allowed(row)]
    notes=[]
    if not include_chat or not (speakers or match or direct):
        for row in memory.recall_rows():
            content=row['content']
            identity=sha256((row['id']+'\n'+content+'\n'+row['keywords']).encode()).hexdigest()
            notes.append({'id':identity,'content':content,'keywords':row['keywords'],'stamp':row['stamp'],
                'note':{'kind':row['kind'],'content':content,'historical':True,
                    'subject':names.get(row['subject'],'群聊'),
                    'when':datetime.fromtimestamp(row['stamp']).isoformat(sep=' ',timespec='seconds')}})
    return {'rows':rows, 'notes':notes, 'names':names, 'query':words if include_chat else re.sub(r'@\d+',' ',query),
            'speaker':next(iter(speakers),'') if include_chat else '', 'dated':bool(match and include_chat),
            'direct':include_chat and (direct or bool(match and speakers))}


def passages(rows, names):
    """Split bursts by time and size, keeping short reactions beside their turns."""
    current = []
    size = 0
    for row in rows:
        previous = current[-1] if current else None
        if previous and (len(current) >= 12 or size + len(row['text']) > 1100 or row['stamp'] - previous['stamp'] > 150):
            yield passage(current, names)
            current, size = [], 0
        current.append(row)
        size += len(row['text'])
    if current:
        yield passage(current, names)


def passage(rows, names):
    content = '\n'.join(f"{names.get(row['speaker'], '群友')}：{row['text']}" for row in rows)
    identity = sha256(('\n'.join(row['id'] for row in rows) + '\n' + content).encode()).hexdigest()
    return {'id':identity, 'content':content, 'rows':rows, 'stamp':rows[-1]['stamp']}


def connection(path):
    db = sqlite3.connect(path, timeout=3)
    db.execute('CREATE TABLE IF NOT EXISTS chat_vectors(id TEXT, model TEXT, stamp REAL, vector BLOB, PRIMARY KEY(id,model))')
    return db


def build(path, passages, embedding):
    with closing(connection(path)) as db, db:
        db.execute('DELETE FROM chat_vectors WHERE stamp<?', (datetime.now().timestamp() - 90 * 86400,))
        existing = {row[0] for row in db.execute('SELECT id FROM chat_vectors WHERE model=?', (embedding.index,))}
    missing = [item for item in passages if item['id'] not in existing]
    for i in range(0, len(missing), BATCH):
        batch = missing[i:i + BATCH]
        vectors = embedding.embed([item['content']+' '+item.get('keywords','') for item in batch], timeout=40)
        with closing(connection(path)) as db, db:
            db.executemany('INSERT OR IGNORE INTO chat_vectors VALUES (?,?,?,?)',
                [(item['id'], embedding.index, item['stamp'], vector.tobytes())
                 for item, vector in zip(batch, vectors)])
    with closing(connection(path)) as db, db:
        db.execute('DELETE FROM chat_vectors WHERE rowid IN (SELECT rowid FROM chat_vectors ORDER BY stamp DESC LIMIT -1 OFFSET 4000)')


def start(path, candidates, embedding):
    key = (path, embedding.index)
    with _lock:
        if key in _jobs:
            return
        _jobs.add(key)

    def run():
        try:
            with _build_lock:
                build(path, candidates, embedding)
        except (sqlite3.Error, OSError, httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
            pass
        finally:
            with _lock:
                _jobs.discard(key)

    Thread(target=run, name='chat-vector-index', daemon=True).start()


def vector_recall(path, candidates, query, embedding):
    if not path.exists():
        return []
    by_id = {item['id']:item for item in candidates}
    with closing(connection(path)) as db:
        cached = [(identity, packed) for identity, packed in
            db.execute('SELECT id,vector FROM chat_vectors WHERE model=?', (embedding.index,)) if identity in by_id]
    if not cached:
        return []
    vector = query_vector(embedding, query[:1000], int(monotonic() // 300))
    scores = []
    for identity, packed in cached:
        if len(packed) != len(vector) * vector.itemsize:
            continue
        other = array('f')
        other.frombytes(packed)
        score = sum(left * right for left, right in zip(vector, other))
        if score >= THRESHOLD:
            scores.append((score, identity))
    return [{**by_id[identity], 'similarity':score} for score, identity in nlargest(RECALL_LIMIT, scores)]


def retrieve(snapshot, path, *, mode, embedding=None, reranker=None):
    if snapshot.get('notice'):
        return [{'kind':'chat_lookup_status', 'historical':True, 'content':snapshot['notice']}]
    names, speaker, query = (snapshot[key] for key in ('names', 'speaker', 'query'))
    exact = bool(speaker or snapshot['dated'])
    grouped = list(passages(snapshot['rows'], names))
    if speaker:
        grouped = [item for item in grouped if any(row['speaker'] == speaker for row in item['rows'])]
    if not exact:
        grouped = [item for item in grouped if len(item['rows']) > 1 or len(item['content'].split('：', 1)[-1]) >= 12]
    for item in grouped:
        item['matched'] = [row for row in item['rows'] if not speaker or row['speaker'] == speaker]
    grouped.extend(snapshot.get('notes',[]))
    search_words = terms(query)
    lexical = sorted(((len(search_words & terms(item['content']+' '+item.get('keywords',''))), i, item) for i, item in enumerate(grouped)),
                     key=lambda row: (row[0], row[1]), reverse=True)
    lexical = [item for score, _, item in lexical if score > 0 or exact and not search_words][:RECALL_LIMIT]
    semantic = []
    if embedding and embedding.provider == 'ollama' and query and grouped:
        path = Path(path)
        try:
            semantic = vector_recall(path, grouped, query, embedding)
        except (sqlite3.Error, OSError, httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
            pass
        start(path, list(reversed(grouped)), embedding)
    if mode == 'hybrid' and semantic:
        scores, rows = {}, {}
        for candidates in (lexical, semantic):
            for rank, item in enumerate(candidates, 1):
                key = item['id']
                rows[key] = item
                scores[key] = scores.get(key, 0) + 1 / (20 + rank)
        candidates = [rows[key] for key in sorted(scores, key=scores.__getitem__, reverse=True)[:RECALL_LIMIT]]
    else:
        candidates = semantic or lexical
    selected = rerank(query, candidates, reranker)[:RESULT_LIMIT] if query else candidates[:RESULT_LIMIT]
    if not selected and exact:
        return [{'kind':'chat_lookup_status', 'historical':True, 'content':'本群保留的历史里没有找到符合日期、群友和关键词的发言。'}]
    result = []
    for item in selected:
        if 'note' in item:
            result.append(item['note'])
            continue
        rows = item['matched'] if speaker else item['rows']
        turns = [{'when':datetime.fromtimestamp(row['stamp']).isoformat(sep=' ', timespec='seconds'),
                  'speaker':names.get(row['speaker'], '群友'), 'text':row['text']} for row in rows]
        result.append({'kind':'chat_history', 'historical':True, 'turns':turns})
    return result
