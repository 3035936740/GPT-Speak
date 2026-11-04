"""Embedding configuration shared by index builds and queries."""
from array import array
from dataclasses import dataclass, field
from hashlib import sha256
from math import isfinite, sqrt
from pathlib import Path
import re
from time import perf_counter
from urllib.parse import urlsplit

import httpx


DEFAULTS = {
    'knowledge_embedding_provider':'ollama',
    'knowledge_embedding_model':'bge-m3',
    'knowledge_embedding_ollama_url':'http://127.0.0.1:11434',
    'knowledge_embedding_ollama_cpu':True,
    'knowledge_embedding_api_url':'https://api.openai.com/v1',
    'knowledge_reranker_url':'',
    'knowledge_reranker_model':'',
}
KEY_FILE = 'knowledge-embedding.key'
MODES = {'keyword':'关键词索引','vector':'向量 RAG','hybrid':'关键词＋向量 RAG'}
VECTOR_MODES = {'vector','hybrid'}


def validate(cfg):
    if cfg['knowledge_retrieval_mode'] not in MODES:
        raise ValueError('知识库检索模式须为 keyword、vector 或 hybrid')
    if cfg.get('memory_retrieval_mode','keyword') not in MODES:
        raise ValueError('群记忆检索模式须为 keyword、vector 或 hybrid')
    if cfg.get('private_memory_retrieval_mode','keyword') not in MODES:
        raise ValueError('私聊记忆检索模式须为 keyword、vector 或 hybrid')
    if cfg['knowledge_embedding_provider'] not in {'ollama','openai_compatible'}:
        raise ValueError('未知 Embedding 服务类型')
    if not re.fullmatch(r'[\w./:-]{1,160}',cfg['knowledge_embedding_model'],re.ASCII):
        raise ValueError('Embedding 模型名无效')
    for field, schemes in [('knowledge_embedding_ollama_url',{'http','https'}),('knowledge_embedding_api_url',{'https'})]:
        url = urlsplit(cfg[field])
        if url.scheme not in schemes or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Embedding 地址无效，请填写无凭据的服务基础地址')
        if field.endswith('ollama_url') and url.hostname not in {'localhost','127.0.0.1','::1'}:
            raise ValueError('Ollama 地址须指向本机')
        _ = url.port
    if cfg['knowledge_reranker_url']:
        url = urlsplit(cfg['knowledge_reranker_url'])
        local = url.hostname in {'localhost','127.0.0.1','::1'}
        if url.scheme not in ({'http','https'} if local else {'https'}) or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Reranker 地址须为本机 HTTP 或 HTTPS，且不包含凭据')
        _ = url.port

def save_key(root,key):
    if not isinstance(key,str):
        raise ValueError('Embedding API Key 格式无效')
    key = key.strip()
    if len(key)>512 or any(not 33<=ord(char)<=126 for char in key):
        raise ValueError('Embedding API Key 格式无效')
    from storage_io import atomic_write_text
    atomic_write_text(Path(root)/KEY_FILE,key)


def read_key(root):
    try:
        return (Path(root)/KEY_FILE).read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return ''


@dataclass(frozen=True)
class Embedding:
    provider: str
    model: str
    base: str
    key: str = field(repr=False)
    cpu: bool = False

    @property
    def index(self):
        return sha256(f'1\n{self.provider}\n{self.base}\n{self.model}\n{self.key}'.encode()).hexdigest()

    def embed(self,texts,timeout):
        data = {'model':self.model,'input':texts}
        if self.provider=='ollama':
            if self.model.endswith((':cloud','-cloud')) or self.model.startswith('https:'):
                raise ValueError('请在 Ollama 模式使用本地模型')
            endpoint = '/api/embed'
            data.update(keep_alive='2m',truncate=True)
            if self.cpu:
                data['options']={'num_gpu':0}
            headers = {}
        else:
            endpoint = '' if self.base.endswith('/embeddings') else '/embeddings'
            headers = {'Authorization':'Bearer '+self.key} if self.key else {}
        with httpx.Client(trust_env=False, timeout=timeout, follow_redirects=False) as client:
            response = client.post(self.base+endpoint,json=data,headers=headers)
            response.raise_for_status()
            result = response.json()
        if self.provider=='ollama':
            vectors = result['embeddings']
        else:
            entries = sorted(result['data'],key=lambda row:row['index'])
            if [row['index'] for row in entries]!=list(range(len(texts))):
                raise ValueError('Embedding API 返回的索引无效')
            vectors = [row['embedding'] for row in entries]
        return normalized(vectors,len(texts))


def configured(cfg,root):
    values = {**DEFAULTS,**cfg}
    provider = values['knowledge_embedding_provider']
    if provider=='ollama':
        return Embedding(provider,values['knowledge_embedding_model'],values['knowledge_embedding_ollama_url'].rstrip('/'),'',
                         values['knowledge_embedding_ollama_cpu'])
    return Embedding(provider,values['knowledge_embedding_model'],values['knowledge_embedding_api_url'].rstrip('/'),
                     read_key(root))


def test_connection(cfg, root):
    """Check the configured model with a fixed string, independent of the index."""
    embedding = configured(cfg, root)
    started = perf_counter()
    try:
        vector, = embedding.embed(['Embedding connection test'], timeout=12)
    except httpx.TimeoutException:
        message = 'Embedding 服务响应超时'
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        reason = {
            400: '请求被拒绝，请检查模型与服务配置',
            401: '认证失败，请检查 API Key',
            403: '访问被拒绝，请检查 API Key 和模型权限',
            404: '请检查服务地址和模型名称，并确认本机模型已下载',
            429: '服务限流或额度不足',
        }.get(status, '服务返回错误')
        message = f'{reason}（HTTP {status}）'
    except httpx.RequestError:
        message = '无法连接 Embedding 服务，请检查服务是否运行及地址是否正确'
    except (KeyError, ValueError, TypeError, OverflowError):
        message = 'Embedding 测试未取得有效向量，请检查模型和服务配置'
    else:
        elapsed = round((perf_counter() - started) * 1000)
        return {'ok': True, 'message': f'测试成功 · {embedding.model} · {len(vector)} 维 · {elapsed} ms'}
    return {'ok': False, 'message': message}


def normalized(vectors,count):
    if not isinstance(vectors,list) or len(vectors)!=count:
        raise ValueError('Invalid embedding count')
    result = []
    dimension = None
    for vector in vectors:
        if not isinstance(vector,list) or not 1<=len(vector)<=8192 or dimension not in (None,len(vector)):
            raise ValueError('Invalid embedding dimensions')
        dimension = len(vector)
        norm = sqrt(sum(value*value for value in vector))
        if not isfinite(norm) or not norm:
            raise ValueError('Invalid embedding norm')
        result.append(array('f',(value/norm for value in vector)))
    return result
