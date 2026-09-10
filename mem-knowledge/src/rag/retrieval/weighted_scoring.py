"""Candidate-corpus keyword cosine scoring, following Dify's weighted rerank."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING

from jieba import Tokenizer

if TYPE_CHECKING:
    from jieba.analyse import TFIDF

_extractor: TFIDF | None = None
_extractor_lock = Lock()


def _keywords(text: str) -> list[str]:
    global _extractor
    # Initialize once inside the scoring worker, then share read-only dictionaries.
    # Do not mutate Jieba's defaults or retain one full dictionary per pool thread.
    if _extractor is None:
        with _extractor_lock:
            if _extractor is None:
                from jieba.analyse import TFIDF

                tokenizer = Tokenizer()
                tokenizer.initialize()
                extractor = TFIDF()
                extractor.tokenizer = tokenizer
                _extractor = extractor
    return _extractor.extract_tags(text, topK=None)


def keyword_similarities(query: str, texts: Sequence[str]) -> list[float]:
    """Return TF-IDF cosine scores in input order, using one candidate corpus."""
    if not texts:
        return []
    query_terms = Counter(_keywords(query))
    documents = [Counter(_keywords(text)) for text in texts]
    frequencies: Counter[str] = Counter()
    for terms in documents:
        frequencies.update(terms.keys())
    idf = {
        term: math.log((1 + len(documents)) / (1 + count)) + 1
        for term, count in frequencies.items()
    }
    query_vector = {term: count * idf.get(term, 0.0) for term, count in query_terms.items()}
    query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
    scores: list[float] = []
    for terms in documents:
        vector = {term: count * idf[term] for term, count in terms.items()}
        norm = math.sqrt(sum(value * value for value in vector.values()))
        numerator = sum(value * query_vector.get(term, 0.0) for term, value in vector.items())
        scores.append(min(1.0, numerator / (norm * query_norm)) if norm and query_norm else 0.0)
    return scores
