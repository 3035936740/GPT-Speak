"""Ephemeral, contact-local image attachments from validated QQ image sources."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import tempfile

import httpx
from PIL import Image
from image_sources import failure_details, validate_source


@asynccontextmanager
async def prepare(rows, cfg, folder, event, client=None, avatars=()):
    pictures = []
    for row in rows:
        segments = row.get('message', [])
        if isinstance(segments, list):
            pictures.extend({'message_id': str(row.get('message_id') or row.get('real_id') or ''),
                             'position': index + 1, 'url': segment.get('data', {}).get('url', '')}
                            for index, segment in enumerate(segments) if segment.get('type') == 'image')
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
    limit = min(4, max(1, int(cfg.get('max_images_per_call', 2))))
    owned_client = client is None
    client = client or httpx.AsyncClient(trust_env=False, timeout=15)
    try:
        with tempfile.TemporaryDirectory(prefix='vision-', dir=folder) as temporary:
            paths = []
            for picture in pictures[-limit:]:
                path = Path(temporary) / (str(len(paths) + 1) + '.jpg')
                try:
                    validate_source(picture['url'],allow_avatar=picture.get('avatar',False))
                    await asyncio.wait_for(download_image(client, picture['url'], path, cfg), timeout=20)
                    paths.append(path)
                    context['attached_images'].append({'image_index': len(paths), 'message_id': picture['message_id'], 'segment': picture['position'],
                        **({'avatar_qq_id':picture['qq_id'],'label':picture['label']} if picture.get('avatar') else {})})
                except (OSError, ValueError, httpx.HTTPError, Image.DecompressionBombError, Image.DecompressionBombWarning, TimeoutError) as error:
                    event('stage', '私聊图片读取失败；' + failure_details(error, picture['url']), level='WARNING')
            context['unread_images'] -= len(paths)
            event('stage', '私聊识图：已附带 ' + str(len(paths)) + ' 张，未读取 ' + str(context['unread_images']) + ' 张',
                  details={'attached_images': len(paths), 'unread_images': context['unread_images']})
            yield paths, context
    finally:
        if owned_client:
            await client.aclose()
