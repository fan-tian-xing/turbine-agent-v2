"""Deterministic Chinese search projection, separate from source quotations.

Chinese character bigrams make full-text recall independent of optional Neo4j
language plugins. Tokens use ASCII hex with the whitespace analyzer, so the
same tokenizer is used at ingestion and query time. This is lexical recall,
not semantic inference or an ontology term inventory.
"""
import re
import unicodedata

SEARCH_INDEX = 'turbine_evidence_lexical_v1'
SEARCH_VERSION = 'cjk-bigram-v2-local-evidence'


def search_tokens(text: str) -> list[str]:
    text = unicodedata.normalize('NFKC', text).casefold()
    result = []
    for run in re.findall(r'[\u3400-\u9fff]+|[a-z0-9]+(?:[.][0-9]+)*', text):
        if '\u3400' <= run[0] <= '\u9fff':
            pieces = [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
            result.extend('c' + ''.join(f'{ord(char):04x}' for char in piece) for piece in pieces)
        else:
            result.append('w' + run.replace('.', 'd'))
    return list(dict.fromkeys(result))


def query_projection(query: str, query_terms=None, *, match='all') -> tuple[str, list[str]]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError('全文查询需要非空问题文本')
    if query_terms is not None and (not isinstance(query_terms, list) or
                                  any(not isinstance(term, str) for term in query_terms)):
        raise ValueError('query_terms须为字符串列表')
    terms = list(dict.fromkeys(term.strip() for term in (query_terms or []) if term.strip()))[:24]
    if match not in ('all', 'any'):
        raise ValueError('全文词组匹配方式须为all或any')
    # A phrase is one recall unit: matching one bigram from an unrelated
    # sentence must not satisfy a multi-character equipment or activity name.
    # Query text is only the fallback when the caller supplied no phrases.
    groups = [search_tokens(term) for term in (terms or [query])]
    groups = [tokens[:96] for tokens in groups if tokens]
    if not groups:
        raise ValueError('问题不包含可检索文字')
    operator = ' AND ' if match == 'all' else ' OR '
    return operator.join('(' + ' AND '.join(tokens) + ')' for tokens in groups), terms
