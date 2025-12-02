"""
Routing functions for LangGraph conditional edges.

This module provides routing functions that determine the next node to execute
based on state values. All functions return Literal types for type safety.
"""

import logging
import re
from typing import Literal

from app.core.memory.agent.langgraph_graph.state.extractors import extract_search_switch
from app.core.memory.agent.utils.llm_tools import ReadState, COUNTState

logger = logging.getLogger(__name__)

# Global counter for Verify routing
counter = COUNTState(limit=3)


def Verify_continue(state: ReadState) -> Literal["Summary", "Summary_fails", "content_input"]:
    """
    Determine routing after Verify node based on verification result.
    
    This function checks the verification result in the last message and routes to:
    - Summary: if verification succeeded
    - content_input: if verification failed and retry limit not reached
    - Summary_fails: if verification failed and retry limit reached
    
    Args:
        state: LangGraph state containing messages
        
    Returns:
        Next node name as Literal type
    """
    messages = state.get("messages", [])
    
    # Boundary check
    if not messages:
        logger.warning("[Verify_continue] No messages in state, defaulting to Summary")
        counter.reset()
        return "Summary"
    
    # Increment counter
    counter.add(1)
    loop_count = counter.get_total()
    logger.debug(f"[Verify_continue] Current loop count: {loop_count}")
    
    # Extract verification result from last message
    last_message = messages[-1]
    last_message_str = str(last_message).replace('\\', '')
    status_tools = re.findall(r'"split_result": "(.*?)"', last_message_str)
    logger.debug(f"[Verify_continue] Status tools: {status_tools}")
    
    # Route based on verification result
    if "success" in status_tools:
        counter.reset()
        return "Summary"
    elif "failed" in status_tools:
        if loop_count < 2:  # Max retry count is 2
            return "content_input"
        else:
            counter.reset()
            return "Summary_fails"
    else:
        # Default to Summary if status is unclear
        counter.reset()
        return "Summary"


def Retrieve_continue(state: dict) -> Literal["Verify", "Retrieve_Summary"]:
    """
    Determine routing after Retrieve node based on search_switch value.
    
    This function routes based on the search_switch parameter:
    - search_switch == '0': Route to Verify (verification needed)
    - search_switch == '1': Route to Retrieve_Summary (direct summary)
    
    Args:
        state: LangGraph state dictionary
        
    Returns:
        Next node name as Literal type
    """
    search_switch = extract_search_switch(state)
    
    logger.debug(f"[Retrieve_continue] search_switch: {search_switch}")
    
    if search_switch == '0':
        return 'Verify'
    elif search_switch == '1':
        return 'Retrieve_Summary'
    
    # Default to Retrieve_Summary
    logger.debug("[Retrieve_continue] No valid search_switch, defaulting to Retrieve_Summary")
    return 'Retrieve_Summary'


def Split_continue(state: dict) -> Literal["Split_The_Problem", "Input_Summary"]:
    """
    Determine routing after content_input node based on search_switch value.
    
    This function routes based on the search_switch parameter:
    - search_switch == '2': Route to Input_Summary (direct input summary)
    - Otherwise: Route to Split_The_Problem (problem decomposition)
    
    Args:
        state: LangGraph state dictionary
        
    Returns:
        Next node name as Literal type
    """
    logger.debug(f"[Split_continue] state keys: {state.keys()}")
    
    search_switch = extract_search_switch(state)
    
    logger.debug(f"[Split_continue] search_switch: {search_switch}")
    
    if search_switch == '2':
        return 'Input_Summary'
    
    # Default to Split_The_Problem
    return 'Split_The_Problem'
