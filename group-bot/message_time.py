"""Second-precision chat times and one replaceable, non-persistent turn clock."""
from datetime import datetime
import json
import math
import os
import re
import subprocess
import time

INSTRUCTION='\n时间：sent_at为原消息时间（精确到秒，带数字时区偏移）；generation_clock是设备本地date格式的本轮生成前参考，可能为英文CST或日文JST等，不是尚未发出的回复时间。不假定固定时区；按实际提供的时分秒回答，缺失则说明未知。\n'


def epoch(value):
    try:
        if isinstance(value,bool):return None
        value=float(value)
        if not math.isfinite(value) or value<=0:return None
        if value>=100000000000:value/=1000
        datetime.fromtimestamp(value).astimezone()
        return value
    except (ValueError,TypeError,OSError,OverflowError):return None


def formatted(value):
    stamp=epoch(value)
    return datetime.fromtimestamp(stamp).astimezone().isoformat(sep=' ',timespec='seconds') if stamp is not None else None


def clock():
    if os.name!='nt':
        try:
            result=subprocess.run(['date'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=1,check=True)
            value=result.stdout.strip()
            if value and len(value)<256:return value
        except (OSError,subprocess.SubprocessError):pass
    now=datetime.now().astimezone()
    return now.strftime('%a %b ')+str(now.day).rjust(2)+now.strftime(' %I:%M:%S %p %Z %Y')


def sent_timestamp(response,fallback):
    for key in ('time','timestamp'):
        stamp=epoch((response or {}).get(key))
        if stamp is not None:return stamp
    return fallback


def quoted(target,history):
    source=target.get('_quoted_message') or {}
    if source and epoch(source.get('_time')) is None:
        saved=next((row for row in history if str(row.get('_id'))==str(target.get('_reply_id'))),{})
        if epoch(saved.get('_time')) is not None:source={**source,'_time':saved['_time']}
    return source


def refresh(prompt):
    # Builders budget this field. Do not append another clock, or
    # touch escaped user text containing the same words.
    return re.sub(r'(?<!\\)("generation_clock"\s*:\s*)"(?:[^"\\]|\\.)*"',
        lambda match:match[1]+json.dumps(clock(),ensure_ascii=False),prompt,count=1)
