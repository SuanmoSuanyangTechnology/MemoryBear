"""Analytics vocabulary shared by the repository and service layers.

These constants are part of the response contract and are read on both sides of
the repository/service boundary: the Neo4j projections derive their field lists
from them, and the service-side assemblers filter with the same lists. They live
in ``common`` so a repository can import them without depending on
``src.services`` (which would be an inverted, circular dependency).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

#: 图谱支持的 Node_Type 集合（api ``graph_data_constants`` 同值）。
SUPPORTED_NODE_TYPES: frozenset[str] = frozenset(
    {
        "Dialogue",
        "Chunk",
        "AssistantOriginal",
        "AssistantPruned",
        "Conversation",
        "Statement",
        "ExtractedEntity",
        "MemorySummary",
        "Perceptual",
        "Preference",
    }
)

NODE_PROPERTY_WHITELIST = {
    "Dialogue": ("content", "created_at"),
    "Chunk": ("content", "created_at"),
    "Statement": (
        "temporal_info",
        "stmt_type",
        "statement",
        "valid_at",
        "created_at",
        "caption",
        "emotion_keywords",
        "emotion_type",
        "emotion_subject",
    ),
    "ExtractedEntity": (
        "description",
        "description_summary",
        "name",
        "entity_type",
        "created_at",
        "caption",
        "aliases",
        "connect_strength",
    ),
    "MemorySummary": ("summary", "content", "created_at", "caption"),
    "Perceptual": ("file_name", "file_path", "file_type", "domain", "topic", "keywords", "summary"),
    "Preference": (
        "domain",
        "subject",
        "situation_key",
        "preference_text_all",
        "created_at",
        "updated_at",
    ),
    "AssistantOriginal": ("text", "created_at"),
    "AssistantPruned": ("text", "memory_type", "created_at"),
    "Conversation": ("conversation_id", "name", "created_at"),
    # 与 api ``NODE_PROPERTY_WHITELIST`` 一致：本表只服务 graph_data 的 Center_Mode
    # 邻居裁剪，社区图谱自己的字段见 ``COMMUNITY_GRAPH_*_FIELDS``。
    "Community": ("name", "summary", "caption", "created_at"),
}

# ``properties()`` 在 label 未登记时只保留 caption，投影必须使用同一回落值。
WHITELIST_DEFAULT_FIELDS: tuple[str, ...] = ("caption",)

#: 情绪桶（10 类）与两个旧值别名 — 与 api ``emotion_buckets.EMOTION_CODES`` +
#: ``LEGACY_EMOTION_MAP`` 一致。``i18n`` 中情绪枚举标签的键集合必须与此相同。
EMOTION_TYPE_STORAGE_VALUES: tuple[str, ...] = (
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
    "fear",
    "surprise",
)

#: 社区图谱（``community_graph``）自己的属性字段；api 侧是同名硬编码元组。
COMMUNITY_GRAPH_NODE_FIELDS: tuple[str, ...] = (
    "community_id",
    "end_user_id",
    "member_count",
    "updated_at",
    "name",
    "summary",
    "core_entities",
)
COMMUNITY_GRAPH_ENTITY_FIELDS: tuple[str, ...] = (
    "name",
    "end_user_id",
    "description",
    "created_at",
    "entity_type",
)

#: ``memory_type_counts`` 返回的计数器名字，顺序与 ``_statements`` 一致。
COUNT_KEYS: tuple[str, ...] = (
    "perceptual",
    "active_conversations",
    "direct_sources",
    "forgotten",
)

# ── 情绪归一（T10 emotion 字段）──────────────────────────────────────────────
#: 10 类情绪 code —— 与 api ``emotion_buckets.EMOTION_CODES`` 同值同序。
EMOTION_CODES: tuple[str, ...] = (
    "neutral", "joy", "relief", "anxiety", "sadness",
    "anger", "frustration", "loneliness", "confusion", "hope",
)

#: 旧 6 类中不在 10 类内的值，仅读取时映射、不回写存量 —— api ``LEGACY_EMOTION_MAP`` 同值。
LEGACY_EMOTION_MAP: Mapping[str, str] = MappingProxyType({
    "fear": "anxiety",
    "surprise": "neutral",
})

_EMOTION_CODE_SET: frozenset[str] = frozenset(EMOTION_CODES)


def normalize_emotion_type(value: object) -> str | None:
    """与 api ``emotion_buckets.normalize_emotion_type`` 行为一致：非 str/空串/未知 → None。"""

    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    if key in _EMOTION_CODE_SET:
        return key
    return LEGACY_EMOTION_MAP.get(key)


def normalize_emotion_type_or_raw(value: object) -> object:
    """已知值归一化为 code；None → None；未知值原样返回（api 同名函数语义）。"""

    if value is None:
        return None
    code = normalize_emotion_type(value)
    return code if code is not None else value


EMOTION_NAMES_ZH: Mapping[str, str] = MappingProxyType(
    {
        "neutral": "中性", "joy": "开心", "relief": "缓解", "anxiety": "焦虑",
        "sadness": "难过", "anger": "生气", "frustration": "挫败",
        "loneliness": "孤独", "confusion": "困惑", "hope": "期待",
    }
)
EMOTION_NAMES_EN: Mapping[str, str] = MappingProxyType(
    {
        "neutral": "Neutral", "joy": "Joy", "relief": "Relief", "anxiety": "Anxiety",
        "sadness": "Sadness", "anger": "Anger", "frustration": "Frustration",
        "loneliness": "Loneliness", "confusion": "Confusion", "hope": "Hope",
    }
)
POSITIVE_EMOTIONS: frozenset[str] = frozenset({"joy", "relief", "hope"})
NEGATIVE_EMOTIONS: frozenset[str] = frozenset(
    {"anxiety", "sadness", "anger", "frustration", "loneliness"}
)
NEUTRAL_EMOTIONS: frozenset[str] = frozenset({"neutral", "confusion"})
EmotionValence = Literal["positive", "negative", "neutral"]


def emotion_valence(value: object) -> EmotionValence | None:
    code = normalize_emotion_type(value)
    if code is None:
        return None
    if code in POSITIVE_EMOTIONS:
        return "positive"
    if code in NEGATIVE_EMOTIONS:
        return "negative"
    return "neutral"


def emotion_display_name(value: object, language: str = "zh") -> str | None:
    code = normalize_emotion_type(value)
    if code is None:
        return None
    return (EMOTION_NAMES_EN if language == "en" else EMOTION_NAMES_ZH)[code]
