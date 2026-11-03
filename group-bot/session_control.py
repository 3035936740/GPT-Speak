"""Per-group cancellation boundary, independent of process restarts."""
import secrets
import time
from pathlib import Path
from console_config import Config,runtime,read_json,atomic
from image_archive import locked


def state(folder):return read_json(Path(folder)/'session-control.json')


def request(root,group,mode):
    if group not in Config(root).load()['chat_group_whitelist']:raise ValueError('该群不在白名单内')
    value=interrupt(runtime(root,group),mode)
    messages={'clear':'已清空当前群短期上下文','stop':'当前群会话已终止并暂停接话，恢复后只处理新消息','resume':'当前群会话已恢复，只处理新消息'}
    return {'ok':True,'selected':group,'message':messages[mode],'request':value}


def interrupt(folder,mode='stop'):
    if mode not in {'stop','clear','resume','interrupt'}:raise ValueError('会话操作为stop、resume或clear')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    with locked(folder/'session-control.lock'):
        previous=state(folder);stamp=time.time()
        value={'id':secrets.token_hex(12),'mode':mode,'created':stamp,
            'paused':True if mode=='stop' else False if mode=='resume' else previous.get('paused',False),
            'clear_before':stamp if mode=='clear' else previous.get('clear_before',0)}
        atomic(folder/'session-control.json',value)
    return value


def allows(folder,target):
    control=state(folder)
    if control.get('paused'):return False
    cutoff=float(control.get('created',0))
    return not cutoff or max(float(target.get('_time',0)),float(target.get('_manual_task_started',0)))>cutoff
