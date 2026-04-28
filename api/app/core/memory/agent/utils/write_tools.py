"""
Write Tools for Memory Knowledge Extraction Pipeline

This module provides the main write function for executing the knowledge extraction
pipeline. Only MemoryConfig is needed - clients are constructed internally.
"""
import asyncio
import time
import uuid
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.get_dialogs import get_chunked_dialogs
from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import _USER_PLACEHOLDER_NAMES
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import ExtractionOrchestrator
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import \
    memory_summary_generation
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.memory.utils.log.logging_utils import log_time
from app.core.memory.utils.memory_count_utils import sync_end_user_memory_count_from_neo4j
from app.db import get_db_context
from app.repositories.neo4j.add_edges import add_memory_summary_statement_edges
from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
from app.repositories.neo4j.graph_saver import save_dialog_and_statements_to_neo4j
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_config_schema import MemoryConfig

load_dotenv()

logger = get_agent_logger(__name__)


async def write(
        end_user_id: str,
        memory_config: MemoryConfig,
        messages: list,
        ref_id: str = "",
        language: str = "zh",
) -> None:
    """
    Execute the complete knowledge extraction pipeline.

    Args:
        end_user_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        messages: Structured message list [{"role": "user", "content": "..."}, ...]
        ref_id: Reference ID, defaults to ""
        language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
    """
    if not ref_id:
        ref_id = uuid.uuid4().hex
    # Extract config values
    embedding_model_id = str(memory_config.embedding_model_id)
    chunker_strategy = memory_config.chunker_strategy
    config_id = str(memory_config.config_id)

    logger.info("=== MemSci Knowledge Extraction Pipeline ===")
    logger.info(f"Config: {memory_config.config_name} (ID: {config_id})")
    logger.info(f"Workspace: {memory_config.workspace_name}")
    logger.info(f"LLM model: {memory_config.llm_model_name}")
    logger.info(f"Embedding model: {memory_config.embedding_model_name}")
    logger.info(f"Chunker strategy: {chunker_strategy}")
    logger.info(f"end_user_id ID: {end_user_id}")

    # Construct clients from memory_config using factory pattern with db session
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        llm_client = factory.get_llm_client_from_config(memory_config)
        embedder_client = factory.get_embedder_client_from_config(memory_config)
    logger.info("LLM and embedding clients constructed")

    # Initialize timing log
    log_file = "logs/time.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n=== Pipeline Run Started: {timestamp} ===\n")
        f.write(f"Config: {memory_config.config_name} (ID: {config_id})\n")

    pipeline_start = time.time()

    # Initialize Neo4j connector
    neo4j_connector = Neo4jConnector()

    # Step 1: Load and chunk data
    step_start = time.time()
    chunked_dialogs = await get_chunked_dialogs(
        chunker_strategy=chunker_strategy,
        end_user_id=end_user_id,
        messages=messages,
        ref_id=ref_id,
        config_id=config_id,
    )
    log_time("Data Loading & Chunking", time.time() - step_start, log_file)

    # Step 2: Initialize and run ExtractionOrchestrator
    step_start = time.time()
    from app.core.memory.utils.config.config_utils import get_pipeline_config
    pipeline_config = get_pipeline_config(memory_config)

    # Fetch ontology types if scene_id is configured
    ontology_types = None
    if memory_config.scene_id:
        try:
            from app.core.memory.ontology_services.ontology_type_loader import load_ontology_types_for_scene

            with get_db_context() as db:
                ontology_types = load_ontology_types_for_scene(
                    scene_id=memory_config.scene_id,
                    workspace_id=memory_config.workspace_id,
                    db=db
                )

                if ontology_types:
                    logger.info(
                        f"Loaded {len(ontology_types.types)} ontology types for scene_id: {memory_config.scene_id}"
                    )
                else:
                    logger.info(f"No ontology classes found for scene_id: {memory_config.scene_id}")
        except Exception as e:
            logger.warning(
                f"Failed to fetch ontology types for scene_id {memory_config.scene_id}: {e}",
                exc_info=True
            )

    orchestrator = ExtractionOrchestrator(
        llm_client=llm_client,
        embedder_client=embedder_client,
        connector=neo4j_connector,
        config=pipeline_config,
        embedding_id=embedding_model_id,
        language=language,
        ontology_types=ontology_types,
    )

    # Run the complete extraction pipeline
    (
        all_dialogue_nodes,
        all_chunk_nodes,
        all_statement_nodes,
        all_entity_nodes,
        all_perceptual_nodes,
        all_statement_chunk_edges,
        all_statement_entity_edges,
        all_entity_entity_edges,
        all_perceptual_edges,
        all_dedup_details,
    ) = await orchestrator.run(chunked_dialogs, is_pilot_run=False)
    
