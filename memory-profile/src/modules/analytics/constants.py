"""Profile analytics presentation constants.

These values define the response vocabulary shared by the analytics service
layer, so API-contract drift stays visible in one place.
"""

from __future__ import annotations

#: 用户名片 Tag 的字符长度上限（api ``user_card_tags.USER_CARD_TAG_MAX_LENGTH`` 同值）。
USER_CARD_TAG_MAX_LENGTH = 12

#: ``node_statistics`` 的固定返回顺序。
MEMORY_TYPES: tuple[str, ...] = (
    "PERCEPTUAL_MEMORY",
    "WORKING_MEMORY",
    "SHORT_TERM_MEMORY",
    "EXPLICIT_MEMORY",
    "IMPLICIT_MEMORY",
    "EMOTIONAL_MEMORY",
    "EPISODIC_MEMORY",
    "FORGET_MEMORY",
)

#: ``ExtractedEntity`` 中做「内容存在性」过滤的字段（api ``_ENTITY_CONTENT_GATED_FIELDS``）。
ENTITY_CONTENT_GATED_FIELDS: frozenset[str] = frozenset({"description", "description_summary"})

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
DEFAULT_PER_TYPE_LIMITS = {
    "Dialogue": 20,
    "Chunk": 30,
    "AssistantOriginal": 20,
    "AssistantPruned": 20,
    "Conversation": 20,
    "Statement": 50,
    "ExtractedEntity": 50,
    "MemorySummary": 20,
    "Perceptual": 20,
    "Preference": 20,
}

#: 单类型 Per_Type_Limit 硬上限与节点总量保护上限（api ``graph_data_constants`` 同值）。
SINGLE_TYPE_LIMIT_HARD_MAX = 500
TOTAL_NODES_CAP = 2000

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

# ``_properties()`` 在 label 未登记时只保留 caption，投影必须使用同一回落值。
WHITELIST_DEFAULT_FIELDS: tuple[str, ...] = ("caption",)

#: 情绪桶（10 类）与两个旧值别名 — 与 api ``emotion_buckets.EMOTION_CODES`` +
#: ``LEGACY_EMOTION_MAP`` 一致。``analytics.constants.EMOTION_TYPE_NAMES`` 的键集合必须与此相同。
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

#: 响应期虚拟对象（api ``graph_data_constants`` 同名同值）：不写入 Neo4j。
PREFERENCE_GROUP_LABEL = "PreferenceGroup"
PREFERENCE_GROUP_EDGE_TYPE = "HAS_PREFERENCE"
PREFERENCE_GROUP_NODE_ID_PREFIX = "virtual:preferencegroup:"

#: ``memory_type_counts`` 返回的计数器名字，顺序与 ``_statements`` 一致。
COUNT_KEYS: tuple[str, ...] = (
    "perceptual",
    "active_conversations",
    "direct_sources",
    "forgotten",
)
