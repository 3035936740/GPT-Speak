"""Bounded PDF text extraction in a disposable process; never execute document actions."""
import asyncio
import json
from pathlib import Path
import sys


async def extract(raw):
    from platform_runtime import process_options,kill_process_group
    proc=await asyncio.create_subprocess_exec(sys.executable,'-I','-u',str(Path(__file__).resolve()),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,**process_options())
    try:
        output,_=await asyncio.wait_for(proc.communicate(raw),15)
        result=json.loads(output)
        if not result.get('text'):raise ValueError(result.get('error','pdf_unreadable'))
        return result
    finally:
        if proc.returncode is None:
            kill_process_group(proc)
            try:proc.kill()
            except ProcessLookupError:pass
            await proc.wait()


def extract_local(raw):
    import io
    from pypdf import PdfReader,overwrite_configuration
    overwrite_configuration(maximum_declared_stream_length=8*1024*1024,
        zlib_maximum_output_length=8*1024*1024,lzw_maximum_output_length=8*1024*1024,
        run_length_maximum_output_length=8*1024*1024,array_based_stream_maximum_output_length=8*1024*1024)
    reader=PdfReader(io.BytesIO(raw),strict=False)
    if reader.is_encrypted and not reader.decrypt(''):return {'error':'pdf_encrypted'}
    count=len(reader.pages);text='';read=0;empty=0;stream_bytes=0
    for index in range(min(count,50)):
        page=reader.pages[index];stream=page.get_contents()
        if stream:
            stream_bytes+=len(stream.get_data())
            if stream_bytes>16*1024*1024:break
        body=(page.extract_text() or '').strip();read+=1
        if not body:empty+=1;continue
        text+='\n[PDF page '+str(index+1)+']\n'+body
        if len(text)>12000:break
    if not text.strip():return {'error':'pdf_no_text'}
    return {'text':text[:12000],'pages':count,'pages_read':read,'empty_pages':empty,
        'truncated':read<count or len(text)>12000 or empty>0,
        'extraction_note':'PDF文字提取；未识别图片和图表，公式与排版可能缺失，不得补造缺失题目'}


if __name__=='__main__':
    try:
        if sys.platform!='win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(384*1024*1024,384*1024*1024))
        result=extract_local(sys.stdin.buffer.read())
    except ImportError:result={'error':'pdf_dependency_missing'}
    except Exception:result={'error':'pdf_unreadable'}
    sys.stdout.write(json.dumps(result,ensure_ascii=True))
