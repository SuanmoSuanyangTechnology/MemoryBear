"""Keyword normalization and matching for preference candidate messages."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .default_keywords import DEFAULT_PREFERENCE_KEYWORDS

_LATIN = re.compile(r"[A-Za-z0-9_]")


def normalize_keyword(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def effective_keywords(custom_keywords: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in (*DEFAULT_PREFERENCE_KEYWORDS, *custom_keywords):
        normalized = normalize_keyword(raw)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


@dataclass(frozen=True)
class KeywordGateResult:
    matched: bool
    matched_terms: tuple[str, ...]
    matched_sources: dict[str, str]


def match_preference_keywords(content: str, custom_keywords: list[str]) -> KeywordGateResult:
    normalized_content = normalize_keyword(content or "")
    defaults = {normalize_keyword(item) for item in DEFAULT_PREFERENCE_KEYWORDS}
    keywords = effective_keywords(custom_keywords)
    matched: list[str] = []
    sources: dict[str, str] = {}

    for keyword in keywords:
        if _LATIN.search(keyword):
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])"
            hit = re.search(pattern, normalized_content) is not None
        else:
            hit = keyword in normalized_content
        if hit:
            matched.append(keyword)
            sources[keyword] = "default" if keyword in defaults else "custom"

    return KeywordGateResult(bool(matched), tuple(matched), sources)
