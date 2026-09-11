"""Transactional Neo4j writer for extracted memory graphs."""

from __future__ import annotations

import logging
from typing import List

from neo4j import AsyncDriver

from app.core.memory.models.graph_models import MemorySummaryNode
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import GraphWriteResult, MemoryGraphWriteCommand
from app.core.memory.storage.provider.neo4j.graph_write_queries import SPECIAL_ENTITY_QUERY
from app.core.utils.datetime_utils import as_utc_aware, to_iso_z

logger = logging.getLogger(__name__)


# 迁移自 repositories/neo4j/graph_saver.py 中的
# save_dialog_and_statements_to_neo4j()，并改为接收 MemoryGraphWriteCommand。
async def save_memory_graph(
    driver: AsyncDriver,
    command: MemoryGraphWriteCommand,
) -> GraphWriteResult:
    """Commit an extracted memory graph through a connected Neo4j driver.

    只负责数据写入，不触发聚类。聚类由写入管线在写入成功后通过 Celery
    任务 run_incremental_clustering 异步触发。

    Args:
        driver: Connected Neo4j async driver
        command: Complete set of nodes and relationships to commit

    Returns:
        IDs of nodes committed by the Neo4j transaction, grouped by label.
    """
    dialogue_nodes = command.dialogue_nodes
    chunk_nodes = command.chunk_nodes
    statement_nodes = command.statement_nodes
    entity_nodes = command.entity_nodes
    perceptual_nodes = command.perceptual_nodes
    entity_edges = command.entity_edges
    statement_chunk_edges = command.statement_chunk_edges
    statement_entity_edges = command.statement_entity_edges
    perceptual_edges = command.perceptual_edges
    assistant_original_nodes = command.assistant_original_nodes
    assistant_pruned_nodes = command.assistant_pruned_nodes
    assistant_pruned_edges = command.assistant_pruned_edges
    conversation_nodes = command.conversation_nodes
    assistant_conversation_edges = command.assistant_conversation_edges
    user_source_nodes = command.user_source_nodes
    user_source_edges = command.user_source_edges

    # TODO 需要在去重消歧节阶段，做以下逻辑的处理
    # 预处理：对特殊实体（"用户"、"AI助手"）复用 Neo4j 中已有节点的 ID，
    # 确保同一个 end_user_id 下只有一个"用户"节点和一个"AI助手"节点。
    if entity_nodes:
        _SPECIAL_NAMES = {"用户", "我", "user", "i", "ai助手", "助手", "ai assistant", "assistant"}
        end_user_id = entity_nodes[0].end_user_id if entity_nodes else None
        if end_user_id:
            try:
                # 查询已有的特殊实体
                async with driver.session() as session:
                    stmt = await session.run(
                        SPECIAL_ENTITY_QUERY,
                        end_user_id=end_user_id,
                        names=list(_SPECIAL_NAMES),
                    )
                    existing = await stmt.data()
                # 建立 name(lower) → existing_id 映射
                existing_id_map = {}
                for record in (existing or []):
                    name_lower = (record.get("name") or "").strip().lower()
                    if name_lower and record.get("id"):
                        existing_id_map[name_lower] = record["id"]

                if existing_id_map:
                    # 替换新实体的 ID 为已有 ID，同时更新所有引用该 ID 的边
                    for ent in entity_nodes:
                        name_lower = (ent.name or "").strip().lower()
                        if name_lower in existing_id_map:
                            old_id = ent.id
                            new_id = existing_id_map[name_lower]
                            if old_id != new_id:
                                ent.id = new_id
                                # 更新 statement_entity_edges 中的引用
                                for edge in statement_entity_edges:
                                    if edge.target == old_id:
                                        edge.target = new_id
                                    if edge.source == old_id:
                                        edge.source = new_id
                                # 更新 entity_edges 中的引用
                                for edge in entity_edges:
                                    if edge.source == old_id:
                                        edge.source = new_id
                                    if edge.target == old_id:
                                        edge.target = new_id
                                logger.info(
                                    f"特殊实体 '{ent.name}' ID 复用: {old_id[:8]}... → {new_id[:8]}..."
                                )
            except Exception as e:
                logger.warning(f"特殊实体 ID 复用查询失败（不影响写入）: {e}")

    # 定义事务函数，将所有写操作放在一个事务中
    async def _save_all_in_transaction(tx):
        """在单个事务中执行所有保存操作，避免死锁"""
        results = {}

        # 1. Save all dialogue nodes in batch
        if dialogue_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import DIALOGUE_NODE_SAVE
            dialogue_data = [node.model_dump() for node in dialogue_nodes]
            result = await tx.run(DIALOGUE_NODE_SAVE, dialogues=dialogue_data)
            dialogue_uuids = [record["uuid"] async for record in result]
            results['dialogues'] = dialogue_uuids
            logger.debug(f"Dialogues saved to Neo4j with UUIDs: {dialogue_uuids}")

        # 2. Save all chunk nodes in batch
        if chunk_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import CHUNK_NODE_SAVE
            chunk_data = [node.model_dump() for node in chunk_nodes]
            result = await tx.run(CHUNK_NODE_SAVE, chunks=chunk_data)
            chunk_uuids = [record["uuid"] async for record in result]
            results['chunks'] = chunk_uuids
            logger.debug(f"Successfully saved {len(chunk_uuids)} chunk nodes to Neo4j")

        if perceptual_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import PERCEPTUAL_NODE_SAVE
            perceptual_data = []
            for node in perceptual_nodes:
                d = node.model_dump()
                ct = d.get("created_at")
                if ct is not None:
                    d["created_at"] = as_utc_aware(ct).replace(tzinfo=None)
                perceptual_data.append(d)
            result = await tx.run(PERCEPTUAL_NODE_SAVE, perceptuals=perceptual_data)
            perceptual_uuids = [record["uuid"] async for record in result]
            results["perceptuals"] = perceptual_uuids
            logger.debug(f"Successfully saved {len(perceptual_uuids)} perceptual nodes to Neo4j")

        # 3. Save all statement nodes in batch
        if statement_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import STATEMENT_NODE_SAVE
            statement_data = [node.model_dump() for node in statement_nodes]
            result = await tx.run(STATEMENT_NODE_SAVE, statements=statement_data)
            statement_uuids = [record["uuid"] async for record in result]
            results['statements'] = statement_uuids
            logger.debug(f"Successfully saved {len(statement_uuids)} statement nodes to Neo4j")

        # 4. Save entities
        if entity_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import EXTRACTED_ENTITY_NODE_SAVE
            entity_data = [entity.model_dump() for entity in entity_nodes]
            result = await tx.run(EXTRACTED_ENTITY_NODE_SAVE, entities=entity_data)
            entity_uuids = [record["uuid"] async for record in result]
            results['entities'] = entity_uuids
            logger.debug(f"Successfully saved {len(entity_uuids)} entity nodes to Neo4j")

        # 5. Create entity relationships
        if entity_edges:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import ENTITY_RELATIONSHIP_SAVE
            relationship_data = []
            for edge in entity_edges:
                relationship_data.append({
                    'source_id': edge.source,
                    'target_id': edge.target,
                    'predicate': edge.relation_type,
                    'predicate_id': edge.relation_type_id,
                    'predicate_surface': edge.relation_type_surface,
                    'predicate_description': edge.relation_type_description,
                    'statement_id': edge.source_statement_id,
                    'value': edge.relation_value,
                    'statement': edge.statement,
                    'valid_at': to_iso_z(edge.valid_at),
                    'invalid_at': to_iso_z(edge.invalid_at),
                    'created_at': edge.created_at,
                    'run_id': edge.run_id,
                    'end_user_id': edge.end_user_id,
                })
            result = await tx.run(ENTITY_RELATIONSHIP_SAVE, relationships=relationship_data)
            rel_uuids = [record["uuid"] async for record in result]
            results['entity_relationships'] = rel_uuids
            logger.debug(f"Successfully saved {len(rel_uuids)} entity relationships to Neo4j")

        # 6. Save statement-chunk edges
        if statement_chunk_edges:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import CHUNK_STATEMENT_EDGE_SAVE
            sc_edge_data = []
            for edge in statement_chunk_edges:
                sc_edge_data.append({
                    "id": edge.id,
                    "source": edge.source,
                    "target": edge.target,
                    "created_at": edge.created_at,
                    "run_id": edge.run_id,
                    "end_user_id": edge.end_user_id,
                })
            result = await tx.run(CHUNK_STATEMENT_EDGE_SAVE, chunk_statement_edges=sc_edge_data)
            sc_uuids = [record["uuid"] async for record in result]
            results['statement_chunk_edges'] = sc_uuids
            logger.debug(f"Successfully saved {len(sc_uuids)} statement-chunk edges to Neo4j")

        # 7. Save statement-entity edges
        if statement_entity_edges:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import STATEMENT_ENTITY_EDGE_SAVE
            se_edge_data = []
            for edge in statement_entity_edges:
                se_edge_data.append({
                    "source": edge.source,
                    "target": edge.target,
                    "created_at": edge.created_at,
                    "run_id": edge.run_id,
                    "end_user_id": edge.end_user_id,
                    "connect_strength": getattr(edge, "connect_strength", "strong"),
                })
            result = await tx.run(STATEMENT_ENTITY_EDGE_SAVE, relationships=se_edge_data)
            se_uuids = [record["uuid"] async for record in result]
            results['statement_entity_edges'] = se_uuids
            logger.debug(f"Successfully saved {len(se_uuids)} statement-entity edges to Neo4j")
        # 8. Save perceptual edges
        # PerceptualEdge 同时承担两种语义，根据 source_type 分发到对应 Cypher：
        #   - source_type="chunk"  → PERCEPTUAL_CHUNK_EDGE_SAVE   (Chunk-[:HAS_PERCEPTUAL]->Perceptual)
        #   - source_type="entity" → PERCEPTUAL_ENTITY_EDGE_SAVE  (ExtractedEntity-[:HAS_PERCEPTUAL]->Perceptual)
        if perceptual_edges:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import (
                PERCEPTUAL_CHUNK_EDGE_SAVE,
                PERCEPTUAL_ENTITY_EDGE_SAVE,
            )

            chunk_edge_payload = []
            entity_edge_payload = []
            for edge in perceptual_edges:
                # 旧实例未设置 source_type 时，默认按 entity 处理（与 PerceptualEdge 字段默认值一致）
                source_type = getattr(edge, "source_type", "entity") or "entity"
                base = {
                    "perceptual_id": edge.target,
                    "end_user_id": edge.end_user_id,
                    "run_id": edge.run_id,
                    "created_at": edge.created_at,
                    "perceptual_type": edge.perceptual_type,
                    "perceptual_type_id": edge.perceptual_type_id,
                }
                if source_type == "chunk":
                    chunk_edge_payload.append({**base, "chunk_id": edge.source})
                else:
                    entity_edge_payload.append({**base, "entity_id": edge.source})

            if entity_edge_payload:
                result = await tx.run(
                    PERCEPTUAL_ENTITY_EDGE_SAVE, edges=entity_edge_payload
                )
                perceptual_entity_uuids = [record["uuid"] async for record in result]
                results['perceptual_entity_edges'] = perceptual_entity_uuids
                logger.debug(
                    f"Successfully saved {len(perceptual_entity_uuids)} perceptual-entity edges to Neo4j"
                )

            if chunk_edge_payload:
                result = await tx.run(
                    PERCEPTUAL_CHUNK_EDGE_SAVE, edges=chunk_edge_payload
                )
                perceptual_chunk_uuids = [record["uuid"] async for record in result]
                results['perceptual_chunk_edges'] = perceptual_chunk_uuids
                logger.debug(
                    f"Successfully saved {len(perceptual_chunk_uuids)} perceptual-chunk edges to Neo4j"
                )

        # 9. Save assistant original nodes
        if assistant_original_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import ASSISTANT_ORIGINAL_NODE_SAVE
            original_data = [node.model_dump() for node in assistant_original_nodes]
            result = await tx.run(ASSISTANT_ORIGINAL_NODE_SAVE, originals=original_data)
            original_uuids = [record["uuid"] async for record in result]
            results['assistant_originals'] = original_uuids
            logger.debug(f"Successfully saved {len(original_uuids)} assistant original nodes to Neo4j")

        # 9. Save assistant pruned nodes
        if assistant_pruned_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import ASSISTANT_PRUNED_NODE_SAVE
            pruned_data = [node.model_dump() for node in assistant_pruned_nodes]
            result = await tx.run(ASSISTANT_PRUNED_NODE_SAVE, pruneds=pruned_data)
            pruned_uuids = [record["uuid"] async for record in result]
            results['assistant_pruneds'] = pruned_uuids
            logger.debug(f"Successfully saved {len(pruned_uuids)} assistant pruned nodes to Neo4j")

        # 10. Save PRUNED_TO edges (Original → Pruned)
        if assistant_pruned_edges:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import ASSISTANT_PRUNED_EDGE_SAVE
            edge_data = [{
                "source": edge.source,
                "target": edge.target,
                "pair_id": edge.pair_id,
                "end_user_id": edge.end_user_id,
                "run_id": edge.run_id,
                "created_at": edge.created_at,
            } for edge in assistant_pruned_edges]
            result = await tx.run(ASSISTANT_PRUNED_EDGE_SAVE, edges=edge_data)
            pruned_edge_uuids = [record["uuid"] async for record in result]
            results['assistant_pruned_edges'] = pruned_edge_uuids
            logger.debug(f"Successfully saved {len(pruned_edge_uuids)} PRUNED_TO edges to Neo4j")

        # 11. Save Conversation hub nodes (MERGE by conversation_id)
        if conversation_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import CONVERSATION_NODE_SAVE
            conv_data = [{
                "id": node.id,
                "name": node.name,
                "end_user_id": node.end_user_id,
                "conversation_id": node.conversation_id,
                "run_id": node.run_id,
                "created_at": node.created_at,
            } for node in conversation_nodes]
            result = await tx.run(CONVERSATION_NODE_SAVE, conversations=conv_data)
            conv_uuids = [record["uuid"] async for record in result]
            results['conversation_nodes'] = conv_uuids
            logger.debug(f"Successfully saved {len(conv_uuids)} Conversation nodes to Neo4j")

        # 12. Save BELONGS_TO_CONVERSATION edges (Original → Conversation)
        if assistant_conversation_edges:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import ASSISTANT_CONVERSATION_EDGE_SAVE
            edge_data = [{
                "source": edge.source,
                "target": edge.target,
                "end_user_id": edge.end_user_id,
                "run_id": edge.run_id,
                "created_at": edge.created_at,
            } for edge in assistant_conversation_edges]
            result = await tx.run(ASSISTANT_CONVERSATION_EDGE_SAVE, edges=edge_data)
            conv_edge_uuids = [record["uuid"] async for record in result]
            results['assistant_conversation_edges'] = conv_edge_uuids
            logger.debug(f"Successfully saved {len(conv_edge_uuids)} BELONGS_TO_CONVERSATION edges to Neo4j")

        # 13. Save UserSource nodes
        if user_source_nodes:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import USER_SOURCE_NODE_SAVE
            node_data = []
            for node in user_source_nodes:
                d = node.model_dump()
                ct = d.get("created_at")
                if ct is not None:
                    d["created_at"] = as_utc_aware(ct).replace(tzinfo=None)
                node_data.append(d)
            result = await tx.run(USER_SOURCE_NODE_SAVE, nodes=node_data)
            us_uuids = [record["uuid"] async for record in result]
            results['user_source_nodes'] = us_uuids
            logger.debug(f"Successfully saved {len(us_uuids)} UserSource nodes to Neo4j")

        # 14. Save HAS_ORIGINAL_CONTENT edges (UserSource → ExtractedEntity)
        if user_source_edges:
            from app.core.memory.storage.provider.neo4j.graph_write_queries import USER_SOURCE_ENTITY_EDGE_SAVE
            edge_data = [{
                "id": edge.id,
                "source": edge.source,
                "target": edge.target,
                "end_user_id": edge.end_user_id,
                "run_id": edge.run_id,
                "created_at": edge.created_at,
            } for edge in user_source_edges]
            result = await tx.run(USER_SOURCE_ENTITY_EDGE_SAVE, edges=edge_data)
            us_edge_uuids = [record["uuid"] async for record in result]
            results['user_source_edges'] = us_edge_uuids
            logger.debug(f"Successfully saved {len(us_edge_uuids)} HAS_ORIGINAL_CONTENT edges to Neo4j")

        return results

    try:
        # 使用显式写事务执行所有操作，避免死锁
        async with driver.session() as session:
            results = await session.execute_write(_save_all_in_transaction)
        summary = {
            key: len(value)
            for key, value in results.items()
            if isinstance(value, (list, tuple, set))
        }
        logger.info("Transaction completed. Summary: %s", summary)
        logger.debug("Full transaction results: %r", results)

        node_result_keys = {
            "dialogues": MemoryNodeType.DIALOGUE,
            "chunks": MemoryNodeType.CHUNK,
            "statements": MemoryNodeType.STATEMENT,
            "entities": MemoryNodeType.EXTRACTED_ENTITY,
            "perceptuals": MemoryNodeType.PERCEPTUAL,
            "assistant_originals": MemoryNodeType.ASSISTANT_ORIGINAL,
            "assistant_pruneds": MemoryNodeType.ASSISTANT_PRUNED,
            "conversation_nodes": MemoryNodeType.CONVERSATION,
            "user_source_nodes": MemoryNodeType.USER_SOURCE,
        }
        node_ids = {
            label: [str(node_id) for node_id in results.get(key, [])]
            for key, label in node_result_keys.items()
            if results.get(key)
        }
        relationship_count = sum(
            len(value)
            for key, value in results.items()
            if key not in node_result_keys and isinstance(value, (list, tuple, set))
        )
        return GraphWriteResult(
            node_ids=node_ids,
            relationship_count=relationship_count,
        )

    except Exception as e:
        logger.error(f"Neo4j integration error: {e}", exc_info=True)
        raise


