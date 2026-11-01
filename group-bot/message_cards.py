"""Plain text for QQ link shares; native videos are record-only attachments."""
import json
import re
from html import unescape
from urllib.parse import urlsplit
from xml.etree import ElementTree


def text(value, limit):
    return re.sub(r'\s+', ' ', unescape(value)).strip()[:limit] if isinstance(value,str) else ''


def link(value):
    value=text(value,2048)
    try:
        parsed=urlsplit(value)
        return value if parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password else ''
    except ValueError:return ''


def card(segment):
    data=segment.get('data') or {}
    raw=data.get('data','')
    kind=segment.get('type')
    fields={}
    try:
        if kind=='share':
            url=link(data.get('url'))
            if url:fields={'title':text(data.get('title'),120),'description':text(data.get('content'),300),'url':url,'source':''}
        elif kind=='json':
            payload=json.loads(raw) if isinstance(raw,str) and len(raw)<=1048576 else raw if isinstance(raw,dict) else {}
            if not isinstance(payload,dict):return {}
            meta=payload.get('meta')
            candidates=[v for v in meta.values() if isinstance(v,dict)] if isinstance(meta,dict) else []
            for row in candidates:
                url=next((found for key in ('qqdocurl','jumpUrl','jump_url','url','newsUrl') if (found:=link(row.get(key)))),'')
                if not url:continue
                fields={'title':text(row.get('title') or row.get('name'),120),
                    'description':text(row.get('desc') or row.get('summary'),300),
                    'url':url,'source':text(row.get('tag') or row.get('source'),60)}
                break
        elif kind=='xml' and isinstance(raw,str) and len(raw)<=1048576:
            # Do not accept DTD/entity declarations or fetch any external content.
            if re.search(r'<!DOCTYPE|<!ENTITY',raw,re.I):return {}
            root=ElementTree.fromstring(raw)
            url=link(root.get('url'))
            if not url:
                url=next((found for row in root.iter() for key in ('url','jumpUrl','actionData') if (found:=link(row.get(key)))), '')
            if url:
                fields={'title':text(root.findtext('.//title'),120),
                    'description':text(root.findtext('.//summary'),300),'url':url,
                    'source':text(next((row.get('name') for row in root.iter('source')), ''),60)}
    except (ValueError,TypeError,ElementTree.ParseError,RecursionError):return {}
    return fields


def render(segment):
    fields=card(segment)
    if not fields:return '[卡片消息：内容暂不可解析]'
    return '[转发文本：链接卡片]\n'+'\n'.join(label+'：'+fields[key] for key,label in
        (('title','标题'),('description','摘要'),('url','链接'),('source','来源')) if fields[key])


def has_video(message):
    segments=message.get('message',[])
    return isinstance(segments,list) and any(isinstance(row,dict) and row.get('type')=='video' for row in segments)
