"""Versioned settings transfer with validation, preview, backup and rollback."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re
import secrets
from dotenv import dotenv_values,set_key
from console_config import Config,GLOBAL_ONLY,read_json,runtime
from storage_io import atomic_write_text
from runtime_paths import project_path
from image_archive import locked

FORMAT='qq-group-bot-settings';VERSION=1
PROMPTS=('persona','reply_protocol')


def fingerprint(value):return hashlib.sha256(json.dumps({key:item for key,item in value.items() if key!='exported_at'},ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def export_bundle(root):
    root=Path(root);cfg=Config(root).load();groups={}
    ids=set(cfg['chat_group_whitelist'])
    if (root/'group-config').exists():
        ids.update(int(path.name) for path in (root/'group-config').iterdir() if path.is_dir() and re.fullmatch(r'[1-9]\d{0,18}',path.name))
    for group in sorted(ids):
        scope=root/'group-config'/str(group);folder=runtime(root,group,cfg)
        groups[str(group)]={'settings':read_json(scope/'settings.json'),
            'prompts':{key:(scope/(key+'.txt')).read_text(encoding='utf-8') if (scope/(key+'.txt')).exists() else None for key in PROMPTS},
            'members':read_json(folder/'member-labels.json').get('members',{}),'no_cooldown':(folder/'NO_COOLDOWN').exists()}
    env=dotenv_values(project_path(cfg['mcp_env_file'],root))
    return {'format':FORMAT,'version':VERSION,'exported_at':datetime.now().isoformat(),
        'global_members':read_json(root/'runtime-shared/member-labels.json').get('members',{}),
        'member_scopes':read_json(root/'runtime-shared/member-labels.json').get('scopes',{}),
        'private_members':read_json(root/'runtime-private/member-labels.json').get('members',{}),
        'settings':cfg,'prompts':{key:Config(root).prompt_path(key).read_text(encoding='utf-8') if Config(root).prompt_path(key).exists() else '' for key in PROMPTS},
        'groups':groups,'connection':{key:env[key] for key in ('NAPCAT_HOST','NAPCAT_TOKEN') if env.get(key) is not None}}


class Validator(Config):
    def __init__(self,root,baseline):super().__init__(root);self.baseline=baseline
    def load(self):return self.baseline
    def validate(self,cfg):
        for key,value in cfg.items():
            reference=self.baseline.get(key)
            if isinstance(reference,(str,list,dict)) and not isinstance(value,type(reference)):raise ValueError('设置字段类型无效：'+key)
            if isinstance(value,str) and len(value)>100000:raise ValueError('设置文本过长：'+key)
        return super().validate(cfg)


def plan(root,bundle,connection=False):
    root=Path(root);current=export_bundle(root)
    if not isinstance(bundle,dict) or bundle.get('format')!=FORMAT or bundle.get('version')!=VERSION:raise ValueError('不支持的设置包格式或版本')
    if len(json.dumps(bundle,ensure_ascii=False).encode())>2800000:raise ValueError('设置包最多2.8MB')
    cfg=bundle.get('settings');groups=bundle.get('groups');prompts=bundle.get('prompts')
    if not isinstance(cfg,dict) or not isinstance(groups,dict) or len(groups)>100:raise ValueError('设置或群配置格式无效')
    if set(cfg)-set(current['settings']):raise ValueError('设置包含当前版本不支持的字段')
    cfg={**current['settings'],**cfg}
    # Binary/env file paths belong to this installation; never take paths from uploads.
    for key in ('group_id','codex_exe','mcp_env_file'):cfg[key]=current['settings'][key]
    if not connection:
        from napcat_connection import DEFAULTS as CONNECTION_DEFAULTS
        for key in CONNECTION_DEFAULTS:cfg[key]=current['settings'][key]
    baseline={**current['settings'],'group_id':cfg['group_id']}
    cfg=Validator(root,baseline).validate(cfg)
    writes={root/'settings.json':json.dumps(cfg,ensure_ascii=False,indent=2)}
    for field,folder in [('global_members','runtime-shared'),('private_members','runtime-private')]:
        if field not in bundle:continue
        members=bundle[field]
        if not isinstance(members,dict) or len(members)>10000:raise ValueError('共享/私聊标记格式无效')
        for account,profile in members.items():
            if not re.fullmatch(r'[1-9]\d{4,19}',account) or not isinstance(profile,dict):raise ValueError('群友标记账号无效')
            for key in ('manual_label','auto_label','model_label'):
                if profile.get(key,'') not in {'','human','owner','AI','bot'}:raise ValueError('群友账号类型无效')
            for key in ('manual_blocked','model_blocked'):
                if key in profile and type(profile[key]) is not bool:raise ValueError('屏蔽标记无效')
            if 'aliases' in profile and (not isinstance(profile['aliases'],list) or any(not isinstance(value,str) for value in profile['aliases'])):raise ValueError('昵称格式无效')
        content={'members':members}
        if field=='global_members':
            scopes=bundle.get('member_scopes',{})
            if not isinstance(scopes,dict) or len(scopes)>10000 or any(not re.fullmatch(r'[1-9]\d{4,19}',account) or not isinstance(mode,str) or mode not in {'global','local'} for account,mode in scopes.items()):raise ValueError('群友标记范围无效')
            content['scopes']=scopes
        writes[root/folder/'member-labels.json']=json.dumps(content,ensure_ascii=False,indent=2)
    def add_prompts(folder,values,global_scope=False):
        if not isinstance(values,dict) or set(values)!=set(PROMPTS):raise ValueError('人设与规则字段不完整')
        for key,value in values.items():
            if value is not None and (not isinstance(value,str) or len(value)>100000):raise ValueError('人设或规则最多100000字')
            if global_scope and value is None:raise ValueError('全局提示词不能为null')
            writes[folder/(key+'.txt')]=value
    add_prompts(root,prompts,True)
    summaries=[]
    for identity,row in groups.items():
        if not re.fullmatch(r'[1-9]\d{0,18}',identity) or not isinstance(row,dict):raise ValueError('群配置编号无效')
        overrides=row.get('settings');members=row.get('members',{})
        if not isinstance(overrides,dict) or set(overrides)&GLOBAL_ONLY or set(overrides)-set(cfg):raise ValueError('群设置包含无效或仅全局可用字段')
        Validator(root,{**cfg,'group_id':int(identity)}).validate({**cfg,**overrides,'group_id':int(identity)})
        if not isinstance(members,dict) or len(members)>10000:raise ValueError('群友标记格式无效')
        for account,profile in members.items():
            if not re.fullmatch(r'[1-9]\d{4,19}',account) or not isinstance(profile,dict):raise ValueError('群友标记账号无效')
            for key in ('manual_label','auto_label','model_label'):
                if profile.get(key,'') not in {'','human','owner','AI','bot'}:raise ValueError('群友账号类型无效')
            if 'aliases' in profile and (not isinstance(profile['aliases'],list) or any(not isinstance(alias,str) for alias in profile['aliases'])):raise ValueError('群友额外昵称格式无效')
            for key in ('manual_blocked','model_blocked'):
                if key in profile and type(profile[key]) is not bool:raise ValueError('群友屏蔽标记必须为布尔值')
        scope=root/'group-config'/identity;folder=runtime(root,int(identity),cfg)
        writes[scope/'settings.json']=json.dumps(overrides,ensure_ascii=False,indent=2)
        add_prompts(scope,row.get('prompts'))
        # Preserve evidence buffers, replacing only the exported member profiles.
        writes[folder/'member-labels.json']=json.dumps({**read_json(folder/'member-labels.json'),'members':members},ensure_ascii=False,indent=2)
        if type(row.get('no_cooldown',False)) is not bool:raise ValueError('无冷却开关无效')
        writes[folder/'NO_COOLDOWN']='' if row.get('no_cooldown') else None
        summaries.append({'group':identity,'overrides':len(overrides),'members':len(members)})
    # Remove obsolete per-group overrides so old local settings cannot shadow an import.
    for identity in set(current['groups'])-set(groups):
        scope=root/'group-config'/identity
        for name in ('settings.json','persona.txt','reply_protocol.txt'):writes[scope/name]=None
    if connection:
        values=bundle.get('connection',{})
        if not isinstance(values,dict) or set(values)-{'NAPCAT_HOST','NAPCAT_TOKEN'}:raise ValueError('连接配置格式无效')
        if any(not isinstance(value,str) or '\n' in value or '\r' in value or len(value)>4096 for value in values.values()):raise ValueError('连接配置值无效')
        if 'NAPCAT_HOST' in values:
            from urllib.parse import urlsplit
            if urlsplit(values['NAPCAT_HOST']).scheme not in {'http','https'}:raise ValueError('NapCat地址必须是HTTP/HTTPS地址')
        env_path=project_path(cfg['mcp_env_file'],root)
        original=env_path.read_text(encoding='utf-8') if env_path.exists() else ''
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'.env';target.write_text(original,encoding='utf-8')
            for key,value in values.items():set_key(str(target),key,value)
            writes[env_path]=target.read_text(encoding='utf-8')
    return writes,{'global_changes':[key for key in cfg if cfg[key]!=current['settings'].get(key)],
        'enabled_groups':cfg['chat_group_whitelist'],'groups':summaries,'connection_restored':bool(connection),
        'preserved_local_paths':['group_id','codex_exe','mcp_env_file'],'expected':fingerprint(current),'selected':cfg['chat_group_whitelist'][0]}


def preview(root,bundle,connection=False):return plan(root,bundle,connection)[1]


def apply_bundle(root,bundle,expected,connection=False):
    root=Path(root)
    with locked(root/'settings-transfer.lock'):
        writes,summary=plan(root,bundle,connection)
        if not expected or summary['expected']!=expected:raise ValueError('设置已发生变化，请重新预览后导入')
        originals={path:path.read_bytes() if path.exists() else None for path in writes}
        backup=root/'config-backups'/('bundle-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3))
        backup.mkdir(parents=True)
        (backup/'previous-settings.json').write_text(json.dumps(export_bundle(root),ensure_ascii=False,indent=2),encoding='utf-8')
        # Numbered backup files cannot escape the backup folder, even for an env path.
        manifest=[]
        for index,(path,raw) in enumerate(originals.items()):
            name=str(index)+'.bin';manifest.append({'path':str(path),'file':name if raw is not None else None})
            if raw is not None:(backup/name).write_bytes(raw)
        (backup/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        try:
            # Apply the whitelist last so the supervisor sees all dependencies together.
            ordered=sorted(writes,key=lambda path:path==root/'settings.json')
            for path in ordered:
                value=writes[path]
                if value is None:path.unlink(missing_ok=True)
                else:path.parent.mkdir(parents=True,exist_ok=True);atomic_write_text(path,value)
            from windows_image_cleanup import sync_tasks
            sync_tasks(root)
        except Exception:
            for path,raw in originals.items():
                if raw is None:path.unlink(missing_ok=True)
                else:atomic_write_text(path,raw.decode('utf-8'))
            try:
                from windows_image_cleanup import sync_tasks
                sync_tasks(root,force=True)
            except Exception:pass
            raise
    return {'ok':True,'selected':summary['selected'],'backup':str(backup),
        'message':'设置已导入并备份；请退出控制台后重新启动，以加载主群、连接和界面设置'}
