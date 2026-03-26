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
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import ExtractionOrchestrator
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import \
    memory_summary_generation
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.memory.utils.log.logging_utils import log_time
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

    log_time("Extraction Pipeline", time.time() - step_start, log_file)

    # Step 3: Save all data to Neo4j database
    step_start = time.time()
    from app.repositories.neo4j.create_indexes import create_fulltext_indexes
    try:
        await create_fulltext_indexes()
    except Exception as e:
        logger.error(f"Error creating indexes: {e}", exc_info=True)

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
                
                # 使用 Celery 异步任务触发聚类（不阻塞主流程）
                if all_entity_nodes:
                    try:
                        from app.tasks import run_incremental_clustering
                        
                        end_user_id = all_entity_nodes[0].end_user_id
                        new_entity_ids = [e.id for e in all_entity_nodes]
                        
                        # 异步提交 Celery 任务
                        task = run_incremental_clustering.apply_async(
                            kwargs={
                                "end_user_id": end_user_id,
                                "new_entity_ids": new_entity_ids,
                                "llm_model_id": str(memory_config.llm_model_id) if memory_config.llm_model_id else None,
                                "embedding_model_id": str(memory_config.embedding_model_id) if memory_config.embedding_model_id else None,
                            },
                            # 设置任务优先级（低优先级，不影响主业务）
                            priority=3,
                        )
                        logger.info(
                            f"[Clustering] 增量聚类任务已提交到 Celery - "
                            f"task_id={task.id}, end_user_id={end_user_id}, entity_count={len(new_entity_ids)}"
                        )
                    except Exception as e:
                        # 聚类任务提交失败不影响主流程
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
    # try:
    #     from app.cache.memory.activity_stats_cache import ActivityStatsCache

    #     stats_to_cache = {
    #         "chunk_count": len(all_chunk_nodes) if all_chunk_nodes else 0,
    #         "statements_count": len(all_statement_nodes) if all_statement_nodes else 0,
    #         "triplet_entities_count": len(all_entity_nodes) if all_entity_nodes else 0,
    #         "triplet_relations_count": len(all_entity_entity_edges) if all_entity_entity_edges else 0,
    #         "temporal_count": 0,
    #     }
    #     await ActivityStatsCache.set_activity_stats(
    #         workspace_id=str(memory_config.workspace_id),
    #         stats=stats_to_cache,
    #     )
    #     logger.info(f"[WRITE] 活动统计已写入 Redis: workspace_id={memory_config.workspace_id}")
    # except Exception as cache_err:
    #     logger.warning(f"[WRITE] 写入活动统计缓存失败（不影响主流程）: {cache_err}", exc_info=True)

    # logger.info("=== Pipeline Complete ===")
    # logger.info(f"Total execution time: {total_time:.2f} seconds")
