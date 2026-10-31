"""Validate the exact outgoing prose without logging model or chat content."""
import re


def prepare(result, limit):
    """Return a normalized result, sendable text, and a content-free rejection code.

    Empty chunks and paragraph spacing are harmless formatting. Never shorten an
    over-budget answer or bypass rejected chunks with alternate `text` content.
    """
    if result.get('should_reply') is not True:
        return result, '', 'model_silence'
    parts = result.get('text_parts', [])
    if not isinstance(parts, list) or any(not isinstance(p, str) for p in parts):
        return result, '', 'invalid_text_parts_type'
    if len(parts) > 10:
        return result, '', 'too_many_text_parts'

    def clean(value):
        return re.sub(r'\n[\s\n]*\n', '\n', value.replace('\r\n', '\n').replace('\r', '\n')).strip().rstrip('。').rstrip()

    normalized = [value for part in parts if (value := clean(part))]
    if normalized:
        text = '\n'.join(normalized)
    else:
        raw = result.get('text', '')
        if not isinstance(raw, str):
            return result, '', 'invalid_text_type'
        text = clean(raw)
    prepared = {**result, 'text': text, 'text_parts': normalized}
    if not text:
        return prepared, '', 'empty_text'
    if len(text) > limit:
        return prepared, '', 'text_over_limit'
    if '[CQ:' in text:
        return prepared, '', 'raw_cq_code'
    if '```' in text:
        return prepared, '', 'code_fence'
    return prepared, text, ''


def diagnostic(result, reason, limit):
    """Metadata only: do not persist generated prose or unrelated model fields."""
    raw = result.get('text')
    parts = result.get('text_parts')
    lengths = ([len(p) if isinstance(p, str) else -1 for p in parts[:10]]
        if isinstance(parts, list) else [])
    return (f'validation={reason}; text_chars={len(raw) if isinstance(raw, str) else -1}; '
        f'part_chars={lengths}; limit={limit}')
