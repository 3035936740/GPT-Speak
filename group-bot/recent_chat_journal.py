"""Crash-safe, bounded recent chat context stored as JSON Lines."""
import json
import os
from pathlib import Path
import time


FIELDS = ('sender', 'text', 'at_bot', 'is_bot', '_time', '_uid', '_id', '_historical', '_record_only',
    '_at_ids', '_reply_id', '_audience', '_reply_to_uid', '_media_sent', '_group_id', '_nickname', '_card', '_image_hashes','_manual','_manual_request_id','_image_count','_sticker_ids','_quoted_message','_bot_command','_forward','_forward_outer_text','_time_source','_files','_attachment_order','_large_task_completed')


def compact(item):
    if not isinstance(item, dict) or item.get('_id') in (None, ''):
        return None
    saved = {key: item[key] for key in FIELDS if key in item}
    hashes = item.get('_image_hashes', []) or list(item.get('_image_keys', {}).values())
    count=item.get('_image_count',len(item.get('_images',[])))
    if isinstance(count,int) and not isinstance(count,bool) and count>0:saved['_image_count']=min(2,count)
    if hashes:
        import re
        saved['_image_hashes'] = [value for value in hashes[:2]
            if isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}', value)]
    saved['_id'] = str(saved['_id'])[:80]
    saved['_uid'] = str(saved.get('_uid', ''))[:40]
    saved['sender'] = str(saved.get('sender', ''))[:80]
    for key in ('_nickname','_card'):
        if key in saved:saved[key]=str(saved[key] or '')[:80]
    saved['text'] = ('[临时内容已清空]' if item.get('_ephemeral_content')
        else str(saved.get('text', '')) if item.get('_forward') else str(saved.get('text', ''))[:2000 if item.get('_manual') else 1000])
    saved['_time'] = float(saved.get('_time', 0) or 0)
    if '_reply_id' in saved and saved['_reply_id'] is not None:
        saved['_reply_id'] = str(saved['_reply_id'])[:80]
    if '_at_ids' in saved:
        saved['_at_ids'] = [str(value)[:40] for value in saved['_at_ids'][:8]]
    if '_quoted_message' in saved:
        source=saved['_quoted_message']
        if isinstance(source,dict):
            saved['_quoted_message']={key:str(source.get(key,''))[:180 if key=='text' else 80] for key in ('sender','text','_id','_uid')}
            from message_time import epoch
            if epoch(source.get('_time')) is not None:saved['_quoted_message']['_time']=epoch(source['_time'])
            if 'is_bot' in source:saved['_quoted_message']['is_bot']=bool(source['is_bot'])
            if source.get('_files'):saved['_quoted_message']['_files']=source['_files']
            if source.get('_image_count') or source.get('_images'):saved['_quoted_message']['_image_count']=source.get('_image_count',len(source.get('_images',[])))
        else:saved.pop('_quoted_message')
    if '_sticker_ids' in saved:
        saved['_sticker_ids']=[str(value)[:80] for value in saved['_sticker_ids'][:5]]
    return saved


class RecentChatJournal:
    def __init__(self, path, max_messages=500, max_age_hours=24):
        self.path = Path(path)
        self.max_messages = max(24, int(max_messages))
        self.max_age = max(1, float(max_age_hours)) * 3600
        self.records = []
        self.ids = set()

    def _bounded(self, items):
        cutoff = time.time() - self.max_age
        unique = {}
        for item in items:
            saved = compact(item)
            if saved and saved['_time'] >= cutoff:
                unique[saved['_id']] = saved
        return sorted(unique.values(), key=lambda value: value['_time'])[-self.max_messages:]

    def load(self):
        rows = []
        try:
            with self.path.open('r', encoding='utf-8') as stream:
                for line in stream:
                    try:
                        rows.append(json.loads(line))
                    except (TypeError, ValueError):
                        # A crash can leave only the last line incomplete.
                        continue
        except FileNotFoundError:
            pass
        self.records = self._bounded(rows)
        self.ids = {item['_id'] for item in self.records}
        return list(self.records)

    def append(self, item):
        saved = compact(item)
        if not saved or saved['_id'] in self.ids:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(saved, ensure_ascii=False, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.records.append(saved)
        self.ids.add(saved['_id'])
        if len(self.records) > self.max_messages:
            self.checkpoint(self.records)
        return True

    def confirm_time(self,identity,stamp):
        from message_time import epoch
        stamp=epoch(stamp)
        if stamp is None:return False
        row=next((row for row in self.records if str(row.get('_id'))==str(identity)),None)
        if row is None or (row.get('_time')==stamp and row.get('_time_source')=='napcat'):return False
        row.update(_time=stamp,_time_source='napcat');self.checkpoint();return True

    def mark_file_completed(self,identity):
        row=next((row for row in self.records if str(row.get('_id'))==str(identity)),None)
        if row is None or row.get('_large_task_completed'):return False
        row['_large_task_completed']=True;self.checkpoint();return True

    def checkpoint(self, items=None):
        records = self._bounded(self.records if items is None else items)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from storage_io import atomic_write_text
        atomic_write_text(self.path, ''.join(json.dumps(item, ensure_ascii=False,
            separators=(',', ':'))+'\n' for item in records))
        self.records = records
        self.ids = {item['_id'] for item in records}

