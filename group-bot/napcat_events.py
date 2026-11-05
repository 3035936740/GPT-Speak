"""Receive live OneBot events without repeatedly reading QQ history."""
import asyncio
import json
import logging
import time
from collections import deque
from urllib.parse import urlsplit,urlunsplit
from napcat_connection import connection


class EventInbox:
    def __init__(self):
        self.key=None
        self.socket=None
        self.reader=None
        self.rows=deque()
        self.error=None
        self.last_message_at=0
        self.last_probe_at=0

    async def close(self):
        reader,self.reader=self.reader,None
        if reader:
            reader.cancel()
            await asyncio.gather(reader,return_exceptions=True)
        socket,self.socket=self.socket,None
        if socket:await socket.close()
        self.rows.clear()
        self.key=None

    async def collect(self,socket,expected):
        try:
            async for frame in socket:
                event=json.loads(frame)
                if not isinstance(event,dict):continue
                if str(event.get('self_id',expected))!=expected:
                    raise ValueError('NapCat event account mismatch')
                if event.get('post_type')=='meta_event' and event.get('meta_event_type')=='heartbeat':
                    if (event.get('status') or {}).get('online') is False:
                        raise ConnectionError('NapCat heartbeat reports QQ offline')
                if event.get('post_type') not in {'message','message_sent'}:continue
                if len(self.rows)>=4096:
                    raise ConnectionError('NapCat event inbox overflow; reconnect to establish a fresh baseline')
                self.rows.append(event)
                self.last_message_at=time.monotonic()
            raise ConnectionError('NapCat event stream closed')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.rows.clear()
            self.error=exc

    async def poll(self,cfg,env):
        url,token,timeout=connection(cfg,env)
        parsed=urlsplit(url)
        if parsed.scheme not in {'ws','wss'}:
            if self.socket:await self.close()
            return None
        # NapCat's /api socket suppresses events. Its parent is the universal socket.
        path=parsed.path.rstrip('/')
        if path.endswith('/api'):path=path[:-4]
        url=urlunsplit((parsed.scheme,parsed.netloc,path or '/','',''))
        expected=str(cfg.get('expected_self_id',''))
        key=(url,token,timeout,expected)
        if self.error:
            error,self.error=self.error,None
            await self.close()
            raise error
        if self.key!=key or self.socket is None:
            await self.close()
            from websockets.asyncio.client import connect
            self.socket=await connect(url,additional_headers={'Authorization':'Bearer '+token} if token else {},
                open_timeout=timeout,close_timeout=2,proxy=None,max_size=16*1024*1024)
            self.key=key
            self.reader=asyncio.create_task(self.collect(self.socket,expected))
            self.last_message_at=self.last_probe_at=time.monotonic()
            logging.getLogger('qq-group-bot').info('NapCat live event stream connected; account=%s',expected)
            # Establish a fresh baseline before accepting events.
            return []
        rows=list(self.rows)
        self.rows.clear()
        now=time.monotonic()
        if not rows and now-self.last_message_at>=60 and now-self.last_probe_at>=60:
            # A successful WS handshake/heartbeat does not prove message delivery.
            # Occasionally verify the latest history through the existing bounded,
            # deduplicated polling path instead of starving it forever.
            self.last_probe_at=now
            logging.getLogger('qq-group-bot').info('NapCat live inbox quiet; verifying latest messages through API')
            return None
        return rows


async def poll(client,cfg,env):
    inbox=getattr(client,'_onebot_event_inbox',None)
    if inbox is None:
        inbox=EventInbox()
        client._onebot_event_inbox=inbox
    return await inbox.poll(cfg,env)
