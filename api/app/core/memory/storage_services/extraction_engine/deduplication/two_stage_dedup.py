from __future__ import annotations

from typing import List, Optional, Tuple

from app.core.memory.models.graph_models import (
    ChunkNode,
    DialogueNode,
    EntityEntityEdge,
    ExtractedEntityNode,
    StatementChunkEdge,
    StatementEntityEdge,
    StatementNode,
)
from app.core.memory.models.message_models import DialogData
from app.core.memory.models.variate_config import ExtractionPipelineConfig
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
    deduplicate_entities_and_edges,
)
from app.core.memory.storage_services.extraction_engine.deduplication.second_layer_dedup import (
    second_layer_dedup_and_merge_with_neo4j,
)

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


async def dedup_layers_and_merge_and_return(
    dialogue_nodes: List[DialogueNode],
    chunk_nodes: List[ChunkNode],
    statement_nodes: List[StatementNode],
    entity_nodes: List[ExtractedEntityNode],
    statement_chunk_edges: List[StatementChunkEdge],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    dialog_data_list: List[DialogData],
    pipeline_config: ExtractionPipelineConfig,
    connector: Optional[Neo4jConnector] = None,
    llm_client = None,
) -> Tuple[
    List[DialogueNode],
    List[ChunkNode],
    List[StatementNode],
    List[ExtractedEntityNode],
    List[StatementChunkEdge],
    List[StatementEntityEdge],
    List[EntityEntityEdge],
    dict,  # 新增：返回去重详情
]:
    """
    执行两层实体去重与融合：
    - 第一层：精确/模糊/LLM 决策去重
    - 第二层：与 Neo4j 同组实体联合去重与融合（依赖传入的 connector）
    返回融合后的实体与边，同时保留原始的对话、片段与语句节点与边。
    """

    # pipeline_config is required - caller must provide it
    if pipeline_config is None:
        raise ValueError("pipeline_config is required for dedup_layers_and_merge_and_return")

    # 先探测 group_id，决定报告写入策略
    group_id: Optional[str] = None
    for dd in dialog_data_list:
        group_id = getattr(dd, "group_id", None)
        if group_id:
            break

    # 第一层去重消歧
    dedup_entity_nodes, dedup_statement_entity_edges, dedup_entity_entity_edges, dedup_details = await deduplicate_entities_and_edges(
        entity_nodes,
        statement_entity_edges,
        entity_entity_edges,
        report_stage="第一层去重消歧",
        report_append=False,
        dedup_config=(pipeline_config.deduplication if pipeline_config else None),
        llm_client=llm_client,
    )

    # 初始化第二层融合结果为第一层结果
    fused_entity_nodes = dedup_entity_nodes
    fused_statement_entity_edges = dedup_statement_entity_edges
    fused_entity_entity_edges = dedup_entity_entity_edges

    # 第二层去重消歧：与 Neo4j 中同组实体联合融合
    try:
        if group_id:
            if connector:
                fused_entity_nodes, fused_statement_entity_edges, fused_entity_entity_edges = await second_layer_dedup_and_merge_with_neo4j(
                    connector=connector,
                    group_id=group_id,
                    entity_nodes=dedup_entity_nodes,
                    statement_entity_edges=dedup_statement_entity_edges,
                    entity_entity_edges=dedup_entity_entity_edges,
                    dedup_config=(pipeline_config.deduplication if pipeline_config else None),
                    llm_client=llm_client,
                )
            else:
                print("Skip second-layer dedup: missing connector")
        else:
            print("Skip second-layer dedup: missing group_id")
    except Exception as e:
        print(f"Second-layer dedup failed: {e}")

    return (
        dialogue_nodes,
        chunk_nodes,
        statement_nodes,
        fused_entity_nodes,
        statement_chunk_edges,
        fused_statement_entity_edges,
        fused_entity_entity_edges,
        dedup_details,  # 返回去重详情
    )
