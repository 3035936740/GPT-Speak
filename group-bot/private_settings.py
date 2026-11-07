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


CATEGORIES = [('private', '私信'), ('access', '白名单与黑名单'), ('identity', 'Bot身份'), ('model', '模型'), ('member', '联系人资料：模型参考'),
              ('reply', '接话风格'), ('image', '识图'), ('knowledge', '知识库'),
              ('memory', '记忆与上下文'), ('limits', '额度'), ('debug', '界面与记录')]
FIELDS = {
    'history_fetch_enabled': field('private', '拉取远端私聊历史（弃用）', '默认关闭。优先使用每次实时收到并保存到本机的聊天记录，重启后继续使用本地对话窗口；开启仅兼容旧历史拉取方式，会额外查询QQ。', 'private_history_fetch_enabled', default=False),
    'access_mode': field('access', '私聊接入模式', '默认全部已加好友的授权群群友；黑名单始终优先。陌生人和临时会话不接入。', 'private_access_mode', 'string', options={'all':'全部授权群好友（排除黑名单）','whitelist':'仅白名单内的授权群好友'}, shared=True),
    'user_whitelist': field('access', '私聊白名单', '点击编辑，用＋逐项添加QQ号、−删除；仅白名单模式下生效。', 'private_user_whitelist', 'qq_list', default=[], shared=True),
    'user_blacklist': field('access', '私聊黑名单', '点击编辑，用＋逐项添加QQ号、−删除；禁止这些账号的自动回复和手动发送。', 'private_user_blacklist', 'qq_list', default=[], shared=True),
    'reply_enabled': field('private', '自动回复', '私聊默认或当前好友的自动回复开关；仍受页面顶部的私信总开关限制。', 'private_reply_enabled', default=True),
    'reply_cooldown_seconds': field('private', '私信回复冷却（秒）', '连续消息在冷却后合并处理；0表示不等待。', 'private_reply_cooldown_seconds', 'integer', low=0, high=300),
    'max_reply_chars': field('private', '私信回复字数上限', '每条文字回复的长度上限。', 'private_max_reply_chars', 'integer', low=20, high=2000),
    'model': field('model', '回复模型', '使用本机现有模型接入；只影响私聊回复。', 'model', 'string'),
    'decision_model': field('model', '决策与执行模型', '用于私聊分工分析与记忆提取。填模型名称，或填 inherit_reply 沿用私聊回复模型。', 'decision_model', 'string', default='inherit_reply'),
    'decision_reasoning_effort': field('model', '决策思考等级', '独立选择决策模型时使用。', 'decision_reasoning_effort', 'string', default='low', options=['none','minimal','low','medium','high','xhigh','max','ultra']),
    'decision_fast_mode': field('model', '决策快速服务模式', '默认关闭，单独控制私聊分工分析和记忆提取的 Fast 档位。', 'decision_fast_mode', default=False),
    'reasoning_effort': field('model', '思考等级', '须与所选模型支持的等级匹配。', 'reasoning_effort', 'string', options=['minimal', 'low', 'medium', 'high', 'xhigh']),
    'fast_mode': field('model', '回复快速服务模式', '仅控制私聊回复，所选回复模型须支持 Fast。', 'fast_mode'),
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
    'image_summary_level': field('image', '图片摘要等级', '默认简略；平衡尽量完整识别可见文字和细节；详细另列可能意图，推测与事实分开。只控制图片理解深度，不要求每条回复逐项描述。', 'image_summary_level', 'string', default='brief', options={'brief':'简略','balanced':'平衡','detailed':'详细'}),
    'max_images_per_call': field('image', '单次识图数量上限（张）', '选择本轮最新的图片；动图读取首帧。', 'max_images_per_call', 'integer', low=1, high=4),
    'max_image_bytes': field('image', '单张图片大小上限（字节）', '超大或损坏图片跳过并写入执行日志；默认8MiB。', 'max_image_bytes', 'integer', low=1024, high=16777216),
    'vision_timeout_seconds': field('image', '识图超时（秒）', '附图回复在文字超时之外增加此预算。', 'vision_timeout_seconds', 'integer', low=1, high=7200),
    'knowledge_enabled': field('knowledge', '检索当前私聊知识库', '只检索当前好友的专属知识。', '_private_knowledge_enabled', default=True),
    'knowledge_retrieval_mode': field('knowledge','知识库检索模式','关键词索引为默认；向量和混合模式使用全局 Embedding 设置。','knowledge_retrieval_mode','string',default='keyword',options={'keyword':'关键词索引','vector':'向量 RAG','hybrid':'关键词＋向量 RAG'}),
    'knowledge_max_chars': field('knowledge', '知识库检索字符预算', '0表示全部匹配、不设独立检索上限；提供给模型时仍受总输入限制。', 'knowledge_max_chars', 'integer', low=0, high=12000),
    'memory_enabled': field('memory', '参考近期私聊记录', '关闭后仅提供最新一条消息；本地聊天记录仍可查看。', '_private_memory_enabled', default=True),
    'context_messages': field('memory', '传入模型的近期消息条数', '只读取当前好友，最多100条。', 'context_messages', 'integer', low=1, high=100),
    'context_message_chars': field('memory', '历史单条消息字符上限', '裁减历史长消息；最新消息保留完整文字。', 'context_message_chars', 'integer', low=100, high=2000),
    'max_prompt_chars': field('memory', '模型输入字符上限', '原默认19200，范围0～2147483647。0表示不设本地字符上限，仍受模型上下文容量及历史选取规则限制。', 'max_prompt_chars', 'integer', low=0, high=2147483647),
    'max_replies_per_hour': field('limits', '全部私信每小时回复上限', '所有好友共享，包含手动发送；仅可在私聊默认中设置。', 'private_max_replies_per_hour', 'integer', low=1, high=10000, shared=True),
    'max_model_calls_per_day': field('limits', '全部私信每日模型调用上限', '所有好友共享；仅可在私聊默认中设置。', 'private_max_model_calls_per_day', 'integer', low=1, high=10000, shared=True),
    'console_max_records': field('debug', '每类显示记录上限（条）', '聊天和执行日志分别显示；本地仍最多保留100条聊天和1000条其他事件。', 'console_max_records', 'integer', low=1, high=1000),
    'show_avatars': field('debug','私聊显示真实头像','好友列表和聊天气泡显示头像；联网仍受全局头像开关控制。','_private_display_avatars',default=True),
    'chat_image_display': field('debug','聊天图片默认显示','与群聊相同：双击气泡可临时切换，点击查看原图。','chat_image_display','string',options={'image':'图片 / 表情包','text':'文字摘要'}),
    'long_memory_enabled': field('memory','参考私聊长期记忆','独立记录当前好友的学习来源，参考可编辑记忆；不混入其他好友的私聊。','_private_long_memory_enabled',default=True),
    'memory_retrieval_mode': field('memory','私聊记忆检索模式','默认关键词。向量 RAG 与混合检索使用本机 Ollama，只查询当前好友的长期记忆。','private_memory_retrieval_mode','string',default='keyword',options={'keyword':'关键词','vector':'向量 RAG','hybrid':'关键词＋向量 RAG'}),
    'learning_min_messages': field('memory','私聊记忆学习最少消息数','使用与群聊相同的记忆学习流程，仅学习当前好友的来源。','learning_min_messages','integer',low=2,high=100),
    'learning_interval_seconds': field('memory','私聊记忆学习间隔（秒）','后台学习，不阻塞发送；每次计入私聊模型调用额度。','learning_interval_seconds','integer',low=30,high=86400),
    'multi_role_enabled': field('model','上下文与情绪分工分析','按需开启；使用与群聊相同的分析角色，每个分析计入私聊模型额度。','_private_role_analysis_enabled',default=False),
    'role_models': field('model','分工角色模型','在快速填充设置决策模型、决策思考等级和决策 Fast。上下文、情绪和视觉可分别覆盖这些设置。','role_models','mapping',default={}),
    'model_max_attachments': field('image','模型单次参考附件数','超出时只取前面的附件。','model_max_attachments','integer',low=1,high=50),
    'model_max_file_bytes': field('image','模型单个附件上限（字节）','达到该大小拒收，默认5MiB。','model_max_file_bytes','integer',low=1024,high=104857600),
    'console_max_text_chars': field('debug', '每类显示文本上限（字符）', '原默认19200，范围0～2147483647。聊天和执行日志分别计数，0表示不限字符。', 'console_max_text_chars', 'integer', low=0, high=2147483647),
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
    if kind=='mapping':
        from model_routing import validate_role_models
        validate_role_models(value)
        for model in value.values():
            if set(model)-{'model','reasoning_effort','fast_mode'} or any(not isinstance(model[k],str) or len(model[k])>101 for k in model.keys()-{'fast_mode'}):raise ValueError('角色配置仅支持模型、思考等级和 Fast')
            if model.get('reasoning_effort') and model['reasoning_effort'] not in {'none','minimal','low','medium','high','xhigh','max','ultra'}:raise ValueError('思考等级无效')
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
    if key in {'model', 'decision_model'} and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', value):
        raise ValueError('模型名称无效')


def save(root, peer, scope, key, value=None, inherit=False):
    if type(inherit) is not bool or not isinstance(key, str) or key not in FIELDS:
        raise ValueError('私聊设置范围或字段无效')
    if not inherit and value is None:
        raise ValueError('私聊设置缺少值')
    return _save_settings(root, peer, scope, {key: None if inherit else value})


def save_decision_settings(root, peer, scope, changes):
    from model_routing import DECISION_SETTINGS

    if not isinstance(changes, dict) or not changes or set(changes) - set(DECISION_SETTINGS):
        raise ValueError('无效的决策模型设置')
    return _save_settings(root, peer, scope, changes)


def _save_settings(root, peer, scope, changes):
    from console_config import Config
    from private_profiles import profile
    from private_view import contacts
    from private_events import emit
    if scope not in {'default', 'contact'}:
        raise ValueError('私聊设置范围或字段无效')
    if scope == 'contact' and (str(peer) not in contacts(root) or any(FIELDS[key]['shared'] for key in changes)):
        raise ValueError('该设置只能修改私聊默认，或联系人不在授权群好友列表中')
    changes = dict(changes)
    for key, value in changes.items():
        if value is None:
            continue
        if FIELDS[key]['type'] == 'qq_list':
            from private_access import normalize_ids
            changes[key] = normalize_ids(value)
        validate(key, changes[key])
    first_key = next(iter(changes))
    if scope=='default' and FIELDS[first_key]['runtime'].startswith('private_'):
        from console_config import DEFAULTS
        runtime_key=FIELDS[first_key]['runtime']
        selected=changes[first_key] if changes[first_key] is not None else DEFAULTS[runtime_key]
        Config(root).set(runtime_key,selected if isinstance(selected,str) else json.dumps(selected))
        return {'ok':True,'message':'私聊默认已保存到配置文件'}
    identity = '*' if scope == 'default' else str(peer)
    with closing(sqlite3.connect(Path(root) / 'runtime-private/private-chat.sqlite3', timeout=5)) as db, db:
        row = profile(db, identity)
        previous = dict(row['settings'])
        next_values = dict(previous)
        for key, value in changes.items():
            if value is None:
                next_values.pop(key, None)
            else:
                next_values[key] = value
        # Match the existing model validation when a catalog is available.
        from model_routing import DECISION_SETTINGS
        if changes.keys() & {'model', 'reasoning_effort', 'fast_mode', *DECISION_SETTINGS}:
            from configure_launch import validate as validate_model
            from model_routing import validate_decision_models
            import os
            catalog_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'models_cache.json'
            if catalog_path.exists():
                effective = values(Config(root).load(), {} if identity == '*' else row['defaults'], next_values)
                catalog = json.loads(catalog_path.read_text(encoding='utf-8-sig'))
                validate_model(catalog, effective['model'], effective['reasoning_effort'], effective['fast_mode'])
                validate_decision_models(catalog, effective)
        db.execute('INSERT INTO private_profile_backups VALUES (?,?,?,?)',
                   (identity, 'settings', json.dumps(previous, ensure_ascii=False), time.time()))
        db.execute('INSERT INTO private_profiles(peer,settings) VALUES (?,?) ON CONFLICT(peer) DO UPDATE SET settings=excluded.settings',
                   (identity, json.dumps(next_values, ensure_ascii=False)))
        if identity != '*':
            labels = '、'.join(FIELDS[key]['label'] for key in changes)
            emit(db, identity, 'stage', '私聊设置已更新：' + labels, details={'local_only': True})
    restored = all(value is None for value in changes.values())
    return {'ok': True, 'message': ('私聊默认' if scope == 'default' else '当前好友') + ('已恢复继承' if restored else '设置已保存')}
