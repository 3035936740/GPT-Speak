"""Paged private knowledge management with unrestricted text imports and contact isolation."""
from contextlib import closing
from pathlib import Path
import sqlite3


def index_store(root):
    from knowledge_base import KnowledgeBase
    return KnowledgeBase(root,Path(root)/'runtime-private/private-chat.sqlite3')


def sync_index(store,peer):
    """Backfill old private documents in the same transaction as the index update."""
    from knowledge_base import write_document
    with store.connect() as db, db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_knowledge'").fetchone():
            return
        db.execute("CREATE TABLE IF NOT EXISTS private_knowledge_options(id TEXT PRIMARY KEY,enabled INTEGER DEFAULT 1,source_file TEXT DEFAULT '')")
        db.execute('''DELETE FROM documents WHERE scope=? AND id NOT IN
            (SELECT id FROM private_knowledge WHERE peer=?)''',(int(peer),str(peer)))
        rows=db.execute('''SELECT k.*,coalesce(o.enabled,1) AS enabled,coalesce(o.source_file,'') AS source
            FROM private_knowledge k LEFT JOIN private_knowledge_options o ON o.id=k.id
            LEFT JOIN documents d ON d.id=k.id AND d.scope=?
            WHERE k.peer=? AND (d.id IS NULL OR d.updated<>k.stamp)''',(int(peer),str(peer)))
        for row in rows.fetchall():
            write_document(db,row['id'],int(peer),row['title'],row['source'],row['content'],row['enabled'],str(row['stamp']),row['stamp'])


def retrieve(root,peer,cfg,query):
    from knowledge_embedding import configured
    from knowledge_ranking import configured_reranker
    return index_store(root).retrieve(int(peer),query,cfg.get('knowledge_max_chars',0),
        embedding=configured(cfg,root),mix=cfg.get('knowledge_retrieval_mode')=='hybrid',reranker=configured_reranker(cfg,root))


def view(root,peer,query='',state='all',offset=0,identity=''):
    from private_view import contacts
    if str(peer) not in contacts(root):raise ValueError('联系人不在授权好友列表')
    path=Path(root)/'runtime-private/private-chat.sqlite3'
    result={'rows':[],'total':0,'offset':max(0,int(offset)),'limit':20}
    if not path.exists():return result
    with closing(sqlite3.connect(path)) as db:
        has=db.execute("SELECT 1 FROM sqlite_master WHERE name='private_knowledge_options'").fetchone()
        options={r[0]:{'enabled':bool(r[1]),'source_file':r[2]} for r in db.execute('SELECT * FROM private_knowledge_options')} if has else {}
        if identity:
            row=db.execute('SELECT id,title,content,stamp FROM private_knowledge WHERE peer=? AND id=?',(str(peer),identity)).fetchone()
            if not row:raise ValueError('知识不存在或不属于当前联系人')
            return {**dict(zip(('id','title','content','stamp'),row)),**options.get(row[0],{'enabled':True,'source_file':''})}
        rows=[]
        for identity,title,content,stamp in db.execute('SELECT id,title,content,stamp FROM private_knowledge WHERE peer=? ORDER BY stamp DESC',(str(peer),)):
            row={'id':identity,'title':title,'preview':content[:350],'characters':len(content),'stamp':stamp,
                **options.get(identity,{'enabled':True,'source_file':''})}
            if query.casefold() not in (title+'\n'+content).casefold():continue
            if state=='enabled' and not row['enabled'] or state=='disabled' and row['enabled']:continue
            rows.append(row)
        result['total']=len(rows);result['rows']=rows[result['offset']:result['offset']+20]
    return result
