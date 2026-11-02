"""Read-only NapCat directory refresh, independent from message processing and reply admission."""
import asyncio
from pathlib import Path
import time
from console_config import Config,atomic


async def load(root,api,cfg):
    login=await api('get_login_info');own=str(login.get('user_id'))
    if own!=str(cfg.get('expected_self_id','169901502')):raise ValueError('Configured QQ account is not logged in')
    friends=await api('get_friend_list');members={};failures=[]
    groups=[g for g in cfg['chat_group_whitelist'] if Config(root,g).load()['enabled']]
    for group in groups:
        try:
            for member in await api('get_group_member_list',{'group_id':group}):
                members.setdefault(str(member['user_id']),{})[group]=str(member.get('card') or member.get('nickname') or '')[:80]
        except Exception:failures.append(group)
    folder=Path(root)/'runtime-private';folder.mkdir(exist_ok=True)
    now=time.time()
    atomic(folder/'directory.json',{'updated_at':now,'self_id':own,'failed_groups':failures,
        'contacts':{str(f['user_id']):{'groups':list(members.get(str(f['user_id']),{})),
            'name':str(f.get('remark') or f.get('nickname') or f['user_id'])[:80]}
            for f in friends if str(f['user_id'])!=own and members.get(str(f['user_id']))}})
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
