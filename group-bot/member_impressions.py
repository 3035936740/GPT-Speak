"""Read source-backed impressions of one QQ account, without other people's history."""
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import time

from console_config import Config, read_json, runtime
from group_memory import memory_text
from member_labels import MemberLabels


def digest(salt, value):
    return hashlib.sha256((salt + str(value)).encode()).hexdigest()[:20]


def person_allowed(root, user_id, groups):
    cfg = Config(root).load()
    common = set(map(int, groups)) & set(cfg['chat_group_whitelist'])
    allowed = []
    for group in sorted(common):
        if not Config(root, group).load()['enabled']:
            continue
        row = MemberLabels(runtime(root, group)).facts().get(str(user_id), {})
        if row.get('blocked') or row.get('label') in {'AI', 'bot'}:
            return []
        allowed.append(group)
    return allowed


def impression(root, user_id, groups, now=None):
    """Only this person's own supported interests and aggregate speech habits cross to DM."""
    root = Path(root)
    now = time.time() if now is None else now
    result = {'user_id': str(user_id), 'observations': [], 'updated_at': now,
              'scope': '同一QQ账号在当前授权群的本人发言；印象是暂时参考，不是身份或性格定论'}
    count = 0
    for group in person_allowed(root, user_id, groups):
        folder = runtime(root, group)
        entry = {'source_group': group, 'message_count': 0, 'interests': []}
        path = folder / 'group-memory.sqlite3'
        if path.exists():
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                    db.row_factory = sqlite3.Row
                    row = db.execute("SELECT value FROM meta WHERE key='salt'").fetchone()
                    if row:
                        speaker = digest(row['value'], user_id)
                        entry['message_count'] = db.execute(
                            'SELECT count(*) FROM sources WHERE speaker=? AND stamp>=?',
                            (speaker, now - 30 * 86400)).fetchone()[0]
                        # Do not use events, multi-person episodes, raw quotes, or superseded facts.
                        rows = db.execute("SELECT n.* FROM notes n WHERE n.subject=? "
                            "AND n.kind IN ('preference','fact') AND n.stamp>=? "
                            "AND n.id NOT IN (SELECT old_id FROM supersessions) ORDER BY n.stamp DESC LIMIT 12",
                            (speaker, now - 90 * 86400)).fetchall()
                        import json
                        for note in rows:
                            evidence = json.loads(note['evidence'])
                            text = memory_text(note['content'], 180)
                            if text and evidence and all(e.get('speaker') == speaker for e in evidence):
                                entry['interests'].append({'id': note['id'], 'content': text,
                                    'kind': note['kind'], 'observed_at': note['stamp']})
                            if len(entry['interests']) >= 4:
                                break
            except (sqlite3.Error, ValueError, TypeError):
                entry['memory_available'] = False
        style = read_json(folder / 'style-memory.json')
        samples = [s for s in style.get('samples', [])
                   if style.get('salt') and s.get('speaker') == digest(style['salt'], user_id)
                   and now - 7 * 86400 <= s.get('time', 0) <= now + 60]
        if len(samples) >= 3:
            lengths = sorted(len(str(s.get('text', ''))) for s in samples)
            entry['speech_style'] = {'sample_count': len(samples),
                'median_message_length': lengths[len(lengths) // 2],
                'question_fraction': round(sum(str(s.get('text', '')).endswith(('?', '？')) for s in samples) / len(samples), 2),
                'description': '只用于调整长短和接话节奏，不模仿身份，不复制原话'}
        entry['message_count'] = max(entry['message_count'], len(samples))
        count += entry['message_count']
        result['observations'].append(entry)
    result['familiarity'] = '较熟悉' if count >= 80 else '有过一些交流' if count >= 15 else '刚认识，印象尚少'
    return result
