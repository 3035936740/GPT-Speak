"""Bounded latest-message polling; history is optional and never a backlog gate."""
import asyncio
import logging
import re
import time
from napcat_errors import NapCatAPIError
from debug_events import safe_log

log=logging.getLogger('qq-group-bot')
HISTORY_BATCH=40


def message_position(row):
    key=row.get('message_id')
    sequence=str(row.get('real_seq',''))
    if key not in (None,'',0,'0') and sequence.isdecimal() and int(sequence)>0:
        return int(sequence),str(key)


async def history_rows(api,client,env,group,count,cursor=0):
    data=await asyncio.wait_for(api(client,env,'get_group_msg_history',
        {'group_id':group,'count':count,'message_seq':cursor,'reverseOrder':not cursor}),5)
    rows=data.get('messages') or []
    if not isinstance(rows,list):
        raise ValueError('Invalid message history')
    return rows


async def bootstrap(api,client,env,group,*,has_archive=False):
    try:
        return {'messages':await history_rows(api,client,env,group,20 if has_archive else 100)}
    except Exception as exc:
        log.warning('Startup history skipped; latest polling continues; error_type=%s',type(exc).__name__)
        return {'messages':[]}


class LatestMessages:
    def __init__(self):
        self.reset()

    def reset(self):
        self.latest=0;self.next_probe=0
        self.cursor=None

    def seed(self,rows):
        cursor=max(filter(None,map(message_position,rows)),default=None)
        if cursor is not None and (self.cursor is None or cursor[0]>self.cursor[0]):
            self.cursor=cursor
            self.next_probe=time.monotonic()+30
        self.latest=max(self.latest,max((float(row.get('time',0)) for row in rows),default=0))

    async def incremental(self,api,client,env,group):
        sequence,anchor=self.cursor
        try:
            # NapCat includes the anchor in the forward page.
            rows=await history_rows(api,client,env,group,HISTORY_BATCH+1,anchor)
        except NapCatAPIError as exc:
            if exc.action!='get_group_msg_history' or not re.search(
                r'消息.*不存在|(?:message|msg).*(?:not found|does not exist)',exc.detail,re.I):
                raise
            rows=[]
        positions=[message_position(row) for row in rows]
        if (sequence,anchor) not in positions or None in positions:
            # A reconnect or an evicted/removed anchor invalidates the short ID.
            self.cursor=None
            return None
        new_rows=sorted((row for row,position in zip(rows,positions) if position[0]>sequence),
                        key=message_position)
        self.seed(new_rows)
        now=time.monotonic()
        if not new_rows and now>=self.next_probe:
            self.next_probe=now+30
            try:
                head=await history_rows(api,client,env,group,1)
            except Exception as exc:
                log.debug('Latest message check unavailable; error_type=%s',type(exc).__name__)
            else:
                if any((message_position(row) or (0,''))[0]>sequence for row in head):
                    self.cursor=None
                    return None
        return {'messages':new_rows}

    async def poll(self,api,client,env,group):
        if self.cursor is not None:
            result=await self.incremental(api,client,env,group)
            if result is not None:
                return result
        rows=[];failure=None;now=time.monotonic()
        try:
            rows=await history_rows(api,client,env,group,HISTORY_BATCH)
        except Exception as exc:failure=exc;rows=[]
        newest=max((float(row.get('time',0)) for row in rows),default=0)
        if newest>self.latest and failure is None:
            self.latest=newest;self.next_probe=now+30
        elif now>=self.next_probe:
            self.next_probe=now+30
            try:
                contacts=await asyncio.wait_for(api(client,env,'get_recent_contact',{'count':100}),3)
                if not isinstance(contacts,list):contacts=[]
                for contact in contacts:
                    if str(contact.get('chatType'))!='2' or str(contact.get('peerUin'))!=str(group):continue
                    if float(contact.get('msgTime',0))<=newest and failure is None:break
                    identity=contact.get('msgId')
                    if not identity:break
                    # A recent-contact snapshot can outlive the message it points
                    # to, or refer to an event NapCat cannot parse as a message.
                    # This optional recovery must not turn that into a poll error.
                    try:
                        message=await asyncio.wait_for(api(client,env,'get_msg',{'message_id':identity}),3)
                    except NapCatAPIError as exc:
                        if exc.action=='get_msg' and re.search(r'消息为空|消息不存在|已被撤回|(?:message|msg).*(?:empty|not found|does not exist|recalled|deleted)',exc.detail,re.I):
                            log.debug('Latest message skipped; reason=empty_or_unavailable; retcode=%s',exc.retcode)
                            break
                        raise
                    if str(message.get('group_id'))!=str(group) or not message.get('message_id'):break
                    rows.append(message);self.latest=max(self.latest,float(message.get('time',0)))
                    log.info('Latest message recovered from recent contact; group=%s',group)
                    failure=None;break
            except Exception as exc:
                log.warning('Latest message probe unavailable; error_type=%s; action=%s; retcode=%s; detail=%s',
                    type(exc).__name__,getattr(exc,'action','unknown'),getattr(exc,'retcode','unknown'),safe_log(str(exc))[:300])
        if failure:raise failure
        unique={str(row.get('message_id',row.get('message_seq',''))):row for row in rows}
        self.seed(unique.values())
        return {'messages':sorted(unique.values(),key=lambda row:row.get('time',0))}
