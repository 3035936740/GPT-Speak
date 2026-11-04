"""The same memory schema and CRUD as groups, in an isolated database per friend."""
from pathlib import Path
from group_memory import GroupMemory


def folder(root,peer,check=True):
    import re
    from private_view import contacts
    if not re.fullmatch(r'[1-9][0-9]{4,14}',str(peer)) or (check and str(peer) not in contacts(root)):raise ValueError('联系人不在授权好友列表')
    return Path(root)/'runtime-private'/'contacts'/str(peer)


def open_memory(root,peer,check=True):
    path=folder(root,peer,check);path.mkdir(parents=True,exist_ok=True)
    return GroupMemory(path/'group-memory.sqlite3')


def view(root,peer,table='notes',query='',offset=0):
    from debug_store import database_view
    return database_view(root,0,table,query,offset,folder=folder(root,peer))


def mutate(root,peer,table,mode,values=None,row_id=None,expected=None):
    from debug_store import database_mutate
    memory=open_memory(root,peer);memory.close()
    return database_mutate(root,0,table,mode,values,row_id,expected,folder=folder(root,peer))


def observe(root,peer,identity,text,stamp):
    memory=open_memory(root,peer,False)
    try:memory.observe({'_id':str(identity),'_uid':str(peer),'text':text,'_time':stamp})
    finally:memory.close()


def recall(root,peer,text,cfg=None):
    from contextlib import closing

    path=folder(root,peer,False)/'group-memory.sqlite3'
    if not path.exists():return []
    target={'_uid':str(peer),'text':text}
    cfg=cfg or {}
    mode=cfg.get('private_memory_retrieval_mode','keyword')
    sample=None
    with closing(GroupMemory(path)) as memory:
        keyword=memory.retrieve(target,[],limit=1200)
        if mode in {'vector','hybrid'} and cfg.get('knowledge_embedding_provider','ollama')=='ollama':
            from chat_retrieval import snapshot
            sample=snapshot(memory,target,include_chat=False)
    if not sample:return keyword

    from chat_retrieval import retrieve
    from knowledge_embedding import configured
    from knowledge_ranking import configured_reranker
    return retrieve(sample,path.with_name('chat-vectors.sqlite3'),mode=mode,
        embedding=configured(cfg,root),reranker=configured_reranker(cfg,root,local_only=True)) or keyword


def prepare_learning(service,peer,cfg):
    path=folder(service.root,peer,False)/'group-memory.sqlite3'
    if not path.exists():return None
    memory=GroupMemory(path)
    batch=memory.learning_batch(cfg.get('learning_min_messages',25),cfg.get('learning_interval_seconds',600))
    if not batch:memory.close();return None
    prompt,evidence=memory.learning_prompt(batch,cfg.get('learning_prompt_chars',4800))
    prompt='下面是当前好友本人的独立私聊来源。模板里的群聊天、群友、群内梗在这里均指本次私聊；不要暗示是对方在群里说过，也不涉及其他好友。\n'+prompt
    session=service.store.get('session:'+peer)
    memory.set_meta('last_learning_attempt',service.clock());memory.close()
    service.store.call_started(service.clock())
    async def run():
        import private_profiles
        import private_events
        from bot import generate_json
        from model_routing import decision_config
        with private_events.trace(service.store,peer):
            result=await generate_json({**decision_config(cfg),'_private_context':True,'group_id':0,'_web_search_mode':'disabled'},prompt,service.root/'learning.schema.json')
        fresh=private_profiles.effective(service.config(),private_profiles.profile(service.store.db,peer))
        if not service.active(fresh) or not service.can_chat(peer) or not fresh.get('_private_long_memory_enabled',True) or session!=service.store.get('session:'+peer):return
        memory=GroupMemory(path)
        try:memory.apply_learning(result,evidence)
        finally:memory.close()
        service.store.event(peer,'stage','当前好友的私聊记忆学习已完成')
    return run()
