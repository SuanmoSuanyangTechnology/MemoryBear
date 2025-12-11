import os
import json
from typing import List
from datetime import datetime

from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage


async def get_chunked_dialogs(
        chunker_strategy: str = "RecursiveChunker",
        group_id: str = "group_1",
        user_id: str = "user1",
        apply_id: str = "applyid",
        content: str = "这是用户的输入",
        ref_id: str = "wyl_20251027",
        config_id: str = None
) -> List[DialogData]:
    """Generate chunks from all test data entries using the specified chunker strategy.

    Args:
        chunker_strategy: The chunking strategy to use (default: RecursiveChunker)
        group_id: Group identifier
        user_id: User identifier
        apply_id: Application identifier
        content: Dialog content
        ref_id: Reference identifier
        config_id: Configuration ID for processing

    Returns:
        List of DialogData objects with generated chunks for each test entry
    """
    dialog_data_list = []
    messages = []

    messages.append(ConversationMessage(role="用户", msg=content))

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
