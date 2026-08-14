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
from dataclasses import dataclass, field
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
    AssistantConversationEdge,
    ConversationNode,
)
from app.core.memory.models.message_models import Chunk, DialogData, Statement
from app.core.memory.models.triplet_models import Entity, Triplet
from app.core.memory.utils.name_similarity_utils import cosine_similarity

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════

# 感知记忆模态类型 → 数字枚举
# 从 100 段位编号，与 EntityEntityEdge.relation_type_id（1-13）在数字上隔离
# 新增模态（如 3d_model=105）只需扩展本映射，无需变更 Cypher
# TODO 数字枚举需要单独一个input_item
PERCEPTUAL_EDGE_TYPE_MAP: Dict[str, int] = {
    "image": 101,
    "video": 102,
    "audio": 103,
    "document": 104,
}

# 语义匹配阈值：与 UserSource → Entity 一致
_PERCEPTUAL_ENTITY_SIMILARITY_THRESHOLD = 0.5
# 兜底建边最低相似度地板：但若最高相似度仍低于本地板值，则放弃建边，避免向知识图注入语义完全无关的"垃圾边"

_PERCEPTUAL_FALLBACK_MIN_SIM = 0.15


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════
# HACK 关系强度需要考虑是否删除，connect_strength之前是为了进行去重消歧的，现在已经不需要，考虑移除工作
def _coalesce_connect_strength(obj: Any, default: str = "Strong") -> str:
    """提取 connect_strength，None 时返回默认值。"""
    val = getattr(obj, "connect_strength", None)
    return val if val is not None else default


def _get_perceptual_match_text(perceptual: PerceptualNode) -> str:
    """降级获取匹配文本：topic → keywords(拼接) → summary → 空。

    不同模态产出的 topic 质量不同（音频常无 topic），通过三级降级确保
    大部分感知记忆都能建边。

    Returns:
        非空字符串表示可用于 embedding 的匹配文本；空串表示三级全空，跳过建边。
    """
    topic = (perceptual.topic or "").strip()
    if topic:
        return topic

    keywords = perceptual.keywords or []
    if keywords:
        joined = " ".join(k for k in keywords if k and str(k).strip())
        if joined:
            return joined

    summary = (perceptual.summary or "").strip()
    if summary:
        return summary

    return ""


def _resolve_perceptual_type(file_type: Optional[str]) -> tuple[str, int]:
    """将 file_type 字符串映射为 (类型名, 类型ID)。

    未识别的类型统一回退为 "document"。
    """
    type_name = (file_type or "document").lower()
    type_id = PERCEPTUAL_EDGE_TYPE_MAP.get(type_name)
    if type_id is not None:
        return type_name, type_id
    logger.debug(
        f"[PerceptualEdge] 未知 file_type='{file_type}'，回退为 'document'"
    )
    return "document", PERCEPTUAL_EDGE_TYPE_MAP["document"]


# ═══════════════════════════════════════════════════════════════════════════
# 原子节点构建函数（纯函数，无副作用）
# ═══════════════════════════════════════════════════════════════════════════

def _build_dialogue_node(dialog_data: DialogData) -> DialogueNode:
    """
    从 DialogData 构建 DialogueNode。
    write_mode 显式置 'normal'，用于覆盖升级快写占位节点。
    """
    return DialogueNode(
        # dialog_data.id 可能已是带 Dialog_ 前缀的确定性 id，name 直接复用，避免 Dialog_Dialog_ 双前缀
        name=dialog_data.id,
        id=dialog_data.id,
        content=dialog_data.context.content if dialog_data.context else "",
        dialog_embedding=dialog_data.dialog_embedding,
        ref_id=dialog_data.ref_id,
        end_user_id=dialog_data.end_user_id,
        # HACK 这里run_id是对话的run_id，不是chunk的run_id，run_id已经没有什么作用
        run_id=dialog_data.run_id,
        created_at=dialog_data.created_at,
        metadata=dialog_data.metadata,
        config_id=dialog_data.config_id,
        write_mode="normal",
    )


