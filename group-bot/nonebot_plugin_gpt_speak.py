"""GPT Speak business plugin using the official NoneBot2 plugin interfaces."""
import hmac
from pathlib import Path

import nonebot
from fastapi import HTTPException, Request
from nonebot import get_app, get_driver, get_plugin_config, get_adapter
from nonebot.adapters.onebot.v11 import Adapter, Bot, Event
from nonebot.compat import model_dump
from nonebot.message import event_preprocessor
from nonebot.plugin import PluginMetadata
from pydantic import BaseModel

from console_config import Config as ProjectConfig
from nonebot_inbox import Inbox
from directory_cache import DirectoryCache, cache_seconds
from quoted_message_cache import QuotedMessageCache

directory_cache = DirectoryCache()
quoted_messages = QuotedMessageCache()


class Config(BaseModel):
    gpt_speak_root: Path


__plugin_meta__ = PluginMetadata(
    name='GPT Speak', description='GPT Speak conversation and management bridge',
    usage='Configure the OneBot reverse client and open the GPT Speak console.',
    type='application', config=Config, supported_adapters={'~onebot.v11'})

cfg = ProjectConfig(get_plugin_config(Config).gpt_speak_root).load()
expected = str(cfg['expected_self_id'])
token = cfg['nonebot_access_token']
inbox = Inbox()
driver = get_driver()


@event_preprocessor
async def collect_event(bot: Bot, event: Event):
    if bot.self_id != expected or str(event.self_id) != expected:
        return
    row = model_dump(event)
    if row.get('notice_type') in {'group_recall', 'friend_recall'}:
        quoted_messages.recall(bot.self_id, row.get('message_id'))
    if row.get('meta_event_type') == 'heartbeat':
        online = (row.get('status') or {}).get('online')
        if online is False and inbox.online:
            inbox.reset(False)
        elif online is True and not inbox.online:
            inbox.reset(True)
    if row.get('post_type') in {'message', 'message_sent'} and inbox.online:
        # Official preprocessing removes reply/at segments from event.message.
        # Its original_message retains the full ordered OneBot message.
        if 'original_message' in row:
            row['message'] = row.pop('original_message')
        quoted_messages.remember(bot.self_id, row)
        reply = row.get('reply')
        if isinstance(reply, dict):
            reply = {**reply, 'user_id': (reply.get('sender') or {}).get('user_id')}
            if row.get('group_id') is not None:
                reply['group_id'] = row['group_id']
            quoted_messages.remember(bot.self_id, reply)
        inbox.append(row)


@driver.on_bot_connect
async def connected(bot):
    if bot.self_id == expected:
        inbox.reset(True)

@driver.on_bot_disconnect
async def disconnected(bot):
    if bot.self_id == expected:
        inbox.reset(False)

app = get_app()

def authorize(request):
    if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
        raise HTTPException(401, 'Authentication required')

@app.get('/gpt-speak/health')
async def health(request: Request):
    authorize(request)
    adapter_type = type(get_adapter(Adapter))
    return dict(service='gpt-speak-nonebot', online=inbox.online, self_id=expected,
                adapter=adapter_type.__module__ + '.' + adapter_type.__name__,
                plugin=__name__)

@app.get('/gpt-speak/events')
async def events(request: Request, epoch: str = '', cursor: int = 0):
    authorize(request)
    try:
        return inbox.read(epoch, cursor)
    except ConnectionError as exc:
        raise HTTPException(503, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post('/gpt-speak/directory-refresh')
async def directory_refresh(request: Request):
    authorize(request)
    value = await request.json()
    if not isinstance(value, dict) or value.get('scope') not in {'friends', 'groups', 'members'}:
        raise HTTPException(400, 'Invalid directory refresh scope')
    directory_cache.clear(value['scope'], value.get('group'))
    return {'ok': True}

@app.post('/gpt-speak/api/{action}')
async def api(action: str, request: Request):
    authorize(request)
    if not inbox.online:
        raise HTTPException(503, 'QQ reverse connection is offline')
    try:
        bot = nonebot.get_bot(expected)
    except KeyError as exc:
        raise HTTPException(503, 'Expected QQ account is not connected') from exc
    params = await request.json()
    if not isinstance(params, dict):
        raise HTTPException(400, 'Parameters must be an object')
    # Live push is authoritative. Workers never fetch history in this mode.
    if action == 'get_group_msg_history' and not params.pop('_gpt_speak_manual_history', False):
        return dict(status='ok', retcode=0, data={'messages': []})
    if action == 'get_group_msg_history':
        params = {'group_id': int(params['group_id']), 'count': 50}
    try:
        current_cfg = ProjectConfig(get_plugin_config(Config).gpt_speak_root).load()
        if action == 'get_msg':
            data = await quoted_messages.get(bot, params)
        else:
            data = await directory_cache.call(bot, action, params, cache_seconds(current_cfg, action))
        return dict(status='ok', retcode=0, data=data)
    except Exception as exc:
        from nonebot.adapters.onebot.v11.exception import ActionFailed
        if isinstance(exc, ActionFailed):
            return dict(status='failed', retcode=exc.info.get('retcode', -1),
                        message=exc.info.get('message') or exc.info.get('wording') or str(exc), data=None)
        # Never retry a send whose result is uncertain.
        raise HTTPException(502, type(exc).__name__ + ': ' + str(exc)) from exc

