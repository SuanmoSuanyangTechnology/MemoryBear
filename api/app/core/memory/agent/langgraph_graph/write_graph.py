import warnings

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.langgraph_graph.routing.write_router import memory_long_term_storage, window_dialogue, \
    aggregate_judgment
from app.core.memory.agent.utils.redis_tool import write_store
from app.db import get_db_context
from app.schemas.memory_agent_schema import AgentMemory_Long_Term
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_konwledges_server import write_rag

warnings.filterwarnings("ignore", category=RuntimeWarning)
logger = get_agent_logger(__name__)


async def long_term_storage(
        long_term_type: str,
        langchain_messages: list,
        memory_config_id: str,
        end_user_id: str,
        scope: int = 6
):
    """
    Handle long-term memory storage with different strategies

    Supports multiple storage strategies including chunk-based, time-based,
    and aggregate judgment approaches for long-term memory persistence.

    Args:
        long_term_type: Storage strategy type ('chunk', 'time', 'aggregate')
        langchain_messages: List of messages to store
        memory_config_id: Memory configuration identifier
        end_user_id: User group identifier
        scope: Scope parameter for chunk-based storage (default: 6)
    """
    if langchain_messages is None:
        langchain_messages = []

    write_store.save_session_write(end_user_id, langchain_messages)
    # 获取数据库会话
    with get_db_context() as db_session:
        config_service = MemoryConfigService(db_session)
        memory_config = config_service.load_memory_config(
            config_id=memory_config_id,  # 改为整数
            service_name="MemoryAgentService"
        )
        if long_term_type == AgentMemory_Long_Term.STRATEGY_CHUNK:
            # Dialogue window with 6 rounds of conversation
            await window_dialogue(end_user_id, langchain_messages, memory_config, scope)
        if long_term_type == AgentMemory_Long_Term.STRATEGY_TIME:
            # Time-based strategy
            await memory_long_term_storage(end_user_id, memory_config, AgentMemory_Long_Term.TIME_SCOPE)
        if long_term_type == AgentMemory_Long_Term.STRATEGY_AGGREGATE:
            # Aggregate judgment
            await aggregate_judgment(end_user_id, langchain_messages, memory_config)


async def write_long_term(
        storage_type: str,
        end_user_id: str,
        messages: list[dict],
        user_rag_memory_id: str,
        actual_config_id: str
):
    """
    Write long-term memory with different storage types

    Handles both RAG-based storage and traditional memory storage approaches.
    For traditional storage, uses chunk-based strategy with paired user-AI messages.

    Args:
        storage_type: Type of storage (RAG or traditional)
        end_user_id: User group identifier
        messages: message list
        user_rag_memory_id: RAG memory identifier
        actual_config_id: Actual configuration ID
    """
    from app.core.memory.agent.langgraph_graph.routing.write_router import term_memory_save
    if storage_type == AgentMemory_Long_Term.STORAGE_RAG:
        message_content = []
        for message in messages:
            message_content.append(f'{message.get("role")}:{message.get("content")}')
        messages_string = "\n".join(message_content)
        await write_rag(end_user_id, messages_string, user_rag_memory_id)
    else:
        # AI reply writing (user messages and AI replies paired, written as complete dialogue at once)
        CHUNK = AgentMemory_Long_Term.STRATEGY_CHUNK
        SCOPE = AgentMemory_Long_Term.DEFAULT_SCOPE
        await long_term_storage(long_term_type=CHUNK,
                                langchain_messages=messages,
                                memory_config_id=actual_config_id,
                                end_user_id=end_user_id,
                                scope=SCOPE)
        await term_memory_save(end_user_id, CHUNK, scope=SCOPE)