def _build_chunk_node(
    chunk: Chunk, chunk_idx: int, dialog_data: DialogData
) -> ChunkNode:
    """从 Chunk 构建 ChunkNode。"""
    return ChunkNode(
        name=f"Chunk_{chunk.id}",
        id=chunk.id,
        content=chunk.content,
        speaker=chunk.speaker,
        chunk_embedding=chunk.chunk_embedding,
        metadata=chunk.metadata,
        dialog_id=dialog_data.id,
        end_user_id=dialog_data.end_user_id,
        run_id=dialog_data.run_id,
        created_at=dialog_data.created_at,
        sequence_number=chunk_idx,
    )


def _build_perceptual_node(p: Any, file_type: str) -> PerceptualNode:
    """从 PerceptualInfo 构建 PerceptualNode（不含 summary_embedding）。"""
    meta = p.meta_data or {}
    content_meta = meta.get("content", {})

    return PerceptualNode(
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
        summary_embedding=None,
    )


def _build_statement_node(
    statement: Statement, chunk: Chunk, dialog_data: DialogData
) -> StatementNode:
    """从 Statement 构建 StatementNode。"""
    tv = statement.temporal_validity
    return StatementNode(
        name=f"Statement_{statement.id}",
        id=statement.id,
        end_user_id=dialog_data.end_user_id,
        statement=statement.statement,
        statement_embedding=statement.statement_embedding,
        speaker=statement.speaker,
        stmt_type=statement.stmt_type,
        temporal_info=statement.temporal_info,
        emotion_type=statement.emotion_type,
        emotion_intensity=statement.emotion_intensity,
        emotion_keywords=statement.emotion_keywords,
        emotion_subject=statement.emotion_subject,
        emotion_target=statement.emotion_target,
        has_unsolved_reference=statement.has_unsolved_reference,
        is_permanent=statement.is_permanent,
        dialog_at=statement.dialog_at,
        chunk_id=chunk.id,
        connect_strength=_coalesce_connect_strength(statement),
        run_id=dialog_data.run_id,
        valid_at=tv.valid_at if tv else None,
        invalid_at=tv.invalid_at if tv else None,
        created_at=dialog_data.created_at,
        config_id=dialog_data.config_id,
    )


