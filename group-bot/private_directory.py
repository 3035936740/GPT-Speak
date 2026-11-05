"""Read-only NapCat directory refresh, independent from message processing and reply admission."""
import asyncio
from pathlib import Path
import time
from console_config import Config,atomic,read_json
from napcat_errors import NapCatAPIError,check_account,group_directory


async def load(root,api,cfg):
    login=await api('get_login_info');own=str(login.get('user_id'))
    check_account(login,cfg['expected_self_id'])
    friends=await api('get_friend_list');members={};failures=[]
    groups=[g for g in cfg['chat_group_whitelist'] if Config(root,g).load()['enabled']]
    try: joined=group_directory(await api('get_group_list'))
    except (NapCatAPIError,AssertionError): joined=None
    for group in groups:
        if joined is not None and str(group) not in joined:continue
        try:
            for member in await api('get_group_member_list',{'group_id':group}):
                members.setdefault(str(member['user_id']),{})[group]=str(member.get('card') or member.get('nickname') or '')[:80]
        except Exception:failures.append(group)
    folder=Path(root)/'runtime-private';folder.mkdir(exist_ok=True)
    now=time.time()
    path=folder/'directory.json'
    previous=read_json(path)
    active={str(f['user_id']):{'groups':list(members.get(str(f['user_id']),{})),
            'name':str(f.get('remark') or f.get('nickname') or f['user_id'])[:80]}
            for f in friends if str(f['user_id'])!=own and members.get(str(f['user_id']))}
    friend_ids={str(f['user_id']) for f in friends}
    known={**previous.get('unavailable_contacts',{}),**previous.get('contacts',{})}
    unavailable={}
    for uid,row in known.items():
        if uid==own or uid in active:continue
        reason='不在当前账号好友列表' if uid not in friend_ids else (
            '暂时无法确认共同授权群' if failures else '没有共同授权群')
        unavailable[uid]={'name':row.get('name') or uid,'groups':[],'unavailable_reason':reason}
    atomic(path,{'updated_at':now,'self_id':own,'failed_groups':failures,
        'contacts':active,'unavailable_contacts':unavailable})
    return own,{str(f['user_id']) for f in friends},members,groups


def refresh(root):
    from private_chat import NapCat
    async def run():
        api=NapCat(root)
        try:return await asyncio.wait_for(load(root,api,Config(root).load()),30)
        finally:await api.close()
    _,_,members,_=asyncio.run(run())
    atomic(Path(root)/'runtime-private/directory-refresh.json',{'stamp':time.time()})
    return {'ok':True,'message':'好友列表已从NapCat刷新；保留现有私聊和权限设置','members':len(members)}
