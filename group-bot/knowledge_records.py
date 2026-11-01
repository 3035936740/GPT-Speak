"""Extract named JSON records as source text, never as executable data."""
import json
import re
import unicodedata


def normalize(value):return unicodedata.normalize('NFKC',str(value)).strip().casefold()


def records(content,anchors):
    if not anchors or not str(content).lstrip().startswith(('{','[')):return []
    try:root=json.loads(content)
    except (ValueError,RecursionError):return []
    found=[];pending=[([],root)]
    while pending:
        path,value=pending.pop()
        if isinstance(value,dict):
            named=any(isinstance(child,str) and normalize(child) in anchors for child in value.values())
            if named:
                try:found.append((path,json.dumps(value,ensure_ascii=False,separators=(',',':'))))
                except (ValueError,RecursionError):pass
                continue
            children=[]
            for key,child in value.items():
                if normalize(key) in anchors and isinstance(child,(dict,list)):
                    try:found.append((path+[str(key)],json.dumps({key:child},ensure_ascii=False,separators=(',',':'))))
                    except (ValueError,RecursionError):pass
                elif isinstance(child,(dict,list)):children.append((path+[str(key)],child))
            pending.extend(reversed(children))
        elif isinstance(value,list):
            pending.extend(reversed([(path+[str(index)],child) for index,child in enumerate(value) if isinstance(child,(dict,list))]))
    return found


def specific_anchors(tokens,frequencies,total):
    return {word for word in tokens if re.fullmatch(r'(?=.*[a-z])[a-z0-9_]{3,40}',word)
        and 0<frequencies.get(word,0)<=max(3,total*.04)}
