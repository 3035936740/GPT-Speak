"""One shared NoneBot2 reverse OneBot connection for all GPT Speak workers."""
import argparse
from collections import deque
import hmac
import json
from pathlib import Path
import uuid


class Inbox:
    def __init__(self, limit=8192):
        self.rows = deque(maxlen=limit)
        self.sequence = 0
        self.epoch = uuid.uuid4().hex
        self.online = False

    def reset(self, online):
        self.rows.clear()
        self.epoch = uuid.uuid4().hex
        self.online = online

    def append(self, event):
        self.sequence += 1
        self.rows.append((self.sequence, event))

    def read(self, epoch, cursor):
        if not self.online:
            raise ConnectionError('QQ reverse connection is offline')
        # A new worker or connection takes a baseline. No replay after reconnect.
        if epoch != self.epoch:
            return dict(epoch=self.epoch, cursor=self.sequence, messages=[])
        if self.rows and cursor < self.rows[0][0] - 1:
            raise OverflowError('Reverse event inbox overflow; establish a new baseline')
        return dict(epoch=self.epoch, cursor=self.sequence,
                    messages=[row for seq, row in self.rows if seq > cursor])


def serve(root):
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter
    from fastapi import HTTPException, Request
    # Resolve postponed annotations used by FastAPI's dependency inspection.
    globals()['Request'] = Request
    from console_config import Config
    cfg = Config(root).load()
    token = cfg['nonebot_access_token']
    if not token:
        raise ValueError('Configure a nonempty nonebot_access_token before starting NoneBot')
    expected = str(cfg['expected_self_id'])
    inbox = Inbox()
    nonebot.init(driver='~fastapi', host=cfg['nonebot_listen_host'],
                 port=cfg['nonebot_port'], onebot_access_token=token,
                 onebot_ws_urls=[], api_timeout=cfg['nonebot_api_timeout_seconds'],
                 log_level='WARNING')
    driver = nonebot.get_driver()

    class SpeakAdapter(Adapter):
        async def _handle_ws(self, websocket):
            if websocket.request.headers.get('x-self-id') != expected:
                await websocket.close(1008, 'Unexpected QQ account')
                return
            await super()._handle_ws(websocket)

        def json_to_event(self, raw):
            if str(raw.get('self_id', '')) == expected:
                if raw.get('meta_event_type') == 'heartbeat':
                    online = (raw.get('status') or {}).get('online')
                    if online is False:
                        inbox.reset(False)
                    elif online is True and not inbox.online:
                        inbox.reset(True)
                if raw.get('post_type') in {'message', 'message_sent'} and inbox.online:
                    inbox.append(raw)
            return super().json_to_event(raw)

    driver.register_adapter(SpeakAdapter)

    @driver.on_bot_connect
    async def connected(bot):
        if bot.self_id == expected:
            inbox.reset(True)

    @driver.on_bot_disconnect
    async def disconnected(bot):
        if bot.self_id == expected:
            inbox.reset(False)

    app = driver.server_app

    def authorize(request):
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
            raise HTTPException(401, 'Authentication required')

    @app.get('/gpt-speak/health')
    async def health(request: Request):
        authorize(request)
        return dict(service='gpt-speak-nonebot', online=inbox.online, self_id=expected)

    @app.get('/gpt-speak/events')
    async def events(request: Request, epoch: str = '', cursor: int = 0):
        authorize(request)
        try:
            return inbox.read(epoch, cursor)
        except ConnectionError as exc:
            raise HTTPException(503, str(exc)) from exc
        except OverflowError as exc:
            raise HTTPException(409, str(exc)) from exc

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
        if action == 'get_group_msg_history':
            return dict(status='ok', retcode=0, data={'messages': []})
        try:
            data = await bot.call_api(action, **params)
            return dict(status='ok', retcode=0, data=data)
        except Exception as exc:
            from nonebot.adapters.onebot.v11.exception import ActionFailed
            if isinstance(exc, ActionFailed):
                return dict(status='failed', retcode=exc.info.get('retcode', -1),
                            message=exc.info.get('message') or exc.info.get('wording') or str(exc), data=None)
            # Never retry a send whose result is uncertain.
            raise HTTPException(502, type(exc).__name__ + ': ' + str(exc)) from exc

    nonebot.run(ws_max_size=64 * 1024 * 1024)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    serve(parser.parse_args().root)
