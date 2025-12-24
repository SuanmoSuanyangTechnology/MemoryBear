"""
Write Tools for Memory Knowledge Extraction Pipeline

This module provides the main write function for executing the knowledge extraction
pipeline. Only MemoryConfig is needed - clients are constructed internally.
"""
import time
from datetime import datetime

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.get_dialogs import get_chunked_dialogs
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import (
    ExtractionOrchestrator,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import (
    memory_summary_generation,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.memory.utils.log.logging_utils import log_time
from app.db import get_db_context
from app.repositories.neo4j.add_edges import add_memory_summary_statement_edges
from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
from app.repositories.neo4j.graph_saver import save_dialog_and_statements_to_neo4j
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_config_schema import MemoryConfig
from dotenv import load_dotenv

load_dotenv()

logger = get_agent_logger(__name__)


async def write(
    content: str,
    user_id: str,
    apply_id: str,
    group_id: str,
    memory_config: MemoryConfig,
    ref_id: str = "wyl20251027",
) -> None:
    """
    Execute the complete knowledge extraction pipeline.
    
    Only MemoryConfig is needed - LLM and embedding clients are constructed
    internally from the config.

    Args:
        content: Dialogue content to process
        user_id: User identifier
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        ref_id: Reference ID, defaults to "wyl20251027"
    """
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
    logger.info(f"Group ID: {group_id}")

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
        group_id=group_id,
        user_id=user_id,
        apply_id=apply_id,
        content=content,
        ref_id=ref_id,
        config_id=config_id,
    )
    log_time("Data Loading & Chunking", time.time() - step_start, log_file)

    # Step 2: Initialize and run ExtractionOrchestrator
    step_start = time.time()
    from app.core.memory.utils.config.config_utils import get_pipeline_config
    pipeline_config = get_pipeline_config(memory_config)

    orchestrator = ExtractionOrchestrator(
        llm_client=llm_client,
        embedder_client=embedder_client,
        connector=neo4j_connector,
        config=pipeline_config,
        embedding_id=embedding_model_id,
    )

    # Run the complete extraction pipeline
    (
        all_dialogue_nodes,
        all_chunk_nodes,
        all_statement_nodes,
        all_entity_nodes,
        all_statement_chunk_edges,
        all_statement_entity_edges,
        all_entity_entity_edges,
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

    try:
        success = await save_dialog_and_statements_to_neo4j(
            dialogue_nodes=all_dialogue_nodes,
            chunk_nodes=all_chunk_nodes,
            statement_nodes=all_statement_nodes,
            entity_nodes=all_entity_nodes,
            statement_chunk_edges=all_statement_chunk_edges,
            statement_entity_edges=all_statement_entity_edges,
            entity_edges=all_entity_entity_edges,
            connector=neo4j_connector
        )
        if success:
            logger.info("Successfully saved all data to Neo4j")
        else:
            logger.warning("Failed to save some data to Neo4j")
    finally:
        await neo4j_connector.close()

    log_time("Neo4j Database Save", time.time() - step_start, log_file)

    # Step 4: Generate Memory summaries and save to Neo4j
    step_start = time.time()
    try:
        summaries = await memory_summary_generation(
            chunked_dialogs, llm_client=llm_client, embedder_client=embedder_client
        )

        try:
            ms_connector = Neo4jConnector()
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

    logger.info("=== Pipeline Complete ===")
    logger.info(f"Total execution time: {total_time:.2f} seconds")
