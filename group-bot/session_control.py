"""Per-group cancellation boundary, independent of process restarts."""
import secrets
import time
from pathlib import Path
from console_config import Config,runtime,read_json,atomic
from image_archive import locked


def state(folder):return read_json(Path(folder)/'session-control.json')


def request(root,group,mode):
    if group not in Config(root).load()['chat_group_whitelist']:raise ValueError('该群不在白名单内')
    if mode not in {'stop','clear'}:raise ValueError('会话操作为stop或clear')
    folder=runtime(root,group);folder.mkdir(parents=True,exist_ok=True)
    with locked(folder/'session-control.lock'):
        previous=state(folder);stamp=time.time()
        value={'id':secrets.token_hex(12),'mode':mode,'created':stamp,
            'clear_before':stamp if mode=='clear' else previous.get('clear_before',0)}
        atomic(folder/'session-control.json',value)
    return {'ok':True,'selected':group,'message':'已请求清空上下文并终止当前会话，等待新消息' if mode=='clear' else '已请求终止当前会话，等待新消息','request':value}


def allows(folder,target):
    cutoff=float(state(folder).get('created',0))
    return not cutoff or float(target.get('_time',0))>cutoff
