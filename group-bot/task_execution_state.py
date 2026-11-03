"""Small persistent task state and objective output progress; stdout is not progress."""
import hashlib
import json

PROBE=r'''
import codecs,hashlib,json
from pathlib import Path
rows=[];total=0
for p in sorted(Path('output').rglob('*')):
 if p.is_symlink():raise ValueError('Output links forbidden')
 if p.is_dir():continue
 total+=p.stat().st_size
 if total>CAP or len(rows)>=64:raise ValueError('Output limit exceeded')
 h=hashlib.sha256();decoder=codecs.getincrementaldecoder('utf-8-sig')();characters=0;binary=False
 with p.open('rb') as f:
  for block in iter(lambda:f.read(65536),b''):
   h.update(block)
   if not binary:
    try:
     text=decoder.decode(block);characters+=len(text)
     binary=any(ord(c)<32 and c not in '\r\n\t' for c in text)
    except UnicodeError:binary=True
 if not binary:
  try:characters+=len(decoder.decode(b'',final=True))
  except UnicodeError:binary=True
 row={'name':p.relative_to('output').as_posix(),'bytes':p.stat().st_size,'sha256':h.hexdigest(),'binary':binary}
 if not binary:row['characters']=characters
 rows.append(row)
print(json.dumps(rows))
'''


class Progress:
    def __init__(self,previous=None,limit=6):
        self.state=dict(previous or {});self.limit=limit;self.stale=0;self.signature=None
        self.state.setdefault('plan',[]);self.state.setdefault('completed',[])
    def baseline(self,inventory):
        self.signature=self.fingerprint(inventory);self.state['files']=inventory
    @staticmethod
    def fingerprint(rows):
        contents=sorted({(r.get('sha256',''),r.get('bytes',0)) for r in rows})
        return hashlib.sha256(json.dumps(contents).encode()).hexdigest()
    def observe(self,result,exit_code,output,inventory,code_path=''):
        supplied=result.get('state',{})
        if isinstance(supplied,dict):
            for key in ('plan','completed','phase','next_step'):
                if key in supplied:self.state[key]=supplied[key]
        self.state['last_action']={'code':result.get('code',''),'summary':result.get('summary',''),
            'exit_code':exit_code,'output':output[-1500:],'code_path':code_path}
        if inventory is None:return ''
        signature=self.fingerprint(inventory)
        changed=signature!=self.signature;self.signature=signature
        self.stale=0 if changed else self.stale+1
        self.state.update(files=inventory,no_output_change_rounds=self.stale,
            evidence='交付目录内容已变化' if changed else '交付目录内容未变化；执行成功或新建分析文件不代表完成任务')
        self.state.pop('correction',None)
        if self.limit and self.stale>=max(2,self.limit//2):
            self.state['correction']='多轮没有目标文件变化。停止重复解析同一输入、打印整曲音符或给同一分析换文件名。复用已有解析文件，按现有计划直接编写/修改output中的可交付文件，验证后返回done；无法推进则明确说明阻塞原因。'
        return 'Task stalled: repeated analysis without deliverable changes; no upload' if self.limit and self.stale>=self.limit else ''
