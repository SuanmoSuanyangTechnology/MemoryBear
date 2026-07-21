from typing import List, Optional

from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage
from app.core.memory.utils.dialogue_id_utils import build_dialogue_id


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
        conversation_id: str = "",
        message_seq: int = 0,
        source: str = "",
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

        # 允许空 content 的消息进入列表
        # 空 content 用空字符串表示
        conversation_messages.append(ConversationMessage(
            role=role,
            msg=content.strip() if content.strip() else "",
            dialog_at=msg.get("dialog_at"),
            files=files,
        ))

    if not conversation_messages:
        raise ValueError("Message list cannot be empty after filtering")

    conversation_context = ConversationContext(msgs=conversation_messages)
    dialog_kwargs = dict(
        context=conversation_context,
        ref_id=ref_id,
        end_user_id=end_user_id,
        config_id=config_id,
    )
    # 有分组信息（正写路径）时，出生即赋与快写统一的确定性 id（Dialog_{...}），使 DialogueNode.id
    # 及所有引用 dialog_data.id 的子节点（Chunk/Assistant*/MemorySummary 的 dialog_id）天然一致，
    # 由正写覆盖升级快写占位节点。缺少分组信息（如试运行）时省略 id，回退模型默认的随机 uuid。
    if conversation_id or source:
        dialog_kwargs["id"] = build_dialogue_id(conversation_id, message_seq, source, end_user_id)
    dialog_data = DialogData(**dialog_kwargs)

# step3： 分块
    chunker = DialogueChunker(chunker_strategy)
    extracted_chunks = await chunker.process_dialogue(dialog_data)
    dialog_data.chunks = extracted_chunks

    logger.info(f"DialogData created with {len(extracted_chunks)} chunks")

# step4: 注入结构化上下文（滑动窗口写入场景）
    # 当 context_before/after 均为空时也显式注入空结构，避免萃取阶段走 fallback
    # 将 dialog.content 错误地当成上文重复放入 before_msgs。
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
    else:
        # 无上下文（MCP 单条写入路径）：注入空结构，防止萃取阶段走 fallback
        from app.core.memory.storage_services.extraction_engine.steps.schema.extraction_step_schema import MessageItem
        dialog_data.metadata["supporting_context"] = {
            "before_msgs": [],
            "after_msgs": [],
        }
        logger.info("[SupportingContext] 无上下文，注入空结构")

    return [dialog_data]
