"""Non-executing structural checks before a generated artifact can be uploaded."""
import ast
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from xml.etree import ElementTree


def problem(name,content):
    if not content.strip():return 'empty_content'
    compact=re.sub(r'\s+','',content)
    if re.fullmatch(r'(.{1,256})\1{128,}',compact,re.S):return 'meaningless_repetition'
    lines=[line.strip() for line in content.splitlines() if line.strip()]
    if len(lines)>=128:
        from collections import Counter
        if Counter(lines).most_common(1)[0][1]>=len(lines)*.9:return 'meaningless_repetition'
    suffix=Path(name).suffix.lower()
    try:
        if suffix in {'.html','.htm'}:
            if not re.search(r'<html\b',content,re.I) or not re.search(r'</html\s*>\s*$',content,re.I):return 'incomplete_html_document'
            class Structure(HTMLParser):
                def __init__(self):super().__init__();self.stack=[];self.broken=False
                def handle_starttag(self,tag,attrs):
                    if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:self.stack.append(tag)
                def handle_startendtag(self,tag,attrs):pass
                def handle_endtag(self,tag):
                    while self.stack and self.stack[-1] in {'p','li','dt','dd','option','thead','tbody','tfoot','tr','td','th'} and self.stack[-1]!=tag:self.stack.pop()
                    if not self.stack or self.stack[-1]!=tag:self.broken=True
                    else:self.stack.pop()
            parser=Structure();parser.feed(content);parser.close()
            if parser.broken or parser.stack:return 'unclosed_html_structure'
        elif suffix=='.json':json.loads(content)
        elif suffix=='.py':ast.parse(content)
        elif suffix in {'.xml','.svg'}:ElementTree.fromstring(content)
        elif suffix=='.md':
            fences=re.findall(r'^\s*(`{3,}|~{3,})',content,re.M)
            if len(fences)%2:return 'unclosed_markdown_fence'
    except (ValueError,SyntaxError,ElementTree.ParseError):return 'incomplete_or_invalid_'+suffix.lstrip('.')
    return ''
