"""Independent DM admission: friends only, blacklist first, optional whitelist."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

DEFAULTS = {'private_access_mode': 'all', 'private_user_whitelist': [],
            'private_user_blacklist': []}


def normalize_ids(values):
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError('QQ名单须为最多1000个QQ号的数组')
    result = []
    for value in values:
        if type(value) not in {str, int} or not re.fullmatch(r'[1-9][0-9]{4,14}', str(value)):
            raise ValueError('QQ号须为5～15位正整数')
        if str(value) not in result:
            result.append(str(value))
    return result


def validate(cfg):
    if not isinstance(cfg['private_access_mode'],str) or cfg['private_access_mode'] not in {'all', 'whitelist'}:
        raise ValueError('私聊接入模式须为 all 或 whitelist')
    for key in ('private_user_whitelist', 'private_user_blacklist'):
        cfg[key] = normalize_ids(cfg[key])
    for key in ('private_max_replies_per_hour','private_max_model_calls_per_day'):
        if type(cfg[key]) is not int or not 1<=cfg[key]<=10000: raise ValueError(key+' 范围1～10000')
    if type(cfg['private_max_reply_chars']) is not int or not 20<=cfg['private_max_reply_chars']<=2000: raise ValueError('私聊回复字数范围20～2000')
    if type(cfg['private_reply_cooldown_seconds']) not in {int,float} or not 0<=cfg['private_reply_cooldown_seconds']<=300: raise ValueError('私聊冷却范围0～300秒')
    return cfg


def config(root, base=None, db=None):
    from console_config import Config
    cfg = {**DEFAULTS, **(Config(root).load() if base is None else base)}
    return cfg


def reason(cfg, peer):
    peer = str(peer)
    if peer in set(map(str, cfg.get('private_user_blacklist', []))):
        return '已加入私聊黑名单'
    if cfg.get('private_access_mode', 'all') == 'whitelist' and peer not in set(map(str, cfg.get('private_user_whitelist', []))):
        return '未加入私聊白名单'
    return ''


def allowed(cfg, peer):
    return not reason(cfg, peer)
