import asyncio
import os
from typing import List, Optional

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.add_nodes import add_dialogue_nodes, add_statement_nodes, add_chunk_nodes
from app.repositories.neo4j.cypher_queries import (
    STATEMENT_ENTITY_EDGE_SAVE,
    ENTITY_RELATIONSHIP_SAVE,
    EXTRACTED_ENTITY_NODE_SAVE,
    CHUNK_STATEMENT_EDGE_SAVE,
    STATEMENT_ENTITY_EDGE_SAVE,
    ENTITY_RELATIONSHIP_SAVE,
    EXTRACTED_ENTITY_NODE_SAVE,
)
from app.core.memory.models.graph_models import (
    DialogueNode,
    ChunkNode,
    StatementChunkEdge,
    StatementEntityEdge,
    StatementNode,
    ExtractedEntityNode,
    EntityEntityEdge,
    PerceptualNode,
    PerceptualEdge,
    AssistantOriginalNode,
    AssistantPrunedNode,
    AssistantPrunedEdge,
    AssistantDialogEdge,
)
import logging

logger = logging.getLogger(__name__)


async def save_entities_and_relationships(
        entity_nodes: List[ExtractedEntityNode],
        entity_entity_edges: List[EntityEntityEdge],
        connector: Neo4jConnector
):
    """Save entities and their relationships using graph models"""
    all_entities = [entity.model_dump() for entity in entity_nodes]
    all_relationships = []

    for edge in entity_entity_edges:
        relationship = {
            'source_id': edge.source,
            'target_id': edge.target,
            'predicate': edge.relation_type,
            'predicate_id': edge.relation_type_id,
            'predicate_surface': edge.relation_type_surface,
            'predicate_description': edge.relation_type_description,
            'statement_id': edge.source_statement_id,
            'value': edge.relation_value,
            'statement': edge.statement,
            'valid_at': edge.valid_at.isoformat() if edge.valid_at else None,
            'invalid_at': edge.invalid_at.isoformat() if edge.invalid_at else None,
            'created_at': edge.created_at.isoformat() if edge.created_at else None,
            'run_id': edge.run_id,
            'end_user_id': edge.end_user_id,
        }
        all_relationships.append(relationship)

    # Save entities
    if all_entities:
        entity_uuids = await connector.execute_query(EXTRACTED_ENTITY_NODE_SAVE, entities=all_entities)
        if entity_uuids:
            print(f"Successfully saved {len(entity_uuids)} entity nodes to Neo4j")
        else:
            print("Failed to save entity nodes to Neo4j")
    else:
        print("No entity nodes to save")

    # Create relationships
    if all_relationships:
        relationship_uuids = await connector.execute_query(ENTITY_RELATIONSHIP_SAVE, relationships=all_relationships)
        if relationship_uuids:
            print(f"Successfully saved {len(relationship_uuids)} entity relationships (edges) to Neo4j")
        else:
            print("Failed to save entity relationships to Neo4j")
    else:
        print("No entity relationships to save")


async def save_chunk_nodes(
        chunk_nodes: List[ChunkNode],
        connector: Neo4jConnector
):
    """Save chunk nodes using graph models"""
    if not chunk_nodes:
        print("No chunk nodes to save")
        return

    chunk_uuids = await add_chunk_nodes(chunk_nodes, connector)
    if chunk_uuids:
        print(f"Successfully saved {len(chunk_uuids)} chunk nodes to Neo4j")
    else:
        print("Failed to save chunk nodes to Neo4j")


async def save_statement_chunk_edges(
        statement_chunk_edges: List[StatementChunkEdge],
        connector: Neo4jConnector
):
    """Save statement-chunk edges using graph models"""
    if not statement_chunk_edges:
        return

    all_sc_edges = []
    for edge in statement_chunk_edges:
        all_sc_edges.append({
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "end_user_id": edge.end_user_id,
            "run_id": edge.run_id,
            "created_at": edge.created_at.isoformat() if edge.created_at else None,
        })

    try:
        await connector.execute_query(
            CHUNK_STATEMENT_EDGE_SAVE,
            chunk_statement_edges=all_sc_edges
        )
    except Exception:
        pass


