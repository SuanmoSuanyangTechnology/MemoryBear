"""
Pilot Run Service - 试运行服务

用于执行记忆系统的试运行流程，不保存到 Neo4j。
"""

import os
import re
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional

from app.core.logging_config import get_memory_logger, log_time
from app.core.memory.models.message_models import (
    ConversationContext,
    ConversationMessage,
    DialogData,
)
from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import (
    ExtractionOrchestrator,
    get_chunked_dialogs_from_preprocessed,
)
from app.core.memory.utils.config.config_utils import (
    get_pipeline_config,
)
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_config_schema import MemoryConfig
from sqlalchemy.orm import Session

logger = get_memory_logger(__name__)


async def run_pilot_extraction(
    memory_config: MemoryConfig,
    dialogue_text: str,
    db: Session,
    progress_callback: Optional[Callable[[str, str, Optional[dict]], Awaitable[None]]] = None,
) -> None:
    """
    执行试运行模式的知识提取流水线。

    Args:
        memory_config: 从数据库加载的内存配置对象
        dialogue_text: 输入的对话文本
        progress_callback: 可选的进度回调函数
            - 参数1 (stage): 当前处理阶段标识符
            - 参数2 (message): 人类可读的进度消息
            - 参数3 (data): 可选的附加数据字典
    """
    log_file = "logs/time.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n=== Pilot Run Started: {timestamp} ===\n")

    pipeline_start = time.time()
    neo4j_connector = None

    try:
        # 步骤 1: 初始化客户端
        logger.info("Initializing clients...")
        step_start = time.time()

        client_factory = MemoryClientFactory(db)
        llm_client = client_factory.get_llm_client(str(memory_config.llm_model_id))
        embedder_client = client_factory.get_embedder_client(str(memory_config.embedding_model_id))

        neo4j_connector = Neo4jConnector()

        log_time("Client Initialization", time.time() - step_start, log_file)

        # 步骤 2: 解析对话文本
        logger.info("Parsing dialogue text...")
        step_start = time.time()

        # 解析对话文本，支持 "用户:" 和 "AI:" 格式
        pattern = r"(用户|AI)[：:]\s*([^\n]+(?:\n(?!(?:用户|AI)[：:])[^\n]*)*?)"
        matches = re.findall(pattern, dialogue_text, re.MULTILINE | re.DOTALL)
        messages = [
            ConversationMessage(role=r, msg=c.strip())
            for r, c in matches
            if c.strip()
        ]

        # 如果没有匹配到格式化的对话，将整个文本作为用户消息
        if not messages:
            messages = [ConversationMessage(role="用户", msg=dialogue_text.strip())]

        context = ConversationContext(msgs=messages)
        dialog = DialogData(
            context=context,
            ref_id="pilot_dialog_1",
            group_id=str(memory_config.workspace_id),
            user_id=str(memory_config.tenant_id),
            apply_id=str(memory_config.config_id),
            metadata={"source": "pilot_run", "input_type": "frontend_text"},
        )

        if progress_callback:
            await progress_callback("text_preprocessing", "开始预处理文本...")

        chunked_dialogs = await get_chunked_dialogs_from_preprocessed(
            data=[dialog],
            chunker_strategy=memory_config.chunker_strategy,
            llm_client=llm_client,
        )
        logger.info(f"Processed dialogue text: {len(messages)} messages")

        # 进度回调：输出每个分块的结果
        if progress_callback:
            for dlg in chunked_dialogs:
                for i, chunk in enumerate(dlg.chunks):
                    chunk_result = {
                        "chunk_index": i + 1,
                        "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                        "full_length": len(chunk.content),
                        "dialog_id": dlg.id,
                        "chunker_strategy": memory_config.chunker_strategy,
                    }
                    await progress_callback("text_preprocessing_result", f"分块 {i + 1} 处理完成", chunk_result)

            preprocessing_summary = {
                "total_chunks": sum(len(dlg.chunks) for dlg in chunked_dialogs),
                "total_dialogs": len(chunked_dialogs),
                "chunker_strategy": memory_config.chunker_strategy,
            }
            await progress_callback("text_preprocessing_complete", "预处理文本完成", preprocessing_summary)

        log_time("Data Loading & Chunking", time.time() - step_start, log_file)

        # 步骤 3: 初始化流水线编排器
        logger.info("Initializing extraction orchestrator...")
        step_start = time.time()

        config = get_pipeline_config(memory_config)
        logger.info(
            f"Pipeline config loaded: enable_llm_dedup_blockwise={config.deduplication.enable_llm_dedup_blockwise}, "
            f"enable_llm_disambiguation={config.deduplication.enable_llm_disambiguation}"
        )

        orchestrator = ExtractionOrchestrator(
            llm_client=llm_client,
            embedder_client=embedder_client,
            connector=neo4j_connector,
            config=config,
            progress_callback=progress_callback,
            embedding_id=str(memory_config.embedding_model_id),
        )

        log_time("Orchestrator Initialization", time.time() - step_start, log_file)

        # 步骤 4: 执行知识提取流水线
        logger.info("Running extraction pipeline...")
        step_start = time.time()

        if progress_callback:
            await progress_callback("knowledge_extraction", "正在知识抽取...")

        extraction_result = await orchestrator.run(
            dialog_data_list=chunked_dialogs,
            is_pilot_run=True,
        )

        # 解包 extraction_result tuple (与 main.py 保持一致)
        (
            dialogue_nodes,
            chunk_nodes,
            statement_nodes,
            entity_nodes,
            statement_chunk_edges,
            statement_entity_edges,
            entity_edges,
        ) = extraction_result

        log_time("Extraction Pipeline", time.time() - step_start, log_file)

        if progress_callback:
            await progress_callback("generating_results", "正在生成结果...")

        # 步骤 5: 生成记忆摘要（与 main.py 保持一致）
        try:
            logger.info("Generating memory summaries...")
            step_start = time.time()

            from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import (
                memory_summary_generation,
            )

            summaries = await memory_summary_generation(
                chunked_dialogs,
                llm_client=llm_client,
                embedder_client=embedder_client,
            )

            log_time("Memory Summary Generation", time.time() - step_start, log_file)
        except Exception as e:
            logger.error(f"Memory summary step failed: {e}", exc_info=True)

        logger.info("Pilot run completed: Skipping Neo4j save")

    except Exception as e:
        logger.error(f"Pilot run failed: {e}", exc_info=True)
        raise
    finally:
        if neo4j_connector:
            try:
                await neo4j_connector.close()
            except Exception:
                pass

    total_time = time.time() - pipeline_start
    log_time("TOTAL PILOT RUN TIME", total_time, log_file)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"=== Pilot Run Completed: {timestamp} ===\n\n")

    logger.info(f"Pilot run complete. Total time: {total_time:.2f}s")
