"""Reserve useful source evidence, then fill leftover space without growing the cap."""
import json


def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def seed_references(rows,limit):
    # Protect a small grounded excerpt before optional style, galleries or archive
    # consume the entire prompt. Retrieval remains unlimited, not delivery.
    return fit_references(rows,min(1800,max(200,limit//5))) if rows else []


def fit_references(rows,budget):
    result=[]
    used=2  # JSON array brackets.
    for original in rows:
        row=dict(original)
        content=row.get('content','')
        if not isinstance(content,str) or not content:continue
        room=budget-used-bool(result)  # A comma precedes every row after the first.
        size=len(encoded(row))
        if size<=room:
            used+=size+bool(result)
            result.append(row)
            continue
        row['truncated']=True
        lo=0;hi=len(content)
        while lo<hi:
            mid=(lo+hi+1)//2;row['content']=content[:mid]
            if len(encoded(row))<=room:lo=mid
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
