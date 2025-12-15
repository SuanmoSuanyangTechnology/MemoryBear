import asyncio
from dotenv import load_dotenv
import time
from datetime import datetime

from app.repositories.neo4j.graph_saver import save_dialog_and_statements_to_neo4j

from app.core.memory.agent.utils.get_dialogs import get_chunked_dialogs
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)
# 使用新的模块化架构
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import ExtractionOrchestrator
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.embedding_generation import (
    embedding_generation_all,
)

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
# 导入配置模块（而不是直接导入变量）
from app.core.memory.utils.config import definitions as config_defs
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.utils.log.logging_utils import log_time
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import Memory_summary_generation
from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
from app.repositories.neo4j.add_edges import add_memory_summary_statement_edges
load_dotenv()


async def write(content: str, user_id: str, apply_id: str, group_id: str, ref_id: str = "wyl20251027", config_id: str = None) -> None:
    """
    执行完整的知识提取流水线（使用新的 ExtractionOrchestrator）

    Args:
        content: 对话内容
        user_id: 用户ID
        apply_id: 应用ID
        group_id: 组ID
        ref_id: 参考ID，默认为 "wyl20251027"
        config_id: 配置ID，用于标记数据处理配置
    """
    logger.info("=== MemSci Knowledge Extraction Pipeline ===")
    logger.info(f"Using model: {config_defs.SELECTED_LLM_NAME}")
    logger.info(f"Using LLM ID: {config_defs.SELECTED_LLM_ID}")
    logger.info(f"Using chunker strategy: {config_defs.SELECTED_CHUNKER_STRATEGY}")
    logger.info(f"Using group ID: {config_defs.SELECTED_GROUP_ID}")
    logger.info(f"Using embedding ID: {config_defs.SELECTED_EMBEDDING_ID}")
    logger.info(f"Config ID: {config_id if config_id else 'None'}")
    logger.info(f"LANGFUSE_ENABLED: {config_defs.LANGFUSE_ENABLED}")
    logger.info(f"AGENTA_ENABLED: {config_defs.AGENTA_ENABLED}")

    # Initialize timing log
    log_file = "logs/time.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n=== Pipeline Run Started: {timestamp} ===\n")

    pipeline_start = time.time()

    # 初始化客户端
    llm_client = get_llm_client(config_defs.SELECTED_LLM_ID)
    
    # 获取 embedder 配置
    from app.core.models.base import RedBearModelConfig
    from app.core.memory.utils.config.config_utils import get_embedder_config
    from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
    
    embedder_config_dict = get_embedder_config(config_defs.SELECTED_EMBEDDING_ID)
    embedder_config = RedBearModelConfig(**embedder_config_dict)
    embedder_client = OpenAIEmbedderClient(embedder_config)
    
    neo4j_connector = Neo4jConnector()
    
    # Step 1: 加载和分块数据
    step_start = time.time()
    chunked_dialogs = await get_chunked_dialogs(
        chunker_strategy=config_defs.SELECTED_CHUNKER_STRATEGY,
        group_id=group_id,
        user_id=user_id,
        apply_id=apply_id,
        content=content,
        ref_id=ref_id,
        config_id=config_id,
    )
    log_time("Data Loading & Chunking", time.time() - step_start, log_file)
    
    # Step 2: 初始化并运行 ExtractionOrchestrator
    step_start = time.time()
    from app.core.memory.utils.config.config_utils import get_pipeline_config
    config = get_pipeline_config()
    
    orchestrator = ExtractionOrchestrator(
        llm_client=llm_client,
        embedder_client=embedder_client,
        connector=neo4j_connector,
        config=config,
    )
    
    # 运行完整的提取流水线
    # orchestrator.run returns a flat tuple of 7 values after deduplication
    (
        all_dialogue_nodes,
        all_chunk_nodes,
        all_statement_nodes,
        all_entity_nodes,
        all_statement_chunk_edges,
        all_statement_entity_edges,
        all_entity_entity_edges,
    ) = await orchestrator.run(chunked_dialogs, is_pilot_run=False)
    
    log_time("Extraction Pipeline", time.time() - step_start, log_file)

    # Step 8: Save all data to Neo4j database using graph models
    step_start = time.time()
    # 运行索引创建
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

    # Step 9: Generate Memory summaries and save to local vector DB and Neo4j
    step_start = time.time()
    try:
        summaries = await Memory_summary_generation(
            chunked_dialogs, llm_client=llm_client, embedding_id=config_defs.SELECTED_EMBEDDING_ID
        )

        # Save memory summaries to Neo4j as nodes
        try:
            ms_connector = Neo4jConnector()
            await add_memory_summary_nodes(summaries, ms_connector)
            # Link summaries to statements via chunks for summary→entity queries
            await add_memory_summary_statement_edges(summaries, ms_connector)
        finally:
            try:
                await ms_connector.close()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Memory summary step failed: {e}", exc_info=True)
    finally:
        log_time("Memory Summary (Local Vector DB & Neo4j)", time.time() - step_start, log_file)



    # Log total pipeline time
    total_time = time.time() - pipeline_start
    log_time("TOTAL PIPELINE TIME", total_time, log_file)

    # Add completion marker to log
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"=== Pipeline Run Completed: {timestamp} ===\n\n")

    logger.info("=== Pipeline Complete ===")
    logger.info(f"Total execution time: {total_time:.2f} seconds")
    logger.info(f"Timing details saved to: {log_file}")


if __name__ == "__main__":
    content = "你好，我是张三，是张曼婷的新朋友。请问张曼婷喜欢什么？"
    asyncio.run(write(content, ref_id="wyl20251027"))
