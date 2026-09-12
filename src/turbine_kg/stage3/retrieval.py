"""Exact-filter plus lightweight full-text retrieval baseline."""

from __future__ import annotations

import re

from .applicability import match_scope
from .models import FixtureDocument, RetrievalHit, ScopeContext


def _terms(value: str) -> set[str]:
    stopwords = {
        "a", "an", "and", "applies", "be", "before", "can", "does", "do", "for",
        "how", "in", "is", "of", "on", "or", "the", "this", "to", "what", "when",
    }
    terms = {
        item.lower()
        for item in re.findall(r"[A-Za-z0-9_-]+|[\u3400-\u9fff]{2,}", value)
        if item.strip() and item.lower() not in stopwords
    }
    for run in re.findall(r"[\u3400-\u9fff]{2,}", value):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def retrieve(
    documents: tuple[FixtureDocument, ...],
    *,
    question: str,
    context: ScopeContext,
) -> tuple[RetrievalHit, ...]:
    query_terms = _terms(question)
    hits: list[RetrievalHit] = []
    for document in documents:
        for statement in document.statements:
            scope = match_scope(statement.scope, context)
            # Missing context is a condition for a qualified result, not an
            # automatic retrieval stop.  Hard mismatches remain excluded.
            if any(reason.startswith("mismatch:") for reason in scope.reasons):
                continue
            searchable = _terms(statement.text + " " + document.title)
            overlap = len(query_terms & searchable)
            if query_terms and overlap == 0:
                continue
            score = float(overlap) + scope.specificity * 0.01
            hits.append(RetrievalHit(statement, document, score, scope.specificity))
    return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.statement.statement_id)))


def json_baseline(
    documents: tuple[FixtureDocument, ...], *, question: str, context: ScopeContext
) -> tuple[str, ...]:
    """Return statement IDs from the simple non-graph baseline."""

    return tuple(hit.statement.statement_id for hit in retrieve(documents, question=question, context=context))
