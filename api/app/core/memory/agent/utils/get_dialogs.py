import os
import json
from typing import List
from datetime import datetime

from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage


async def get_chunked_dialogs(
        chunker_strategy: str = "RecursiveChunker",
        end_user_id: str = "group_1",
        messages: list = None,
        ref_id: str = "wyl_20251027",
        config_id: str = None
) -> List[DialogData]:
    """Generate chunks from structured messages using the specified chunker strategy.

    Args:
        chunker_strategy: The chunking strategy to use (default: RecursiveChunker)
        end_user_id: Group identifier
        messages: Structured message list [{"role": "user", "content": "..."}, ...]
        ref_id: Reference identifier
        config_id: Configuration ID for processing (used to load pruning config)

    Returns:
        List of DialogData objects with generated chunks
    """
    from app.core.logging_config import get_agent_logger
    logger = get_agent_logger(__name__)

    if not messages or not isinstance(messages, list) or len(messages) == 0:
        raise ValueError("messages parameter must be a non-empty list")

    conversation_messages = []

    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
            raise ValueError(f"Message {idx} format error: must contain 'role' and 'content' fields")

        role = msg['role']
        content = msg['content']

        if role not in ['user', 'assistant']:
            raise ValueError(f"Message {idx} role must be 'user' or 'assistant', got: {role}")

        if content.strip():
            conversation_messages.append(ConversationMessage(role=role, msg=content.strip()))

    if not conversation_messages:
        raise ValueError("Message list cannot be empty after filtering")

    conversation_context = ConversationContext(msgs=conversation_messages)
    dialog_data = DialogData(
        context=conversation_context,
        ref_id=ref_id,
        end_user_id=end_user_id,
        config_id=config_id
    )
    
    # 语义剪枝步骤（在分块之前）
    try:
        from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_pruning import SemanticPruner
        from app.core.memory.models.config_models import PruningConfig
        from app.db import get_db_context
        from app.services.memory_config_service import MemoryConfigService
        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        
        # 加载剪枝配置
        pruning_config = None
        if config_id:
            try:
                with get_db_context() as db:
                    # 使用 MemoryConfigService 加载完整的 MemoryConfig 对象
                    config_service = MemoryConfigService(db)
                    memory_config = config_service.load_memory_config(
                        config_id=config_id,
                        service_name="semantic_pruning"
                    )
                    
                    if memory_config:
                        pruning_config = PruningConfig(
                            pruning_switch=memory_config.pruning_enabled,
                            pruning_scene=memory_config.pruning_scene or "education",
                            pruning_threshold=memory_config.pruning_threshold,
                            scene_id=str(memory_config.scene_id) if memory_config.scene_id else None,
                            ontology_class_infos=memory_config.ontology_class_infos,
                        )
                        logger.info(f"[剪枝] 加载配置: switch={pruning_config.pruning_switch}, scene={pruning_config.pruning_scene}, threshold={pruning_config.pruning_threshold}")
                        
                        # 获取LLM客户端用于剪枝
                        if pruning_config.pruning_switch:
                            factory = MemoryClientFactory(db)
                            llm_client = factory.get_llm_client_from_config(memory_config)
                            
                            # 执行剪枝 - 使用 prune_dataset 支持消息级剪枝
                            pruner = SemanticPruner(config=pruning_config, llm_client=llm_client)
                            original_msg_count = len(dialog_data.context.msgs)
                            
                            # 使用 prune_dataset 而不是 prune_dialog
                            # prune_dataset 会进行消息级剪枝，即使对话整体相关也会删除不重要消息
                            pruned_dialogs = await pruner.prune_dataset([dialog_data])
                            
                            if pruned_dialogs:
                                dialog_data = pruned_dialogs[0]
                                remaining_msg_count = len(dialog_data.context.msgs)
                                deleted_count = original_msg_count - remaining_msg_count
                                logger.info(f"[剪枝] 完成: 原始{original_msg_count}条 -> 保留{remaining_msg_count}条 (删除{deleted_count}条)")
                            else:
                                logger.warning("[剪枝] prune_dataset 返回空列表")
                        else:
                            logger.info("[剪枝] 配置中剪枝开关关闭，跳过剪枝")
            except Exception as e:
                logger.warning(f"[剪枝] 加载配置失败，跳过剪枝: {e}", exc_info=True)
    except Exception as e:
        logger.warning(f"[剪枝] 执行失败，跳过剪枝: {e}", exc_info=True)

    chunker = DialogueChunker(chunker_strategy)
    extracted_chunks = await chunker.process_dialogue(dialog_data)
    dialog_data.chunks = extracted_chunks

    logger.info(f"DialogData created with {len(extracted_chunks)} chunks")

    return [dialog_data]
