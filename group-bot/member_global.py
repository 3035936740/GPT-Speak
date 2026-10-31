"""Optional shared member marks; command-response evidence stays group-local."""
from pathlib import Path
from console_config import Config, read_json, atomic


def location(folder):
    folder = Path(folder); root = folder.parent
    if not (root / 'settings.json').exists(): return None
    if folder.name not in {'runtime', 'runtime-private'} and not (folder.name.startswith('runtime-') and folder.name[8:].isdigit()): return None
    if not Config(root).load().get('member_labels_global_enabled'): return None
    shared = root / 'runtime-shared'; shared.mkdir(exist_ok=True)
    return shared


def initial_members(root):
    members = {}
    paths = [root / 'runtime/member-labels.json', root / 'runtime-private/member-labels.json']
    paths += list(root.glob('runtime-[0-9]*/member-labels.json'))
    for path in paths:
        for uid, row in read_json(path).get('members', {}).items():
            old = members.get(uid, {})
            if not old or (bool(row.get('manual_label')), row.get('updated_at', 0)) > (bool(old.get('manual_label')), old.get('updated_at', 0)):
                members[uid] = row
    return members


def read(folder, shared):
    global_path = shared / 'member-labels.json'
    data = read_json(global_path) if global_path.exists() else {'members': initial_members(shared.parent)}
    data['rule_messages'] = read_json(Path(folder) / 'member-rules.json').get('rule_messages')
    return data


def save(folder, shared, data):
    atomic(shared / 'member-labels.json', {'members': data.get('members', {})})
    if data.get('rule_messages') is not None:
        atomic(Path(folder) / 'member-rules.json', {'rule_messages': data['rule_messages']})
