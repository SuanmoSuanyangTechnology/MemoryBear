"""
Tool execution node for LangGraph workflow.

This module provides the ToolExecutionNode class which wraps tool execution
with parameter transformation logic using the ParameterBuilder service.
"""

import logging
import time
from typing import Any, Callable, Dict

from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode

from app.core.memory.agent.langgraph_graph.state.extractors import (
    extract_tool_call_id,
    extract_content_payload
)
from app.core.memory.agent.mcp_server.services.parameter_builder import ParameterBuilder

logger = logging.getLogger(__name__)


class ToolExecutionNode:
    """
    Custom LangGraph node that wraps tool execution with parameter transformation.
    
    This node extracts content from previous tool results, transforms parameters
    based on tool type using ParameterBuilder, and invokes the tool with the
    correct argument structure.
    
    Attributes:
        tool_node: LangGraph ToolNode wrapping the actual tool
        id: Node identifier for message IDs
        tool_name: Name of the tool being executed
        namespace: Namespace for session management
        search_switch: Search routing parameter
        apply_id: Application identifier
        group_id: Group identifier
        parameter_builder: Service for building tool-specific arguments
    """
    
    def __init__(
        self,
        tool: Callable,
        node_id: str,
        namespace: str,
        search_switch: str,
        apply_id: str,
        group_id: str,
        parameter_builder: ParameterBuilder,
        storage_type:str,
        user_rag_memory_id:str
    ):
        """
        Initialize the tool execution node.
        
        Args:
            tool: The tool function to execute
            node_id: Identifier for this node (used in message IDs)
            namespace: Namespace for session management
            search_switch: Search routing parameter
            apply_id: Application identifier
            group_id: Group identifier
            parameter_builder: Service for building tool-specific arguments
        """
        self.tool_node = ToolNode([tool])
        self.id = node_id
        self.tool_name = tool.name if hasattr(tool, 'name') else str(tool)
        self.namespace = namespace
        self.search_switch = search_switch
        self.apply_id = apply_id
        self.group_id = group_id
        self.parameter_builder = parameter_builder
        self.storage_type=storage_type
        self.user_rag_memory_id=user_rag_memory_id
        
        logger.info(
            f"[ToolExecutionNode] Initialized node '{self.id}' for tool '{self.tool_name}'"
        )
    
    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the tool with transformed parameters.
        
        This method:
        1. Extracts the last message from state
        2. Extracts tool call ID using state extractors
        3. Extracts content payload using state extractors
        4. Builds tool arguments using parameter builder
        5. Constructs AIMessage with tool_calls
        6. Invokes the tool and returns the result
        
        Args:
            state: LangGraph state dictionary
            
        Returns:
            Updated state with tool result in messages
        """
        messages = state.get("messages", [])
        logger.debug( self.tool_name)
        
        if not messages:
            logger.warning(f"[ToolExecutionNode] {self.id} - No messages in state")
            return {"messages": [AIMessage(content="Error: No messages in state")]}
        
        last_message = messages[-1]
        logger.debug(
            f"[ToolExecutionNode] {self.id} - Processing message at {time.time()}"
        )
        
        try:
            # Extract tool call ID using state extractors
            tool_call_id = extract_tool_call_id(last_message)
            logger.debug(f"[ToolExecutionNode] {self.id} - Extracted tool_call_id: {tool_call_id}")
            
        except ValueError as e:
            logger.error(
                f"[ToolExecutionNode] {self.id} - Failed to extract tool call ID: {e}"
            )
            return {"messages": [AIMessage(content=f"Error: {str(e)}")]}
        
        try:
            # Extract content payload using state extractors
            content = extract_content_payload(last_message)
            logger.debug(
                f"[ToolExecutionNode] {self.id} - Extracted content type: {type(content)}"
            )
            
        except Exception as e:
            logger.error(
                f"[ToolExecutionNode] {self.id} - Failed to extract content: {e}",
                exc_info=True
            )
            content = {}
        
        try:
            # Build tool arguments using parameter builder
            tool_args = self.parameter_builder.build_tool_args(
                tool_name=self.tool_name,
                content=content,
                tool_call_id=tool_call_id,
                search_switch=self.search_switch,
                apply_id=self.apply_id,
                group_id=self.group_id,
                storage_type=self.storage_type,
                user_rag_memory_id=self.user_rag_memory_id
            )
            logger.debug(
                f"[ToolExecutionNode] {self.id} - Built tool args with keys: {list(tool_args.keys())}"
            )
            
        except Exception as e:
            logger.error(
                f"[ToolExecutionNode] {self.id} - Failed to build tool args: {e}",
                exc_info=True
            )
            return {"messages": [AIMessage(content=f"Error building arguments: {str(e)}")]}
        
        # Construct tool input message
        tool_input = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{
                        "name": self.tool_name,
                        "args": tool_args,
                        "id": f"{self.id}_{tool_call_id}",
                    }]
                )
            ]
        }
        
        try:
            # Invoke the tool
            result = await self.tool_node.ainvoke(tool_input)
            
            logger.debug(
                f"[ToolExecutionNode] {self.id} - Tool execution completed"
            )
            
            # Return the result directly - it already contains the messages list
            return result
            
        except Exception as e:
            logger.error(
                f"[ToolExecutionNode] {self.id} - Tool execution failed: {e}",
                exc_info=True
            )
            # Return error as ToolMessage to maintain message chain consistency
            from langchain_core.messages import ToolMessage
            return {
                "messages": [
                    ToolMessage(
                        content=f"Error executing tool: {str(e)}",
                        tool_call_id=f"{self.id}_{tool_call_id}"
                    )
                ]
            }
