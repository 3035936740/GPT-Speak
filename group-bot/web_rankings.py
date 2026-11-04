"""Validate and deliver complete numbered web results."""
import re


def requested_count(text):
    match = re.search(r'(?:前\s*|\btop\s+)(\d{1,3})(?!\d|\s*[天年月小时分钟秒%％])', str(text), re.I)
    count = int(match.group(1)) if match else 0
    return count if 2 <= count <= 150 else 0


def ranking_rows(text, count):
    rows = re.findall(r'^\s*(\d{1,3})[.、．)）]\s*(\S[^\r\n]*)$', text, re.M)
    if [int(rank) for rank, _ in rows] != list(range(1, count + 1)):
        return []
    names = [re.split(r'\s*[｜|]\s*', name, maxsplit=1)[0].strip().casefold() for _, name in rows]
    if len(set(names)) != count or any(not name or '…' in name or '...' in name for name in names):
        return []
    return [f'{rank}. {name.strip()}' for rank, name in rows]


def chunks(rows, source):
    parts, block = [], ''
    for line in [*rows, '来源 ' + source]:
        if block and len(block) + len(line) + 1 > 450:
            parts.append(block)
            block = ''
        block = block + '\n' + line if block else line
    return parts + [block]
