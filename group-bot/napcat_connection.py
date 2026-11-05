"""Shared forward OneBot connection settings and non-replaying API transport."""
import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from urllib.parse import urlsplit, urlunsplit

import httpx
from napcat_errors import NapCatAPIError

_activity=Counter()
_activity_since=time.monotonic()


def record_activity(action):
    global _activity_since
    _activity[str(action)]+=1
    now=time.monotonic()
    if now-_activity_since>=60:
        logging.getLogger('qq-group-bot').info('NapCat request activity; seconds=%s; actions=%s',
            round(now-_activity_since),json.dumps(dict(_activity),sort_keys=True))
        _activity.clear()
        _activity_since=now


DEFAULTS = {'napcat_connection_mode': 'current', 'napcat_host': '', 'napcat_port': 0,
    'napcat_path': '', 'napcat_secure': False, 'napcat_token': '',
    'napcat_timeout_seconds': 60, 'napcat_mcp_url': '', 'napcat_mcp_token': ''}


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https', 'ws', 'wss'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('NapCat地址须为http(s)或ws(s) URL，不含账号、查询参数或片段')
    try: parsed.port
    except ValueError: raise ValueError('NapCat端口无效')
    return parsed


def validate(cfg):
    if not isinstance(cfg['napcat_connection_mode'],str) or cfg['napcat_connection_mode'] not in {'current', 'http', 'ws'}:
        raise ValueError('NapCat连接方式为current / http / ws')
    host = cfg['napcat_host']
    if not isinstance(host, str) or any(c.isspace() or c in '/?#@' for c in host):
        raise ValueError('NapCat主机填写IP或域名，不含协议或路径')
    if type(cfg['napcat_port']) is not int or not 0 <= cfg['napcat_port'] <= 65535:
        raise ValueError('NapCat端口范围0～65535；0继承当前端口')
    if not isinstance(cfg['napcat_path'], str) or any(c in cfg['napcat_path'] for c in '?\r\n#'):
        raise ValueError('NapCat路径无效')
    if type(cfg['napcat_secure']) is not bool or type(cfg['napcat_timeout_seconds']) not in {int,float} or not 1 <= cfg['napcat_timeout_seconds'] <= 300:
        raise ValueError('NapCat超时范围1～300秒')
    for key in ('napcat_token', 'napcat_mcp_token'):
        if not isinstance(cfg[key], str) or any(ord(c) < 32 or ord(c) == 127 for c in cfg[key]):
            raise ValueError('NapCat令牌不能含控制字符')
    if not isinstance(cfg['napcat_mcp_url'],str):raise ValueError('NapCat MCP地址须为文本')
    if cfg['napcat_mcp_url']: validate_url(cfg['napcat_mcp_url'])
    # Validate an explicitly configured host, including IPv6 bracket handling.
    if host: validate_url('http://' + (host if host.startswith('[') or ':' not in host else '[' + host + ']'))


def connection(cfg, env=None, mcp=False):
    cfg = {**DEFAULTS, **cfg}; env = env or {}
    old = validate_url(env.get('NAPCAT_HOST') or 'http://127.0.0.1:3000')
    mode = cfg['napcat_connection_mode']
    scheme = old.scheme if mode == 'current' else mode + ('s' if cfg['napcat_secure'] else '')
    host = (cfg['napcat_host'] or old.hostname).strip('[]')
    authority = '[' + host + ']' if ':' in host else host
    port = cfg['napcat_port'] or old.port
    if port: authority += ':' + str(port)
    path = cfg['napcat_path'] or old.path
    url = urlunsplit((scheme, authority, '/' + path.lstrip('/') if path else '', '', ''))
    token = cfg['napcat_token'] or env.get('NAPCAT_TOKEN', '') or ''
    if mcp:
        url = cfg['napcat_mcp_url'] or url
        token = cfg['napcat_mcp_token'] or token
    validate_url(url)
    return url.rstrip('/'), token, float(cfg['napcat_timeout_seconds'])


class ForwardWebSocket:
    def __init__(self, url, token, timeout):
        self.url, self.token, self.timeout = url, token, timeout
        self.socket = None
        self.lock = asyncio.Lock()

    async def close(self):
        socket, self.socket = self.socket, None
        if socket:
            try: await socket.close()
            except Exception: pass

    async def request(self, action, params):
        # Serialize send+receive so concurrent API calls cannot consume each other's echoes.
        async with self.lock:
            sent=False
            try:
                async def exchange():
                    nonlocal sent
                    if self.socket is None:
                        from websockets.asyncio.client import connect
                        self.socket = await connect(self.url, additional_headers={'Authorization': 'Bearer ' + self.token} if self.token else {},
                            open_timeout=self.timeout, close_timeout=2, proxy=None, max_size=16 * 1024 * 1024)
                    echo = uuid.uuid4().hex
                    await self.socket.send(json.dumps({'action': action, 'params': params or {}, 'echo': echo}))
                    sent=True
                    while True:
                        result = json.loads(await self.socket.recv())
                        if isinstance(result, dict) and result.get('echo') == echo:
                            return result
                return await asyncio.wait_for(exchange(), self.timeout)
            except (asyncio.TimeoutError,TimeoutError,asyncio.CancelledError):
                # recv cancellation is safe. Keep the connection for late replies,
                # which the next request discards by echo. Never replay a send.
                if not sent:await self.close()
                raise
            except BaseException:
                # A write may have reached QQ: reconnect on the NEXT call, never replay this one.
                await self.close()
                raise


class NapCatHTTPClient(httpx.AsyncClient):
    async def aclose(self):
        for transport in getattr(self, '_onebot_sockets', {}).values(): await transport.close()
        await super().aclose()


async def request(client, env, action, params=None, cfg=None):
    record_activity(action)
    url, token, timeout = connection(cfg or {}, env)
    if urlsplit(url).scheme in {'ws', 'wss'}:
        transports = getattr(client, '_onebot_sockets', None)
        if transports is None:
            transports = {}; client._onebot_sockets = transports
        key = (url, token, timeout)
        if key not in transports:
            for old in transports.values(): await old.close()
            transports.clear(); transports[key] = ForwardWebSocket(*key)
        result = await transports[key].request(action, params)
    else:
        response = await client.post(url + '/' + action, headers={'Authorization': 'Bearer ' + token}, json=params or {}, timeout=timeout)
        response.raise_for_status(); result = response.json()
    if result.get('retcode') != 0 or result.get('status') == 'failed': raise NapCatAPIError(action, result)
    return result.get('data')
