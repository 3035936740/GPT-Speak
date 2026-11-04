"""Resolve current/quoted/recent media references without importing unrelated threads."""
import re


def urls(text):
    # Chinese request text is often immediately adjacent to a pasted URL.
    found = re.findall(r'https?://[^\s<>"\x00-\x1f\u3000-\u9fff]+', text or '', re.I)
    return list(dict.fromkeys(u.rstrip('.,;!?)]}，。；！？）') for u in found))[:2]


def assets(item):
    if item.get('_record_only'):
        return {'links':[],'images':[],'files':[],'source_id':item.get('_id'),'image_hashes':[],'image_keys':{}}
    links = urls(item.get('_forward_outer_text','') if item.get('_forward') else item.get('text', ''))
    raw_images = item.get('_images', [])
    images = [u for u in raw_images if u]
    order = item.get('_attachment_order', [])
    if order and len(images) != len(raw_images):
        indexes = {old: new for new, old in enumerate(i for i, url in enumerate(raw_images) if url)}
        order = [(kind, indexes[index] if kind == 'image' else index)
                 for kind, index in order if kind != 'image' or index in indexes]
    return {'links': links, 'images': images,'files':item.get('_files',[]), 'source_id': item.get('_id'),
        'attachment_order':order,
        'image_sizes':item.get('_image_sizes',{}),
        'image_hashes':item.get('_image_hashes',[]) or list(item.get('_image_keys',{}).values()),
        'image_keys': {url: key for url, key in item.get('_image_keys', {}).items() if url in images}}


def quoted_assets(target, context):
    reply_id = target.get('_reply_id')
    saved = next((row for row in context if reply_id and str(row.get('_id')) == str(reply_id)), {})
    quote = target.get('_quoted_message') or saved
    if not quote.get('_images') and saved.get('_images'):
        quote = saved
    return assets(quote)


def direct_images(target, context):
    """Select only images explicitly attached to this turn or its quote."""
    quoted, own = quoted_assets(target, context), assets(target)
    if not (quoted['images'] or own['images']):
        return None
    order = []
    image_offset = file_offset = 0
    for source in (quoted, own):
        for kind, index in source['attachment_order'] or (
                [('image', i) for i in range(len(source['images']))] +
                [('file', i) for i in range(len(source['files']))]):
            order.append((kind, index + (image_offset if kind == 'image' else file_offset)))
        image_offset += len(source['images'])
        file_offset += len(source['files'])
    primary = quoted if quoted['images'] else own
    return {**primary, 'images': quoted['images'] + own['images'],
            'files': quoted['files'] + own['files'], 'attachment_order': order,
            'links': list(dict.fromkeys(quoted['links'] + own['links'])),
            'image_keys': {**quoted['image_keys'], **own['image_keys']},
            'image_sizes': {**quoted['image_sizes'], **own['image_sizes']}}


def resolve(target, context, *, for_drawing=False):
    own = assets(target)
    if for_drawing and own['images']:
        quote=target.get('_quoted_message') or next((row for row in context if target.get('_reply_id') and str(row.get('_id'))==str(target['_reply_id'])),{})
        source=assets(quote)
        if source['images']:
            images=list(dict.fromkeys(source['images']+own['images']))
            return {**source,'images':images,'links':list(dict.fromkeys(source['links']+own['links'])),
                'image_keys':{**source['image_keys'],**own['image_keys']},'image_sizes':{**source['image_sizes'],**own['image_sizes']},
                'attachment_order':[('image',i) for i in range(len(images))]}
    if own['links'] or own['images'] or own['files']:
        return own
    quoted = target.get('_quoted_message') or next((m for m in context
        if target.get('_reply_id') and m.get('_id') == target['_reply_id']), None)
    if quoted is not None:
        saved=next((m for m in context if target.get('_reply_id') and str(m.get('_id'))==str(target['_reply_id'])),None)
        if saved and not (quoted.get('_images') or quoted.get('_files')) and (saved.get('_images') or saved.get('_files')):quoted=saved
        # A quote anchors the request; don't substitute somebody else's later image.
        found=assets(quoted)
        if quoted.get('is_bot') and not (found['links'] or found['images']):
            found['links']=urls(quoted.get('_reply_to_text',''))
            if found['links']:
                found['source_id']=quoted.get('_reply_to_id')
                found['original_request']=quoted.get('_reply_to_text','')[:600]
        return found
    words = re.sub(r'@\d+|\[[^\]]*\]|\s+', '', target.get('text', ''))
    if for_drawing and not re.search(
            r'这(?:个|张|幅)?图|这张|上(?:面|一张)?(?:的)?图|刚(?:才|刚).{0,8}图|'
            r'原图|参考(?:一下)?(?:这|上|前)|按(?:照)?(?:这|上|前)|把.{0,8}(?:图|表情)|'
            r'(?:修改|改成|重画|重绘|编辑|照着|基于)', words):
        # A fresh drawing's noun “图片/表情包” is not a reference to the last
        # group's picture. Explicit attachments and quotes were handled above.
        return own
    image_request = for_drawing or bool(re.search(r'这图|这张|图片|看图|上图|图里|图中|表情包|image', words, re.I))
    link_request = bool(re.search(r'链接|网页|页面|网址|这篇|文章', words))
    file_request=bool(re.search(r'文件|日志|附件|文档',words))
    generic = bool(re.search(r'^(?:你)?(?:帮我|给我)?(?:看(?:一下|一眼|看|下)(?:这个|这是什么|这啥)?|总结(?:一下)?(?:这个)?|这是(?:什么|啥)|这啥|讲讲|解释(?:一下)?(?:这个)?)[（(!！?？\s]*$', words))
    if not (image_request or link_request or file_request or generic or not words):
        return own
    for item in reversed(list(context)):
        age = target.get('_time', 0) - item.get('_time', 0)
        if item.get('is_bot') or not 0 <= age <= 180 or item.get('_id') == target.get('_id'):
            continue
        found = assets(item)
        if not (found['images'] or found['links'] or found['files']):
            continue
        if not words and (item.get('_uid') != target.get('_uid') or age > 60):
            return own
        if image_request and not link_request and not found['images']:
            continue
        if link_request and not image_request and not found['links']:
            continue
        if file_request and not found['files']:continue
        return found
    return own
