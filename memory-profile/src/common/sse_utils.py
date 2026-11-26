"""
Server-Sent Events (SSE) Utility Functions

Provides shared utilities for formatting and handling SSE messages.
"""

import json
from typing import Dict, Any


def format_sse_message(event_type: str, data: Dict[str, Any]) -> str:
    """
    Format a message in Server-Sent Events (SSE) format.
    
    Args:
        event_type: Type of event (stage name, result, error, done)
        data: Event data dictionary to be serialized as JSON
        
    Returns:
        SSE formatted string: "event: <type>\\ndata: <json>\\n\\n"
        
    Example:
        >>> format_sse_message("loading", {"message": "Loading..."})
        'event: loading\\ndata: {"message": "Loading..."}\\n\\n'
    """
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"
