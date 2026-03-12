from app.core.memory.agent.utils.llm_tools import ReadState, WriteState


def content_input_node(state: ReadState) -> ReadState:
    """
    Start node - Extract content and maintain state information
    
    Extracts the content from the first message in the state and returns it
    as the data field while preserving all other state information.
    
    Args:
        state: ReadState containing messages and other state data
        
    Returns:
        ReadState: Updated state with extracted content in data field
    """

    content = state['messages'][0].content if state.get('messages') else ''
    # Return content and maintain all state information
    return {"data": content}

def content_input_write(state: WriteState) -> WriteState:
    """
    Start node - Extract content and maintain state information for write operations
    
    Extracts the content from the first message in the state for write operations.
    
    Args:
        state: WriteState containing messages and other state data
        
    Returns:
        WriteState: Updated state with extracted content in data field
    """

    content = state['messages'][0].content if state.get('messages') else ''
    # Return content and maintain all state information
    return {"data": content}