# region TODO 乐力齐 重构流水线切换至生产环境稳定后，移除快照对比代码
    # ── Snapshot: 旧流水线萃取结果（按 phase2_step_io_schema_v1.md 格式） ──
    from app.core.memory.utils.debug.pipeline_snapshot import PipelineSnapshot
    snapshot = PipelineSnapshot("legacy")

    # Statement 输出（从 dialog_data_list 中提取）
    stmt_snapshot = []
    for d in all_dedup_details:
        if not hasattr(d, "chunks"):
            continue
        for c in d.chunks:
            for s in c.statements:
                stmt_snapshot.append({
                    "statement_id": s.id,
                    "statement_text": s.statement,
                    "statement_type": str(getattr(s, "stmt_type", "")),
                    "temporal_type": str(getattr(s, "temporal_info", "")),
                    "relevance": str(getattr(s, "relevence_info", "RELEVANT")),
                    "speaker": getattr(s, "speaker", "user") or "user",
                    "valid_at": s.temporal_validity.valid_at if s.temporal_validity else "NULL",
                    "invalid_at": s.temporal_validity.invalid_at if s.temporal_validity else "NULL",
                })
    snapshot.save_stage("2_statement_outputs", stmt_snapshot)

    # Triplet 输出（从 dialog_data_list 中提取）
    triplet_snapshot = {}
    for d in all_dedup_details:
        if not hasattr(d, "chunks"):
            continue
        for c in d.chunks:
            for s in c.statements:
                if s.triplet_extraction_info:
                    triplet_snapshot[s.id] = {
                        "entities": [
                            {
                                "entity_idx": e.entity_idx, "name": e.name,
                                "type": e.type, "type_description": getattr(e, "type_description", ""),
                                "description": e.description,
                                "is_explicit_memory": getattr(e, "is_explicit_memory", False),
                            }
                            for e in s.triplet_extraction_info.entities
                        ],
                        "triplets": [
                            {
                                "subject_name": t.subject_name, "subject_id": t.subject_id,
                                "predicate": t.predicate,
                                "predicate_description": getattr(t, "predicate_description", ""),
                                "object_name": t.object_name, "object_id": t.object_id,
                            }
                            for t in s.triplet_extraction_info.triplets
                        ],
                    }
    snapshot.save_stage("3_triplet_outputs", triplet_snapshot)

    # 图节点和边（去重后）
    snapshot.save_stage("6_nodes_edges_after_dedup", {
        "dialogue_nodes_count": len(all_dialogue_nodes),
        "chunk_nodes_count": len(all_chunk_nodes),
        "statement_nodes_count": len(all_statement_nodes),
        "entity_nodes": [
            {"id": e.id, "name": e.name, "entity_type": e.entity_type, "type_description": e.type_description, "description": e.description}
            for e in all_entity_nodes
        ],
        "entity_entity_edges": [
            {
                "source": e.source, "target": e.target,
                "relation_type": e.relation_type, "relation_type_description": e.relation_type_description, "statement": e.statement,
            }
            for e in all_entity_entity_edges
        ],
    })
    snapshot.save_summary({
        "dialogue_count": len(all_dialogue_nodes),
        "chunk_count": len(all_chunk_nodes),
        "statement_count": len(all_statement_nodes),
        "entity_count": len(all_entity_nodes),
        "relation_count": len(all_entity_entity_edges),
    })
