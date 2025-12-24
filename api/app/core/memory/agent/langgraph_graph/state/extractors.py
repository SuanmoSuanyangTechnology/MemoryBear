"""
State extraction utilities for type-safe access to LangGraph state values.

This module provides utility functions for extracting values from LangGraph state
dictionaries with proper error handling and sensible defaults.
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

def extract_search_switch(state: dict) -> Optional[str]:
    """
    Extract search_switch from state or messages.
    """

    search_switch = state.get("search_switch")

    if search_switch is not None:
        return str(search_switch)

    # Try to extract from messages
    messages = state.get("messages", [])
    if not messages:
        return None

    # 从最新的消息开始查找
    for message in reversed(messages):
        # 尝试从 tool_calls 中提取
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tool_call in message.tool_calls:
                if isinstance(tool_call, dict):
                    # 从 tool_call 的 args 中提取
                    if "args" in tool_call and isinstance(tool_call["args"], dict):
                        search_switch = tool_call["args"].get("search_switch")
                        if search_switch is not None:
                            return str(search_switch)
                    # 直接从 tool_call 中提取
                    search_switch = tool_call.get("search_switch")
                    if search_switch is not None:
                        return str(search_switch)

        # 尝试从 content 中提取（如果是 JSON 格式）
        if hasattr(message, "content"):
            try:
                import json
                if isinstance(message.content, str):
                    content_data = json.loads(message.content)
                    if isinstance(content_data, dict):
                        search_switch = content_data.get("search_switch")
                        if search_switch is not None:
                            return str(search_switch)
            except (json.JSONDecodeError, ValueError):
                pass

    return None


def extract_tool_call_id(message: Any) -> str:
    """
    Extract tool call ID from message using structured attributes.
    
    This function extracts the tool call ID from a message object, handling both
    direct attribute access and tool_calls list structures.
    
    Args:
        message: Message object (typically ToolMessage or AIMessage)
        
    Returns:
        Tool call ID as string
        
    Raises:
        ValueError: If tool call ID cannot be extracted
        
    Examples:
        >>> message = ToolMessage(content="...", tool_call_id="call_123")
        >>> extract_tool_call_id(message)
        'call_123'
    """
    # Try direct attribute access for ToolMessage
    if hasattr(message, "tool_call_id"):
        tool_call_id = message.tool_call_id
        if tool_call_id:
            return str(tool_call_id)
    
    # Try extracting from tool_calls list for AIMessage
    if hasattr(message, "tool_calls") and message.tool_calls:
        tool_call = message.tool_calls[0]
        if isinstance(tool_call, dict) and "id" in tool_call:
            return str(tool_call["id"])
    
    # Try extracting from id attribute
    if hasattr(message, "id"):
        message_id = message.id
        if message_id:
            return str(message_id)
    
    # If all else fails, raise an error
    raise ValueError(f"Could not extract tool call ID from message: {type(message)}")


def extract_content_payload(message: Any) -> Any:
    """
    Extract content payload from ToolMessage, parsing JSON if needed.
    
    This function extracts the content from a message and attempts to parse it as JSON
    if it appears to be a JSON string. It handles various message formats and provides
    sensible fallbacks.
    
    Args:
        message: Message object (typically ToolMessage)
        
    Returns:
        Parsed content (dict, list, or str)
        
    Examples:
        >>> message = ToolMessage(content='{"key": "value"}')
        >>> extract_content_payload(message)
        {'key': 'value'}
        
        >>> message = ToolMessage(content='plain text')
        >>> extract_content_payload(message)
        'plain text'
    """
    # Extract raw content
    # For ToolMessages (responses from tools), extract from content
    if hasattr(message, "content"):
        raw_content = message.content
        logger.info(f"extract_content_payload: raw_content type={type(raw_content)}, value={str(raw_content)[:500]}")
        
        # Handle MCP content format: [{'type': 'text', 'text': '...'}]
        if isinstance(raw_content, list):
            for block in raw_content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    raw_content = block.get('text', '')
                    logger.info(f"extract_content_payload: extracted text from MCP format: {str(raw_content)[:300]}")
                    break
        
        # If content is empty and this is an AIMessage with tool_calls,
        # extract from args (this handles the initial tool call from content_input)
        if not raw_content and hasattr(message, "tool_calls") and message.tool_calls:
            tool_call = message.tool_calls[0]
            if isinstance(tool_call, dict) and "args" in tool_call:
                return tool_call["args"]
    else:
        raw_content = str(message)
    
    # If content is already a dict or list, return it directly
    if isinstance(raw_content, (dict, list)):
        logger.info(f"extract_content_payload: returning raw dict/list with keys={list(raw_content.keys()) if isinstance(raw_content, dict) else 'list'}")
        return raw_content
    
    # Try to parse as JSON
    if isinstance(raw_content, str):
        # First, try direct JSON parsing
        try:
            parsed = json.loads(raw_content)
            logger.info(f"extract_content_payload: parsed JSON, keys={list(parsed.keys()) if isinstance(parsed, dict) else 'list'}")
            return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        
        # If that fails, try to extract JSON from the string
        # This handles cases where the content is embedded in a larger string
        import re
        json_candidates = re.findall(r'[\[{].*[\]}]', raw_content, flags=re.DOTALL)
        for candidate in json_candidates:
            try:
                parsed = json.loads(candidate)
                logger.info(f"extract_content_payload: parsed JSON from candidate, keys={list(parsed.keys()) if isinstance(parsed, dict) else 'list'}")
                return parsed
            except (json.JSONDecodeError, ValueError):
                continue
    
    # If all parsing attempts fail, return the raw content
    logger.info(f"extract_content_payload: returning raw content (parsing failed)")
    return raw_content
