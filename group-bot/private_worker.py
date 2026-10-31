"""Private chat worker owned by the same console as the group workers."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from console_config import Config
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
    return await bot.generate_json({key: value for key, value in {**cfg, 'group_id': 0, '_private_context':True}.items() if key != '_private_image_paths'},
                                   prompt, schema, images or None)


async def run(check=False):
    api = NapCat(ROOT)
    service = PrivateChat(ROOT, api, generate)
    try:
        if check:
            await service.refresh(Config(ROOT).load())
            print('Private OneBot APIs and group membership: PASS')
            print('Eligible friends:', sum(service.can_chat(p) for p in service.friends))
            return
        while service.active():
            try:
                await service.tick()
            except Exception as exc:
                service.status(False, type(exc).__name__)
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
