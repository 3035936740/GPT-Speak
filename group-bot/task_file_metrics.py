"""Measured file sizes and text lengths, separate from model token accounting."""
import hashlib


def describe(name,raw,hashed=False):
    from task_sandbox import preview
    text=preview(raw)
    row={'name':name,'bytes':len(raw),'binary':text is None}
    if text is not None:row['characters']=len(text)
    if hashed:row['sha256']=hashlib.sha256(raw).hexdigest()
    return row
