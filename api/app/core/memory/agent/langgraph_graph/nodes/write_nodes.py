
from app.core.memory.agent.utils.llm_tools import  WriteState
from app.core.memory.agent.utils.write_tools import write
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)
async def write_node(state: WriteState) -> WriteState:
    """
        Write data to the database/file system.

        Args:
            ctx: FastMCP context for dependency injection
            content: Data content to write
            user_id: User identifier
            apply_id: Application identifier
            group_id: Group identifier
            memory_config: MemoryConfig object containing all configuration

        Returns:
            dict: Contains 'status', 'saved_to', and 'data' fields
        """
    content=state.get('data','')
    group_id=state.get('group_id','')
    memory_config=state.get('memory_config', '')
    try:
        result=await write(
            content=content,
            user_id=group_id,
            apply_id=group_id,
            group_id=group_id,
            memory_config=memory_config,
        )
        logger.info(f"Write completed successfully! Config: {memory_config.config_name}")

        write_result= {
            "status": "success",
            "data": content,
            "config_id": memory_config.config_id,
            "config_name": memory_config.config_name,
        }
        return {"write_result":write_result}


    except Exception as e:
        logger.error(f"Data_write failed: {e}", exc_info=True)
        write_result= {
            "status": "error",
            "message": str(e),
        }
        return {"write_result": write_result}
