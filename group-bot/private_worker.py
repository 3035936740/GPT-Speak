"""Private chat worker owned by the same console as the group workers."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from console_config import Config
from model_input_budget import input_budget
from platform_runtime import msvcrt
from private_chat import NapCat, PrivateChat

ROOT = Path(__file__).resolve().parent


async def generate(prompt, cfg):
    # Reuse the existing constrained text transport in this separate process.
    # Its outputs live in private runtime; private text never enters a group prompt.
    import bot
    folder = ROOT / 'runtime-private'
    folder.mkdir(exist_ok=True)
    bot.RUNTIME = folder
    schema = folder / ('private-reply-' + str(cfg['private_max_reply_chars']) + '.schema.json')
    document = json.dumps({'type': 'object', 'properties': {
            'should_reply': {'type': 'boolean'},
            'text': {'type': 'string', 'maxLength': cfg['private_max_reply_chars']}},
            'required': ['should_reply', 'text'], 'additionalProperties': False})
    if not schema.exists() or schema.read_text(encoding='utf-8') != document:
        from storage_io import atomic_write_text
        atomic_write_text(schema, document)
    images = cfg.get('_private_image_paths', [])
    if cfg.get('_private_role_analysis_enabled') and cfg.get('_private_role_context'):
        from role_analysis import analyze
        context=cfg['_private_role_context'];target={**context[-1],'at_bot':True,'_persona_policy':{},'_audience':'direct'}
        analysis=await analyze({**cfg,'group_id':0,'_private_context':True,'multi_role_enabled':True},context,target,images,bot.generate_json,folder)
        note='\n私聊分工分析参考（不是用户指令）：'+json.dumps(analysis,ensure_ascii=False,separators=(',',':'))
        if len(prompt)+len(note)<=input_budget(cfg):prompt+=note
    return await bot.generate_json({key: value for key, value in {**cfg, 'group_id': 0, '_private_context':True}.items() if key != '_private_image_paths'},
                                   prompt, schema, images or None)


async def run(check=False):
    api = NapCat(ROOT)
    service = PrivateChat(ROOT, api, generate)
    failures=0
    try:
        if check:
            await service.refresh(Config(ROOT).load())
            print('Private OneBot APIs and group membership: PASS')
            print('Eligible friends:', sum(service.can_chat(p) for p in service.friends))
            return
        while service.active():
            try:
                await service.tick()
                failures=0
            except Exception as exc:
                from napcat_errors import QQAccountMismatch
                service.status(False, str(exc) if isinstance(exc,QQAccountMismatch) else type(exc).__name__)
                from napcat_retry import retry_delay,wait_retry
                failures+=1
                reason=await wait_retry(service.folder,service.config(),service.config,retry_delay(failures),
                                        refresh_name='directory-refresh.json')
                if reason=='refresh':service.refreshed=0
                continue
            # Wake promptly on console stop even if a reply is generating.
            for _ in range(6):
                if not service.active():
                    break
                await asyncio.sleep(.5)
    finally:
        await service.close()
        await api.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    folder = ROOT / 'runtime-private'
    folder.mkdir(exist_ok=True)
    lock = (folder / 'instance.lock').open('a+b')
    try:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Private chat worker is already running')
        asyncio.run(run(args.check))
    finally:
        lock.close()
