"""Scoped index management and retrieval preview for the dashboard."""
from contextlib import closing
import sqlite3

from console_config import Config
from knowledge_base import checked
from knowledge_embedding import VECTOR_MODES, configured, read_key
from knowledge_vectors import start, status
from knowledge_ranking import configured_reranker


def context(root,group,peer=''):
    if not peer:
        store,scope=checked(root,group)
        return store,scope,Config(root,scope).load()
    from private_knowledge import index_store
    from private_profiles import effective,profile
    from private_view import contacts
    if str(peer) not in contacts(root):
        raise ValueError('联系人不在授权好友列表')
    store=index_store(root)
    cfg=Config(root).load()
    if store.path.exists():
        with closing(sqlite3.connect(store.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            cfg=effective(cfg,profile(db,peer))
    return store,int(peer),cfg


def index(root,group,peer='',*,automatic=False):
    store,scope,cfg=context(root,group,peer)
    if cfg['knowledge_retrieval_mode'] not in VECTOR_MODES:
        if automatic:return
        raise ValueError('请先把当前知识库切换到向量 RAG')
    embedding=configured(cfg,root)

    def active():
        _,_,current=context(root,group,peer)
        return current['knowledge_retrieval_mode'] in VECTOR_MODES and configured(current,root)==embedding

    prepare=None
    if peer:
        from private_knowledge import sync_index
        prepare=lambda:sync_index(store,peer)
    start(store,scope,embedding,active,prepare)


def inspect(root,group,peer=''):
    store,scope,cfg=context(root,group,peer)
    embedding=configured(cfg,root)
    return {**status(store,scope,embedding),'mode':cfg['knowledge_retrieval_mode'],
            'provider':embedding.provider,'has_api_key':bool(read_key(root))}


def preview(root,group,peer,query):
    store,scope,cfg=context(root,group,peer)
    query=str(query).strip()[:1000]
    if not query:
        raise ValueError('请输入测试问题')
    mode=cfg['knowledge_retrieval_mode']
    embedding=configured(cfg,root) if mode in VECTOR_MODES else None
    rows=store.retrieve(scope,query,cfg.get('knowledge_max_chars',0),embedding=embedding, mix=mode=='hybrid', reranker=configured_reranker(cfg,root)) if not peer or embedding else []
    if peer and not rows and store.path.exists():
        from private_profiles import knowledge
        with closing(sqlite3.connect(store.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            rows=knowledge(db,peer,query,cfg.get('knowledge_max_chars',0))
    return {'mode':mode,'rows':[{**row,'content':row['content'][:900]} for row in rows[:8]]}
