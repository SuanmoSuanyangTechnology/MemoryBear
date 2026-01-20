
from typing import Literal

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.llm_tools import ReadState, COUNTState


logger = get_agent_logger(__name__)
counter = COUNTState(limit=3)
def Split_continue(state:ReadState) -> Literal["Split_The_Problem", "Input_Summary"]:
    """
    Determine routing based on search_switch value.

    Args:
        state: State dictionary containing search_switch

    Returns:
        Next node to execute
    """
    logger.debug(f"Split_continue state: {state}")
    search_switch = state.get('search_switch', '')
    if search_switch is not None:
        search_switch = str(search_switch)
        if search_switch == '2':
            return 'Input_Summary'
    return 'Split_The_Problem'  # 默认情况

def Retrieve_continue(state) -> Literal["Verify", "Retrieve_Summary"]:
    """
    Determine routing based on search_switch value.

    Args:
        state: State dictionary containing search_switch

    Returns:
        Next node to execute
    """
    search_switch = state.get('search_switch', '')
    if search_switch is not None:
        search_switch = str(search_switch)
        if search_switch == '0':
            return 'Verify'
        elif search_switch == '1':
            return 'Retrieve_Summary'
    return 'Retrieve_Summary'  # Default based on business logic
def Verify_continue(state: ReadState) -> Literal["Summary", "Summary_fails", "content_input"]:
    status=state.get('verify', '')['status']
    # loop_count = counter.get_total()
    if "success" in status:
        # counter.reset()
        return "Summary"
    elif "failed" in status:
        # if loop_count < 2:  # Maximum loop count is 3
        #     return "content_input"
        # else:
            # counter.reset()
        return "Summary_fails"
    else:
        # Add default return value to avoid returning None
        # counter.reset()
        return "Summary"  # Default based on business requirements
