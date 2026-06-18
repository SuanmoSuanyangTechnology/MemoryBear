from typing import List, Optional

from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage


async def get_chunked_dialogs(
        chunker_strategy: str = "RecursiveChunker",
        end_user_id: str = "group_1",
        messages: list = None,
        ref_id: str = "",
        config_id: str = None,
        workspace_id=None,
        snapshot=None,
        context_before: Optional[List[dict]] = None,
        context_after: Optional[List[dict]] = None,
) -> List[DialogData]:
    """Generate chunks from structured messages using the specified chunker strategy.

    Args:
        chunker_strategy: The chunking strategy to use (default: RecursiveChunker)
        end_user_id: Group identifier
        messages: Structured message list [{"role": "user", "content": "...", "dialog_at": "..."}]
        ref_id: Reference identifier
        config_id: Configuration ID for processing (used to load pruning config)
        snapshot: Optional PipelineSnapshot instance for saving pruning output
        context_before: Optional upstream context messages (already pruned), each dict with "role" and "content".
            Defaults to None (treated as empty list). Used by sliding window write to inject SupportingContext.
        context_after: Optional downstream context messages (already pruned), each dict with "role" and "content".
            Defaults to None (treated as empty list). Used by sliding window write to inject SupportingContext.

    Returns:
        List of DialogData objects with generated chunks. When context_before or context_after is provided,
        dialog_data.metadata["supporting_context"] will contain a dict with two keys
        ``{"before_msgs": List[MessageItem], "after_msgs": List[MessageItem]}``,
        directly mirroring the SupportingContext schema. The target message itself is
        NEVER placed in either list — its position is implied by the field names.
    """
    from app.core.logging_config import get_agent_logger
    logger = get_agent_logger(__name__)

    if not messages or not isinstance(messages, list) or len(messages) == 0:
        raise ValueError("messages parameter must be a non-empty list")

    conversation_messages = []

# step1: 消息格式校验 role：user、assistant。content
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
            raise ValueError(f"Message {idx} format error: must contain 'role' and 'content' fields")

        role = msg['role']
        content = msg['content']
        files = msg.get("file_content", [])

        if role not in ['user', 'assistant']:
            raise ValueError(f"Message {idx} role must be 'user' or 'assistant', got: {role}")

        if content.strip():
            conversation_messages.append(ConversationMessage(
                role=role,
                msg=content.strip(),
                dialog_at=msg.get("dialog_at"),
                files=files,
            ))

    if not conversation_messages:
        raise ValueError("Message list cannot be empty after filtering")

    conversation_context = ConversationContext(msgs=conversation_messages)
    dialog_data = DialogData(
        context=conversation_context,
        ref_id=ref_id,
        end_user_id=end_user_id,
        config_id=config_id,
    )
    
# step2: 语义剪枝步骤（在分块之前）
    try:
        from app.core.memory.storage_services.extraction_engine.data_preprocessing import SemanticPruner
        from app.core.memory.models.config_models import PruningConfig
        from app.db import get_db_context
        from app.services.memory_config_service import MemoryConfigService
        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        
        # 加载剪枝配置（短暂 DB session，查完即关）
        pruning_config = None
        llm_client = None
        if config_id:
            try:
                with get_db_context() as db:
                    config_service = MemoryConfigService(db)
                    memory_config = config_service.load_memory_config(
                        config_id=config_id,
                        workspace_id=workspace_id,
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
                        
                        # 获取 LLM 客户端（仅读取 API key/base_url，不做 LLM 调用）
                        if pruning_config.pruning_switch:
                            factory = MemoryClientFactory(db)
                            llm_client = factory.get_llm_client_from_config(memory_config)
                        else:
                            logger.info("[剪枝] 配置中剪枝开关关闭，跳过剪枝")
                # session 在此关闭，关闭DB连接

                # 执行剪枝（LLM 调用在 session 外，不占用 DB 连接）
                if pruning_config and pruning_config.pruning_switch and llm_client:
                    import re
                    import langid
                    user_text = " ".join(
                        re.sub(r"<input-file-summary>.*?</input-file-summary>", "", m.msg, flags=re.DOTALL).strip()
                        for m in dialog_data.context.msgs if m.role == "user" and m.msg
                    )
                    pruning_language = langid.classify(user_text)[0] if user_text else "zh"
                    if pruning_language not in ("zh", "en"):
                        pruning_language = "en"
                    pruner = SemanticPruner(config=pruning_config, llm_client=llm_client, language=pruning_language, snapshot=snapshot)
                    original_msg_count = len(dialog_data.context.msgs)
                    
                    # 使用 prune_dataset 而不是 prune_dialog
                    # prune_dataset 会进行消息级剪枝，即使对话整体相关也会删除不重要消息
                    pruned_dialogs = await pruner.prune_dataset([dialog_data])
                    
                    if pruned_dialogs:
                        dialog_data = pruned_dialogs[0]
                        remaining_msg_count = len(dialog_data.context.msgs)
                        deleted_count = original_msg_count - remaining_msg_count
                        logger.info(f"[剪枝] 完成: 原始{original_msg_count}条 -> 保留{remaining_msg_count}条 (删除{deleted_count}条)")
                        
                        # 将剪枝记录挂到 metadata，供 graph_build_step 构建节点
                        if pruner.pruning_records:
                            dialog_data.metadata["assistant_pruning_records"] = [
                                r.model_dump() for r in pruner.pruning_records
                            ]
                            logger.info(f"[剪枝] 收集到 {len(pruner.pruning_records)} 条剪枝记录")
                    else:
                        logger.warning("[剪枝] prune_dataset 返回空列表")
            except Exception as e:
                logger.warning(f"[剪枝] 加载配置失败，跳过剪枝: {e}", exc_info=True)
    except Exception as e:
        logger.warning(f"[剪枝] 执行失败，跳过剪枝: {e}", exc_info=True)

# step3： 分块
    chunker = DialogueChunker(chunker_strategy)
    extracted_chunks = await chunker.process_dialogue(dialog_data)
    dialog_data.chunks = extracted_chunks

    logger.info(f"DialogData created with {len(extracted_chunks)} chunks")

# step4: 注入结构化上下文（滑动窗口写入场景）
    if context_before or context_after:
        from app.core.memory.storage_services.extraction_engine.steps.schema.extraction_step_schema import MessageItem
        before_msgs = [
            MessageItem(role=msg["role"], msg=msg["content"])
            for msg in (context_before or [])
        ]
        after_msgs = [
            MessageItem(role=msg["role"], msg=msg["content"])
            for msg in (context_after or [])
        ]
        # 直接用方向化字段，让 target_content 在结构上夹在 before_msgs 与 after_msgs 之间，
        # LLM 通过字段名而非额外提示理解位置关系。
        dialog_data.metadata["supporting_context"] = {
            "before_msgs": before_msgs,
            "after_msgs": after_msgs,
        }
        logger.info(
            f"[SupportingContext] 注入上下文消息: "
            f"before={len(before_msgs)}, after={len(after_msgs)}"
        )

    return [dialog_data]
