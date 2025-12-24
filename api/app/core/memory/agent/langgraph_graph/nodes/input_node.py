"""
Input node for LangGraph workflow entry point.

This module provides the create_input_message function which processes initial
user input with multimodal support and creates the first tool call message.
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict

from app.core.memory.agent.utils.multimodal import MultimodalProcessor
from app.schemas.memory_config_schema import MemoryConfig
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


async def create_input_message(
    state: Dict[str, Any],
    tool_name: str,
    session_id: str,
    search_switch: str,
    apply_id: str,
    group_id: str,
    multimodal_processor: MultimodalProcessor,
    memory_config: MemoryConfig,
) -> Dict[str, Any]:
    """
    Create initial tool call message from user input.
    
    This function:
    1. Extracts the last message content from state
    2. Processes multimodal inputs (images/audio) using the multimodal processor
    3. Generates a unique message ID
    4. Extracts namespace from session_id
    5. Handles verified_data extraction for backward compatibility
    6. Returns AIMessage with complete tool_calls structure
    
    Args:
        state: LangGraph state dictionary containing messages
        tool_name: Name of the tool to invoke (typically "Split_The_Problem")
        session_id: Session identifier (format: "call_id_{namespace}")
        search_switch: Search routing parameter
        apply_id: Application identifier
        group_id: Group identifier
        multimodal_processor: Processor for handling image/audio inputs
        memory_config: MemoryConfig object containing all configuration
        
    Returns:
        State update with AIMessage containing tool_call
        
    Examples:
        >>> state = {"messages": [HumanMessage(content="What is AI?")]}
        >>> result = await create_input_message(
        ...     state, "Split_The_Problem", "call_id_user123", "0", "app1", "group1", processor, config
        ... )
        >>> result["messages"][0].tool_calls[0]["name"]
        'Split_The_Problem'
    """
    messages = state.get("messages", [])
    
    # Extract last message content
    if messages:
        last_message = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])
    else:
        logger.warning("[create_input_message] No messages in state, using empty string")
        last_message = ""
    
    logger.debug(f"[create_input_message] Original input: {last_message[:100]}...")
    
    # Process multimodal input (images/audio)
    try:
        processed_content = await multimodal_processor.process_input(last_message)
        if processed_content != last_message:
            logger.info(
                f"[create_input_message] Multimodal processing converted input "
                f"from {len(last_message)} to {len(processed_content)} chars"
            )
        last_message = processed_content
    except Exception as e:
        logger.error(
            f"[create_input_message] Multimodal processing failed: {e}",
            exc_info=True
        )
        # Continue with original content
    
    # Generate unique message ID
    uuid_str = uuid.uuid4()
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Extract namespace from session_id
    # Expected format: "call_id_{namespace}" or similar
    try:
        namespace = str(session_id).split('_id_')[1]
    except (IndexError, AttributeError):
        logger.warning(
            f"[create_input_message] Could not extract namespace from session_id: {session_id}"
        )
        namespace = "unknown"
    
    # Handle verified_data extraction (backward compatibility)
    # This regex-based extraction is kept for compatibility with existing data formats
    if 'verified_data' in str(last_message):
        try:
            messages_last = str(last_message).replace('\\n', '').replace('\\', '')
            query_match = re.findall(r'"query": "(.*?)",', messages_last)
            if query_match:
                last_message = query_match[0]
                logger.debug(
                    f"[create_input_message] Extracted query from verified_data: {last_message}"
                )
        except Exception as e:
            logger.warning(
                f"[create_input_message] Failed to extract query from verified_data: {e}"
            )
    
    # Construct tool call message
    tool_call_id = f"{session_id}_{uuid_str}"
    
    logger.info(
        f"[create_input_message] Creating tool call for '{tool_name}' "
        f"with ID: {tool_call_id}"
    )
    
    # Build tool arguments
    tool_args = {
        "sentence": last_message,
        "sessionid": session_id,
        "messages_id": str(uuid_str),
        "search_switch": search_switch,
        "apply_id": apply_id,
        "group_id": group_id,
        "memory_config": memory_config,
    }
    
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": tool_name,
                    "args": tool_args,
                    "id": tool_call_id
                }]
            )
        ]
    }
