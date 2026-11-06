"""Worker-side authenticated local bridge. NoneBot owns the QQ transport."""
from urllib.parse import urlsplit


DEFAULTS = {'nonebot_listen_host': '0.0.0.0', 'nonebot_port': 8080,
            'nonebot_bridge_url': 'http://127.0.0.1:8080', 'nonebot_access_token': '',
            'nonebot_api_timeout_seconds': 60}


def validate(cfg):
    timeout = cfg['nonebot_api_timeout_seconds']
    if type(timeout) not in {int, float} or not 1 <= timeout <= 300:
        raise ValueError('NoneBot API timeout must be in 1..300 seconds')
    if type(cfg['nonebot_port']) is not int or not 1 <= cfg['nonebot_port'] <= 65535:
        raise ValueError('NoneBot port must be in 1..65535')
    host = cfg['nonebot_listen_host']
    if not isinstance(host, str) or not host or any(c.isspace() or c in '/?#@' for c in host):
        raise ValueError('Invalid NoneBot listen host')
    url = urlsplit(cfg['nonebot_bridge_url'])
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Invalid NoneBot bridge URL')
    url.port
    token = cfg['nonebot_access_token']
    if not isinstance(token, str) or any(ord(c) < 32 or ord(c) == 127 for c in token):
        raise ValueError('Invalid NoneBot access token')


def headers(cfg):
    token = cfg.get('nonebot_access_token', '')
    if not token:
        raise ConnectionError('Set NoneBot access token and connect NapCat reverse WS')
    return {'Authorization': 'Bearer ' + token}


async def request(client, cfg, action, params):
    from napcat_errors import NapCatAPIError
    response = await client.post(cfg['nonebot_bridge_url'].rstrip('/') + '/gpt-speak/api/' + action,
                                headers=headers(cfg), json=params or {},
                                timeout=cfg.get('nonebot_api_timeout_seconds', 60) + 5)
    response.raise_for_status()
    result = response.json()
    if result.get('retcode') != 0 or result.get('status') == 'failed':
        raise NapCatAPIError(action, result)
    return result.get('data')


async def poll(client, cfg):
    key = (cfg['nonebot_bridge_url'], cfg['nonebot_access_token'])
    previous = getattr(client, '_nonebot_cursor', None)
    epoch, cursor = previous[1:] if previous and previous[0] == key else ('', 0)
    try:
        response = await client.get(key[0].rstrip('/') + '/gpt-speak/events',
                                    headers=headers(cfg), params={'epoch': epoch, 'cursor': cursor},
                                    timeout=10)
        response.raise_for_status()
        result = response.json()
    except BaseException:
        client._nonebot_cursor = None
        raise
    client._nonebot_cursor = (key, result['epoch'], result['cursor'])
    if epoch and epoch != result['epoch']:
        raise ConnectionError('NoneBot connection changed; cancel unfinished work and establish a fresh baseline')
    return result['messages']
