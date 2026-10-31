"""Per-account global/local marks, independent of the legacy default switch."""
from copy import deepcopy
from pathlib import Path
from console_config import Config,read_json,atomic


def root_for(folder):
    folder=Path(folder);root=folder.parent
    if not (root/'settings.json').is_file():return None
    if folder.name in {'runtime','runtime-private'} or folder.name.startswith('runtime-') and folder.name[8:].isdigit():return root
    return None


def paths(folder):
    folder=Path(folder);root=root_for(folder)
    if root is None:return None
    shared=root/'runtime-shared';shared.mkdir(exist_ok=True)
    return root,folder/'member-labels.json',shared/'member-labels.json'


def documents(folder):
    root,local_path,global_path=paths(folder)
    local=read_json(local_path,{'members':{}})
    global_data=read_json(global_path,{'members':{}})
    legacy_global=Config(root).load().get('member_labels_global_enabled',False)
    if legacy_global and not global_path.exists():
        from member_global import initial_members
        global_data={'members':initial_members(root)}
    return local,global_data,legacy_global


def read(folder):
    local,shared,legacy_global=documents(folder)
    members=deepcopy(shared.get('members',{}) if legacy_global else local.get('members',{}))
    scopes={uid:('global' if legacy_global else 'private' if Path(folder).name=='runtime-private' else 'group') for uid in members}
    for uid,mode in shared.get('scopes',{}).items():
        rows=shared.get('members',{}) if mode=='global' else local.get('members',{})
        if uid in rows:
            members[uid]=deepcopy(rows[uid]);scopes[uid]='global' if mode=='global' else 'private' if Path(folder).name=='runtime-private' else 'group'
        else:members.pop(uid,None);scopes.pop(uid,None)
    result={**local,'members':members,'member_scopes':scopes}
    if legacy_global:
        result['rule_messages']=read_json(Path(folder)/'member-rules.json').get('rule_messages',local.get('rule_messages'))
    return result


def save(folder,data,before,requested=None):
    root,local_path,global_path=paths(folder)
    local,shared,legacy_global=documents(folder)
    local.setdefault('members',{});shared.setdefault('members',{})
    modes=shared.setdefault('scopes',{})
    current=data.get('members',{});previous=before.get('members',{})
    global_changed=False;local_changed=False
    for uid in set(current)|set(previous):
        explicit=requested and requested[0]==uid
        if not explicit and current.get(uid)==previous.get(uid):continue
        mode=requested[1] if explicit else modes.get(uid,'global' if legacy_global else 'local')
        if mode in {'group','private'}:mode='local'
        if explicit:modes[uid]=mode;global_changed=True
        destination=shared['members'] if mode=='global' else local['members']
        if uid in current:destination[uid]=deepcopy(current[uid])
        else:destination.pop(uid,None)
        if mode=='global':global_changed=True
        else:local_changed=True
    if 'rule_messages' in data and data['rule_messages'] is not None:
        if legacy_global:atomic(Path(folder)/'member-rules.json',{'rule_messages':data['rule_messages']})
        else:local['rule_messages']=data['rule_messages'];local_changed=True
    if global_changed:atomic(global_path,shared)
    if local_changed:
        Path(folder).mkdir(parents=True,exist_ok=True)
        atomic(local_path,local)
