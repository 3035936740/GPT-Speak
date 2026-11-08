"""Explicit, one-shot recent-history import; never creates live reply triggers."""
from collections import Counter


def sent_key(row):
    if row.get('kind') != 'sent' or not isinstance(row.get('time'), (float, int)) or not row.get('text') or any(row.get(key) for key in (
        'reply_id', 'image_count', 'image_hashes', 'files', 'forward', 'at_ids', 'quote'
    )) or '[' in row['text']:
        return None
    return row.get('sender'), int(row['time']), row['text']


def deduplicate_events(records):
    records = list(records)
    preferred = {}
    for index, row in enumerate(records):
        if row.get('kind') not in {'sent', 'received'} or not row.get('message_id'):
            continue
        key = row.get('group_id'), str(row['message_id'])
        if key not in preferred or records[preferred[key]].get('historical') and not row.get('historical'):
            preferred[key] = index
    live = Counter((records[i].get('group_id'), sent_key(records[i])) for i in preferred.values()
                   if not records[i].get('historical'))
    for index, row in enumerate(records):
        key = row.get('group_id'), str(row.get('message_id'))
        if row.get('kind') in {'sent', 'received'} and key in preferred and preferred[key] != index:
            continue
        signature = sent_key(row)
        if row.get('historical') and signature and live[row.get('group_id'), signature] == 1:
            continue
        yield row


def merge(rows, history, self_id, group, convert, deleted=(), clear_before=0, events=()):
    from debug_references import message_metadata

    known = {str(item.get('_id')) for item in history}
    local = list(deduplicate_events(events))
    known.update(str(row['message_id']) for row in local
                 if row.get('kind') in {'sent', 'received'} and row.get('message_id'))
    live = Counter(sent_key(row) for row in local if not row.get('historical'))
    hidden = {str(value) for value in deleted}
    added = []
    for row in sorted(rows, key=lambda value: value.get('time', 0)):
        if str(row.get('group_id', group)) != str(group):
            continue
        item = convert(row, self_id)
        identity = str(item.get('_id', ''))
        if not identity or identity in known or identity in hidden or item.get('_time', 0) <= clear_before:
            continue
        if item.get('is_bot'):
            signature = sent_key({'kind':'sent', 'sender':item.get('sender'), 'time':item['_time'],
                                  'text':item.get('text'), **message_metadata(item)})
            if signature and live[signature] == 1:
                continue
        item['_historical'] = True
        known.add(identity)
        added.append(item)
    return added
