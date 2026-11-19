"""Contact-scoped message metadata and authenticated, fixed-host image previews."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import httpx
from PIL import Image


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS private_message_media(peer TEXT,id TEXT,data TEXT,PRIMARY KEY(peer,id))')


def store_metadata(db,peer,row):
    initialize(db)
    identity=str(row.get('message_id') or row.get('real_id') or '')
    if not identity:return
    segments=row.get('message',[]);images=[];files=[];reply='';forward=''
    for segment in segments if isinstance(segments,list) else []:
        kind=segment.get('type');data=segment.get('data',{})
        if kind=='image' and len(images)<10:
            from image_sources import validate_source
            url=data.get('url','')
            try:validate_source(url)
            except ValueError:continue
            images.append({'url':url,'name':str(data.get('file',''))[:100]})
        elif kind=='file':
            from file_messages import metadata
            files.extend(metadata({'message':[segment]}))
        elif kind=='reply':reply=str(data.get('id',''))[:80]
        elif kind=='forward':forward=str(data.get('id',''))[:200]
    details={'images':images,'files':files,'reply_id':reply,'forward_id':forward}
    if any(details.values()):db.execute('INSERT OR REPLACE INTO private_message_media VALUES (?,?,?)',(str(peer),identity,json.dumps(details,ensure_ascii=False)))
    db.commit()


def metadata(db,peer,identity,public=True):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_message_media'").fetchone():return {}
    row=db.execute('SELECT data FROM private_message_media WHERE peer=? AND id=?',(str(peer),str(identity))).fetchone()
    result=json.loads(row[0]) if row else {}
    if public:
        result['images']=[{'slot':i,'url':'/api/private-image?peer='+str(peer)+'&id='+str(identity)+'&slot='+str(i)} for i,_ in enumerate(result.get('images',[]))]
        result['files']=[{'name':f['name'],'size':f['size']} for f in result.get('files',[])]
    return result


def preview_path(folder, peer, identity, slot):
    key=hashlib.sha256((str(peer)+'\n'+str(identity)+'\n'+str(slot)).encode()).hexdigest()
    return Path(folder)/'preview-cache'/(key+'.bin')


def image(root,peer,identity,slot):
    from private_view import contacts
    from image_sources import validate_source
    root=Path(root)
    if str(peer) not in contacts(root):raise ValueError('联系人不在授权好友列表')
    with closing(sqlite3.connect(root/'runtime-private/private-chat.sqlite3')) as db:
        values=metadata(db,peer,identity,False).get('images',[])
    slot=int(slot)
    if not 0<=slot<len(values):raise ValueError('图片不存在')
    if values[slot].get('local_request'):
        from manual_images import read_image
        raw=read_image(root/'runtime-private/manual',{'id':values[slot]['local_request']},values[slot])
        with Image.open(io.BytesIO(raw)) as picture:return raw,Image.MIME[picture.format]
    url=values[slot]['url'];validate_source(url)
    path=preview_path(root/'runtime-private',peer,identity,slot);folder=path.parent
    if path.exists():raw=path.read_bytes()
    else:
        with httpx.Client(trust_env=False,timeout=10,follow_redirects=False) as client:
            with client.stream('GET',url) as response:
                response.raise_for_status();raw=b''
                for piece in response.iter_bytes():
                    raw+=piece
                    if len(raw)>8*1024*1024:raise ValueError('预览图片超过8MiB')
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.width*picture.height>20000000:raise ValueError('图片尺寸过大')
            if picture.format not in {'PNG','JPEG','WEBP','GIF'}:raise ValueError('不支持的图片格式')
        import uuid
        folder.mkdir(exist_ok=True);temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        try:temporary.write_bytes(raw);temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)
    with Image.open(io.BytesIO(raw)) as picture:mime=Image.MIME[picture.format]
    return raw,mime


def hydrate(root,peer):
    import asyncio
    from private_view import contacts
    from private_chat import NapCat
    if str(peer) not in contacts(root):raise ValueError('联系人不在授权好友列表')
    async def read():
        api=NapCat(root)
        try:return await api('get_friend_msg_history',{'user_id':int(peer),'count':100})
        finally:await api.close()
    result=asyncio.run(read())
    with closing(sqlite3.connect(Path(root)/'runtime-private/private-chat.sqlite3')) as db:
        known={row[0] for row in db.execute('SELECT id FROM messages WHERE peer=?',(str(peer),))}
        for row in result.get('messages',[]):
            if str(row.get('message_id') or row.get('real_id')) in known:store_metadata(db,peer,row)
    return {'ok':True,'message':'已补全当前私聊近期消息的图片与引用；不会重放历史回复'}


def fetch_recent(root, peer):
    import asyncio
    import time
    from console_config import read_json
    from private_chat import NapCat, message_key, message_order, private_text
    from private_events import emit
    from private_view import contacts

    root = Path(root)
    if peer not in contacts(root):
        raise ValueError('联系人不在授权好友列表')
    folder = root / 'runtime-private'
    self_id = str(read_json(folder / 'directory.json').get('self_id') or '')

    async def read():
        api = NapCat(root)
        try:
            login = await api('get_login_info')
            if not self_id or str(login.get('user_id')) != self_id:
                raise ValueError('QQ 账号已切换，请先刷新好友列表')
            return await api('get_friend_msg_history', {'user_id': int(peer), 'count': 50})
        finally:
            await api.close()

    rows = asyncio.run(read()).get('messages') or []
    added = 0
    with closing(sqlite3.connect(folder / 'private-chat.sqlite3')) as db:
        session = db.execute('SELECT value FROM meta WHERE key=?', ('session:' + peer,)).fetchone()
        cutoff = max(time.time() - 30 * 86400, json.loads(session[0]).get('stamp', 0) if session else 0)
        for row in sorted(rows, key=message_order)[-50:]:
            uid = str(row.get('user_id') or (row.get('sender') or {}).get('user_id') or '')
            identity, stamp, body = message_key(row), message_order(row)[0], private_text(row)
            if uid not in {peer, self_id} or not identity or stamp <= cutoff or not body:
                continue
            if (row.get('message_type', 'private') != 'private' or row.get('group_id')
                    or (uid == self_id and str(row.get('target_id') or peer) != peer)):
                continue
            role = 'assistant' if uid == self_id else 'user'
            key = 'message:' + peer + ':' + identity
            inserted = emit(db, peer, 'sent' if role == 'assistant' else 'received', body,
                            stamp=stamp, details={'message_id': identity, 'historical': True}, key=key)
            if inserted:
                db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?)', (peer, identity, role, body, stamp))
                added += 1
            db.execute('INSERT OR IGNORE INTO raw_messages VALUES (?,?,?,?)',
                       (peer, identity, stamp, json.dumps(row, ensure_ascii=False)))
            store_metadata(db, peer, row)
        db.execute('DELETE FROM messages WHERE peer=? AND id NOT IN '
                   '(SELECT id FROM messages WHERE peer=? ORDER BY stamp DESC,rowid DESC LIMIT 100)', (peer, peer))
        db.commit()
    return {'ok': True, 'message': f'已补入 {added} 条私聊记录，不触发回复'}
