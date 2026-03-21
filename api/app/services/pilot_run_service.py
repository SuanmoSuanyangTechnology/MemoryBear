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
    language: str = "zh",
) -> None:
    """
    执行试运行模式的知识提取流水线。

    Args:
        memory_config: 从数据库加载的内存配置对象
        dialogue_text: 输入的对话文本
        db: 数据库会话
        progress_callback: 可选的进度回调函数
            - 参数1 (stage): 当前处理阶段标识符
            - 参数2 (message): 人类可读的进度消息
            - 参数3 (data): 可选的附加数据字典
        language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
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
            end_user_id=str(memory_config.workspace_id),
            user_id=str(memory_config.tenant_id),
            apply_id=str(memory_config.config_id),
            metadata={"source": "pilot_run", "input_type": "frontend_text"},
        )

        if progress_callback:
            await progress_callback("text_preprocessing", "开始预处理文本（语义剪枝 + 语义分块）...")

        # ========== 步骤 2.1: 语义剪枝 ==========
        pruned_dialogs = [dialog]
        deleted_messages = []  # 记录被删除的消息
        pruning_stats = None  # 保存剪枝统计信息，用于最终汇总
        
        if memory_config.pruning_enabled:
            try:
                from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_pruning import (
                    SemanticPruner,
                )
                from app.core.memory.models.config_models import PruningConfig
                
                # 构建剪枝配置
                pruning_config_dict = {
                    "pruning_switch": memory_config.pruning_enabled,
                    "pruning_scene": memory_config.pruning_scene,
                    "pruning_threshold": memory_config.pruning_threshold,
                    "scene_id": str(memory_config.scene_id) if memory_config.scene_id else None,
                    "ontology_classes": memory_config.ontology_classes,
                }
                config = PruningConfig(**pruning_config_dict)
                
                logger.info(f"[PILOT_RUN] 开始语义剪枝: scene={config.pruning_scene}, threshold={config.pruning_threshold}")
                
                # 记录剪枝前的消息（用于对比）
                original_messages = [{"role": msg.role, "content": msg.msg} for msg in dialog.context.msgs]
                original_msg_count = len(original_messages)
                
                # 执行剪枝
                pruner = SemanticPruner(config=config, llm_client=llm_client)
                pruned_dialogs = await pruner.prune_dataset([dialog])
                
                # 计算剪枝结果并找出被删除的消息
                if pruned_dialogs and pruned_dialogs[0].context:
                    remaining_messages = [{"role": msg.role, "content": msg.msg} for msg in pruned_dialogs[0].context.msgs]
                    remaining_msg_count = len(remaining_messages)
                    deleted_msg_count = original_msg_count - remaining_msg_count
                    
                    # 找出被删除的消息（基于索引精确匹配）
                    # 为剩余消息创建带索引的列表，用于精确追踪
                    remaining_with_index = []
                    remaining_idx = 0
                    for orig_idx, orig_msg in enumerate(original_messages):
                        if remaining_idx < len(remaining_messages) and \
                           orig_msg["role"] == remaining_messages[remaining_idx]["role"] and \
                           orig_msg["content"] == remaining_messages[remaining_idx]["content"]:
                            remaining_with_index.append(orig_idx)
                            remaining_idx += 1
                    
                    # 找出未在保留列表中的消息索引
                    deleted_messages = [
                        {"index": idx, "role": msg["role"], "content": msg["content"]}
                        for idx, msg in enumerate(original_messages)
                        if idx not in remaining_with_index
                    ]
                    
                    # 保存剪枝统计信息（用于最终汇总，只保留deleted_count）
                    pruning_stats = {
                        "enabled": True,
                        "scene": config.pruning_scene,
                        "threshold": config.pruning_threshold,
                        "deleted_count": deleted_msg_count,
                    }
                    
                    # 输出剪枝结果（显示删除的消息详情）
                    pruning_result = {
                        "type": "pruning",
                        "deleted_messages": deleted_messages,
                    }
                    
                    logger.info(
                        f"[PILOT_RUN] 语义剪枝完成: 原始{original_msg_count}条 -> "
                        f"保留{remaining_msg_count}条 (删除{deleted_msg_count}条)"
                    )
                    
                    if progress_callback:
                        await progress_callback("text_preprocessing_result", "语义剪枝完成", pruning_result)
                else:
                    logger.warning("[PILOT_RUN] 剪枝后对话为空，使用原始对话")
                    pruned_dialogs = [dialog]
                    
            except Exception as e:
                logger.error(f"[PILOT_RUN] 语义剪枝失败，使用原始对话: {e}", exc_info=True)
                pruned_dialogs = [dialog]
                if progress_callback:
                    error_result = {
                        "type": "pruning",
                        "error": str(e),
                        "fallback": "使用原始对话"
                    }
                    await progress_callback("text_preprocessing_result", "语义剪枝失败", error_result)
        else:
            logger.info("[PILOT_RUN] 语义剪枝已关闭，跳过")
            pruning_stats = {
                "enabled": False,
            }

        # ========== 步骤 2.2: 语义分块 ==========
        chunked_dialogs = await get_chunked_dialogs_from_preprocessed(
            data=pruned_dialogs,
            chunker_strategy=memory_config.chunker_strategy,
            llm_client=llm_client,
        )
        
        remaining_msg_count = len(pruned_dialogs[0].context.msgs) if pruned_dialogs and pruned_dialogs[0].context else 0
        logger.info(f"Processed dialogue text: {remaining_msg_count} messages after pruning")

        # 进度回调：输出每个分块的结果
        if progress_callback:
            for dlg in chunked_dialogs:
                if hasattr(dlg, 'chunks') and dlg.chunks:
                    for i, chunk in enumerate(dlg.chunks):
                        chunk_result = {
                            "type": "chunking",
                            "chunk_index": i + 1,
                            "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                            "full_length": len(chunk.content),
                            "dialog_id": dlg.id,
                            "chunker_strategy": memory_config.chunker_strategy,
                        }
                        await progress_callback("text_preprocessing_result", f"分块 {i + 1} 处理完成", chunk_result)

            # 构建预处理完成总结（包含剪枝统计）
            preprocessing_summary = {
                "total_chunks": sum(len(dlg.chunks) for dlg in chunked_dialogs if hasattr(dlg, 'chunks') and dlg.chunks),
                "total_dialogs": len(chunked_dialogs),
                "chunker_strategy": memory_config.chunker_strategy,
            }
            
            # 添加剪枝统计信息
            if pruning_stats:
                preprocessing_summary["pruning"] = pruning_stats
            
            await progress_callback("text_preprocessing_complete", "预处理文本完成（剪枝 + 分块）", preprocessing_summary)

        log_time("Data Loading & Chunking", time.time() - step_start, log_file)

        # 步骤 3: 初始化流水线编排器
        logger.info("Initializing extraction orchestrator...")
        step_start = time.time()

        config = get_pipeline_config(memory_config)
        logger.info(
            f"Pipeline config loaded: enable_llm_dedup_blockwise={config.deduplication.enable_llm_dedup_blockwise}, "
            f"enable_llm_disambiguation={config.deduplication.enable_llm_disambiguation}"
        )

        # 加载本体类型（如果配置了 scene_id），支持通用类型回退
        ontology_types = None
        try:
            from app.core.memory.ontology_services.ontology_type_loader import load_ontology_types_with_fallback
            
            ontology_types = load_ontology_types_with_fallback(
                scene_id=memory_config.scene_id,
                workspace_id=memory_config.workspace_id,
                db=db,
                enable_general_fallback=True
            )
        except Exception as e:
            logger.warning(f"Failed to load ontology types: {e}", exc_info=True)

        orchestrator = ExtractionOrchestrator(
            llm_client=llm_client,
            embedder_client=embedder_client,
            connector=neo4j_connector,
            config=config,
            progress_callback=progress_callback,
            embedding_id=str(memory_config.embedding_model_id),
            language=language,
            ontology_types=ontology_types,
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
                language=language,
            )

            log_time("Memory Summary Generation", time.time() - step_start, log_file)
        except Exception as e:
            logger.error(f"Memory summary step failed: {e}", exc_info=True)

        logger.info("Pilot run completed: Skipping Neo4j save")

        # 将提取统计写入 Redis，按 workspace_id 存储
        try:
            from app.cache.memory.activity_stats_cache import ActivityStatsCache

            stats_to_cache = {
                "chunk_count": len(chunk_nodes) if chunk_nodes else 0,
                "statements_count": len(statement_nodes) if statement_nodes else 0,
                "triplet_entities_count": len(entity_nodes) if entity_nodes else 0,
                "triplet_relations_count": len(entity_edges) if entity_edges else 0,
                "temporal_count": 0,  # temporal 数据在日志中，此处暂置0
            }
            await ActivityStatsCache.set_activity_stats(
                workspace_id=str(memory_config.workspace_id),
                stats=stats_to_cache,
            )
            logger.info(f"[PILOT_RUN] 活动统计已写入 Redis: workspace_id={memory_config.workspace_id}")
        except Exception as cache_err:
            logger.warning(f"[PILOT_RUN] 写入活动统计缓存失败（不影响主流程）: {cache_err}", exc_info=True)

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
