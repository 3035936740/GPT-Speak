"""One live conversation packet per reply turn, preserving speaker attribution."""
import json


INSTRUCTION='conversation_batch是本轮新到消息组成的一次对话，按时间顺序整体理解，只生成一次自然回应，不逐条点名答题，不把不同人的话归给target发送者。target仅是QQ投递锚点；可回应共同话题或最需要回应的问题，无需逐条回复。omitted表示预算内省略的较早消息，不猜测其内容。'


def attach(target, rows, cfg):
    unique={str(row.get('_id')):row for row in rows if row.get('_id') and not row.get('is_bot')
        and not row.get('_historical') and not row.get('_record_only') and not row.get('_bot_command')}
    ordered=sorted(unique.values(),key=lambda row:row.get('_time',0))
    if len(ordered)<2:return target
    messages=[{'id':str(row['_id']),'qq_id':str(row.get('_uid','')),'sender':str(row.get('sender',''))[:60],
        'sent_at':__import__('message_time').formatted(row.get('_time')),
        'time':row.get('_time',0),'text':str(row.get('text',''))[:1000],
        'at_bot':bool(row.get('at_bot')),'reply_to':row.get('_reply_id')} for row in ordered]
    budget=max(600,min(4000,cfg.get('max_prompt_chars',12800)//4))
    total=len(messages)
    while len(messages)>1 and len(json.dumps(messages,ensure_ascii=False))>budget:messages.pop(0)
    ids=list(dict.fromkeys(key for row in ordered for key in row.get('_source_ids',[str(row['_id'])])))
    return {**target,'_source_ids':ids,'_conversation_batch':{'messages':messages,'count':total,'omitted':total-len(messages)}}


def trim(packet):
    if len(packet.get('messages',[]))>1:
        packet['messages'].pop(0);packet['omitted']=packet.get('omitted',0)+1
        return True
    return False
