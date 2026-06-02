"""
去重消歧模块

提供实体去重和消歧功能，分为"决策"与"执行"两层：
- 决策：在内存对象集合上判定哪些实体应该合并（产出 id_redirect 等指令）
- 执行：把决策应用到 Neo4j 图数据库（物理删除节点 + 重定向边）

具体能力包括：
- 基础去重和消歧（精确匹配、模糊匹配、LLM 决策）
- 第二层去重（与 Neo4j 数据库联合决策）
- 两阶段去重（完整的去重流程）
- 应用决策结果（删除冗余节点、重定向边）
"""

# ───── 决策：内存对象集合上的去重判定 ─────
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
    # ↓ 执行：把决策应用到 Neo4j（物理删除冗余节点、重定向边）
    cleanup_merged_entities,
)
from app.core.memory.storage_services.extraction_engine.deduplication.two_stage_dedup import (
    dedup_layers_and_merge_and_return,
)

__all__ = [
    # 决策
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
    # 执行
    "cleanup_merged_entities",
]
