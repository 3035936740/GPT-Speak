"""Local semantic search for the scoped group knowledge store."""
from array import array
from functools import lru_cache
from heapq import nlargest
from threading import Lock, Thread
from time import monotonic

import httpx
from knowledge_ranking import RECALL_LIMIT


THRESHOLD = 0.53
BATCH = 16
_jobs = {}
_lock = Lock()
_build_lock = Lock()


def missing(db, group, index, limit=BATCH):
    return db.execute('''SELECT c.id,c.position,c.content,d.id AS document_id,d.title,d.version
        FROM chunks c JOIN documents d ON d.id=c.document_id
        LEFT JOIN knowledge_vectors v ON v.chunk_id=c.id AND v.model=?
        WHERE d.enabled=1 AND d.scope IN (0,?) AND v.chunk_id IS NULL
        ORDER BY d.updated DESC,c.position LIMIT ?''',(index,group,limit)).fetchall()


def build(store, group, embedding, active=lambda: True):
    """Resume an index and commit each batch, without holding a lock during inference."""
    while active():
        with store.connect() as db:
            rows = missing(db, group, embedding.index)
        if not rows:
            return
        vectors = embedding.embed([row['title']+'\n'+row['content'] for row in rows], timeout=40)
        with store.connect() as db, db:
            for row, vector in zip(rows, vectors):
                # SQLite can reuse deleted chunk IDs. Check the live document revision.
                db.execute('''INSERT OR IGNORE INTO knowledge_vectors(chunk_id,model,embedding)
                    SELECT c.id,?,? FROM chunks c JOIN documents d ON d.id=c.document_id
                    WHERE c.id=? AND d.id=? AND d.version=? AND d.enabled=1 AND d.scope IN (0,?)''',
                    (embedding.index,vector.tobytes(),row['id'],row['document_id'],row['version'],group))


def start(store, group, embedding, active=lambda: True, prepare=None):
    key = (store.path.resolve(),group,embedding.index)

    def run():
        state = 'idle'
        try:
            with _build_lock:
                if active():
                    if prepare:
                        prepare()
                    build(store,group,embedding,active)
        except Exception:
            state = 'error'
        finally:
            with _lock:
                _jobs[key] = state

    with _lock:
        if _jobs.get(key)!='indexing':
            _jobs[key] = 'indexing'
            Thread(target=run,name='knowledge-index',daemon=True).start()


def status(store, group, embedding):
    with _lock:
        state = _jobs.get((store.path.resolve(),group,embedding.index),'idle')
    if not store.path.exists():
        return {'model':embedding.model,'total':0,'indexed':0,'state':state,'other_index':False}
    with store.connect() as db:
        total, indexed = db.execute('''SELECT count(*),count(v.chunk_id) FROM chunks c
            JOIN documents d ON d.id=c.document_id
            LEFT JOIN knowledge_vectors v ON v.chunk_id=c.id AND v.model=?
            WHERE d.enabled=1 AND d.scope IN (0,?)''',(embedding.index,group)).fetchone()
        other = db.execute('''SELECT 1 FROM knowledge_vectors v JOIN chunks c ON c.id=v.chunk_id
            JOIN documents d ON d.id=c.document_id WHERE d.enabled=1 AND d.scope IN (0,?)
            AND v.model<>? LIMIT 1''',(group,embedding.index)).fetchone()
    return {'model':embedding.model,'total':total,'indexed':indexed,'state':state,'other_index':bool(other)}


@lru_cache(maxsize=128)
def query_vector(embedding, text, time_slice):
    # Cache only computed vectors in memory. The cache expires every five minutes.
    if embedding.model.lower().split('/')[-1].startswith('qwen3-embedding'):
        text = 'Instruct: Given a question, retrieve relevant passages that answer the question\nQuery: ' + text
    return embedding.embed([text], timeout=4)[0]


def rank(store, group, query, embedding):
    if not query.strip():
        return []
    sql = '''FROM knowledge_vectors v JOIN chunks c ON c.id=v.chunk_id
        JOIN documents d ON d.id=c.document_id WHERE v.model=? AND d.enabled=1 AND d.scope IN (0,?)'''
    with store.connect() as db:
        if not db.execute('SELECT 1 '+sql+' LIMIT 1',(embedding.index,group)).fetchone():
            return []
    try:
        query_embedding = query_vector(embedding,query[:1000],int(monotonic()//300))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        return []

    def candidates(rows):
        for identity, packed in rows:
            if len(packed) != len(query_embedding) * query_embedding.itemsize:
                continue
            embedding = array('f')
            embedding.frombytes(packed)
            score = sum(left * right for left, right in zip(embedding,query_embedding))
            if score >= THRESHOLD:
                yield score, identity

    with store.connect() as db:
        db.execute('BEGIN')
        matches = nlargest(RECALL_LIMIT,candidates(db.execute('SELECT c.id,v.embedding '+sql,(embedding.index,group))))
        results = []
        for score, identity in matches:
            row = db.execute('''SELECT c.id,c.position,c.content,d.id AS document_id,d.title,d.source,d.scope
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE c.id=? AND d.enabled=1 AND d.scope IN (0,?)''',(identity,group)).fetchone()
            if row:
                results.append({**dict(row),'similarity':round(score,3)})
    return results
