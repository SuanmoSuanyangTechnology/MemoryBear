"""API-compatible emotion labels and legacy aliases used by read cards.

The canonical codes, legacy mapping and normalizers live in
``src.common.vocabulary`` so repositories can import them without depending
on a service; this module re-exports them for existing display callers.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from src.common.vocabulary import (
    EMOTION_CODES,
    LEGACY_EMOTION_MAP,
    normalize_emotion_type,
    normalize_emotion_type_or_raw,
)

__all__ = [
    "EMOTION_CODES",
    "EMOTION_NAMES_EN",
    "EMOTION_NAMES_ZH",
    "LEGACY_EMOTION_MAP",
    "normalize_emotion_type",
    "normalize_emotion_type_or_raw",
]

EMOTION_NAMES_ZH: Mapping[str, str] = MappingProxyType(
    {
        "neutral": "中性",
        "joy": "开心",
        "relief": "缓解",
        "anxiety": "焦虑",
        "sadness": "难过",
        "anger": "生气",
        "frustration": "挫败",
        "loneliness": "孤独",
        "confusion": "困惑",
        "hope": "期待",
    }
)

EMOTION_NAMES_EN: Mapping[str, str] = MappingProxyType(
    {
        "neutral": "Neutral",
        "joy": "Joy",
        "relief": "Relief",
        "anxiety": "Anxiety",
        "sadness": "Sadness",
        "anger": "Anger",
        "frustration": "Frustration",
        "loneliness": "Loneliness",
        "confusion": "Confusion",
        "hope": "Hope",
    }
)

