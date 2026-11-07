"""Explicit, one-shot recent-history import; never creates live reply triggers."""


def merge(rows, history, self_id, group, convert, deleted=(), clear_before=0):
    known = {str(item.get('_id')) for item in history}
    hidden = {str(value) for value in deleted}
    added = []
    for row in sorted(rows, key=lambda value: value.get('time', 0)):
        if str(row.get('group_id', group)) != str(group):
            continue
        item = convert(row, self_id)
        identity = str(item.get('_id', ''))
        if not identity or identity in known or identity in hidden or item.get('_time', 0) <= clear_before:
            continue
        item['_historical'] = True
        known.add(identity)
        added.append(item)
    return added
