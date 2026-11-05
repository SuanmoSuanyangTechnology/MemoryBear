"""Coding Agent preference contracts shared by every service.

The API's write pipeline gates preference extraction on these keywords, and the
preference configuration endpoints (now served by memory-profile) report the
same list back to clients. Keeping one copy is what makes the reported
``effective_keywords`` and the engine's actual matches identical.
"""

from memory_contracts.preferences.gate import (
    KeywordGateResult,
    effective_keywords,
    match_preference_keywords,
    normalize_keyword,
)
from memory_contracts.preferences.keywords import DEFAULT_PREFERENCE_KEYWORDS

__all__ = [
    "DEFAULT_PREFERENCE_KEYWORDS",
    "KeywordGateResult",
    "effective_keywords",
    "match_preference_keywords",
    "normalize_keyword",
]