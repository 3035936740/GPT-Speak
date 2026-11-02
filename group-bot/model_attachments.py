"""Limits for model input files, independent of operator QQ uploads."""
DEFAULTS={'model_max_files':10,'model_max_file_bytes':5*1024*1024,'group_file_read_enabled':True}


def limits(cfg):
    return int(cfg.get('model_max_files',10)),int(cfg.get('model_max_file_bytes',5*1024*1024))


def too_large(size,cfg):
    try:return int(size)>=limits(cfg)[1]
    except (ValueError,TypeError,OverflowError):return False


def notice(index,cfg,reason='too_large',**extra):
    return {'file_index':index,'status':reason,**extra,'reason':
        ('文件太大，达到或超过'+str(limits(cfg)[1])+'字节，不读取或处理' if reason=='too_large'
         else '附件未读取，不猜测其内容')}


async def read_bytes(response,cfg):
    if too_large(response.headers.get('content-length'),cfg):
        from image_sources import ImageDownloadError
        raise ImageDownloadError('model_file_too_large')
    result=bytearray()
    async for chunk in response.aiter_bytes():
        if too_large(len(result)+len(chunk),cfg):
            from image_sources import ImageDownloadError
            raise ImageDownloadError('model_file_too_large')
        result.extend(chunk)
    return bytes(result)


def local_inputs(paths,cfg):
    from pathlib import Path
    selected=[];rejected=[]
    for index,path in enumerate(list(paths or [])[:limits(cfg)[0]],1):
        if Path(path).is_file() and too_large(Path(path).stat().st_size,cfg):rejected.append(notice(index,cfg))
        else:selected.append(path)
    return selected,rejected


def select(reference,cfg):
    images=reference.get('images',[]);files=reference.get('files',[])
    order=reference.get('attachment_order') or ([('image',i) for i in range(len(images))]+[('file',i) for i in range(len(files))])
    chosen=order[:limits(cfg)[0]]
    return ([images[i] for kind,i in chosen if kind=='image' and i<len(images)],
        [files[i] for kind,i in chosen if kind=='file' and i<len(files)],max(0,len(order)-len(chosen)))
