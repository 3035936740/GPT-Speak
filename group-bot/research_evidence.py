"""Recognize completed web actions without trusting model-written citations."""
import json
import re
from collections import Counter


def items(stdout):
    for line in stdout.decode('utf-8',errors='replace').splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict):continue
        envelope=event.get('type') or event.get('method','')
        params=event.get('params')
        item=event.get('item') or (params.get('item') if isinstance(params,dict) else None)
        if not isinstance(item,dict):
            if event.get('type')=='web_search_call':item=event
            else:continue
        kind=re.sub(r'(?<!^)(?=[A-Z])','_',str(item.get('type',''))).lower()
        completed=envelope in {'item.completed','item/completed','response.output_item.done'} or (item is event and item.get('status')=='completed')
        yield envelope,kind,item,completed


def checked(stdout,live=False):
    completed=set()
    for envelope,kind,item,done in items(stdout):
        if kind in {'command_execution','mcp_tool_call','file_change'}:
            raise RuntimeError('Unexpected tool activity in reply generation')
        if kind in {'web_search','web_search_call'}:
            if not live:raise RuntimeError('Unexpected tool activity in reply generation')
            if done and item.get('status') not in {'failed','in_progress','inProgress','cancelled','interrupted'} and not item.get('error'):
                completed.add(str(item.get('id') or len(completed)))
    return len(completed)


def summary(stdout):
    # Keep only event types/counts, never agent text, raw reasoning, or tool bodies.
    events=Counter();kinds=Counter()
    for envelope,kind,item,done in items(stdout):
        events[str(envelope)[:60]]+=1;kinds[kind[:60]]+=1
    return {'events':dict(events),'items':dict(kinds)}


def opened_urls(stdout):
    """Completed page opens when the CLI exposes an action URL."""
    urls = set()
    for _, kind, item, done in items(stdout):
        action = item.get('action')
        if (done and kind in {'web_search','web_search_call'} and isinstance(action,dict)
                and action.get('type') in {'open_page','openPage'}
                and item.get('status') not in {'failed','in_progress','inProgress','cancelled','interrupted'}
                and not item.get('error') and isinstance(action.get('url'),str)):
            urls.add(action['url'])
    return urls
