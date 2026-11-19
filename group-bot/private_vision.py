"""Ephemeral, contact-local image attachments from validated QQ image sources."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import tempfile

import httpx
from PIL import Image
from image_sources import failure_details, validate_source


def with_quotes(rows, history):
    from private_chat import message_key
    by_id = {message_key(row): row for row in history}
    seen = {message_key(row) for row in rows}
    result = list(rows)
    for row in rows:
        segments = row.get('message', [])
        for segment in segments if isinstance(segments, list) else []:
            if segment.get('type') != 'reply':
                continue
            identity = str(segment.get('data', {}).get('id') or '')
            if identity and identity not in seen and identity in by_id:
                seen.add(identity)
                result.append(by_id[identity])
    return result


@asynccontextmanager
async def prepare(rows, cfg, folder, event, client=None, avatars=(), peer=''):
    pictures = []
    for row in rows:
        segments = row.get('message', [])
        if isinstance(segments, list):
            slot = 0
            for index, segment in enumerate(segments):
                if segment.get('type') != 'image':
                    continue
                url = segment.get('data', {}).get('url', '')
                try:
                    validate_source(url)
                    cache_slot = slot
                    slot += 1
                except ValueError:
                    cache_slot = None
                pictures.append({'message_id': str(row.get('message_id') or row.get('real_id') or ''),
                                 'position': index + 1, 'url': url, 'cache_slot': cache_slot})
    if cfg.get('model_avatar_query_enabled',True):
        pictures.extend({'message_id':'public-avatar','position':0,'url':'https://q1.qlogo.cn/g?b=qq&s=640&nk='+uid,
            'avatar':True,'qq_id':uid,'label':label} for uid,label in avatars)
    enabled = cfg['private_vision_enabled']
    context = {'enabled': enabled, 'total_images': len(pictures), 'attached_images': [], 'unread_images': len(pictures)}
    if not pictures or not enabled:
        if pictures:
            event('stage', '私聊识图已关闭；未读取本轮图片', details={'image_count': len(pictures)})
        yield [], context
        return
    from bot import download_image
    from model_attachments import limits,notice
    limit = min(limits(cfg)[0], max(1, int(cfg.get('max_images_per_call', 2))))
    owned_client = client is None
    client = client or httpx.AsyncClient(trust_env=False, timeout=15)
    try:
        with tempfile.TemporaryDirectory(prefix='vision-', dir=folder) as temporary:
            paths = []
            context['omitted_images']=max(0,len(pictures)-limit)
            for picture in pictures[:limit]:
                path = Path(temporary) / (str(len(paths) + 1) + '.jpg')
                try:
                    validate_source(picture['url'],allow_avatar=picture.get('avatar',False))
                    from private_media import preview_path
                    from model_attachments import too_large
                    slot = picture.get('cache_slot')
                    cached = preview_path(folder, peer, picture['message_id'], slot) if peer and slot is not None else None
                    if cached and cached.is_file() and not too_large(cached.stat().st_size, cfg) and cached.stat().st_size <= cfg.get('max_image_bytes', 8388608):
                        from PIL import ImageOps
                        with Image.open(cached) as source:
                            if source.width * source.height > 20000000:
                                raise ValueError('image_pixel_limit')
                            image = ImageOps.exif_transpose(source).convert('RGB')
                            image.thumbnail((1536, 1536))
                            image.save(path, 'JPEG', quality=85)
                    else:
                        await asyncio.wait_for(download_image(client, picture['url'], path, cfg), timeout=20)
                    paths.append(path)
                    context['attached_images'].append({'image_index': len(paths), 'message_id': picture['message_id'], 'segment': picture['position'],
                        **({'avatar_qq_id':picture['qq_id'],'label':picture['label']} if picture.get('avatar') else {})})
                except (OSError, ValueError, httpx.HTTPError, Image.DecompressionBombError, Image.DecompressionBombWarning, TimeoutError) as error:
                    if getattr(error,'code',None)=='model_file_too_large':context.setdefault('rejected_files',[]).append(notice(picture['position'],cfg))
                    event('stage', '私聊图片读取失败；' + failure_details(error, picture['url']), level='WARNING')
            context['unread_images'] -= len(paths)
            event('stage', '私聊识图：已附带 ' + str(len(paths)) + ' 张，未读取 ' + str(context['unread_images']) + ' 张',
                  details={'attached_images': len(paths), 'unread_images': context['unread_images']})
            yield paths, context
    finally:
        if owned_client:
            await client.aclose()
