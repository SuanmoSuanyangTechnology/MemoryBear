"""
去重消歧模块

提供实体去重功能：
- 第一层去重：内存中的精确匹配（name + entity_type 相同时合并）

更精细的去重（模糊匹配、alias-to-name、LLM 决策）留给反思阶段执行。
"""

# ───── 第一层去重：内存对象集合上的去重判定 ─────
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
    deduplicate_entities_and_edges,
    accurate_match,
)

# ───── 工具函数 ─────
from app.core.memory.storage_services.extraction_engine.deduplication.second_layer_dedup import (
    _row_to_entity,
)

__all__ = [
    # 第一层去重
    "deduplicate_entities_and_edges",
    "accurate_match",
    # 工具函数
    "_row_to_entity",
]
