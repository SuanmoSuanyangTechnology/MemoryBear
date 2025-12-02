"""
去重消歧模块

提供实体去重和消歧功能，包括：
- 基础去重和消歧（精确匹配、模糊匹配）
- LLM 实体去重
- 第二层去重（与 Neo4j 数据库联合去重）
- 两阶段去重（完整的去重流程）
"""

from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
    deduplicate_entities_and_edges,
    accurate_match,
    fuzzy_match,
    LLM_decision,
    LLM_disamb_decision,
)
from app.core.memory.storage_services.extraction_engine.deduplication.entity_dedup_llm import (
    llm_dedup_entities,
    llm_dedup_entities_iterative_blocks,
    llm_disambiguate_pairs_iterative,
)
from app.core.memory.storage_services.extraction_engine.deduplication.second_layer_dedup import (
    second_layer_dedup_and_merge_with_neo4j,
)
from app.core.memory.storage_services.extraction_engine.deduplication.two_stage_dedup import (
    dedup_layers_and_merge_and_return,
)

__all__ = [
    "deduplicate_entities_and_edges",
    "accurate_match",
    "fuzzy_match",
    "LLM_decision",
    "LLM_disamb_decision",
    "llm_dedup_entities",
    "llm_dedup_entities_iterative_blocks",
    "llm_disambiguate_pairs_iterative",
    "second_layer_dedup_and_merge_with_neo4j",
    "dedup_layers_and_merge_and_return",
]
