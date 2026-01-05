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
    """Generate chunks from all test data entries using the specified chunker strategy.

    Args:
        chunker_strategy: The chunking strategy to use (default: RecursiveChunker)
        group_id: Group identifier
        user_id: User identifier
        apply_id: Application identifier
        messages_list: List of messages with role info [{"role": "user", "content": "..."}, ...]
        ref_id: Reference identifier
        config_id: Configuration ID for processing

    Returns:
        List of DialogData objects with generated chunks for each test entry
        
    Note:
        - role 映射: "user" -> "用户", "assistant" -> "AI助手"
    """
    if not messages_list:
        raise ValueError("必须提供 messages_list 参数")
    
    dialog_data_list = []
    messages = []

    # 角色映射字典
    role_mapping = {
        "user": "用户",
        "assistant": "AI助手",
        "system": "系统"
    }

    for msg in messages_list:
        role = msg.get("role", "user")
        content_text = msg.get("content", "")
        # 映射角色名称
        mapped_role = role_mapping.get(role, role)
        messages.append(ConversationMessage(role=mapped_role, msg=content_text))

    # Create DialogData
    conversation_context = ConversationContext(msgs=messages)
    # Create DialogData with group_id based on the entry's id for uniqueness
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

    # Convert to dict with datetime serialized
    def serialize_datetime(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

    combined_output = [dd.model_dump() for dd in dialog_data_list]

    print(dialog_data_list)

    # with open(os.path.join(os.path.dirname(__file__), "chunker_test_output.txt"), "w", encoding="utf-8") as f:
    #     json.dump(combined_output, f, ensure_ascii=False, indent=4, default=serialize_datetime)


    return dialog_data_list
