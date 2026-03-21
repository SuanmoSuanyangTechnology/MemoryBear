from app.core.memory.agent.utils.llm_tools import ReadState, WriteState


def content_input_node(state: ReadState) -> ReadState:
    """开始节点 - 提取内容并保持状态信息"""

    content = state['messages'][0].content if state.get('messages') else ''
    # 返回内容并保持所有状态信息
    return {"data": content}

def content_input_write(state: WriteState) -> WriteState:
    """开始节点 - 提取内容并保持状态信息"""

    content = state['messages'][0].content if state.get('messages') else ''
    # 返回内容并保持所有状态信息
    return {"data": content}