async def save_statement_entity_edges(
        statement_entity_edges: List[StatementEntityEdge],
        connector: Neo4jConnector
):
    """Save statement-entity edges using graph models"""
    if not statement_entity_edges:
        print("No statement-entity edges to save")
        return

    all_se_edges = []
    for edge in statement_entity_edges:
        edge_data = {
            "source": edge.source,
            "target": edge.target,
            "end_user_id": edge.end_user_id,
            "run_id": edge.run_id,
            "connect_strength": edge.connect_strength,
            "created_at": edge.created_at.isoformat() if edge.created_at else None,
        }
        all_se_edges.append(edge_data)

    if all_se_edges:
        try:
            await connector.execute_query(
                STATEMENT_ENTITY_EDGE_SAVE,
                relationships=all_se_edges
            )
        except Exception:
            pass


async def save_dialog_and_statements_to_neo4j(
        dialogue_nodes: List[DialogueNode],
        chunk_nodes: List[ChunkNode],
        statement_nodes: List[StatementNode],
        entity_nodes: List[ExtractedEntityNode],
        perceptual_nodes: List[PerceptualNode],
        entity_edges: List[EntityEntityEdge],
        statement_chunk_edges: List[StatementChunkEdge],
        statement_entity_edges: List[StatementEntityEdge],
        perceptual_edges: List[PerceptualEdge],
        connector: Neo4jConnector,
        assistant_original_nodes: Optional[List[AssistantOriginalNode]] = None,
        assistant_pruned_nodes: Optional[List[AssistantPrunedNode]] = None,
        assistant_pruned_edges: Optional[List[AssistantPrunedEdge]] = None,
        assistant_dialog_edges: Optional[List[AssistantDialogEdge]] = None,
) -> bool:
    """Save dialogue nodes, chunk nodes, statement nodes, entities, and all relationships to Neo4j using graph models.

    只负责数据写入，不触发聚类。聚类由调用方在写入成功后通过
    _trigger_clustering_sync() 显式触发。

    Args:
        dialogue_nodes: List of DialogueNode objects to save
        chunk_nodes: List of ChunkNode objects to save
        statement_nodes: List of StatementNode objects to save
        entity_nodes: List of ExtractedEntityNode objects to save
        perceptual_nodes: List of PerceptualNode objects to save
        entity_edges: List of EntityEntityEdge objects to save
        statement_chunk_edges: List of StatementChunkEdge objects to save
        statement_entity_edges: List of StatementEntityEdge objects to save
        perceptual_edges: List of PerceptualEdge objects to save
        connector: Neo4j connector instance

    Returns:
        bool: True if successful, False otherwise
    """
    # TODO 需要在去重消歧节阶段，做以下逻辑的处理
    # 预处理：对特殊实体（"用户"、"AI助手"）复用 Neo4j 中已有节点的 ID，
    # 确保同一个 end_user_id 下只有一个"用户"节点和一个"AI助手"节点。
    if entity_nodes:
        _SPECIAL_NAMES = {"用户", "我", "user", "i", "ai助手", "助手", "ai assistant", "assistant"}
        end_user_id = entity_nodes[0].end_user_id if entity_nodes else None
        if end_user_id:
            try:
                # 查询已有的特殊实体
                cypher = """
                MATCH (e:ExtractedEntity)
                WHERE e.end_user_id = $end_user_id AND toLower(e.name) IN $names
                RETURN e.id AS id, e.name AS name
                """
                existing = await connector.execute_query(
                    cypher,
                    end_user_id=end_user_id,
                    names=list(_SPECIAL_NAMES),
                )
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

    # 定义事务函数 — Phase 3: 拆分为 3 个独立事务
    split_enabled = os.getenv("NEO4J_SAVE_TRANSACTION_SPLIT", "true").lower() != "false"

    if not split_enabled:
        # ── 旧逻辑：单事务（Feature Flag 关闭时回退）──
        async def _save_all_in_transaction(tx):
            """在单个事务中执行所有保存操作"""
            results = {}

            if dialogue_nodes:
                from app.repositories.neo4j.cypher_queries import DIALOGUE_NODE_SAVE
                dialogue_data = [node.model_dump() for node in dialogue_nodes]
                result = await tx.run(DIALOGUE_NODE_SAVE, dialogues=dialogue_data)
                results['dialogues'] = [record["uuid"] async for record in result]

            if chunk_nodes:
                from app.repositories.neo4j.cypher_queries import CHUNK_NODE_SAVE
                chunk_data = [node.model_dump() for node in chunk_nodes]
                result = await tx.run(CHUNK_NODE_SAVE, chunks=chunk_data)
                results['chunks'] = [record["uuid"] async for record in result]

            if perceptual_nodes:
                from app.repositories.neo4j.cypher_queries import PERCEPTUAL_NODE_SAVE
                perceptual_data = [node.model_dump() for node in perceptual_nodes]
                result = await tx.run(PERCEPTUAL_NODE_SAVE, perceptuals=perceptual_data)
                results["perceptuals"] = [record["uuid"] async for record in result]

            if statement_nodes:
                from app.repositories.neo4j.cypher_queries import STATEMENT_NODE_SAVE
                statement_data = [node.model_dump() for node in statement_nodes]
                result = await tx.run(STATEMENT_NODE_SAVE, statements=statement_data)
                results['statements'] = [record["uuid"] async for record in result]

            if entity_nodes:
                from app.repositories.neo4j.cypher_queries import EXTRACTED_ENTITY_NODE_SAVE
                entity_data = [entity.model_dump() for entity in entity_nodes]
                result = await tx.run(EXTRACTED_ENTITY_NODE_SAVE, entities=entity_data)
                results['entities'] = [record["uuid"] async for record in result]

            if entity_edges:
                from app.repositories.neo4j.cypher_queries import ENTITY_RELATIONSHIP_SAVE
                relationship_data = [{
                    'source_id': edge.source, 'target_id': edge.target,
                    'predicate': edge.relation_type, 'predicate_id': edge.relation_type_id,
                    'predicate_surface': edge.relation_type_surface,
                    'predicate_description': edge.relation_type_description,
                    'statement_id': edge.source_statement_id, 'value': edge.relation_value,
                    'statement': edge.statement,
                    'valid_at': edge.valid_at.isoformat() if edge.valid_at else None,
                    'invalid_at': edge.invalid_at.isoformat() if edge.invalid_at else None,
                    'created_at': edge.created_at.isoformat() if edge.created_at else None,
                    'run_id': edge.run_id, 'end_user_id': edge.end_user_id,
                } for edge in entity_edges]
                result = await tx.run(ENTITY_RELATIONSHIP_SAVE, relationships=relationship_data)
                results['entity_relationships'] = [record["uuid"] async for record in result]

            if statement_chunk_edges:
                from app.repositories.neo4j.cypher_queries import CHUNK_STATEMENT_EDGE_SAVE
                sc_edge_data = [{"id": e.id, "source": e.source, "target": e.target,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "run_id": e.run_id, "end_user_id": e.end_user_id} for e in statement_chunk_edges]
                result = await tx.run(CHUNK_STATEMENT_EDGE_SAVE, chunk_statement_edges=sc_edge_data)
                results['statement_chunk_edges'] = [record["uuid"] async for record in result]

            if statement_entity_edges:
                from app.repositories.neo4j.cypher_queries import STATEMENT_ENTITY_EDGE_SAVE
                se_edge_data = [{"source": e.source, "target": e.target,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "run_id": e.run_id, "end_user_id": e.end_user_id,
                    "connect_strength": getattr(e, "connect_strength", "strong")} for e in statement_entity_edges]
                result = await tx.run(STATEMENT_ENTITY_EDGE_SAVE, relationships=se_edge_data)
                results['statement_entity_edges'] = [record["uuid"] async for record in result]

            if perceptual_edges:
                from app.repositories.neo4j.cypher_queries import PERCEPTUAL_CHUNK_EDGE_SAVE
                perceptual_edge_data = [{"perceptual_id": e.source, "chunk_id": e.target,
                    "end_user_id": e.end_user_id,
                    "created_at": e.created_at.isoformat() if e.created_at else None} for e in perceptual_edges]
                result = await tx.run(PERCEPTUAL_CHUNK_EDGE_SAVE, edges=perceptual_edge_data)
                results['perceptual_chunk_edges'] = [record["uuid"] async for record in result]

            if assistant_original_nodes:
                from app.repositories.neo4j.cypher_queries import ASSISTANT_ORIGINAL_NODE_SAVE
                result = await tx.run(ASSISTANT_ORIGINAL_NODE_SAVE, originals=[n.model_dump() for n in assistant_original_nodes])
                results['assistant_originals'] = [record["uuid"] async for record in result]

            if assistant_pruned_nodes:
                from app.repositories.neo4j.cypher_queries import ASSISTANT_PRUNED_NODE_SAVE
                result = await tx.run(ASSISTANT_PRUNED_NODE_SAVE, pruneds=[n.model_dump() for n in assistant_pruned_nodes])
                results['assistant_pruneds'] = [record["uuid"] async for record in result]

            if assistant_pruned_edges:
                from app.repositories.neo4j.cypher_queries import ASSISTANT_PRUNED_EDGE_SAVE
                edge_data = [{"source": e.source, "target": e.target, "pair_id": e.pair_id,
                    "end_user_id": e.end_user_id, "run_id": e.run_id,
                    "created_at": e.created_at.isoformat() if e.created_at else None} for e in assistant_pruned_edges]
                result = await tx.run(ASSISTANT_PRUNED_EDGE_SAVE, edges=edge_data)
                results['assistant_pruned_edges'] = [record["uuid"] async for record in result]

            if assistant_dialog_edges:
                from app.repositories.neo4j.cypher_queries import ASSISTANT_DIALOG_EDGE_SAVE
                edge_data = [{"source": e.source, "target": e.target,
                    "end_user_id": e.end_user_id, "run_id": e.run_id,
                    "created_at": e.created_at.isoformat() if e.created_at else None} for e in assistant_dialog_edges]
                result = await tx.run(ASSISTANT_DIALOG_EDGE_SAVE, edges=edge_data)
                results['assistant_dialog_edges'] = [record["uuid"] async for record in result]

            return results

        try:
            results = await connector.execute_write_transaction(_save_all_in_transaction)
            summary = {k: len(v) for k, v in results.items() if isinstance(v, (list, tuple, set))}
            logger.info("Transaction completed (single). Summary: %s", summary)
            return True
        except Exception as e:
            logger.error(f"Neo4j integration error: {e}", exc_info=True)
            return False

    # ── 新逻辑：拆分为 3 个事务（Phase 3）──

    # Tx-A: 节点（dialogue / chunks / statements / perceptual / entities）
    async def _save_nodes_tx(tx):
        results = {}
        if dialogue_nodes:
            from app.repositories.neo4j.cypher_queries import DIALOGUE_NODE_SAVE
            result = await tx.run(DIALOGUE_NODE_SAVE, dialogues=[n.model_dump() for n in dialogue_nodes])
            results['dialogues'] = [r["uuid"] async for r in result]

        if chunk_nodes:
            from app.repositories.neo4j.cypher_queries import CHUNK_NODE_SAVE
            result = await tx.run(CHUNK_NODE_SAVE, chunks=[n.model_dump() for n in chunk_nodes])
            results['chunks'] = [r["uuid"] async for r in result]

        if perceptual_nodes:
            from app.repositories.neo4j.cypher_queries import PERCEPTUAL_NODE_SAVE
            result = await tx.run(PERCEPTUAL_NODE_SAVE, perceptuals=[n.model_dump() for n in perceptual_nodes])
            results['perceptuals'] = [r["uuid"] async for r in result]

        if statement_nodes:
            from app.repositories.neo4j.cypher_queries import STATEMENT_NODE_SAVE
            result = await tx.run(STATEMENT_NODE_SAVE, statements=[n.model_dump() for n in statement_nodes])
            results['statements'] = [r["uuid"] async for r in result]

        if entity_nodes:
            from app.repositories.neo4j.cypher_queries import EXTRACTED_ENTITY_NODE_SAVE
            result = await tx.run(EXTRACTED_ENTITY_NODE_SAVE, entities=[e.model_dump() for e in entity_nodes])
            results['entities'] = [r["uuid"] async for r in result]

        return results

    # Tx-B: 边（statement_chunk / statement_entity / entity_entity / perceptual_chunk）
    async def _save_edges_tx(tx):
        results = {}
        if entity_edges:
            from app.repositories.neo4j.cypher_queries import ENTITY_RELATIONSHIP_SAVE
            relationship_data = [{
                'source_id': edge.source, 'target_id': edge.target,
                'predicate': edge.relation_type, 'predicate_id': edge.relation_type_id,
                'predicate_surface': edge.relation_type_surface,
                'predicate_description': edge.relation_type_description,
                'statement_id': edge.source_statement_id, 'value': edge.relation_value,
                'statement': edge.statement,
                'valid_at': edge.valid_at.isoformat() if edge.valid_at else None,
                'invalid_at': edge.invalid_at.isoformat() if edge.invalid_at else None,
                'created_at': edge.created_at.isoformat() if edge.created_at else None,
                'run_id': edge.run_id, 'end_user_id': edge.end_user_id,
            } for edge in entity_edges]
            result = await tx.run(ENTITY_RELATIONSHIP_SAVE, relationships=relationship_data)
            results['entity_relationships'] = [r["uuid"] async for r in result]

        if statement_chunk_edges:
            from app.repositories.neo4j.cypher_queries import CHUNK_STATEMENT_EDGE_SAVE
            sc_data = [{"id": e.id, "source": e.source, "target": e.target,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "run_id": e.run_id, "end_user_id": e.end_user_id} for e in statement_chunk_edges]
            result = await tx.run(CHUNK_STATEMENT_EDGE_SAVE, chunk_statement_edges=sc_data)
            results['statement_chunk_edges'] = [r["uuid"] async for r in result]

        if statement_entity_edges:
            from app.repositories.neo4j.cypher_queries import STATEMENT_ENTITY_EDGE_SAVE
            se_data = [{"source": e.source, "target": e.target,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "run_id": e.run_id, "end_user_id": e.end_user_id,
                "connect_strength": getattr(e, "connect_strength", "strong")} for e in statement_entity_edges]
            result = await tx.run(STATEMENT_ENTITY_EDGE_SAVE, relationships=se_data)
            results['statement_entity_edges'] = [r["uuid"] async for r in result]

        if perceptual_edges:
            from app.repositories.neo4j.cypher_queries import PERCEPTUAL_CHUNK_EDGE_SAVE
            pe_data = [{"perceptual_id": e.source, "chunk_id": e.target,
                "end_user_id": e.end_user_id,
                "created_at": e.created_at.isoformat() if e.created_at else None} for e in perceptual_edges]
            result = await tx.run(PERCEPTUAL_CHUNK_EDGE_SAVE, edges=pe_data)
            results['perceptual_chunk_edges'] = [r["uuid"] async for r in result]

        return results

    # Tx-C: assistant 旁路（original / pruned 节点与边）
    async def _save_assistant_tx(tx):
        results = {}
        if assistant_original_nodes:
            from app.repositories.neo4j.cypher_queries import ASSISTANT_ORIGINAL_NODE_SAVE
            result = await tx.run(ASSISTANT_ORIGINAL_NODE_SAVE, originals=[n.model_dump() for n in assistant_original_nodes])
            results['assistant_originals'] = [r["uuid"] async for r in result]

        if assistant_pruned_nodes:
            from app.repositories.neo4j.cypher_queries import ASSISTANT_PRUNED_NODE_SAVE
            result = await tx.run(ASSISTANT_PRUNED_NODE_SAVE, pruneds=[n.model_dump() for n in assistant_pruned_nodes])
            results['assistant_pruneds'] = [r["uuid"] async for r in result]

        if assistant_pruned_edges:
            from app.repositories.neo4j.cypher_queries import ASSISTANT_PRUNED_EDGE_SAVE
            edge_data = [{"source": e.source, "target": e.target, "pair_id": e.pair_id,
                "end_user_id": e.end_user_id, "run_id": e.run_id,
                "created_at": e.created_at.isoformat() if e.created_at else None} for e in assistant_pruned_edges]
            result = await tx.run(ASSISTANT_PRUNED_EDGE_SAVE, edges=edge_data)
            results['assistant_pruned_edges'] = [r["uuid"] async for r in result]

        if assistant_dialog_edges:
            from app.repositories.neo4j.cypher_queries import ASSISTANT_DIALOG_EDGE_SAVE
            edge_data = [{"source": e.source, "target": e.target,
                "end_user_id": e.end_user_id, "run_id": e.run_id,
                "created_at": e.created_at.isoformat() if e.created_at else None} for e in assistant_dialog_edges]
            result = await tx.run(ASSISTANT_DIALOG_EDGE_SAVE, edges=edge_data)
            results['assistant_dialog_edges'] = [r["uuid"] async for r in result]

        return results

    # ── 执行 3 个事务 ──
    try:
        # Tx-A: 节点（失败则中断）
        node_results = await connector.execute_write_transaction(_save_nodes_tx)
        logger.info("Tx-A (nodes) completed: %s", {k: len(v) for k, v in node_results.items()})
    except Exception as e:
        logger.error(f"Tx-A (nodes) failed, aborting: {e}", exc_info=True)
        return False

    try:
        # Tx-B: 边（死锁可重试）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                edge_results = await connector.execute_write_transaction(_save_edges_tx)
                logger.info("Tx-B (edges) completed: %s", {k: len(v) for k, v in edge_results.items()})
                break
            except Exception as e:
                if "deadlock" in str(e).lower() and attempt < max_retries - 1:
                    logger.warning(f"Tx-B deadlock, retry ({attempt + 2}/{max_retries})")
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    raise
    except Exception as e:
        logger.error(f"Tx-B (edges) failed after retries: {e}", exc_info=True)
        # 节点已写入，边缺失 — 不中断，后续去重/聚类仍可基于节点工作

    try:
        # Tx-C: assistant 旁路（失败仅记日志）
        if assistant_original_nodes or assistant_pruned_nodes or assistant_pruned_edges or assistant_dialog_edges:
            assistant_results = await connector.execute_write_transaction(_save_assistant_tx)
            logger.info("Tx-C (assistant) completed: %s", {k: len(v) for k, v in assistant_results.items()})
    except Exception as e:
        logger.warning(f"Tx-C (assistant) failed (non-critical): {e}")

    return True


