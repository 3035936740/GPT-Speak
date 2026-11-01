"""Fit every optional analysis request before invoking the model transport."""
from copy import deepcopy
import json


class RoleBudgetError(ValueError):pass


def fit_role_prompt(prefix,payload,limit,compact_prefix):
    base=dict(payload);references=base.get('knowledge_references',[])
    if references:base['knowledge_references']=[]
    result=_fit_role_prompt(prefix,base,limit,compact_prefix)
    if not references:return result
    used_prefix=prefix if result.startswith(prefix) else compact_prefix
    data=json.loads(result[len(used_prefix):])
    from knowledge_budget import attach
    return attach(used_prefix,data,'knowledge_references',references,limit)


def _fit_role_prompt(prefix,payload,limit,compact_prefix):
    data=deepcopy(payload)
    def render():return prefix+json.dumps(data,ensure_ascii=False,separators=(',',':'))
    def fits():return len(render())<=limit
    if fits():return render()
    from conversation_batch import trim
    while not fits() and trim(data.get('conversation_batch',{})):pass
    if fits():return render()
    for key,empty in [('expression_candidates',[]),('memory_candidates',[]),('person_profile',{}),
        ('peer_scene',{}),('continuation',{}),('previous_analysis_summary',{})]:
        if key in data:data[key]=empty
        if fits():return render()
    for key in ('recent_members','mentioned_members'):
        if key in data.get('group_identities',{}):data['group_identities'][key]=[]
        if fits():return render()
    while len(data.get('knowledge_references',[]))>1 and not fits():data['knowledge_references'].pop()
    if fits():return render()
    for key in ('group_turn','reaction_options'):
        if key in data:
            for name in ('batch','candidates'):
                if name in data[key]:data[key][name]=[]
        if fits():return render()
    for message in data.get('recent_chat',[])+[data.get('target',{}),data.get('quoted_message',{})]:
        for key in ('image_repetition','qq_nickname','group_card','image_facts','seconds_before_target'):
            message.pop(key,None)
    if fits():return render()
    # Replace verbose built-in planning prose with equivalent concise guidance.
    # Configured member policies and optional moderation instructions are retained.
    prefix=compact_prefix
    if fits():return render()
    protected=set(data.get('context_selection',{}).get('protected_ids',[]))
    while len(data.get('recent_chat',[]))>1 and not fits():
        removable=next((i for i,row in enumerate(data['recent_chat']) if str(row.get('id')) not in protected),None)
        if removable is None:break
        data['recent_chat'].pop(removable)
    if fits():return render()
    for person in data.get('group_identities',{}).values():
        if isinstance(person,dict):person.pop('account_note',None);person.pop('extra_names',None)
    for page in data.get('reference_pages',[]):
        if isinstance(page.get('body'),str) and len(page['body'])>180:page['body']=page['body'][:180];page['truncated']=True
    if fits():return render()
    for entry in data.get('knowledge_references',[]):
        if isinstance(entry.get('content'),str) and len(entry['content'])>180:
            entry['content']=entry['content'][:180];entry['truncated']=True
    if fits():return render()
    # Remove empty optional fields, then shorten only older turns. Never slice JSON.
    data={key:value for key,value in data.items() if value not in ('',[],{},None) or key=='target'}
    for message in data.get('recent_chat',[])+[data.get('quoted_message',{})]:
        if isinstance(message.get('text'),str) and len(message['text'])>80:message['text']=message['text'][:80];message['truncated']=True
    if fits():return render()
    raise RoleBudgetError('Role prompt cannot fit required content; chars='+str(len(render()))+'; cap='+str(limit)+'; rules='+str(len(prefix)))
