"""Validated private defaults and contact overrides, independent of group settings."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time


def field(category, label, help, runtime, kind='boolean', *, default=None, low=None, high=None, options=None, shared=False):
    return dict(category=category, label=label, help=help, runtime=runtime, type=kind,
                default=default, min=low, max=high, options=options, shared=shared)


CATEGORIES = [('private', '私信'), ('access', '白名单与黑名单'), ('identity', '主人身份'), ('model', '模型'), ('member', '联系人资料：模型参考'),
              ('reply', '接话风格'), ('image', '识图'), ('knowledge', '知识库'),
              ('memory', '记忆与上下文'), ('limits', '额度'), ('debug', '界面与记录')]
FIELDS = {
    'access_mode': field('access', '私聊接入模式', '默认全部已加好友的授权群群友；黑名单始终优先。陌生人和临时会话不接入。', 'private_access_mode', 'string', options={'all':'全部授权群好友（排除黑名单）','whitelist':'仅白名单内的授权群好友'}, shared=True),
    'user_whitelist': field('access', '私聊白名单', '点击编辑，用＋逐项添加QQ号、−删除；仅白名单模式下生效。', 'private_user_whitelist', 'qq_list', default=[], shared=True),
    'user_blacklist': field('access', '私聊黑名单', '点击编辑，用＋逐项添加QQ号、−删除；禁止这些账号的自动回复和手动发送。', 'private_user_blacklist', 'qq_list', default=[], shared=True),
    'reply_enabled': field('private', '自动回复', '私聊默认或当前好友的自动回复开关；仍受页面顶部的私信总开关限制。', 'private_reply_enabled', default=True),
    'reply_cooldown_seconds': field('private', '私信回复冷却（秒）', '连续消息在冷却后合并处理；0表示不等待。', 'private_reply_cooldown_seconds', 'integer', low=0, high=300),
    'max_reply_chars': field('private', '私信回复字数上限', '每条文字回复的长度上限。', 'private_max_reply_chars', 'integer', low=20, high=2000),
    'model': field('model', '回复模型', '使用本机现有模型接入；只影响私聊回复。', 'model', 'string'),
    'reasoning_effort': field('model', '思考等级', '须与所选模型支持的等级匹配。', 'reasoning_effort', 'string', options=['minimal', 'low', 'medium', 'high', 'xhigh']),
    'fast_mode': field('model', 'Fast 模式', '需要所选模型支持 Fast。', 'fast_mode'),
    'text_transport': field('model', '文本模型传输方式', '沿用本机已有登录和模型传输，不填写API密钥。', 'text_transport', 'string', options=['http', 'default']),
    'model_timeout_seconds': field('model', '文字回复超时（秒）', '私聊文字生成的等待时间。', 'model_timeout_seconds', 'integer', low=1, high=7200),
    'use_group_impressions': field('member', '参考此人的群聊印象', '仅参考当前好友本人的有来源印象。', 'private_use_group_impressions'),
    'avatar_query_enabled': field('member', '按需查询头像', '默认开启，仅当前明确询问或使用头像时读取；还需开启私聊识图。与后台头像显示独立。', 'model_avatar_query_enabled', default=True),
    'profile_query_enabled': field('member', '按需查询公开简介', '默认开启，仅对象明确的简介、签名等请求查询；接口未提供时不编造。', 'model_profile_query_enabled', default=True),
    'reference_aliases': field('member', '参考联系人别名', '允许模型参考联系人标记中的额外昵称。', 'member_reference_aliases_enabled'),
    'reference_note': field('member', '参考联系人说明', '允许模型参考联系人标记中的说明。', 'member_reference_note_enabled'),
    'high_eq_enabled': field('reply', '高情商接话', '理解倾诉和夸奖，避免客服式解释或被吐槽后顶嘴。', 'high_eq_enabled'),
    'peer_reply_enabled': field('reply', '自然接话风格', '顺着当前好友的话题自然接话，减少分析玩梗动机。', 'peer_reply_enabled'),
    'friendly_tease_probability': field('reply', '朋友间玩笑偏好', '0关闭主动调侃；其余为模型参考的语气偏好。', 'friendly_tease_probability', 'number', low=0, high=1),
    'vision_enabled': field('image', '私聊识图', '读取此好友新私信中的图片，结合文字回答；关闭时不下载、不附图给模型。', 'private_vision_enabled', default=True),
    'max_images_per_call': field('image', '单次识图数量上限（张）', '选择本轮最新的图片；动图读取首帧。', 'max_images_per_call', 'integer', low=1, high=4),
    'max_image_bytes': field('image', '单张图片大小上限（字节）', '超大或损坏图片跳过并写入执行日志；默认8MiB。', 'max_image_bytes', 'integer', low=1024, high=16777216),
    'vision_timeout_seconds': field('image', '识图超时（秒）', '附图回复在文字超时之外增加此预算。', 'vision_timeout_seconds', 'integer', low=1, high=7200),
    'knowledge_enabled': field('knowledge', '检索当前私聊知识库', '只检索当前好友的专属知识。', '_private_knowledge_enabled', default=True),
    'knowledge_max_chars': field('knowledge', '知识库检索字符预算', '仍受模型总输入长度限制；过长条目使用相关片段。', 'knowledge_max_chars', 'integer', low=200, high=12000),
    'memory_enabled': field('memory', '参考近期私聊记录', '关闭后仅提供最新一条消息；本地聊天记录仍可查看。', '_private_memory_enabled', default=True),
    'context_messages': field('memory', '传入模型的近期消息条数', '只读取当前好友，最多100条。', 'context_messages', 'integer', low=1, high=100),
    'context_message_chars': field('memory', '历史单条消息字符上限', '裁减历史长消息；最新消息保留完整文字。', 'context_message_chars', 'integer', low=100, high=2000),
    'max_prompt_chars': field('memory', '模型输入字符上限', '空间不足时依次裁减历史、印象和知识；不能容纳人设时停止本轮并记日志。', 'max_prompt_chars', 'integer', low=1000, high=100000),
    'max_replies_per_hour': field('limits', '全部私信每小时回复上限', '所有好友共享，包含手动发送；仅可在私聊默认中设置。', 'private_max_replies_per_hour', 'integer', low=1, high=10000, shared=True),
    'max_model_calls_per_day': field('limits', '全部私信每日模型调用上限', '所有好友共享；仅可在私聊默认中设置。', 'private_max_model_calls_per_day', 'integer', low=1, high=10000, shared=True),
    'console_max_records': field('debug', '每类显示记录上限（条）', '聊天和执行日志分别显示；本地仍最多保留100条聊天和1000条其他事件。', 'console_max_records', 'integer', low=1, high=1000),
    'console_max_text_chars': field('debug', '每类显示文本上限（字符）', '聊天和执行日志分别计算，超出窗口的旧记录暂不显示。', 'console_max_text_chars', 'integer', low=500, high=1000000),
}


def defaults(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_profiles'").fetchone():
        return {}
    row = db.execute("SELECT settings FROM private_profiles WHERE peer='*'").fetchone()
    return json.loads(row[0]) if row else {}


def values(cfg, default_values=None, overrides=None):
    base = {key: cfg.get(meta['runtime'], meta['default']) for key, meta in FIELDS.items()}
    # This preference sits below the global private-service master switch.
    base['reply_enabled'] = True
    base['console_max_records'] = min(base['console_max_records'], 1000)
    inherited={key:value for key,value in (default_values or {}).items() if key in FIELDS and not FIELDS[key]['runtime'].startswith('private_')}
    return {**base, **inherited, **(overrides or {})}


def apply(cfg, row):
    settings = values(cfg, row.get('defaults'), row['settings'])
    result = {**cfg, **{meta['runtime']: settings[key] for key, meta in FIELDS.items()}}
    result['private_reply_enabled'] = cfg['private_reply_enabled'] and settings['reply_enabled'] and not row['blocked']
    return result


def validate(key, value):
    if key not in FIELDS:
        raise ValueError('未知私聊设置')
    meta = FIELDS[key]
    kind = meta['type']
    if kind == 'boolean' and type(value) is not bool:
        raise ValueError('开关必须为布尔值')
    if kind == 'qq_list':
        from private_access import normalize_ids
        normalize_ids(value)
    if kind == 'textarea' and (not isinstance(value, str) or len(value)>1000):
        raise ValueError('提示词最多1000字')
    if kind in {'integer', 'number'}:
        if type(value) not in ({int} if kind == 'integer' else {int, float}) or not meta['min'] <= value <= meta['max']:
            raise ValueError(meta['label'] + '范围为' + str(meta['min']) + '～' + str(meta['max']))
    if kind == 'string' and (not isinstance(value, str) or not 1 <= len(value) <= 101):
        raise ValueError(meta['label'] + '必须为短文本')
    if meta['options'] and value not in meta['options']:
        raise ValueError('无效选项')
    if key == 'model' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', value):
        raise ValueError('模型名称无效')


def save(root, peer, scope, key, value=None, inherit=False):
    from console_config import Config
    from private_profiles import profile
    from private_view import contacts
    from private_events import emit
    if scope not in {'default', 'contact'} or key not in FIELDS or type(inherit) is not bool:
        raise ValueError('私聊设置范围或字段无效')
    if scope == 'contact' and (str(peer) not in contacts(root) or FIELDS[key]['shared']):
        raise ValueError('该设置只能修改私聊默认，或联系人不在授权群好友列表中')
    if not inherit:
        if FIELDS[key]['type'] == 'qq_list':
            from private_access import normalize_ids
            value = normalize_ids(value)
        validate(key, value)
    if scope=='default' and FIELDS[key]['runtime'].startswith('private_'):
        from console_config import DEFAULTS
        runtime_key=FIELDS[key]['runtime']
        selected=DEFAULTS[runtime_key] if inherit else value
        Config(root).set(runtime_key,selected if isinstance(selected,str) else json.dumps(selected))
        return {'ok':True,'message':'私聊默认已保存到配置文件'}
    identity = '*' if scope == 'default' else str(peer)
    with closing(sqlite3.connect(Path(root) / 'runtime-private/private-chat.sqlite3', timeout=5)) as db, db:
        row = profile(db, identity)
        previous = dict(row['settings'])
        next_values = dict(previous)
        if inherit:
            next_values.pop(key, None)
        else:
            next_values[key] = value
        # Match the existing model validation when a catalog is available.
        if key in {'model', 'reasoning_effort', 'fast_mode'}:
            from configure_launch import validate as validate_model
            import os
            catalog_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'models_cache.json'
            if catalog_path.exists():
                effective = values(Config(root).load(), {} if identity == '*' else row['defaults'], next_values)
                validate_model(json.loads(catalog_path.read_text(encoding='utf-8-sig')),
                               effective['model'], effective['reasoning_effort'], effective['fast_mode'])
        db.execute('INSERT INTO private_profile_backups VALUES (?,?,?,?)',
                   (identity, 'settings', json.dumps(previous, ensure_ascii=False), time.time()))
        db.execute('INSERT INTO private_profiles(peer,settings) VALUES (?,?) ON CONFLICT(peer) DO UPDATE SET settings=excluded.settings',
                   (identity, json.dumps(next_values, ensure_ascii=False)))
        if identity != '*':
            emit(db, identity, 'stage', '私聊设置已更新：' + FIELDS[key]['label'], details={'local_only': True})
    return {'ok': True, 'message': ('私聊默认' if scope == 'default' else '当前好友') + ('已恢复继承' if inherit else '设置已保存')}