# 合并迁移自 repositories/neo4j/add_nodes.py::add_memory_summary_nodes() 和
# repositories/neo4j/add_edges.py::add_memory_summary_statement_edges()，统一为单事务写入。
async def save_memory_summaries(
    driver: AsyncDriver,
    summaries: List[MemorySummaryNode],
) -> GraphWriteResult:
    """Commit summary nodes and their derived-statement edges atomically."""
    if not summaries:
        return GraphWriteResult()

    from app.core.memory.storage.provider.neo4j.graph_write_queries import (
        MEMORY_SUMMARY_NODE_SAVE,
        MEMORY_SUMMARY_STATEMENT_EDGE_SAVE,
    )

    async def _save_summaries(tx):
        summary_data = [summary.model_dump() for summary in summaries]
        result = await tx.run(MEMORY_SUMMARY_NODE_SAVE, summaries=summary_data)
        summary_ids = [record["uuid"] async for record in result]

        edge_data = [
            {
                "summary_id": summary.id,
                "chunk_id": chunk_id,
                "end_user_id": summary.end_user_id,
                "run_id": summary.run_id,
                "created_at": summary.created_at,
            }
            for summary in summaries
            for chunk_id in summary.chunk_ids
        ]
        relationship_ids = []
        if edge_data:
            result = await tx.run(MEMORY_SUMMARY_STATEMENT_EDGE_SAVE, edges=edge_data)
            relationship_ids = [record["uuid"] async for record in result]
        return summary_ids, relationship_ids

    async with driver.session() as session:
        summary_ids, relationship_ids = await session.execute_write(_save_summaries)
    return GraphWriteResult(
        node_ids={
            MemoryNodeType.MEMORY_SUMMARY: [str(node_id) for node_id in summary_ids]
        },
        relationship_count=len(relationship_ids),
    )