# endregion
    
    log_time("Extraction Pipeline", time.time() - step_start, log_file)

    # Step 3: Save all data to Neo4j database
    step_start = time.time()

    # Neo4j 写入前：清洗用户/AI助手实体之间的别名交叉污染
    # 从 Neo4j 查询已有的 AI 助手别名，与本轮实体中的 AI 助手别名合并，
    # 确保用户实体的 aliases 不包含 AI 助手的名字
    try:
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
            clean_cross_role_aliases,
            fetch_neo4j_assistant_aliases,
        )
        neo4j_assistant_aliases = set()
        if all_entity_nodes:
            _eu_id = all_entity_nodes[0].end_user_id
            if _eu_id:
                neo4j_assistant_aliases = await fetch_neo4j_assistant_aliases(neo4j_connector, _eu_id)
        clean_cross_role_aliases(all_entity_nodes, external_assistant_aliases=neo4j_assistant_aliases)
        logger.info(f"Neo4j 写入前别名清洗完成，AI助手别名排除集大小: {len(neo4j_assistant_aliases)}")
    except Exception as e:
        logger.warning(f"Neo4j 写入前别名清洗失败（不影响主流程）: {e}")

    # 添加死锁重试机制
    max_retries = 3
    retry_delay = 1  # 秒

    for attempt in range(max_retries):
        try:
            success = await save_dialog_and_statements_to_neo4j(
                dialogue_nodes=all_dialogue_nodes,
                chunk_nodes=all_chunk_nodes,
                statement_nodes=all_statement_nodes,
                entity_nodes=all_entity_nodes,
                perceptual_nodes=all_perceptual_nodes,
                statement_chunk_edges=all_statement_chunk_edges,
                statement_entity_edges=all_statement_entity_edges,
                entity_edges=all_entity_entity_edges,
                perceptual_edges=all_perceptual_edges,
                connector=neo4j_connector,
            )
            if success:
                logger.info("Successfully saved all data to Neo4j")
                
                if all_entity_nodes:
                    end_user_id = all_entity_nodes[0].end_user_id

                    # Neo4j 写入完成后，用 PgSQL 权威 aliases 覆盖 Neo4j 用户实体
                    try:
                        from app.repositories.end_user_info_repository import EndUserInfoRepository
                        if end_user_id:
                            with get_db_context() as db_session:
                                info = EndUserInfoRepository(db_session).get_by_end_user_id(uuid.UUID(end_user_id))
                                pg_aliases = info.aliases if info and info.aliases else []
                            if info is not None:
                                # 将 Python 侧占位名集合作为参数传入，避免 Cypher 硬编码
                                placeholder_names = list(_USER_PLACEHOLDER_NAMES)
                                await neo4j_connector.execute_query(
                                    """
                                    MATCH (e:ExtractedEntity)
                                    WHERE e.end_user_id = $end_user_id AND toLower(e.name) IN $placeholder_names
                                    SET e.aliases = $aliases
                                    """,
                                    end_user_id=end_user_id, aliases=pg_aliases,
                                    placeholder_names=placeholder_names,
                                )
                                logger.info(f"[AliasSync] Neo4j 用户实体 aliases 已用 PgSQL 权威源覆盖: {pg_aliases}")
                    except Exception as sync_err:
                        logger.warning(f"[AliasSync] PgSQL→Neo4j aliases 同步失败（不影响主流程）: {sync_err}")

                    # 使用 Celery 异步任务触发聚类（不阻塞主流程）
                    try:
                        from app.tasks import run_incremental_clustering
                        
                        new_entity_ids = [e.id for e in all_entity_nodes]
                        task = run_incremental_clustering.apply_async(
                            kwargs={
                                "end_user_id": end_user_id,
                                "new_entity_ids": new_entity_ids,
                                "llm_model_id": str(memory_config.llm_model_id) if memory_config.llm_model_id else None,
                                "embedding_model_id": str(memory_config.embedding_model_id) if memory_config.embedding_model_id else None,
                            },
                            priority=3,
                        )
                        logger.info(
                            f"[Clustering] 增量聚类任务已提交到 Celery - "
                            f"task_id={task.id}, end_user_id={end_user_id}, entity_count={len(new_entity_ids)}"
                        )
                    except Exception as e:
                        logger.error(f"[Clustering] 提交聚类任务失败（不影响主流程）: {e}", exc_info=True)
                
                break
            else:
                logger.warning("Failed to save some data to Neo4j")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying... (attempt {attempt + 2}/{max_retries})")
                    await asyncio.sleep(retry_delay * (attempt + 1))  # 指数退避
        except Exception as e:
            error_msg = str(e)
            # 检查是否是死锁错误
            if "DeadlockDetected" in error_msg or "deadlock" in error_msg.lower():
                if attempt < max_retries - 1:
                    logger.warning(f"Deadlock detected, retrying... (attempt {attempt + 2}/{max_retries})")
                    await asyncio.sleep(retry_delay * (attempt + 1))  # 指数退避
                else:
                    logger.error(f"Failed after {max_retries} attempts due to deadlock: {e}")
                    raise
            else:
                # 非死锁错误，直接抛出
                raise

    try:
        await neo4j_connector.close()
    except Exception as e:
        logger.error(f"Error closing Neo4j connector: {e}")

    log_time("Neo4j Database Save", time.time() - step_start, log_file)

    # Step 4: Generate Memory summaries and save to Neo4j
    step_start = time.time()
    try:
        summaries = await memory_summary_generation(
            chunked_dialogs, llm_client=llm_client, embedder_client=embedder_client, language=language
        )
        ms_connector = Neo4jConnector()
        try:
            await add_memory_summary_nodes(summaries, ms_connector)
            await add_memory_summary_statement_edges(summaries, ms_connector)
        finally:
            try:
                await ms_connector.close()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Memory summary step failed: {e}", exc_info=True)
    finally:
        log_time("Memory Summary (Neo4j)", time.time() - step_start, log_file)

    # Log total pipeline time
    total_time = time.time() - pipeline_start
    log_time("TOTAL PIPELINE TIME", total_time, log_file)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"=== Pipeline Run Completed: {timestamp} ===\n\n")

    # 将提取统计写入 Redis，按 workspace_id 存储
    try:
        from app.cache.memory.activity_stats_cache import ActivityStatsCache

        stats_to_cache = {
            "chunk_count": len(all_chunk_nodes) if all_chunk_nodes else 0,
            "statements_count": len(all_statement_nodes) if all_statement_nodes else 0,
            "triplet_entities_count": len(all_entity_nodes) if all_entity_nodes else 0,
            "triplet_relations_count": len(all_entity_entity_edges) if all_entity_entity_edges else 0,
            "temporal_count": 0,
        }
        await ActivityStatsCache.set_activity_stats(
            workspace_id=str(memory_config.workspace_id),
            stats=stats_to_cache,
        )
        logger.info(f"[WRITE] 活动统计已写入 Redis: workspace_id = {memory_config.workspace_id}")
    except Exception as cache_err:
        logger.warning(f"[WRITE] 写入活动统计缓存失败（不影响主流程）: {cache_err}", exc_info=True)

    # 同步 Neo4j 记忆节点总数到 PostgreSQL end_users.memory_count
    if end_user_id:
        try:
            memory_count_connector = Neo4jConnector()
            try:
                node_count = await sync_end_user_memory_count_from_neo4j(
                    end_user_id,
                    memory_count_connector,
                )
            finally:
                await memory_count_connector.close()

            logger.info(
                f"[MemoryCount] 写入后同步 memory_count: "
                f"end_user_id={end_user_id}, count={node_count}"
            )
        except Exception as e:
            logger.warning(
                f"[MemoryCount] 写入后同步 memory_count 失败（不影响主流程）: {e}",
                exc_info=True,
            )
    
    # Close LLM/Embedder underlying httpx clients to prevent
    # 'RuntimeError: Event loop is closed' during garbage collection
    for client_obj in (llm_client, embedder_client):
        try:
            underlying = getattr(client_obj, 'client', None) or getattr(client_obj, 'model', None)
            if underlying is None:
                continue
            # Unwrap RedBearLLM / RedBearEmbeddings to get the LangChain model
            inner = getattr(underlying, '_model', underlying)
            # LangChain OpenAI models expose async_client (httpx.AsyncClient)
            http_client = getattr(inner, 'async_client', None)
            if http_client is not None and hasattr(http_client, 'aclose'):
                await http_client.aclose()
        except Exception:
            pass

    logger.info("=== Pipeline Complete ===")
    logger.info(f"Total execution time: {total_time:.2f} seconds")

