"""图数据可视化接口的常量定义。

修改本文件即可扩展支持的节点类型 / 调整默认上限，无需改动控制器与服务层签名。

注意：``Community`` 节点不在本接口的查询范围内 —— 社区数据由独立的
``GET /api/memory-storage/analytics/community_graph`` 接口（service 层
``analytics_community_graph_data``）专门暴露。本接口默认仅返回除 Community
之外的 8 种节点类型，避免与社区接口的语义重叠。
"""
from typing import Dict, FrozenSet, List

# 受支持的节点类型集合（与 Cypher labels(n)[0] 对齐，区分大小写）
# 注意：``Community`` 节点交由 ``/analytics/community_graph`` 接口处理，
# 故不出现在本集合中。
SUPPORTED_NODE_TYPES: FrozenSet[str] = frozenset({
    "Dialogue",
    "Chunk",
    "AssistantOriginal",
    "AssistantPruned",
    "Conversation",
    "Statement",
    "ExtractedEntity",
    "MemorySummary",
    "Perceptual",
})

# 每种节点类型的默认 Per_Type_Limit
DEFAULT_PER_TYPE_LIMIT_MAP: Dict[str, int] = {
    "Dialogue": 20,
    "Chunk": 30,
    "AssistantOriginal": 20,
    "AssistantPruned": 20,
    "Conversation": 20,
    "Statement": 50,
    "ExtractedEntity": 50,
    "MemorySummary": 20,
    "Perceptual": 20,
}

# 单类型 Per_Type_Limit 硬上限（Requirement 2.8）
SINGLE_TYPE_LIMIT_HARD_MAX: int = 500

# 节点总量保护上限（Requirement 8.1）
TOTAL_NODES_CAP: int = 2000

# Center_Mode 下单一 limit 的硬上限（与控制器现有逻辑保持一致）
CENTER_MODE_LIMIT_HARD_MAX: int = 1000

# Center_Mode 下 ``depth`` 参数的硬上限（与控制器现有逻辑保持一致）
DEPTH_HARD_MAX: int = 3

# 节点属性白名单：未在表中的类型 fallback 到 _DEFAULT_FIELDS（Requirement 7.6）
_DEFAULT_FIELDS: List[str] = ["caption"]

NODE_PROPERTY_WHITELIST: Dict[str, List[str]] = {
    "Dialogue": ["content", "created_at"],
    "Chunk": ["content", "created_at"],
    "Statement": [
        "temporal_info",
        "stmt_type",
        "statement",
        "valid_at",
        "created_at",
        "caption",
        "emotion_keywords",
        "emotion_type",
        "emotion_subject",
    ],
    "ExtractedEntity": [
        "description",
        "name",
        "entity_type",
        "created_at",
        "caption",
        "aliases",
        "connect_strength",
    ],
    "MemorySummary": ["summary", "content", "created_at", "caption"],
    "Perceptual": [
        "file_name",
        "file_path",
        "file_type",
        "domain",
        "topic",
        "keywords",
        "summary",
    ],
    # 助手回复节点 — 实际写入字段名为 ``text``（参见
    # ``api/app/core/memory/models/graph_models.py`` 的
    # ``AssistantOriginalNode`` / ``AssistantPrunedNode``），因此白名单也使用
    # ``text``。前端按 ``properties.text`` 取节点正文内容。
    "AssistantOriginal": ["text", "created_at"],
    "AssistantPruned": ["text", "memory_type", "created_at"],
    # 会话级中心节点（参见 ``graph_models.py`` 的 ``ConversationNode``）。
    # 用于把同一会话的 AssistantOriginal / AssistantPruned 聚成一个连通子图。
    # 前端按 ``properties.conversation_id`` 识别会话，``caption`` 回落到 label。
    "Conversation": ["conversation_id", "name", "created_at"],
    # 注意：Community 由 /analytics/community_graph 接口独立处理，
    # 不出现在 SUPPORTED_NODE_TYPES 中。但保留白名单以支持其它流程
    # （如 Center_Mode 中心节点扩展碰巧命中 Community 邻居）的属性裁剪。
    "Community": ["name", "summary", "caption", "created_at"],
}
