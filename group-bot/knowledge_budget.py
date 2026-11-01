"""Knowledge uses leftover prompt space after conversational context is fitted."""
import json


def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def fit_references(rows,budget):
    result=[]
    for original in rows:
        row=dict(original)
        content=row.get('content','')
        if not isinstance(content,str) or not content:continue
        if len(encoded(result+[row]))<=budget:
            result.append(row);continue
        row['truncated']=True
        lo=0;hi=len(content)
        while lo<hi:
            mid=(lo+hi+1)//2;row['content']=content[:mid]
            if len(encoded(result+[row]))<=budget:lo=mid
            else:hi=mid-1
        if lo>=min(80,len(content)):
            row['content']=content[:lo];result.append(row)
        # Results are ranked; do not trade the best partial match for many weak ones.
        break
    return result


def attach(prefix,data,key,rows,limit):
    if not rows:return prefix+encoded(data)
    existed=key in data
    data[key]=[]
    room=limit-len(prefix)-len(encoded(data))+2
    if room<2:
        if not existed:data.pop(key,None)
        return prefix+encoded(data)
    data[key]=fit_references(rows,room)
    return prefix+encoded(data)
