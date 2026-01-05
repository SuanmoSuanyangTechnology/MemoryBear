import os
import json
from typing import List, Dict
from datetime import datetime

from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage


async def get_chunked_dialogs(
        chunker_strategy: str = "RecursiveChunker",
        group_id: str = "group_1",
        user_id: str = "user1",
        apply_id: str = "applyid",
        messages_list: List[Dict[str, str]] = None,
        ref_id: str = "wyl_20251027",
        config_id: str = None
) -> List[DialogData]:
    """Generate chunks from message string with role markers.

    Args:
        chunker_strategy: The chunking strategy to use (default: RecursiveChunker)
        group_id: Group identifier
        user_id: User identifier
        apply_id: Application identifier
        messages_list: List of messages with role info [{"role": "user", "content": "..."}, ...]
        ref_id: Reference identifier
        config_id: Configuration ID for processing

    Returns:
        List of DialogData objects with generated chunks
        
    Note:
        - 解析格式: "user: 内容\\nassistant: 内容"
        - role 映射: "user" -> "用户", "assistant" -> "AI助手", "system" -> "系统"
    """
    
    dialog_data_list = []
    messages = []

    # 角色映射字典
    role_mapping = {
        "user": "用户",
        "assistant": "AI助手",
        "system": "系统"
    }

    # 解析 content 字符串，提取角色和内容
    # 支持格式: "user: 内容\nassistant: 内容" 或 "user：内容\nassistant：内容"
    import re
    
    # 匹配模式: 角色标记(user/assistant/system) + 冒号 + 内容
    pattern = r'(user|assistant|system)\s*[:：]\s*([^\n]*(?:\n(?!user|assistant|system\s*[:：])[^\n]*)*)'
    matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
    
    if not matches:
        # 如果没有匹配到角色标记，将整个 content 作为用户消息
        messages.append(ConversationMessage(role="用户", msg=content))
    else:
        for role, content_text in matches:
            role_lower = role.lower()
            # 映射角色名称
            mapped_role = role_mapping.get(role_lower, "用户")
            content_clean = content_text.strip()
            if content_clean:  # 只添加非空内容
                messages.append(ConversationMessage(role=mapped_role, msg=content_clean))

    # Create DialogData
    conversation_context = ConversationContext(msgs=messages)
    dialog_data = DialogData(
        context=conversation_context,
        ref_id=ref_id,
        group_id=group_id,
        user_id=user_id,
        apply_id=apply_id,
        config_id=config_id
    )
    # Create DialogueChunker and process the dialogue
    chunker = DialogueChunker(chunker_strategy)
    extracted_chunks = await chunker.process_dialogue(dialog_data)
    dialog_data.chunks = extracted_chunks

    dialog_data_list.append(dialog_data)

    print(dialog_data_list)

    return dialog_data_list
