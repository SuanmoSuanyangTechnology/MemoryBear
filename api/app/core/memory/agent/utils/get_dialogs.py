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
        group_id: Group identifier
        messages: Structured message list [{"role": "user", "content": "..."}, ...]
        ref_id: Reference identifier
        config_id: Configuration ID for processing

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

    chunker = DialogueChunker(chunker_strategy)
    extracted_chunks = await chunker.process_dialogue(dialog_data)
    dialog_data.chunks = extracted_chunks

    logger.info(f"DialogData created with {len(extracted_chunks)} chunks")

    return [dialog_data]
