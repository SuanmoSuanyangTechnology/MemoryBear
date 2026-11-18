"""Retrieval policy constants (ranking + prompts), not storage definitions.

Index aliases, full-text/embedding fields, dimensions and the writer's managed
index/constraint definitions live in the storage substrate
(:mod:`src.core.adapters.storage.indexes`), which mirrors the API writer and
verifies it via ``scripts/sync_storage_index_contract.py``. This module keeps
only the ranking thresholds, the default content families, the profile/sentinel
markers and the ontology predicates offered to the relation agent.
"""

from __future__ import annotations

from src.core.domain.enums import MemorySource

# ── Ranking policy (host ``Neo4jSearchService`` defaults) ──────────────────────

DEFAULT_ALPHA = 0.6
DEFAULT_FULLTEXT_SCORE_THRESHOLD = 1.5
DEFAULT_COSINE_SCORE_THRESHOLD = 0.5
DEFAULT_CONTENT_SCORE_THRESHOLD = 0.5
MAX_RERANK_CHARS_PER_DOC = 2000
RELATIONSHIP_LOOP_LIMIT = 6

#: Fallback content families when the caller does not narrow ``includes``.
DEFAULT_INCLUDES: tuple[str, ...] = (
    MemorySource.STATEMENT.value,
    MemorySource.CHUNK.value,
    MemorySource.EXTRACTED_ENTITY.value,
    MemorySource.MEMORY_SUMMARY.value,
    MemorySource.PERCEPTUAL.value,
    MemorySource.DIALOGUE.value,
)

#: The user-profile ExtractedEntity is located by ``entity_type`` (not by name).
USER_ENTITY_TYPE = "用户"
#: Sentry application excluded from the RECENT conversation lookup.
EXCLUDED_APP_ID = "00000000-0000-0000-0000-000000000001"

# ── Ontology predicates offered to the relation agent ────────────────────────
# Host ``TripletPredicate.to_dict()`` order and wording are part of the prompt.
TRIPLET_PREDICATES: tuple[dict[str, object], ...] = (
    {
        "predicate_id": 1,
        "predicate": "别名属于",
        "description": (
            "表达实体名称、别名、称呼之间的对应关系；"
            "仅用于名字性表达，不用于职业、角色、评价词。"
        ),
    },
    {
        "predicate_id": 2,
        "predicate": "属于类型",
        "description": "表达主体所属的身份、职业、角色、组织或群体；用于稳定归属关系。",
    },
    {
        "predicate_id": 3,
        "predicate": "位于",
        "description": (
            "表达实体与地点、场所之间的稳定空间位置关系；不用于时间表达或未解析位置指代。"
        ),
    },
    {
        "predicate_id": 4,
        "predicate": "前往",
        "description": "表达主体前往、到访某地点、组织或场所；优先保留具有长期记忆价值的到访关系。",
    },
    {
        "predicate_id": 5,
        "predicate": "组成部分",
        "description": "表达部分与整体之间的结构包含关系；采用 part-to-whole 方向。",
    },
    {
        "predicate_id": 6,
        "predicate": "拥有",
        "description": (
            "表达主体拥有、持有、配有某对象、账号、联系方式或生命体；不用于抽象概念或情绪状态。"
        ),
    },
    {
        "predicate_id": 7,
        "predicate": "使用",
        "description": "表达主体使用某工具、平台、语言、资源或设备；不用于抽象结果。",
    },
    {
        "predicate_id": 8,
        "predicate": "创建了",
        "description": "表达主体创建、生产、撰写某对象；方向为 创建者 -> 创建了 -> 被创建对象。",
    },
    {
        "predicate_id": 9,
        "predicate": "了解",
        "description": (
            "表达主体对知识、技能、语言或学科具有学习、了解或认知关系；"
            "关系对象必须属于知识能力类。"
        ),
    },
    {
        "predicate_id": 10,
        "predicate": "偏好",
        "description": "表达主体的稳定偏好、习惯或明确目标倾向；不用于抽象愿望或情绪状态。",
    },
    {
        "predicate_id": 11,
        "predicate": "负责",
        "description": "表达主体负责某项事务、工作、项目或职责；对象应为具体事务。",
    },
    {
        "predicate_id": 12,
        "predicate": "沟通于",
        "description": "表达两个主体之间存在沟通、交流或交互关系；两端通常应为可交互主体。",
    },
    {
        "predicate_id": 13,
        "predicate": "关联于",
        "description": "受限弱关联关系；仅用于明确稳定但缺少更精确谓词的关系；不能作为兜底关系。",
    },
)

__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_CONTENT_SCORE_THRESHOLD",
    "DEFAULT_COSINE_SCORE_THRESHOLD",
    "DEFAULT_FULLTEXT_SCORE_THRESHOLD",
    "DEFAULT_INCLUDES",
    "EXCLUDED_APP_ID",
    "MAX_RERANK_CHARS_PER_DOC",
    "RELATIONSHIP_LOOP_LIMIT",
    "TRIPLET_PREDICATES",
    "USER_ENTITY_TYPE",
]