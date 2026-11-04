"""Bounded second-stage relevance ranking for retrieved passages."""
from math import isfinite, sqrt
from urllib.parse import urlsplit

import httpx

RECALL_LIMIT = 20
RESULT_LIMIT = 5


def configured_reranker(cfg, root, *, local_only=False):
    url = cfg.get('knowledge_reranker_url', '')
    if not url:
        return None
    endpoint = urlsplit(url)
    local = endpoint.hostname in {'localhost', '127.0.0.1', '::1'}
    if local_only and not local:
        return None
    key = ''
    if not local and cfg.get('knowledge_embedding_provider') == 'openai_compatible':
        from knowledge_embedding import read_key
        api = urlsplit(cfg['knowledge_embedding_api_url'])
        if (endpoint.scheme, endpoint.hostname, endpoint.port or 443) == (api.scheme, api.hostname, api.port or 443):
            key = read_key(root)
    return url, cfg.get('knowledge_reranker_model', ''), key


def rerank(query, rows, reranker=None):
    """Score query/passage pairs, retaining recall order as a weak prior."""
    from knowledge_base import terms

    rows = [dict(row) for row in rows[:RECALL_LIMIT]]
    if not rows:
        return []
    documents = [str(row.get('title', '')) + '\n' + row['content'] for row in rows]
    if reranker:
        url, model, key = reranker
        body = {'query': query[:1000], 'documents': [text[:1800] for text in documents], 'top_n': len(rows)}
        if model:
            body['model'] = model
        try:
            headers = {'Authorization': 'Bearer ' + key} if key else {}
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
                response = client.post(url, json=body, headers=headers)
                response.raise_for_status()
                results = response.json()['results']
            scores = {item['index']: float(item['relevance_score']) for item in results}
            if set(scores) == set(range(len(rows))) and all(map(isfinite, scores.values())):
                return [rows[index] for index in sorted(scores, key=scores.__getitem__, reverse=True)]
        except (httpx.HTTPError, KeyError, ValueError, TypeError, OverflowError):
            pass

    query_words = terms(query)
    word_sets = [terms(text) for text in documents]
    weights = {word: 1 / sqrt(1 + sum(word in words for words in word_sets)) for word in query_words}
    phrase = ''.join(query.casefold().split())

    def score(index):
        words = word_sets[index]
        overlap = sum(weights[word] for word in query_words & words) / max(1, sqrt(len(words)))
        exact = 0.2 if len(phrase) >= 3 and phrase in ''.join(documents[index].casefold().split()) else 0
        return (rows[index].get('similarity', 0) * 0.75 + overlap * 0.35 + exact + 0.25 / (index + 2))

    return [rows[index] for index in sorted(range(len(rows)), key=score, reverse=True)]
