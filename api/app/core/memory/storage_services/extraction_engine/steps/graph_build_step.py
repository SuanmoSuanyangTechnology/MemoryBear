"""
GraphBuildStep — 从 DialogData 构建 Neo4j 图节点和边。

职责：
- 遍历 DialogData 列表，构建 DialogueNode、ChunkNode、StatementNode、
  ExtractedEntityNode、PerceptualNode 及各类 Edge
- 不涉及 LLM 调用、去重、Neo4j 写入

依赖：
- embedder_client（可选）：为 PerceptualNode 生成 summary embedding
- progress_callback（可选）：流式输出关系创建进度

从 ExtractionOrchestrator._create_nodes_and_edges() 提取而来，
旧编排器保留原方法不变，新旧流水线完全隔离。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.core.utils.datetime_utils import to_iso_z
from app.core.memory.models.graph_models import (
    ChunkNode,
    DialogueNode,
    EntityEntityEdge,
    ExtractedEntityNode,
    PerceptualEdge,
    PerceptualNode,
    StatementChunkEdge,
    StatementEntityEdge,
    StatementNode,
    AssistantOriginalNode,
    AssistantPrunedNode,
    AssistantPrunedEdge,
    AssistantDialogEdge,
)
from app.core.memory.models.message_models import DialogData, TemporalInfo

logger = logging.getLogger(__name__)


class GraphBuildResult:
    """图构建步骤的输出。"""

    __slots__ = (
        "dialogue_nodes",
        "chunk_nodes",
        "statement_nodes",
        "entity_nodes",
        "perceptual_nodes",
        "stmt_chunk_edges",
        "stmt_entity_edges",
        "entity_entity_edges",
        "perceptual_edges",
        "assistant_original_nodes",
        "assistant_pruned_nodes",
        "assistant_pruned_edges",
        "assistant_dialog_edges",
    )

    def __init__(
        self,
        dialogue_nodes: List[DialogueNode],
        chunk_nodes: List[ChunkNode],
        statement_nodes: List[StatementNode],
        entity_nodes: List[ExtractedEntityNode],
        perceptual_nodes: List[PerceptualNode],
        stmt_chunk_edges: List[StatementChunkEdge],
        stmt_entity_edges: List[StatementEntityEdge],
        entity_entity_edges: List[EntityEntityEdge],
        perceptual_edges: List[PerceptualEdge],
        assistant_original_nodes: Optional[List[AssistantOriginalNode]] = None,
        assistant_pruned_nodes: Optional[List[AssistantPrunedNode]] = None,
        assistant_pruned_edges: Optional[List[AssistantPrunedEdge]] = None,
        assistant_dialog_edges: Optional[List[AssistantDialogEdge]] = None,
    ):
        self.dialogue_nodes = dialogue_nodes
        self.chunk_nodes = chunk_nodes
        self.statement_nodes = statement_nodes
        self.entity_nodes = entity_nodes
        self.perceptual_nodes = perceptual_nodes
        self.stmt_chunk_edges = stmt_chunk_edges
        self.stmt_entity_edges = stmt_entity_edges
        self.entity_entity_edges = entity_entity_edges
        self.perceptual_edges = perceptual_edges
        self.assistant_original_nodes = assistant_original_nodes or []
        self.assistant_pruned_nodes = assistant_pruned_nodes or []
        self.assistant_pruned_edges = assistant_pruned_edges or []
        self.assistant_dialog_edges = assistant_dialog_edges or []


async def build_graph_nodes_and_edges(
    dialog_data_list: List[DialogData],
    embedder_client: Any = None,
    progress_callback: Optional[
        Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
    ] = None,
) -> GraphBuildResult:
    """
    从 DialogData 列表构建完整的图节点和边。

    Args:
        dialog_data_list: 经过萃取和数据赋值后的 DialogData 列表
        embedder_client: 可选的嵌入客户端，用于 PerceptualNode summary embedding
        progress_callback: 可选的进度回调

    Returns:
        GraphBuildResult 包含所有节点和边
    """
    logger.info("开始创建节点和边")

    dialogue_nodes: List[DialogueNode] = []
    chunk_nodes: List[ChunkNode] = []
    statement_nodes: List[StatementNode] = []
    entity_nodes: List[ExtractedEntityNode] = []
    perceptual_nodes: List[PerceptualNode] = []
    stmt_chunk_edges: List[StatementChunkEdge] = []
    stmt_entity_edges: List[StatementEntityEdge] = []
    entity_entity_edges: List[EntityEntityEdge] = []
    perceptual_edges: List[PerceptualEdge] = []

    entity_id_set: set = set()
    perceptual_id_set: set = set()
    total_dialogs = len(dialog_data_list)
    processed_dialogs = 0

    for dialog_data in dialog_data_list:
        processed_dialogs += 1
# region TODO 乐力齐 重构流水线切换生产环境稳定后修改
        # ── 对话节点 ──
        dialogue_node = DialogueNode(
            id=dialog_data.id,
            name=f"Dialog_{dialog_data.id}",
            ref_id=dialog_data.ref_id,
            end_user_id=dialog_data.end_user_id,
            run_id=dialog_data.run_id,
            content=dialog_data.context.content if dialog_data.context else "",
            dialog_embedding=dialog_data.dialog_embedding if hasattr(dialog_data, "dialog_embedding") else None,
            created_at=dialog_data.created_at,
            metadata=dialog_data.metadata,
            config_id=dialog_data.config_id if hasattr(dialog_data, "config_id") else None,
        )
        dialogue_nodes.append(dialogue_node)

        # ── 分块节点 ──
        for chunk_idx, chunk in enumerate(dialog_data.chunks):
            chunk_node = ChunkNode(
                id=chunk.id,
                name=f"Chunk_{chunk.id}",
                dialog_id=dialog_data.id,
                end_user_id=dialog_data.end_user_id,
                run_id=dialog_data.run_id,
                content=chunk.content,
                speaker=getattr(chunk, "speaker", None),
                chunk_embedding=chunk.chunk_embedding,
                sequence_number=chunk_idx,
                created_at=dialog_data.created_at,
                metadata=chunk.metadata,
            )
            chunk_nodes.append(chunk_node)

            # ── 感知节点 ──
            for p, file_type in chunk.files:
                perceptual_key = str(p.id)
                if perceptual_key in perceptual_id_set:
                    continue
                perceptual_id_set.add(perceptual_key)

                meta = p.meta_data or {}
                content_meta = meta.get("content", {})

                summary_embedding = None
                if embedder_client and p.summary:
                    try:
                        summary_embedding = (await embedder_client.response([p.summary]))[0]
                    except Exception as emb_err:
                        logger.warning(f"Failed to embed perceptual summary: {emb_err}")

                perceptual = PerceptualNode(
                    name=f"Perceptual_{p.id}",
                    id=str(p.id),
                    end_user_id=str(p.end_user_id),
                    perceptual_type=p.perceptual_type,
                    file_path=p.file_path or "",
                    file_name=p.file_name or "",
                    file_ext=p.file_ext or "",
                    summary=p.summary or "",
                    keywords=content_meta.get("keywords", []),
                    topic=content_meta.get("topic", ""),
                    domain=content_meta.get("domain", ""),
                    created_at=to_iso_z(p.created_time),
                    file_type=file_type,
                    summary_embedding=summary_embedding,
                )
                perceptual_nodes.append(perceptual)
                perceptual_edges.append(
                    PerceptualEdge(
                        source=perceptual.id,
                        target=chunk.id,
                        end_user_id=dialog_data.end_user_id,
                        run_id=dialog_data.run_id,
                        created_at=dialog_data.created_at,
                    )
                )

            # ── 陈述句节点 + 边 ──
            for statement in chunk.statements:
                statement_node = StatementNode(
                    id=statement.id,
                    name=f"Statement_{statement.id}",
                    chunk_id=chunk.id,
                    stmt_type=getattr(statement, "stmt_type", "general"),
                    temporal_info=getattr(statement, "temporal_info", TemporalInfo.ATEMPORAL),
                    connect_strength=(
                        statement.connect_strength
                        if statement.connect_strength is not None
                        else "Strong"
                    ),
                    end_user_id=dialog_data.end_user_id,
                    run_id=dialog_data.run_id,
                    statement=statement.statement,
                    speaker=getattr(statement, "speaker", None),
                    statement_embedding=statement.statement_embedding,
                    valid_at=(
                        statement.temporal_validity.valid_at
                        if hasattr(statement, "temporal_validity") and statement.temporal_validity
                        else None
                    ),
                    invalid_at=(
                        statement.temporal_validity.invalid_at
                        if hasattr(statement, "temporal_validity") and statement.temporal_validity
                        else None
                    ),
                    created_at=dialog_data.created_at,
                    dialog_at=getattr(statement, "dialog_at", None),
                    config_id=dialog_data.config_id if hasattr(dialog_data, "config_id") else None,
                    has_unsolved_reference=getattr(statement, "has_unsolved_reference", False),
                    emotion_type=getattr(statement, "emotion_type", None),
                    emotion_intensity=getattr(statement, "emotion_intensity", None),
                    emotion_keywords=getattr(statement, "emotion_keywords", None),
                    emotion_subject=getattr(statement, "emotion_subject", None),
                    emotion_target=getattr(statement, "emotion_target", None),
                )
                statement_nodes.append(statement_node)

                stmt_chunk_edges.append(
                    StatementChunkEdge(
                        source=statement.id,
                        target=chunk.id,
                        end_user_id=dialog_data.end_user_id,
                        run_id=dialog_data.run_id,
                        created_at=dialog_data.created_at,
                    )
                )

                # ── 三元组 → 实体节点 + 边 ──
                if not statement.triplet_extraction_info:
                    continue

                triplet_info = statement.triplet_extraction_info
                entity_idx_to_id: Dict[int, str] = {}

                for entity_idx, entity in enumerate(triplet_info.entities):
                    entity_idx_to_id[entity.entity_idx] = entity.id
                    entity_idx_to_id[entity_idx] = entity.id

                    if entity.id not in entity_id_set:
                        entity_connect_strength = getattr(entity, "connect_strength", "Strong")
                        entity_node = ExtractedEntityNode(
                            id=entity.id,
                            name=getattr(entity, "name", f"Entity_{entity.id}"),
                            entity_idx=entity.entity_idx,
                            statement_id=statement.id,
                            entity_type=getattr(entity, "type", "unknown"),
                            type_id=entity.type_id,
                            type_description=getattr(entity, "type_description", ""),
                            description=getattr(entity, "description", ""),
                            example=getattr(entity, "example", ""),
                            connect_strength=(
                                entity_connect_strength
                                if entity_connect_strength is not None
                                else "Strong"
                            ),
                            aliases=getattr(entity, "aliases", []) or [],
                            name_embedding=getattr(entity, "name_embedding", None),
                            is_explicit_memory=getattr(entity, "is_explicit_memory", False),
                            end_user_id=dialog_data.end_user_id,
                            run_id=dialog_data.run_id,
                            created_at=dialog_data.created_at,
                            config_id=dialog_data.config_id if hasattr(dialog_data, "config_id") else None,
                        )
                        entity_nodes.append(entity_node)
                        entity_id_set.add(entity.id)

                    entity_connect_strength = getattr(entity, "connect_strength", "Strong")
                    stmt_entity_edges.append(
                        StatementEntityEdge(
                            source=statement.id,
                            target=entity.id,
                            connect_strength=(
                                entity_connect_strength
                                if entity_connect_strength is not None
                                else "Strong"
                            ),
                            end_user_id=dialog_data.end_user_id,
                            run_id=dialog_data.run_id,
                            created_at=dialog_data.created_at,
                        )
                    )
# endregion

                for triplet in triplet_info.triplets:
                    subject_entity_id = entity_idx_to_id.get(triplet.subject_id)
                    object_entity_id = entity_idx_to_id.get(triplet.object_id)

                    if subject_entity_id and object_entity_id:
                        _tv = getattr(statement, "temporal_validity", None)
                        entity_entity_edges.append(
                            EntityEntityEdge(
                                source=subject_entity_id,
                                target=object_entity_id,
                                relation_type=triplet.predicate,
                                relation_type_id=triplet.predicate_id,
                                relation_type_surface=triplet.predicate_surface,
                                relation_type_description=getattr(triplet, "predicate_description", ""),
                                statement=statement.statement,
                                source_statement_id=statement.id,
                                end_user_id=dialog_data.end_user_id,
                                run_id=dialog_data.run_id,
                                created_at=dialog_data.created_at,
                                valid_at=_tv.valid_at if _tv else None,
                                invalid_at=_tv.invalid_at if _tv else None,
                            )
                        )

                        if progress_callback and len(entity_entity_edges) <= 10:
                            relationship_result = {
                                "result_type": "relationship_creation",
                                "relationship_index": len(entity_entity_edges),
                                "source_entity": triplet.subject_name,
                                "relation_type": triplet.predicate,
                                "target_entity": triplet.object_name,
                                "relationship_text": f"{triplet.subject_name} -[{triplet.predicate}]-> {triplet.object_name}",
                                "dialog_progress": f"{processed_dialogs}/{total_dialogs}",
                            }
                            await progress_callback(
                                "creating_nodes_edges_result",
                                f"关系创建中 ({processed_dialogs}/{total_dialogs})",
                                relationship_result,
                            )
                    else:
                        missing_subject = "subject" if not subject_entity_id else ""
                        missing_object = "object" if not object_entity_id else ""
                        missing_both = " and " if (not subject_entity_id and not object_entity_id) else ""
                        logger.debug(
                            f"跳过三元组 - 无法找到{missing_subject}{missing_both}{missing_object}实体ID: "
                            f"subject_id={triplet.subject_id} ({triplet.subject_name}), "
                            f"object_id={triplet.object_id} ({triplet.object_name}), "
                            f"predicate={triplet.predicate}, "
                            f"statement_id={statement.id}, "
                            f"available_indices={sorted(entity_idx_to_id.keys())}"
                        )

    logger.info(
        f"节点和边创建完成 - 对话节点: {len(dialogue_nodes)}, "
        f"分块节点: {len(chunk_nodes)}, 陈述句节点: {len(statement_nodes)}, "
        f"实体节点: {len(entity_nodes)}, 陈述句-分块边: {len(stmt_chunk_edges)}, "
        f"陈述句-实体边: {len(stmt_entity_edges)}, "
        f"实体-实体边: {len(entity_entity_edges)}"
    )

    # ── Assistant 剪枝节点和边 ──
    assistant_original_nodes: List[AssistantOriginalNode] = []
    assistant_pruned_nodes: List[AssistantPrunedNode] = []
    assistant_pruned_edges: List[AssistantPrunedEdge] = []
    assistant_dialog_edges: List[AssistantDialogEdge] = []

    for dialog_data in dialog_data_list:
        pruning_records = dialog_data.metadata.get("assistant_pruning_records", [])
        for record in pruning_records:
            pair_id = record["pair_id"]
            original_id = f"ao_{pair_id}"
            pruned_id = f"ap_{pair_id}"

            # AssistantOriginal 始终创建（记录原始对话）
            original_node = AssistantOriginalNode(
                id=original_id,
                name=f"AssistantOriginal_{pair_id[:8]}",
                end_user_id=dialog_data.end_user_id,
                run_id=dialog_data.run_id,
                created_at=dialog_data.created_at,
                pair_id=pair_id,
                dialog_id=dialog_data.id,
                text=record["original_text"],
            )
            assistant_original_nodes.append(original_node)

            # BELONGS_TO_DIALOG: Original → Dialogue
            assistant_dialog_edges.append(AssistantDialogEdge(
                source=original_id,
                target=dialog_data.id,
                end_user_id=dialog_data.end_user_id,
                run_id=dialog_data.run_id,
                created_at=dialog_data.created_at,
            ))

            # pruned_text 为 NULL 时不创建 AssistantPruned 节点和 PRUNED_TO 边
            if record["pruned_text"] == "NULL":
                continue

            pruned_node = AssistantPrunedNode(
                id=pruned_id,
                name=f"AssistantPruned_{pair_id[:8]}",
                end_user_id=dialog_data.end_user_id,
                run_id=dialog_data.run_id,
                created_at=dialog_data.created_at,
                pair_id=pair_id,
                dialog_id=dialog_data.id,
                text=record["pruned_text"],
                memory_type=record["memory_type"],
            )
            assistant_pruned_nodes.append(pruned_node)

            # PRUNED_TO: Original → Pruned
            assistant_pruned_edges.append(AssistantPrunedEdge(
                source=original_id,
                target=pruned_id,
                end_user_id=dialog_data.end_user_id,
                run_id=dialog_data.run_id,
                created_at=dialog_data.created_at,
                pair_id=pair_id,
            ))

    if assistant_original_nodes:
        logger.info(
            f"Assistant 剪枝节点创建完成 - "
            f"原始节点: {len(assistant_original_nodes)}, "
            f"剪枝节点: {len(assistant_pruned_nodes)}"
        )

    if progress_callback:
        nodes_edges_stats = {
            "dialogue_nodes_count": len(dialogue_nodes),
            "chunk_nodes_count": len(chunk_nodes),
            "statement_nodes_count": len(statement_nodes),
            "entity_nodes_count": len(entity_nodes),
            "statement_chunk_edges_count": len(stmt_chunk_edges),
            "statement_entity_edges_count": len(stmt_entity_edges),
            "entity_entity_edges_count": len(entity_entity_edges),
        }
        await progress_callback("creating_nodes_edges_complete", "创建节点和边完成", nodes_edges_stats)

    return GraphBuildResult(
        dialogue_nodes=dialogue_nodes,
        chunk_nodes=chunk_nodes,
        statement_nodes=statement_nodes,
        entity_nodes=entity_nodes,
        perceptual_nodes=perceptual_nodes,
        stmt_chunk_edges=stmt_chunk_edges,
        stmt_entity_edges=stmt_entity_edges,
        entity_entity_edges=entity_entity_edges,
        perceptual_edges=perceptual_edges,
        assistant_original_nodes=assistant_original_nodes,
        assistant_pruned_nodes=assistant_pruned_nodes,
        assistant_pruned_edges=assistant_pruned_edges,
        assistant_dialog_edges=assistant_dialog_edges,
    )
