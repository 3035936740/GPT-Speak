"""Contact-scoped public execution events; never collect model reasoning or prompts."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import re
import sqlite3
import time

from debug_events import clean, safe_log

TRACE = ContextVar('private_chat_trace', default=None)


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS private_events(
        id INTEGER PRIMARY KEY, peer TEXT, kind TEXT, text TEXT, stamp REAL,
        level TEXT, details TEXT, event_key TEXT UNIQUE);
        CREATE INDEX IF NOT EXISTS private_event_peer ON private_events(peer,id);''')


def emit(db, peer, kind, text, stamp=None, level='INFO', details=None, key=None):
    # System instructions and actual chat text are shown verbatim to the operator.
    text = clean(text) if kind in {'system', 'received', 'sent'} else safe_log(re.sub(r'(?i)\bbearer\s+[^\s]+', 'Bearer [隐藏]', str(text)))
    cursor = db.execute('INSERT OR IGNORE INTO private_events(peer,kind,text,stamp,level,details,event_key) VALUES (?,?,?,?,?,?,?)',
        (str(peer), kind, text, time.time() if stamp is None else stamp, level,
         json.dumps(details or {}, ensure_ascii=False), key))
    # Bound the two lanes independently, so verbose logs never evict conversation.
    for clause, limit in [("kind IN ('received','sent')", 100), ("kind NOT IN ('received','sent')", 1000)]:
        db.execute('DELETE FROM private_events WHERE peer=? AND ' + clause +
            ' AND id NOT IN (SELECT id FROM private_events WHERE peer=? AND ' + clause + ' ORDER BY id DESC LIMIT ?)',
            (str(peer), str(peer), limit))
    return cursor.rowcount == 1


@contextmanager
def trace(store, peer):
    token = TRACE.set((store, str(peer)))
    try:
        yield
    finally:
        TRACE.reset(token)


class ModelLogHandler(logging.Handler):
    def __init__(self, store):
        super().__init__()
        self.store = store

    def emit(self, record):
        context = TRACE.get()
        if context is None or context[0] is not self.store():
            return
        try:
            self.store().event(context[1], 'stage', record.getMessage(),
                               level=record.levelname, details={'source': 'model'})
        except (sqlite3.Error, OSError):
            pass
