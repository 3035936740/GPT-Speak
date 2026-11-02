"""Separate a lexical name match, the recipient, and the subject of a sentence."""
import re
from group_identity import name_mentioned

GENERIC={'gpt','chatgpt','ai','bot','机器人','模型'}
INSTRUCTION=('\n点名与对象：reference_context中的名字命中只是线索，不等于收件人或话题对象已确认。'
    '分别判断“这句话对谁说”和“句子里的名字指谁”：GPT/AI/模型可能是产品、类别、别的账号，也可能是在叫你。'
    '“没用过GPT/订阅GPT/用GPT写代码/与其他工具比较”通常是在聊工具，不是这个QQ账号被用过或被截图点名。'
    '原生@只说明被提及：结合提问、求助、称呼、引用和前后发言判断是否需要回应；名单、转述、顺带通知可不回，不为被点名硬接话。'
    '普通水群可以作为旁观者自然接话，但不认领别人的话，不说对方正在使用你，不自辩自己被点名。'
    '敏感度是语境匹配倾向，不是随机回复概率，不代替当前原话的证据。\n')


def plain(item):
    text=item.get('_forward_outer_text','') if item.get('_forward') else item.get('text','')
    return re.sub(r'https?://\S+|\[[^\]]*\]',' ',str(text))


def evidence(cfg,item):
    text=plain(item)
    names=list(dict.fromkeys([*item.get('_unique_bot_names',[]),*item.get('_ambiguous_bot_names',[]),
        *item.get('_self_names',[]),cfg.get('bot_name',''),*cfg.get('bot_aliases',[])]))
    matches=[name for name in names if name and name_mentioned(text,name)]
    if not matches and not item.get('at_bot'):return {}
    if not matches:return {'native_at':bool(item.get('_native_at_bot',item.get('at_bot'))),'at_sensitivity':float(cfg.get('at_context_sensitivity',.95))}
    product=bool(re.search(r'(?:没|未|从没|没有|不)?(?:用过|使用|用到|用上)|订阅|客户端|官网|API|额度|调用|模型|工具|写代码',text,re.I)
        and any(name.casefold() in GENERIC or re.match(r'^gpt(?:[ -]?\d)',name,re.I) for name in matches))
    vocative=any(re.search(r'^\s*(?:@\d+\s*)?'+re.escape(name)+r'\s*(?:[，,:：！!]|你|在吗|说话|说句|帮我|给我|醒醒|出来)',text,re.I) for name in matches)
    score=.95 if vocative else .1 if product else .3 if matches and all(name.casefold() in GENERIC for name in matches) else .65
    sensitivity=float(cfg.get('name_match_sensitivity',.5))
    return {'matched_names':matches[:4],'lexical_match_only':True,'product_usage_hint':product,'vocative_hint':vocative,
        'name_match_score':score,'name_match_threshold':round(.8-.5*sensitivity,3),'name_sensitivity':sensitivity,
        'at_sensitivity':float(cfg.get('at_context_sensitivity',.95)),
        'native_at':bool(item.get('_native_at_bot',item.get('at_bot'))),
        'note':'收件人与名字指代分别判断；产品用法只作语境线索，不把别名出现升级为QQ原生@'}


def bind(cfg,target):
    packet=evidence(cfg,target)
    if packet:target['_reference_context']=packet
    else:target.pop('_reference_context',None)
    return packet


def quality_issue(text,target):
    packet=target.get('_reference_context',{})
    analysis=target.get('_role_analysis',{}).get('context',{})
    topic=analysis.get('reference_topic')
    product=topic in {'product_or_class','other'} or (topic!='self' and packet.get('product_usage_hint'))
    if product and re.search(r'(?:我|自己).{0,8}(?:被|遭).{0,8}(?:点名|截图|叫到)|(?:你|对方).{0,6}(?:在叫我|点(?:了|的)?我)',str(text)):
        return 'wrong_self_reference'
    return ''
