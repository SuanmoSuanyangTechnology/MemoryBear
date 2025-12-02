"""
Parameter Builder for constructing tool call arguments.

This service provides tool-specific parameter transformation logic
to build correct arguments for each tool type.
"""
import json
from typing import Any, Dict, Optional

from app.core.logging_config import get_agent_logger


logger = get_agent_logger(__name__)


class ParameterBuilder:
    """Service for building tool call arguments based on tool type."""
    
    def __init__(self):
        """Initialize the parameter builder."""
        logger.info("ParameterBuilder initialized")
    
    def build_tool_args(
        self,
        tool_name: str,
        content: Any,
        tool_call_id: str,
        search_switch: str,
        apply_id: str,
        group_id: str,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build tool arguments based on tool type.
        
        Different tools expect different argument formats:
        - Verify: dict context
        - Retrieve: dict context + search_switch
        - Summary/Summary_fails: JSON string context
        - Retrieve_Summary: unwrap nested context structures
        - Input_Summary: raw message string
        
        Args:
            tool_name: Name of the tool being invoked
            content: Parsed content from previous tool result
            tool_call_id: Extracted tool call identifier
            search_switch: Search routing parameter
            apply_id: Application identifier
            group_id: Group identifier
            storage_type: Storage type for the workspace (optional)
            user_rag_memory_id: User RAG memory ID for knowledge base retrieval (optional)
            
        Returns:
            Dictionary of tool arguments ready for invocation
        """
        # Base arguments common to most tools
        base_args = {
            "usermessages": tool_call_id,
            "apply_id": apply_id,
            "group_id": group_id
        }
        
        # Always add storage_type and user_rag_memory_id (with defaults if None)
        base_args["storage_type"] = storage_type if storage_type is not None else ""
        base_args["user_rag_memory_id"] = user_rag_memory_id if user_rag_memory_id is not None else ""
        
        # Tool-specific argument construction
        if tool_name == "Verify":
            # Verify expects dict context
            return {
                "context": content if isinstance(content, dict) else {},
                **base_args
            }
        
        elif tool_name == "Retrieve":
            # Retrieve expects dict context + search_switch
            return {
                "context": content if isinstance(content, dict) else {},
                "search_switch": search_switch,
                **base_args
            }
        
        elif tool_name in ["Summary", "Summary_fails"]:
            # Summary tools expect JSON string context
            if isinstance(content, dict):
                context_str = json.dumps(content, ensure_ascii=False)
            elif isinstance(content, str):
                context_str = content
            else:
                context_str = json.dumps({"data": content}, ensure_ascii=False)
            
            return {
                "context": context_str,
                **base_args
            }
        
        elif tool_name == "Retrieve_Summary":
            # Retrieve_Summary needs to unwrap nested context structures
            # Handle both 'content' and 'context' keys
            context_dict = content
            
            if isinstance(content, dict):
                # Check for nested 'content' wrapper
                if "content" in content:
                    inner = content["content"]
                    
                    # If it's a JSON string, parse it
                    if isinstance(inner, str):
                        try:
                            parsed = json.loads(inner)
                            # Check if parsed has 'context' wrapper
                            if isinstance(parsed, dict) and "context" in parsed:
                                context_dict = parsed["context"]
                            else:
                                context_dict = parsed
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Failed to parse JSON content for {tool_name}: {inner[:100]}"
                            )
                            context_dict = {"Query": "", "Expansion_issue": []}
                    elif isinstance(inner, dict):
                        context_dict = inner
                
                # Check for 'context' wrapper
                elif "context" in content:
                    context_dict = content["context"] if isinstance(content["context"], dict) else content
            
            return {
                "context": context_dict,
                **base_args
            }
        
        elif tool_name == "Input_Summary":
            # Input_Summary expects raw message string + search_switch
            # Content should be the raw message string
            if isinstance(content, dict):
                # Try to extract message from dict
                message_str = content.get("sentence", str(content))
            else:
                message_str = str(content)
            
            return {
                "context": message_str,
                "search_switch": search_switch,
                **base_args
            }
        
        else:
            # Default: pass content as context
            logger.warning(
                f"Unknown tool name '{tool_name}', using default argument structure"
            )
            return {
                "context": content,
                **base_args
            }
