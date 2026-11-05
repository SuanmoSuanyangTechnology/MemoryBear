"""API-compatible emotion labels and legacy aliases used by read cards."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

EMOTION_CODES: tuple[str, ...] = (
    "neutral",
    "joy",
    "relief",
    "anxiety",
    "sadness",
    "anger",
    "frustration",
    "loneliness",
    "confusion",
    "hope",
)

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

LEGACY_EMOTION_MAP: Mapping[str, str] = MappingProxyType(
    {
        "fear": "anxiety",
        "surprise": "neutral",
    }
)

_CODE_SET: frozenset[str] = frozenset(EMOTION_CODES)


def normalize_emotion_type(value: object) -> str | None:
    """把任意输入归一化为 10 类 code。

    Args:
        value: 原始情绪值（可能是 10 类、旧 6 类、未知值或非 str）。

    Returns:
        10 类 code；旧值按 ``LEGACY_EMOTION_MAP`` 映射；非 str、空串或未知值返回 None。不抛异常。
    """
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    if key in _CODE_SET:
        return key
    return LEGACY_EMOTION_MAP.get(key)


def normalize_emotion_type_or_raw(value: object) -> object:
    """已知值（10 类或旧值）归一化为 code；None 返回 None；未知值原样返回（供既有兜底入口使用）。"""
    if value is None:
        return None
    code = normalize_emotion_type(value)
    return code if code is not None else value