def _build_entity_node(
    entity: Entity, statement: Statement, dialog_data: DialogData
) -> ExtractedEntityNode:
    """从 ExtractedEntity 构建 ExtractedEntityNode。"""
    return ExtractedEntityNode(
        id=entity.id,
        entity_idx=entity.entity_idx,
        name=entity.name,
        name_embedding=entity.name_embedding,
        entity_type=entity.type,
        type_id=entity.type_id,
        type_description=entity.type_description,
        description=entity.description,
        example=entity.example,
        aliases=entity.aliases,
        is_explicit_memory=entity.is_explicit_memory,
        connect_strength=_coalesce_connect_strength(entity),
        statement_id=statement.id,
        end_user_id=dialog_data.end_user_id,
        run_id=dialog_data.run_id,
        created_at=dialog_data.created_at,
        config_id=dialog_data.config_id,
        extraction_count=1,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 原子边构建函数
# ═══════════════════════════════════════════════════════════════════════════

def _build_stmt_chunk_edge(
    statement: Statement, chunk: Chunk, dialog_data: DialogData
) -> StatementChunkEdge:
    return StatementChunkEdge(
        source=statement.id,
        target=chunk.id,
        end_user_id=dialog_data.end_user_id,
        run_id=dialog_data.run_id,
        created_at=dialog_data.created_at,
    )


def _build_stmt_entity_edge(
    statement: Statement, entity: Entity, dialog_data: DialogData
) -> StatementEntityEdge:
    return StatementEntityEdge(
        source=statement.id,
        target=entity.id,
        connect_strength=_coalesce_connect_strength(entity),
        end_user_id=dialog_data.end_user_id,
        run_id=dialog_data.run_id,
        created_at=dialog_data.created_at,
    )


def _build_entity_entity_edge(
    triplet: Triplet,
    subject_entity_id: str,
    object_entity_id: str,
    statement: Statement,
    dialog_data: DialogData,
) -> EntityEntityEdge:
    """从三元组构建 EntityEntityEdge。"""
    tv = statement.temporal_validity
    return EntityEntityEdge(
        source=subject_entity_id,
        target=object_entity_id,
        relation_type=triplet.predicate,
        relation_type_id=triplet.predicate_id,
        relation_type_surface=triplet.predicate_surface,
        relation_type_description=triplet.predicate_description,
        statement=statement.statement,
        source_statement_id=statement.id,
        end_user_id=dialog_data.end_user_id,
        run_id=dialog_data.run_id,
        created_at=dialog_data.created_at,
        valid_at=tv.valid_at if tv else None,
        invalid_at=tv.invalid_at if tv else None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 感知记忆 ↔ 实体 语义匹配建边
# ═══════════════════════════════════════════════════════════════════════════

async def _build_perceptual_entity_edges(
    perceptual_nodes: List[PerceptualNode],
    entity_nodes: List[ExtractedEntityNode],
    embedder_client: Any = None,
) -> List[PerceptualEdge]:
    """语义匹配建边：PerceptualNode 匹配文本 ↔ Entity.name_embedding。

    对每个 PerceptualNode：
        1. 通过 _get_perceptual_match_text 获取匹配文本（三级降级）
        2. 批量生成所有 match_embedding（一次网络往返，避免逐节点串行）
        3. 与每个 Entity.name_embedding 做余弦相似度匹配
        4. sim >= 阈值 → 创建 PerceptualEdge(source=entity.id, target=perceptual.id)
        5. 未命中阈值时 → 兜底取相似度最高的单个实体建边
           前提：best_sim >= _PERCEPTUAL_FALLBACK_MIN_SIM，避免低质量"垃圾边"污染知识图

    异常处理：
        - 匹配文本为空 / embedder_client 为空 / 批量 embedding 失败 → 相关节点跳过，不阻断
        - Entity 无 name_embedding → 跳过该 Entity
    """
    if not perceptual_nodes or not entity_nodes:
        return []

    if embedder_client is None:
        logger.warning(
            "[PerceptualEdge] embedder_client 为空，跳过感知记忆-实体建边 "
            f"(perceptual={len(perceptual_nodes)}, entity={len(entity_nodes)})"
        )
        return []

    # ── 批量生成 match_embedding（一次网络往返完成，避免 K 次串行）──
    match_embeddings = await _batch_embed_perceptual_match_texts(
        perceptual_nodes, embedder_client
    )
    if not match_embeddings:
        return []

    edges: List[PerceptualEdge] = []
    fallback_count = 0
    fallback_skipped_count = 0

    for perceptual in perceptual_nodes:
        match_embedding = match_embeddings.get(perceptual.id)
        if match_embedding is None:
            # 已在批量步骤内记录跳过原因（空文本 / embedding 失败）
            continue

        # 模态类型映射
        p_type_name, p_type_id = _resolve_perceptual_type(perceptual.file_type) 

        matched_count = 0
        best_sim = -1.0
        best_entity: Optional[ExtractedEntityNode] = None

        for entity in entity_nodes:
            if not entity.name_embedding:
                continue

            sim = cosine_similarity(match_embedding, entity.name_embedding)

            # 跟踪当前 perceptual 对所有实体的最高相似度（为兜底规则服务）
            if sim > best_sim:
                best_sim = sim
                best_entity = entity

            if sim < _PERCEPTUAL_ENTITY_SIMILARITY_THRESHOLD:
                continue

            edges.append(
                PerceptualEdge(
                    source=entity.id,
                    target=perceptual.id,
                    end_user_id=perceptual.end_user_id,
                    run_id=perceptual.run_id,
                    created_at=perceptual.created_at,
                    perceptual_type=p_type_name,
                    perceptual_type_id=p_type_id,
                    source_type="entity",
                )
            )
            matched_count += 1

        # 兜底规则：未命中阈值且最高相似度 >= 地板值时，建一条兜底边
        # 地板值防止把完全无关的实体挂到知识图上，污染读侧检索
        if matched_count == 0 and best_entity is not None:
            if best_sim >= _PERCEPTUAL_FALLBACK_MIN_SIM:
                edges.append(
                    PerceptualEdge(
                        source=best_entity.id,
                        target=perceptual.id,
                        end_user_id=perceptual.end_user_id,
                        run_id=perceptual.run_id,
                        created_at=perceptual.created_at,
                        perceptual_type=p_type_name,
                        perceptual_type_id=p_type_id,
                        source_type="entity",
                    )
                )
                fallback_count += 1
                logger.info(
                    f"[PerceptualEdge][fallback] perceptual_id={perceptual.id} "
                    f"无实体达阈值，采用最高相似度实体 entity_id={best_entity.id} "
                    f"best_sim={best_sim:.4f} threshold={_PERCEPTUAL_ENTITY_SIMILARITY_THRESHOLD} "
                    f"floor={_PERCEPTUAL_FALLBACK_MIN_SIM}"
                )
            else:
                fallback_skipped_count += 1
                logger.info(
                    f"[PerceptualEdge][fallback-skip] perceptual_id={perceptual.id} "
                    f"最高相似度低于兜底地板，放弃建边 "
                    f"best_sim={best_sim:.4f} floor={_PERCEPTUAL_FALLBACK_MIN_SIM}"
                )
        else:
            logger.debug(
                f"[PerceptualEdge] perceptual_id={perceptual.id} "
                f"matched_entities={matched_count}/{len(entity_nodes)} best_sim={best_sim:.4f}"
            )

    logger.info(
        f"[PerceptualEdge] 语义匹配建边完成，共 {len(edges)} 条"
        f"（其中兜底 {fallback_count} 条，兜底放弃 {fallback_skipped_count} 条）"
    )
    return edges


async def _batch_embed_perceptual_match_texts(
    perceptual_nodes: List[PerceptualNode],
    embedder_client: Any,
) -> Dict[str, List[float]]:
    """批量生成 PerceptualNode 匹配文本的 embedding。

    对所有非空匹配文本一次性调用 aembed_documents，替代旧实现里逐节点 aembed_query
    的串行网络往返，5 万次写入场景下可将 embedding 阶段耗时从小时级压到分钟级。

    Returns:
        Dict[perceptual_id, embedding]。只包含匹配文本非空且批量 embedding 成功
        的节点；调用方通过 dict.get() 拿不到值即视为该节点跳过。
    """
    # 收集非空匹配文本，同时保留 perceptual_id 用于回填
    id_text_pairs: List[tuple[str, str]] = []
    for node in perceptual_nodes:
        text = _get_perceptual_match_text(node)
        if text:
            id_text_pairs.append((node.id, text))
        else:
            logger.debug(
                "[PerceptualEdge] 跳过（topic/keywords/summary 全空）"
                f" perceptual_id={node.id}"
            )

    if not id_text_pairs:
        return {}

    try:
        texts = [t for _, t in id_text_pairs]
        embeddings = await embedder_client.aembed_documents(texts)
    except Exception as emb_err:
        logger.warning(
            f"[PerceptualEdge] 批量生成 match_embedding 失败，"
            f"影响 {len(id_text_pairs)} 个节点: {emb_err}"
        )
        return {}

    return {pid: emb for (pid, _), emb in zip(id_text_pairs, embeddings)}


# ═══════════════════════════════════════════════════════════════════════════
# GraphBuildResult
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class GraphBuildResult:
    """图构建步骤的输出。"""

    dialogue_nodes: List[DialogueNode] = field(default_factory=list)
    chunk_nodes: List[ChunkNode] = field(default_factory=list)
    statement_nodes: List[StatementNode] = field(default_factory=list)
    entity_nodes: List[ExtractedEntityNode] = field(default_factory=list)
    perceptual_nodes: List[PerceptualNode] = field(default_factory=list)
    stmt_chunk_edges: List[StatementChunkEdge] = field(default_factory=list)
    stmt_entity_edges: List[StatementEntityEdge] = field(default_factory=list)
    entity_entity_edges: List[EntityEntityEdge] = field(default_factory=list)
    perceptual_edges: List[PerceptualEdge] = field(default_factory=list)
    assistant_original_nodes: List[AssistantOriginalNode] = field(default_factory=list)
    assistant_pruned_nodes: List[AssistantPrunedNode] = field(default_factory=list)
    assistant_pruned_edges: List[AssistantPrunedEdge] = field(default_factory=list)
    conversation_nodes: List[ConversationNode] = field(default_factory=list)
    assistant_conversation_edges: List[AssistantConversationEdge] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 遍历层：从 DialogData 列表构建主体节点和边
# ═══════════════════════════════════════════════════════════════════════════

async def _populate_dialog_graph(
    result: GraphBuildResult,
    dialog_data_list: List[DialogData],
    embedder_client: Any,
    progress_callback: Optional[
        Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
    ],
    total_dialogs: int,
) -> None:
    """遍历 dialog_data_list，构建 DialogueNode / ChunkNode / StatementNode /
    ExtractedEntityNode / PerceptualNode 及相应边，写入 result。
    """
    entity_id_set: set = set()
    perceptual_id_set: set = set()
    # 用于构建 Chunk-[HAS_PERCEPTUAL]->Perceptual 边
    _chunk_perceptual_records: list[tuple[str, str, str, DialogData]] = []
    processed_dialogs = 0

    if progress_callback:
        await progress_callback(
            "creating_nodes_edges", "正在创建节点和边",
            {"total_dialogs": total_dialogs},
        )

    for dialog_data in dialog_data_list:
        processed_dialogs += 1

        # ── 对话节点 ──
        result.dialogue_nodes.append(_build_dialogue_node(dialog_data))

        # ── 分块 → 感知节点 + 陈述句节点 ──
        for chunk_idx, chunk in enumerate(dialog_data.chunks):
            result.chunk_nodes.append(_build_chunk_node(chunk, chunk_idx, dialog_data))

            # 感知节点
            for p, file_type in chunk.files:
                perceptual_key = str(p.id)
                if perceptual_key not in perceptual_id_set:
                    perceptual_id_set.add(perceptual_key)
                    result.perceptual_nodes.append(
                        _build_perceptual_node(p, file_type)
                    )
                # 记录 Chunk→Perceptual 关联（即使节点已去重，每个 chunk 引用仍需建边）
                _chunk_perceptual_records.append(
                    (chunk.id, perceptual_key, file_type, dialog_data)
                )

            # 陈述句节点 + 边
            for statement in chunk.statements:
                result.statement_nodes.append(
                    _build_statement_node(statement, chunk, dialog_data)
                )
                result.stmt_chunk_edges.append(
                    _build_stmt_chunk_edge(statement, chunk, dialog_data)
                )

                # 三元组 → 实体节点 + 实体边
                if statement.triplet_extraction_info:
                    await _process_triplet_info(
                        result=result,
                        statement=statement,
                        dialog_data=dialog_data,
                        entity_id_set=entity_id_set,
                        processed_dialogs=processed_dialogs,
                        total_dialogs=total_dialogs,
                        progress_callback=progress_callback,
                    )

    # ── 构建 Chunk-[HAS_PERCEPTUAL]->Perceptual 边 ──
    _seen_chunk_perceptual: set[tuple[str, str]] = set()
    for chunk_id, perceptual_id, file_type, dd in _chunk_perceptual_records:
        pair = (chunk_id, perceptual_id)
        if pair in _seen_chunk_perceptual:
            continue
        _seen_chunk_perceptual.add(pair)
        p_type_name, p_type_id = _resolve_perceptual_type(file_type)
        result.perceptual_edges.append(
            PerceptualEdge(
                source=chunk_id,
                target=perceptual_id,
                end_user_id=dd.end_user_id,
                run_id=dd.run_id,
                created_at=dd.created_at,
                perceptual_type=p_type_name,
                perceptual_type_id=p_type_id,
                source_type="chunk",
            )
        )

    # ── 批量生成 PerceptualNode 的 summary_embedding ──
    await _batch_embed_perceptual_summaries(result.perceptual_nodes, embedder_client)


async def _batch_embed_perceptual_summaries(
    perceptual_nodes: List[PerceptualNode],
    embedder_client: Any,
) -> None:
    """批量对 PerceptualNode.summary 生成 embedding，一次网络调用完成。"""
    if not embedder_client:
        return

    # 收集所有有 summary 的节点索引
    indexed: List[tuple[int, str]] = []
    for i, node in enumerate(perceptual_nodes):
        if node.summary:
            indexed.append((i, node.summary))

    if not indexed:
        return

    try:
        summaries = [s for _, s in indexed]
        embeddings = await embedder_client.aembed_documents(summaries)
        for (idx, _), emb in zip(indexed, embeddings):
            perceptual_nodes[idx].summary_embedding = emb
    except Exception as emb_err:
        logger.warning(f"批量生成 PerceptualNode summary_embedding 失败: {emb_err}")


async def _process_triplet_info(
    result: GraphBuildResult,
    statement: Statement,
    dialog_data: DialogData,
    entity_id_set: set,
    processed_dialogs: int,
    total_dialogs: int,
    progress_callback: Optional[
        Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
    ],
) -> None:
    """处理单个 Statement 的 triplet_extraction_info，构建实体节点和边。"""
    triplet_info = statement.triplet_extraction_info
    entity_idx_to_id: Dict[int, str] = {}

    # ── 实体节点 + Statement-Entity 边 ──
    for entity_idx, entity in enumerate(triplet_info.entities):
        entity_idx_to_id[entity.entity_idx] = entity.id
        entity_idx_to_id[entity_idx] = entity.id

        if entity.id not in entity_id_set:
            result.entity_nodes.append(
                _build_entity_node(entity, statement, dialog_data)
            )
            entity_id_set.add(entity.id)

        result.stmt_entity_edges.append(
            _build_stmt_entity_edge(statement, entity, dialog_data)
        )

    # ── Entity-Entity 边（三元组） ──
    for triplet in triplet_info.triplets:
        subject_entity_id = entity_idx_to_id.get(triplet.subject_id)
        object_entity_id = entity_idx_to_id.get(triplet.object_id)

        if subject_entity_id and object_entity_id:
            result.entity_entity_edges.append(
                _build_entity_entity_edge(
                    triplet=triplet,
                    subject_entity_id=subject_entity_id,
                    object_entity_id=object_entity_id,
                    statement=statement,
                    dialog_data=dialog_data,
                )
            )

            if progress_callback and len(result.entity_entity_edges) <= 10:
                await progress_callback(
                    "creating_nodes_edges_result",
                    f"关系创建中 ({processed_dialogs}/{total_dialogs})",
                    {
                        "result_type": "relationship_creation",
                        "relationship_index": len(result.entity_entity_edges),
                        "source_entity": triplet.subject_name,
                        "relation_type": triplet.predicate,
                        "target_entity": triplet.object_name,
                        "relationship_text": f"{triplet.subject_name} -[{triplet.predicate}]-> {triplet.object_name}",
                        "dialog_progress": f"{processed_dialogs}/{total_dialogs}",
                    },
                )
        else:
            missing_subject = "subject" if not subject_entity_id else ""
            missing_object = "object" if not object_entity_id else ""
            missing_both = (
                " and "
                if (not subject_entity_id and not object_entity_id)
                else ""
            )
            logger.debug(
                f"跳过三元组 - 无法找到{missing_subject}{missing_both}{missing_object}实体ID: "
                f"subject_id={triplet.subject_id} ({triplet.subject_name}), "
                f"object_id={triplet.object_id} ({triplet.object_name}), "
                f"predicate={triplet.predicate}, "
                f"statement_id={statement.id}, "
                f"available_indices={sorted(entity_idx_to_id.keys())}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Assistant 剪枝节点和边
# ═══════════════════════════════════════════════════════════════════════════

def _populate_assistant_pruning(
    result: GraphBuildResult,
    dialog_data_list: List[DialogData],
) -> None:
    """从 dialog_data_list 的 metadata 中提取 assistant_pruning_records，
    构建 Assistant 剪枝相关节点和边，写入 result。
    """
    seen_conv_ids: Dict[str, ConversationNode] = {}

    for dialog_data in dialog_data_list:
        pruning_records = dialog_data.metadata.get("assistant_pruning_records", [])
        if not pruning_records:
            continue

        conv_id = dialog_data.metadata.get("conversation_id", "")

        # ConversationNode 去重（同 conv_id 只创建一次）
        if conv_id and conv_id not in seen_conv_ids:
            conv_node = ConversationNode(
                id=conv_id,
                name=f"Conversation_{conv_id[:8]}",
                end_user_id=dialog_data.end_user_id,
                run_id=dialog_data.run_id,
                created_at=dialog_data.created_at,
                conversation_id=conv_id,
            )
            seen_conv_ids[conv_id] = conv_node
            result.conversation_nodes.append(conv_node)

        for record in pruning_records:
            pair_id = record["pair_id"]

            # 跳过空 original_text
            if not record.get("original_text", "").strip():
                continue

            original_id = f"orig_{pair_id}"
            pruned_id = f"pruned_{pair_id}"

            # AssistantOriginalNode
            result.assistant_original_nodes.append(
                AssistantOriginalNode(
                    id=original_id,
                    name=f"AssistantOriginal_{pair_id[:8]}",
                    end_user_id=dialog_data.end_user_id,
                    run_id=dialog_data.run_id,
                    created_at=dialog_data.created_at,
                    pair_id=pair_id,
                    dialog_id=dialog_data.id,
                    text=record["original_text"],
                )
            )

            # BELONGS_TO_CONVERSATION
            if conv_id:
                result.assistant_conversation_edges.append(
                    AssistantConversationEdge(
                        source=original_id,
                        target=conv_id,
                        end_user_id=dialog_data.end_user_id,
                        run_id=dialog_data.run_id,
                        created_at=dialog_data.created_at,
                    )
                )

            # pruned_text 为 NULL 时不创建 Pruned 节点和边
            if record["pruned_text"] == "NULL":
                continue

            result.assistant_pruned_nodes.append(
                AssistantPrunedNode(
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
            )

            result.assistant_pruned_edges.append(
                AssistantPrunedEdge(
                    source=original_id,
                    target=pruned_id,
                    end_user_id=dialog_data.end_user_id,
                    run_id=dialog_data.run_id,
                    created_at=dialog_data.created_at,
                    pair_id=pair_id,
                )
            )

    if result.assistant_original_nodes:
        logger.info(
            "Assistant 剪枝节点创建完成 - "
            f"原始节点: {len(result.assistant_original_nodes)}, "
            f"剪枝节点: {len(result.assistant_pruned_nodes)}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 编排层：主入口
# ═══════════════════════════════════════════════════════════════════════════

async def build_graph_nodes_and_edges(
    dialog_data_list: List[DialogData],
    embedder_client: Any = None,
    progress_callback: Optional[
        Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
    ] = None,
) -> GraphBuildResult:
    """从 DialogData 列表构建完整的图节点和边。

    Args:
        dialog_data_list: 经过萃取和数据赋值后的 DialogData 列表
        embedder_client: 可选的嵌入客户端，用于 PerceptualNode summary embedding
        progress_callback: 可选的进度回调

    Returns:
        GraphBuildResult 包含所有节点和边
    """
    logger.info("开始创建节点和边")

    result = GraphBuildResult()
    total_dialogs = len(dialog_data_list)

    # 1. 遍历对话，构建主体节点和边
    await _populate_dialog_graph(
        result=result,
        dialog_data_list=dialog_data_list,
        embedder_client=embedder_client,
        progress_callback=progress_callback,
        total_dialogs=total_dialogs,
    )

    # 2. 感知记忆 ↔ 实体 语义匹配建边
    result.perceptual_edges.extend(await _build_perceptual_entity_edges(
        perceptual_nodes=result.perceptual_nodes,
        entity_nodes=result.entity_nodes,
        embedder_client=embedder_client,
    ))

    # 3. Assistant 剪枝节点和边
    _populate_assistant_pruning(result=result, dialog_data_list=dialog_data_list)

    # ── 汇总日志 ──
    logger.info(
        f"节点和边创建完成 - 对话节点: {len(result.dialogue_nodes)}, "
        f"分块节点: {len(result.chunk_nodes)}, 陈述句节点: {len(result.statement_nodes)}, "
        f"实体节点: {len(result.entity_nodes)}, 感知节点: {len(result.perceptual_nodes)}, "
        f"陈述句-分块边: {len(result.stmt_chunk_edges)}, "
        f"陈述句-实体边: {len(result.stmt_entity_edges)}, "
        f"实体-实体边: {len(result.entity_entity_edges)}, "
        f"实体-感知边: {len(result.perceptual_edges)}"
    )

    # ── 进度回调 ──
    if progress_callback:
        await progress_callback(
            "creating_nodes_edges_complete",
            "创建节点和边完成",
            {
                "dialogue_nodes_count": len(result.dialogue_nodes),
                "chunk_nodes_count": len(result.chunk_nodes),
                "statement_nodes_count": len(result.statement_nodes),
                "entity_nodes_count": len(result.entity_nodes),
                "statement_chunk_edges_count": len(result.stmt_chunk_edges),
                "statement_entity_edges_count": len(result.stmt_entity_edges),
                "entity_entity_edges_count": len(result.entity_entity_edges),
            },
        )

    return result
