from app.core.memory.agent.utils.llm_tools import WriteState
from app.core.memory.agent.utils.write_tools import write
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)


async def write_node(state: WriteState) -> WriteState:
    """
        Write data to the database/file system.

        Args:
            state: WriteState containing messages, end_user_id, and memory_config

        Returns:
            dict: Contains 'write_result' with status and data fields
        """
    messages = state.get('messages', [])
    end_user_id = state.get('end_user_id', '')
    memory_config = state.get('memory_config', '')

    # Convert LangChain messages to structured format expected by write()
    structured_messages = []
    for msg in messages:
        if hasattr(msg, 'type') and hasattr(msg, 'content'):
            # Map LangChain message types to role names
            role = 'user' if msg.type == 'human' else 'assistant' if msg.type == 'ai' else msg.type
            structured_messages.append({
                "role": role,
                "content": msg.content  # content is now guaranteed to be a string
            })

    try:
        result = await write(
            messages=structured_messages,
            end_user_id=end_user_id,
            memory_config=memory_config,
        )
        logger.info(f"Write completed successfully! Config: {memory_config.config_name}")

        write_result = {
            "status": "success",
            "data": structured_messages,
            "config_id": memory_config.config_id,
            "config_name": memory_config.config_name,
        }
        return {"write_result": write_result}

    except Exception as e:
        logger.error(f"Data_write failed: {e}", exc_info=True)
        write_result = {
            "status": "error",
            "message": str(e),
        }
        return {"write_result": write_result}
