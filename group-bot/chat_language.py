"""Contextual shorthand hints, without a slang dictionary or extra model calls."""
import re
import unicodedata

KEY='pinyin_abbreviation_guess_enabled'
INSTRUCTION=('\n拼音缩写：shorthand_context仅标出可能的首字母/圈内缩写，不是释义词典，也不是辱骂证据。'
    '结合当前原话、引用对象、同一人的前后发言理解，可考虑重复音节、粗口、挑衅、亲昵玩笑、英文或技术名词等多种解释。'
    '区分可能的展开与交际意图；拿不准就保持不确定，不把自己的猜测当对方确实说过的原话。'
    '粗口可能是朋友互损，不因猜测升级冲突、训人、屏蔽或修改账号标记。'
    '正常接话不必翻译或解释缩写；没懂时可以短回、用合适表情或旁观，确需释义才简短确认，不逢缩写必追问。'
    '不要没读懂就夸“这句是在认真接话”“终于好好说话”或自夸自己接住了。\n')
READING_SCHEMA={'type':'object','properties':{
    'surface':{'type':'string','maxLength':24},'possible_reading':{'type':'string','maxLength':80},
    'speech_act':{'type':'string','enum':['friendly_tease','hostile_or_vulgar','question','technical','unclear']},
    'confidence':{'type':'string','enum':['low','medium','high']},
    'evidence_message_ids':{'type':'array','maxItems':4,'items':{'type':'string','maxLength':60}}},
    'required':['surface','possible_reading','speech_act','confidence','evidence_message_ids'],'additionalProperties':False}


def candidates(text,names=()):
    text=unicodedata.normalize('NFKC',str(text or ''))
    text=re.sub(r'```[\s\S]*?```|`[^`]*`|https?://\S+|@\d+|\[[^\]]*\]',' ',text)
    # Commands/code identifiers and ordinary English sentences are not guessed.
    if re.match(r'^\s*[/!！.#。]',text) or re.search(r'\b(?:def|class|import|SELECT|INSERT)\b|=>|\w+\([^)]*\)',text):return []
    if re.search(r'API|配置|接口|模型|代码|端口|报错|命令|变量|函数|版本|文件',text,re.I):return []
    if re.fullmatch(r'[A-Za-z]+(?:[ ,]+[A-Za-z]+){2,}[.!? ]*',text.strip()):return []
    excluded={str(name).casefold() for name in names if name}
    result=[]
    for match in re.finditer(r'(?<![A-Za-z0-9_./:-])([A-Za-z]{3,18})(?![A-Za-z0-9_./:-])',text):
        raw=match[1];word=raw.lower()
        if word in excluded or sum(c not in 'aeiou' for c in word)/len(word)<.7:continue
        if any(row['normalized']==word for row in result):continue
        row={'surface':raw,'normalized':word}
        for size in range(2,len(word)//2+1):
            if len(word)%size==0 and word[:size]*(len(word)//size)==word:
                row['repeated_unit']=word[:size];break
        result.append(row)
        if len(result)==4:break
    return result


def context(cfg,target):
    if not cfg.get(KEY,True):return {}
    names=[cfg.get('bot_name',''),*cfg.get('bot_aliases',[])]
    for field in ('sender','self','quoted_sender'):
        person=target.get('_identity_snapshot',{}).get(field,{})
        names.extend([person.get('nickname',''),person.get('card',''),*person.get('aliases',[])])
    rows=candidates(target.get('text',''),names)
    if not rows:return {}
    plain=re.sub(r'@\d+|\[[^\]]*\]','',unicodedata.normalize('NFKC',str(target.get('text',''))))
    for row in rows:plain=re.sub(re.escape(row['surface']),'',plain,flags=re.I)
    return {'candidates':rows,'compact_turn':not bool(re.search(r'[\w\u3400-\u9fff]',plain)),
        'note':'仅候选，不代表已解码；同串字母可能有不同意思，先看引用和语气，不自动当善意或辱骂'}


def checked_reading(value,packet,valid_ids):
    if not packet or not isinstance(value,dict) or set(value)!=set(READING_SCHEMA['required']):return None
    if any(not isinstance(value[key],str) for key in ('surface','possible_reading','speech_act','confidence')):return None
    if value['speech_act'] not in READING_SCHEMA['properties']['speech_act']['enum'] or value['confidence'] not in {'low','medium','high'}:return None
    if not isinstance(value['surface'],str) or value['surface'].casefold() not in {row['normalized'] for row in packet['candidates']}:return None
    if not isinstance(value['possible_reading'],str) or len(value['possible_reading'])>80:return None
    ids=value['evidence_message_ids']
    if not isinstance(ids,list) or len(ids)>4 or any(not isinstance(mid,str) or mid not in valid_ids for mid in ids):return None
    result={**value,'evidence_message_ids':list(ids),'tentative':True}
    if not ids or not value['possible_reading'].strip():result['confidence']='low'
    return result


def bind(cfg,target):
    target['_pinyin_guess_enabled']=cfg.get(KEY,True)
    packet=context(cfg,target)
    if packet:target['_shorthand_context']=packet
    else:target.pop('_shorthand_context',None)
    return packet


def quality_issue(text,target):
    if not target.get('_pinyin_guess_enabled',True):return ''
    packet=target.get('_shorthand_context',{})
    if not packet.get('candidates'):return ''
    reading=target.get('_role_analysis',{}).get('context',{}).get('shorthand_reading',{})
    risky=(packet.get('compact_turn') or reading.get('speech_act')=='hostile_or_vulgar')
    if risky:
        for match in re.finditer(r'(?:这回|这句|你这句|终于).{0,16}(?:认真|正常|好好).{0,8}(?:接|说|聊)|'
                r'(?:认真|正常).{0,6}接话|我.{0,8}(?:接住|读懂|懂你).{0,8}(?:了|啦)',str(text)):
            if not re.search(r'没|不|未|并非',match[0]):return 'unsupported_shorthand_affirmation'
    return ''
