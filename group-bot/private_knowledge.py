"""Paged private knowledge management with unrestricted text imports and contact isolation."""
from contextlib import closing
from pathlib import Path
import sqlite3


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