async def _trigger_clustering_sync(
        entity_nodes: List,
        llm_model_id: Optional[str] = None,
        embedding_model_id: Optional[str] = None,
) -> None:
    """
    同步等待聚类完成，避免与其他 LLM 任务并发冲突。
    """
    if not entity_nodes:
        return

    clustering_enabled = os.getenv("CLUSTERING_ENABLED", "true").lower() != "false"
    if not clustering_enabled:
        logger.info("[Clustering] 聚类已禁用（CLUSTERING_ENABLED=false），跳过聚类触发")
        return

    end_user_id = entity_nodes[0].end_user_id
    new_entity_ids = [e.id for e in entity_nodes]
    logger.info(f"[Clustering] 准备触发聚类（同步），实体数: {len(new_entity_ids)}, end_user_id: {end_user_id}")
    await _trigger_clustering(new_entity_ids, end_user_id, llm_model_id=llm_model_id,
                              embedding_model_id=embedding_model_id)


async def _trigger_clustering(
        new_entity_ids: List[str],
        end_user_id: str,
        llm_model_id: Optional[str] = None,
        embedding_model_id: Optional[str] = None,
) -> None:
    """
    聚类触发函数，自动判断全量初始化还是增量更新。
    """
    connector = None
    try:
        from app.core.memory.storage_services.clustering_engine import LabelPropagationEngine
        logger.info(f"[Clustering] 开始聚类，end_user_id={end_user_id}, 实体数={len(new_entity_ids)}")
        connector = Neo4jConnector()
        engine = LabelPropagationEngine(connector, llm_model_id=llm_model_id, embedding_model_id=embedding_model_id)
        await engine.run(end_user_id=end_user_id, new_entity_ids=new_entity_ids)
        logger.info(f"[Clustering] 聚类完成，end_user_id={end_user_id}")
    except Exception as e:
        logger.error(f"[Clustering] 聚类触发失败: {e}", exc_info=True)
    finally:
        if connector:
            try:
                await connector.close()
            except Exception:
                pass
