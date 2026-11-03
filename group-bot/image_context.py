"""Non-destructive, hash-bound repeated summary references in a chat view."""
import json
from pathlib import Path
import re

SUMMARY = re.compile(r'\[图片摘要[:：][^\]]*\]')
HASH = re.compile(r'[a-f0-9]{64}')


def compress(records, runtime=None):
    """Keep the newest full occurrence so rolling eviction cannot orphan refs.

    Original text is retained locally for recomputing a different view/window;
    callers explicitly select model fields so this backup never enters prompts.
    No sender, timestamp, message or mixed text is removed.
    """
    sources={}
    if runtime:
        try:sources=json.loads((Path(runtime)/'image-memory.json').read_text(encoding='utf-8')).get('sources',{})
        except (OSError,ValueError):pass
    seen={};result=[]
    for original in reversed(list(records)):
        row=dict(original)
        text=str(row.pop('_image_full_text',row.get('text','')))
        keys=row.get('_image_hashes') or row.get('image_hashes') or list(row.get('_image_keys',{}).values())
        identity=str(row.get('_id') or row.get('message_id') or '')
        index=0
        def replace(match):
            nonlocal index
            slot=index;index+=1
            key=keys[slot] if slot<len(keys) else ''
            if not isinstance(key,str) or not HASH.fullmatch(key):return match[0]
            digest=sources.get(key,{}).get('digest') or key
            if digest in seen:
                reference='[同图：'+seen[digest]+']'
                return reference if len(reference)<len(match[0]) else match[0]
            # A missing message ID cannot be used as a reference destination.
            if identity:seen[digest]='消息'+identity[:80]+'第'+str(slot+1)+'张'
            return match[0]
        shortened=SUMMARY.sub(replace,text)
        row['text']=shortened
        if shortened!=text:row['_image_full_text']=text
        result.append(row)
    return list(reversed(result))


INSTRUCTION='\n[同图：消息ID第N张]表示与当前上下文该消息的第N张图片内容相同，复用那一份摘要；每次发送者、时间和附带文字仍各自独立，不把复读当新图片内容或新任务。\n'
