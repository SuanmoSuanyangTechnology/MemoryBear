
from app.core.memory.agent.utils.llm_tools import  WriteState
from app.core.memory.agent.utils.write_tools import write
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)
async def write_node(state: WriteState) -> WriteState:
    """
        Write data to the database/file system.

        Args:
            content: Data content to write
            end_user_id: End user identifier
            memory_config: MemoryConfig object containing all configuration

        Returns:
            dict: Contains 'status', 'saved_to', and 'data' fields
        """
    content=state.get('data','')
    end_user_id=state.get('end_user_id','')
    memory_config=state.get('memory_config', '')
    try:
        result=await write(
            end_user_id=end_user_id,
            memory_config=memory_config,
            messages=content,  # 修复：使用正确的参数名 messages
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
