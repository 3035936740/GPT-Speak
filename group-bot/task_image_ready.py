"""Wait for the images belonging to the current artifact request."""
import asyncio
import time

from content_reference import resolve
from image_memory import source_key
from model_attachments import select


async def wait(cfg, target, context, *, drawing=False):
    def current():
        latest = next((row for row in reversed(context)
                       if str(row.get('_id')) == str(target.get('_id'))), {})
        merged = {**target}
        for field in ('_images', '_image_keys', '_image_count', '_quoted_message'):
            if latest.get(field):
                merged[field] = latest[field]
        return merged, resolve(merged, context, for_drawing=drawing)

    message, reference = current()
    quote = message.get('_quoted_message') or next(
        (row for row in context if message.get('_reply_id') and
         str(row.get('_id')) == str(message['_reply_id'])), {})
    expected = max(message.get('_image_count', 0), quote.get('_image_count', 0),
                   len(reference.get('images', [])))
    if not expected and not reference.get('image_hashes'):
        return reference

    async def ready():
        nonlocal reference
        while True:
            _, reference = current()
            images, _, _ = select(reference, cfg)
            needed = min(expected, cfg.get('model_max_files', 10))
            if len(images) >= needed or reference.get('image_hashes') and not images:
                break
            await asyncio.sleep(.2)
        reader = cfg.get('_image_reader')
        if reader and images:
            keys = reference.get('image_keys', {})
            summaries = await asyncio.gather(*(reader.read(url, keys.get(url) or source_key({'url': url}))
                                               for url in images))
            if not all(summaries):
                raise ValueError('任务参考图片识别未完成，请稍后重试')
            source = next((row for row in context if str(row.get('_id')) == str(reference.get('source_id'))),
                          quote or target)
            reader.publish(source, summaries, 'read')
        return reference

    started = time.monotonic()
    timeout = cfg.get('task_image_wait_timeout_seconds', 30)
    try:
        result = await asyncio.wait_for(ready(), timeout)
    except asyncio.TimeoutError as error:
        raise ValueError(f'等待任务参考图片就绪超过{timeout}秒，请稍后重试') from error
    import logging
    logging.getLogger('qq-group-bot').info('Task images ready; seconds=%.1f; images=%s',
        time.monotonic() - started, len(result.get('images', [])))
    return result
