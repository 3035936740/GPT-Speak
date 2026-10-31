"""Owner chat commands use the real console dispatcher through a durable queue."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata


def validate(cfg):
    prefix = cfg['owner_command_prefix']; aliases = cfg['owner_command_aliases']
    if not isinstance(prefix, str) or not 1 <= len(prefix) <= 12 or any(c.isspace() or ord(c) < 32 for c in prefix):
        raise ValueError('主人指令前缀须为1～12字且不含空白')
    if not isinstance(aliases, dict) or len(aliases) > 100:
        raise ValueError('主人指令别名须为最多100项的JSON对象')
    from console_help import COMMANDS
    for name, command in aliases.items():
        if not isinstance(name, str) or not re.fullmatch(r'[^\s/]{1,40}', name) or not isinstance(command, str) or not 1 <= len(command) <= 300:
            raise ValueError('主人指令别名格式：{"清空":"session clear","概率":"config local set passive_probability"}')
        words=command.lstrip('/').split()
        if not words or words[0] not in COMMANDS or '\n' in command or '\r' in command:
            raise ValueError('别名只能映射到现有CLI命令')


def db_open(root):
    path = Path(root) / 'runtime/owner-commands.sqlite3'; path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.execute('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,actor TEXT,scope TEXT,command TEXT,status TEXT,stamp REAL,result TEXT)')
    return db


def authorized(root, actor, scope):
    from console_config import Config, runtime
    from member_labels import MemberLabels
    cfg = Config(root).load()
    if scope == 'private':
        from private_view import contacts
        if str(actor) not in contacts(root):return False
        folders = [Path(root) / 'runtime-private']
    else:
        if not str(scope).isdecimal() or int(scope) not in cfg['chat_group_whitelist']: return False
        folders = [runtime(root, int(scope))]
    row = MemberLabels(folders[0]).facts().get(str(actor), {})
    return row.get('manual_label') == 'owner' and not row.get('blocked')


def parse(cfg, text):
    from console_help import COMMANDS
    text = re.sub(r'^(?:@\d+\s*)+', '', str(text).strip())
    prefix = cfg['owner_command_prefix']
    normalized = unicodedata.normalize('NFKC', text)
    prefix = unicodedata.normalize('NFKC', prefix)
    if not normalized.casefold().startswith(prefix.casefold()): return None
    # Slice the original text so arbitrary JSON/config values are not NFKC-normalized.
    body = text[len(prefix):].strip()
    if not body: return None
    name, _, args = body.partition(' ')
    aliases = cfg['owner_command_aliases']
    if name in aliases: command = aliases[name].lstrip('/')
    elif name in COMMANDS: command = name
    else: return None
    return '/' + command + (' ' + args.strip() if args.strip() else '')


def handle(root, actor, text, message_id, scope):
    from console_config import Config
    cfg = Config(root, int(scope) if str(scope).isdecimal() else None).load()
    if not cfg.get('owner_commands_enabled') or not authorized(root, actor, scope): return None
    command = parse(cfg, text)
    if not command: return None
    identity = str(scope) + ':' + str(actor) + ':' + str(message_id)
    with closing(db_open(root)) as db, db:
        db.execute("INSERT OR IGNORE INTO commands VALUES (?,?,?,?,'queued',?,'')", (identity, str(actor), str(scope), command, time.time()))
    return '已接收主人指令，交由CLI执行'


def drain(console):
    from console_config import Config
    with closing(db_open(console.root)) as db, db:
        # Never replay operations after an interrupted execution or an old backlog.
        db.execute("UPDATE commands SET status='cancelled' WHERE status='running' OR (status='queued' AND stamp<?)", (time.time() - 120,))
        row = db.execute("SELECT id,actor,scope,command FROM commands WHERE status='queued' ORDER BY stamp LIMIT 1").fetchone()
        if not row: return
        identity, actor, scope, command = row
        try:cfg = Config(console.root, int(scope) if scope.isdecimal() else None).load()
        except ValueError:
            db.execute("UPDATE commands SET status='cancelled' WHERE id=?", (identity,));return
        if not cfg.get('owner_commands_enabled') or not authorized(console.root, actor, scope):
            db.execute("UPDATE commands SET status='cancelled' WHERE id=?", (identity,)); return
        db.execute("UPDATE commands SET status='running' WHERE id=?", (identity,))
    original, config, panel = console.group, console.config, console.panel
    try:
        if scope == 'private' and command in {'/session clear', '/session stop', '/session resume'}:
            from private_view import control_session
            result = control_session(console.root, actor, command.rsplit(' ', 1)[1])['message']
        else:
            if scope.isdecimal(): console.group = int(scope)
            console.config = Config(console.root)
            console.execute(command)
            result = console.notice
        status = 'done'
    except Exception as error:
        status, result = 'failed', type(error).__name__ + ': ' + str(error)
    finally:
        console.group, console.config, console.panel = original, config, panel
        if original not in console.groups(): console.group = console.groups()[0]
    # Results remain in the authenticated console, never echo keys/personas into QQ.
    with closing(db_open(console.root)) as db, db:
        db.execute('UPDATE commands SET status=?,result=? WHERE id=?', (status, result, identity))
    console.notice = '主人指令 ' + command.split()[0] + (' 已执行\n' if status == 'done' else ' 执行失败\n') + result
