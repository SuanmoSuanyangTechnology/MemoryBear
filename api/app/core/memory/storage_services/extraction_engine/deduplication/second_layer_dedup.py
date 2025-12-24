# 导入 Python 的annotations特性，允许在类型注解中使用尚未定义的类（支持 “向前引用”），提升代码中类型注解的灵活性。
# 这是什么意思？ 该类的属性的类型是这个类本身（递归定义）？
"""
这段代码是 “第二层去重消歧” 的核心实现，逻辑可分为四步：
1.从第一层去重消歧后的实体中提取核心信息，作为索引查询 Neo4j 中同组的候选实体；
2.对候选实体去重并转换为统一模型；
3.构建预重定向关系（第一层实体 ID→数据库实体 ID），确保优先使用数据库 ID；
4.合并数据库候选实体与第一层实体，调用去重函数完成最终融合，返回结果。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from app.core.memory.models.graph_models import (
    EntityEntityEdge,
    ExtractedEntityNode,
    StatementEntityEdge,
)
from app.core.memory.models.variate_config import DedupConfig
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (  # 导入报告写入以在跳过时追加说明
    _write_dedup_fusion_report,
    deduplicate_entities_and_edges,
)
from app.repositories.neo4j.graph_search import (
    get_dedup_candidates_for_entities,  # 导入ge函数，用于从 Neo4j 中检索与输入实体可能重复的候选实体（去重的核心检索逻辑）。
)

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import (
    Neo4jConnector,  # 导入 Neo4j 数据库连接器类，用于与 Neo4j 数据库进行交互
)


def _parse_dt(val: Any) -> datetime: # 定义内部辅助函数_parse_dt，用于将任意类型的输入值解析为datetime对象（处理实体节点中的时间字段）
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val) # 使用fromisoformat方法将 ISO 格式的字符串（如 "2023-10-01T12:00:00"）解析为datetime对象
        except Exception:
            pass
    # Fallback: now; upstream should provide real times
    return datetime.now()


def _row_to_entity(row: Dict[str, Any]) -> ExtractedEntityNode:
    """
    将 Neo4j 返回的数据库记录转换为 ExtractedEntityNode 模型对象

    Args:
        row: Neo4j 查询返回的记录字典

    Returns:
        ExtractedEntityNode: 实体节点对象

    Note:
        从数据库中查询到的内容是 JSON 格式的字符串，需要先解析为 Python 对象
    """
    return ExtractedEntityNode(
        id=row.get("id"),
        name=row.get("name") or "",
        group_id=row.get("group_id") or "",
        user_id=row.get("user_id") or "",
        apply_id=row.get("apply_id") or "",
        created_at=_parse_dt(row.get("created_at")),
        expired_at=_parse_dt(row.get("expired_at")) if row.get("expired_at") else None,
        entity_idx=int(row.get("entity_idx") or 0),
        statement_id=row.get("statement_id") or "",
        entity_type=row.get("entity_type") or "",
        description=row.get("description") or "",
        aliases=row.get("aliases") or [],
        name_embedding=row.get("name_embedding") or [],
        fact_summary=row.get("fact_summary") or "",
        connect_strength=row.get("connect_strength") or "",
    )


async def second_layer_dedup_and_merge_with_neo4j( # 二层去重的核心逻辑，与 Neo4j 中同组实体联合去重
    connector: Neo4jConnector,
    group_id: str, # 用于定位neo4j中同一组的实体，确保只在同组内去重
    entity_nodes: List[ExtractedEntityNode], # 输入的实体节点列表，包含待去重的实体
    statement_entity_edges: List[StatementEntityEdge], # 输入的语句实体边列表，用于处理实体之间的关系
    entity_entity_edges: List[EntityEntityEdge], # 输入的实体实体边列表，用于处理实体之间的关系
    dedup_config: DedupConfig | None = None,
    llm_client = None,
) -> Tuple[List[ExtractedEntityNode], List[StatementEntityEdge], List[EntityEntityEdge]]:
    """
    第二层去重消歧：
    - 以第一层结果为索引，检索相同 group_id 下的 DB 候选实体
    - 将 DB 候选与当前实体集合联合，按既有精确/模糊/LLM 决策进行融合
    - 返回融合后的实体与重定向后的边（边已指向规范 ID，优先 DB ID）
    """
    if not entity_nodes:
        return entity_nodes, statement_entity_edges, entity_entity_edges

    # 构造批量行并检索候选（精确/别名 + CONTAINS 召回）
    # 将第一层去重消歧的结果作为索引，批量查询DB候选实体
    incoming_rows: List[Dict[str, Any]] = [ # 定义 包含第一层实体的核心信息（用于数据库查询）
        {"id": e.id, "name": e.name, "entity_type": e.entity_type} for e in entity_nodes   # 对entity_nodes中的每个实体e，提取id（实体 ID）、name（名称）、entity_type（类型），构造字典作为查询条件。

    ]
    candidates_map = await get_dedup_candidates_for_entities( # 从 Neo4j 中查询候选实体，并将结果赋值给candidates_map（等待异步操作完成）。
        connector=connector, group_id=group_id,
        entities=incoming_rows,  # 传入参数：第一层实体的核心信息（作为查询索引）
        use_contains_fallback=True # 传入参数：启用 “包含关系” 作为匹配失败的降级策略（若精确匹配无结果，用包含关系召回候选），与src\database\cypher_queries.py的307产生联动
    )

    # 拉平候选，转为模型（按 DB 节点优先）
    db_candidate_rows: List[Dict[str, Any]] = [] # 存储去重后的数据库候选实体记录（行）
    seen_db_ids: set[str] = set() # 集合，用于记录已处理的数据库实体 ID（避免重复添加同一实体）
    for _, rows in candidates_map.items():
        for r in rows:
            rid = r.get("id")
            if rid and rid not in seen_db_ids:  # 如果rid存在且未被处理
                seen_db_ids.add(rid)  # 将rid加入seen_db_ids，标记为已处理
                db_candidate_rows.append(r) # 将该记录r添加到db_candidate_rows（确保数据库实体唯一）

    db_candidate_models: List[ExtractedEntityNode] = []
    for r in db_candidate_rows:  # db_candidate_rows：去重后的数据库候选实体记录（行）
        try:
            m = _row_to_entity(r) # 调用_row_to_entity函数，将数据库记录r转换为实体模型m
            db_candidate_models.append(m) # m添加到db_candidate_models
        except Exception:
            # 忽略无法解析的记录
            pass

    # 若 DB 候选为空：跳过二层融合，直接返回第一层结果，并在报告中标注候选数
    candidate_count = len(db_candidate_models)
    if candidate_count == 0:
        try:
            _write_dedup_fusion_report(
                exact_merge_map={},
                fuzzy_merge_records=[],
                local_llm_records=[],
                disamb_records=[],
                stage_label="第二层去重消歧",
                append=True,
                stage_notes=[f"候选数：{candidate_count}（DB 为空则标注跳过）"],
            )
        except Exception:
            # 报告写入失败不影响主流程
            pass
        return entity_nodes, statement_entity_edges, entity_entity_edges

    # 联合集合（DB 在前，确保规范 ID 优先使用 DB ID）
    # 将从 DB 检索到的候选实体与第一层去重消歧的实体合并，作为输入继续调用去重方法。
    # 由于按顺序遍历，规范实体将优先选择位于前面的 DB 节点，因此无需显式预重定向。
    union_entities: List[ExtractedEntityNode] = db_candidate_models + list(entity_nodes)

    # 融合（内部执行精确/模糊/LLM 决策；随后再做边重定向与去重）
    fused_entities, fused_stmt_entity_edges, fused_entity_entity_edges, _ = await deduplicate_entities_and_edges(
        union_entities,
        statement_entity_edges,
        entity_entity_edges,
        report_stage="第二层去重消歧",
        report_append=True,
        dedup_config=dedup_config,
        llm_client=llm_client,
    )

    return fused_entities, fused_stmt_entity_edges, fused_entity_entity_edges
