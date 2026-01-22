"""
Memory Agent Service

Handles business logic for memory agent operations including read/write services,
health checks, and message type classification.
"""
import json
import os
import re
import time
import uuid
from uuid import UUID
from typing import Any, AsyncGenerator, Dict, List, Optional

import redis
from app.core.config import settings
from app.core.logging_config import get_config_logger, get_logger
from app.core.memory.agent.langgraph_graph.read_graph import make_read_graph
from app.core.memory.agent.langgraph_graph.write_graph import make_write_graph
from app.core.memory.agent.logger_file.log_streamer import LogStreamer
from app.core.memory.agent.utils.messages_tools import (
    merge_multiple_search_results,
    reorder_output_results,
)
from app.core.memory.agent.utils.type_classifier import status_typle
from app.core.memory.agent.utils.write_tools import write  # 新增：直接导入 write 函数
from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.models.knowledge_model import Knowledge, KnowledgeType
from app.repositories.memory_short_repository import ShortTermMemoryRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_agent_schema import Write_UserInput
from app.schemas.memory_config_schema import ConfigurationError
from app.services.memory_base_service import Translation_English
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_konwledges_server import (
    write_rag,
)
from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

try:
    from app.core.memory.utils.log.audit_logger import audit_logger
except ImportError:
    audit_logger = None
logger = get_logger(__name__)
config_logger = get_config_logger()

# Initialize Neo4j connector for analytics functions
_neo4j_connector = Neo4jConnector()


class MemoryAgentService:
    """Service for memory agent operations"""

    def writer_messages_deal(self, messages, start_time, end_user_id, config_id, message, context):
        duration = time.time() - start_time
        if str(messages) == 'success':
            logger.info(f"Write operation successful for group {end_user_id} with config_id {config_id}")
            # 记录成功的操作
            if audit_logger:
                audit_logger.log_operation(operation="WRITE", config_id=config_id, end_user_id=end_user_id, success=True,
                                           duration=duration, details={"message_length": len(message)})
            return context
        else:
            logger.warning(f"Write operation failed for group {end_user_id}")

            # 记录失败的操作
            if audit_logger:
                audit_logger.log_operation(
                    operation="WRITE",
                    config_id=config_id,
                    end_user_id=end_user_id,
                    success=False,
                    duration=duration,
                    error=f"写入失败: {messages[:100]}"
                )

            raise ValueError(f"写入失败: {messages}")



    def extract_tool_call_info(self, event: Dict) -> bool:
        """Extract tool call information from event"""
        last_message = event["messages"][-1]

        # Check if AI message contains tool calls
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            tool_calls = last_message.tool_calls
            for i, tool_call in enumerate(tool_calls):
                if isinstance(tool_call, dict):
                    tool_call_id = tool_call.get('id')
                    tool_name = tool_call.get('name')
                    tool_args = tool_call.get('args', {})
                else:
                    tool_call_id = getattr(tool_call, 'id', None)
                    tool_name = getattr(tool_call, 'name', None)
                    tool_args = getattr(tool_call, 'args', {})

                logger.debug(f"Tool Call {i + 1}: ID={tool_call_id}, Name={tool_name}, Args={tool_args}")
            return True

        # Check if tool message
        elif hasattr(last_message, 'tool_call_id'):
            tool_call_id = getattr(last_message, 'tool_call_id', None)
            if hasattr(last_message, 'name') and hasattr(last_message, 'content'):
                tool_name = getattr(last_message, 'name', None)
                try:
                    content = json.loads(getattr(last_message, 'content', '{}'))
                    tool_args = content.get('args', {})
                    logger.debug(f"Tool Call 1: ID={tool_call_id}, Name={tool_name}, Args={tool_args}")
                except:
                    logger.debug(f"Tool Response ID: {tool_call_id}")
            else:
                logger.debug(f"Tool Response ID: {tool_call_id}")
            return True

        return False

    async def get_health_status(self) -> Dict:
        """
        Get latest health status from Redis cache

        Returns health status information written by Celery periodic task
        """
        logger.info("Checking health status")

        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None
        )
        payload = client.hgetall("memsci:health:read_service") or {}

        if payload:
            # decode bytes to str
            decoded = {k.decode("utf-8"): v.decode("utf-8") for k, v in payload.items()}
            status = decoded.get("status", "unknown")
        else:
            status = "unknown"

        # Add database connection pool status
        try:
            from app.db import get_pool_status
            pool_status = get_pool_status()
            logger.info(f"Database pool status: {pool_status}")

            # Check if pool usage is too high
            if pool_status.get("usage_percent", 0) > 80:
                logger.warning(f"High database pool usage: {pool_status['usage_percent']}%")
                status = "warning"

        except Exception as e:
            logger.error(f"Failed to get pool status: {e}")
            pool_status = {"error": str(e)}

        logger.info(f"Health status: {status}")
        return {
            "status": status,
            "database_pool": pool_status
        }

    def get_log_content(self) -> str:
        """
        Read and return agent service log file content

        Returns cleaned log content using the same cleaning logic as transmission mode

        Returns cleaned log content using the same cleaning logic as transmission mode
        """
        logger.info("Reading log file")


        current_file = os.path.abspath(__file__)  # app/services/memory_agent_service.py
        app_dir = os.path.dirname(os.path.dirname(current_file))  # app directory
        project_root = os.path.dirname(app_dir)  # redbear-mem directory
        log_path = os.path.join(project_root, "logs", "agent_service.log")

        summer = ''

        with open(log_path, "r", encoding="utf-8") as infile:
            for line in infile:
                # Use the same cleaning logic as LogStreamer for consistency
                cleaned = LogStreamer.clean_log_line(line)
                summer += cleaned

        if len(summer) < 10:
            raise ValueError("NO LOGS")

        logger.info(f"Log content retrieved, size: {len(summer)} bytes")
        return summer

    async def stream_log_content(self) -> AsyncGenerator[str, None]:
        """
        Stream log content in real-time using Server-Sent Events (SSE)

        This method establishes a streaming connection and transmits log entries
        as they are written to the log file. It uses the LogStreamer to watch
        the file and yields SSE-formatted messages.

        Yields:
            SSE-formatted strings with the following event types:
            - log: Contains log content and timestamp
            - keepalive: Periodic keepalive messages to maintain connection
            - error: Error information if streaming fails
            - done: Indicates streaming has completed

        Raises:
            FileNotFoundError: If log file doesn't exist at stream start
            Exception: For other unexpected errors during streaming
        """
        logger.info("Starting log content streaming")

        # Get log file path - use project root directory
        current_file = os.path.abspath(__file__)  # app/services/memory_agent_service.py
        app_dir = os.path.dirname(os.path.dirname(current_file))  # app directory
        project_root = os.path.dirname(app_dir)  # redbear-mem directory
        log_path = os.path.join(project_root, "logs", "agent_service.log")

        # Check if file exists before starting stream
        if not os.path.exists(log_path):
            logger.error(f"Log file not found: {log_path}")
            # Send error event in SSE format
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件不存在', 'error': f'File not found: {log_path}'})}\n\n"
            return

        streamer = None
        try:
            # Initialize LogStreamer with keepalive interval from settings (default 300 seconds)
            keepalive_interval = getattr(settings, 'LOG_STREAM_KEEPALIVE_INTERVAL', 300)
            streamer = LogStreamer(log_path, keepalive_interval=keepalive_interval)

            logger.info(f"LogStreamer initialized for {log_path}")

            # Stream log content using read_existing_and_stream to get all existing content first
            async for message in streamer.read_existing_and_stream():
                event_type = message.get("event")
                data = message.get("data")

                # Format as SSE message
                # SSE format: "event: <type>\ndata: <json_data>\n\n"
                sse_message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

                logger.debug(f"Streaming event: {event_type}")
                yield sse_message

                # If error or done event, stop streaming
                if event_type in ["error", "done"]:
                    logger.info(f"Stream ended with event: {event_type}")
                    break

        except FileNotFoundError as e:
            logger.error(f"Log file not found during streaming: {e}")
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件在流式传输期间变得不可用', 'error': str(e)})}\n\n"

        except Exception as e:
            logger.error(f"Unexpected error during log streaming: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'code': 8001, 'message': '流式传输期间发生错误', 'error': str(e)})}\n\n"

        finally:
            # Resource cleanup
            logger.info("Log streaming completed, cleaning up resources")
            # LogStreamer uses context manager for file handling, so cleanup is automatic

    async def write_memory(self, end_user_id: str, messages:  list[dict], config_id: Optional[uuid.UUID], db: Session, storage_type: str, user_rag_memory_id: str) -> str:
        """
        Process write operation with config_id

        Args:
            end_user_id: Group identifier (also used as end_user_id)
            message: Message to write
            config_id: Configuration ID from database
            db: SQLAlchemy database session
            storage_type: Storage type (neo4j or rag)
            user_rag_memory_id: User RAG memory ID

        Returns:
            Write operation result status

        Raises:
            ValueError: If config loading fails or write operation fails
        """
        # Resolve config_id if None using end_user's connected config
        if config_id is None:
            try:
                connected_config = get_end_user_connected_config(end_user_id, db)
                config_id = connected_config.get("memory_config_id")
                if config_id is None:
                    raise ValueError(f"No memory configuration found for end_user {end_user_id}. Please ensure the user has a connected memory configuration.")
            except Exception as e:
                if "No memory configuration found" in str(e):
                    raise  # Re-raise our specific error
                logger.error(f"Failed to get connected config for end_user {end_user_id}: {e}")
                raise ValueError(f"Unable to determine memory configuration for end_user {end_user_id}: {e}")

        import time
        start_time = time.time()

        # Load configuration from database only
        try:
            config_service = MemoryConfigService(db)
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                service_name="MemoryAgentService"
            )
            logger.info(f"Configuration loaded successfully: {memory_config.config_name}")
        except ConfigurationError as e:
            error_msg = f"Failed to load configuration for config_id: {config_id}: {e}"
            logger.error(error_msg)

            # Log failed operation
            if audit_logger:
                duration = time.time() - start_time
                audit_logger.log_operation(operation="WRITE", config_id=config_id, end_user_id=end_user_id, success=False, duration=duration, error=error_msg)

            raise ValueError(error_msg)

        try:
            if storage_type == "rag":
                # For RAG storage, convert messages to single string
                message_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
                result = await write_rag(end_user_id, message_text, user_rag_memory_id)
                return result
            else:
                async with make_write_graph() as graph:
                    config = {"configurable": {"thread_id": end_user_id}}
                    # Convert structured messages to LangChain messages
                    langchain_messages = []
                    for msg in messages:
                        if msg['role'] == 'user':
                            langchain_messages.append(HumanMessage(content=msg['content']))
                        elif msg['role'] == 'assistant':
                            langchain_messages.append(AIMessage(content=msg['content']))

                    # 初始状态 - 包含所有必要字段
                    initial_state = {
                        "messages": langchain_messages,
                        "end_user_id": end_user_id,
                        "memory_config": memory_config
                    }

                    # 获取节点更新信息
                    async for update_event in graph.astream(
                            initial_state,
                            stream_mode="updates",
                            config=config
                    ):
                        for node_name, node_data in update_event.items():
                            if 'save_neo4j' == node_name:
                                massages = node_data
                    massagesstatus = massages.get('write_result')['status']
                    contents = massages.get('write_result')
                    # Convert messages back to string for logging
                    message_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
                    return self.writer_messages_deal(massagesstatus, start_time, end_user_id, config_id, message_text, contents)
        except Exception as e:
            # Ensure proper error handling and logging
            error_msg = f"Write operation failed: {str(e)}"
            logger.error(error_msg)
            if audit_logger:
                duration = time.time() - start_time
                audit_logger.log_operation(operation="WRITE", config_id=config_id, end_user_id=end_user_id, success=False, duration=duration, error=error_msg)
            raise ValueError(error_msg)




    async def read_memory(
        self,
        end_user_id: str,
        message: str,
        history: List[Dict],
        search_switch: str,
        config_id: Optional[UUID],
        db: Session,
        storage_type: str,
        user_rag_memory_id: str) -> Dict:
        """
        Process read operation with config_id

        search_switch values:
        - "0": Requires verification
        - "1": No verification, direct split
        - "2": Direct answer based on context

        Args:
            end_user_id: Group identifier (also used as end_user_id)
            message: User message
            history: Conversation history
            search_switch: Search mode switch
            config_id: Configuration ID from database
            db: SQLAlchemy database session
            storage_type: Storage type (neo4j or rag)
            user_rag_memory_id: User RAG memory ID

        Returns:
            Dict with 'answer' and 'intermediate_outputs' keys

        Raises:
            ValueError: If config loading fails
        """

        import time
        start_time = time.time()
        ori_message= message

        # Resolve config_id if None using end_user's connected config
        if config_id is None:
            try:
                connected_config = get_end_user_connected_config(end_user_id, db)
                config_id = connected_config.get("memory_config_id")
                if config_id is None:
                    raise ValueError(f"No memory configuration found for end_user {end_user_id}. Please ensure the user has a connected memory configuration.")
            except Exception as e:
                if "No memory configuration found" in str(e):
                    raise  # Re-raise our specific error
                logger.error(f"Failed to get connected config for end_user {end_user_id}: {e}")
                raise ValueError(f"Unable to determine memory configuration for end_user {end_user_id}: {e}")

        logger.info(f"Read operation for group {end_user_id} with config_id {config_id}")

        # 导入审计日志记录器
        try:
            from app.core.memory.utils.log.audit_logger import audit_logger
        except ImportError:
            audit_logger = None


        try:
            config_service = MemoryConfigService(db)
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                service_name="MemoryAgentService"
            )
            logger.info(f"Configuration loaded successfully: {memory_config.config_name}")
        except ConfigurationError as e:
            error_msg = f"Failed to load configuration for config_id: {config_id}: {e}"
            logger.error(error_msg)

            # Log failed operation
            if audit_logger:
                duration = time.time() - start_time
                audit_logger.log_operation(
                    operation="READ",
                    config_id=config_id,
                    end_user_id=end_user_id,
                    success=False,
                    duration=duration,
                    error=error_msg
                )

            raise ValueError(error_msg)

        # Step 2: Prepare history
        history.append({"role": "user", "content": message})
        logger.debug(f"Group ID:{end_user_id}, Message:{message}, History:{history}, Config ID:{config_id}")

        # Step 3: Initialize MCP client and execute read workflow
        graph_exec_start = time.time()
        try:
            async with make_read_graph() as graph:
                config = {"configurable": {"thread_id": end_user_id}}
                # 初始状态 - 包含所有必要字段
                initial_state = {"messages": [HumanMessage(content=message)], "search_switch": search_switch,
                                 "end_user_id": end_user_id
                    , "storage_type": storage_type, "user_rag_memory_id": user_rag_memory_id,
                                 "memory_config": memory_config}
                # 获取节点更新信息
                _intermediate_outputs = []
                summary = ''
                async for update_event in graph.astream(
                        initial_state,
                        stream_mode="updates",
                        config=config
                ):
                    for node_name, node_data in update_event.items():
                        # if 'save_neo4j' == node_name:
                        #     massages = node_data
                        print(f"处理节点: {node_name}")

                        # 处理不同Summary节点的返回结构
                        if 'Summary' in node_name:
                            if 'InputSummary' in node_data and 'summary_result' in node_data['InputSummary']:
                                summary = node_data['InputSummary']['summary_result']
                            elif 'RetrieveSummary' in node_data and 'summary_result' in node_data['RetrieveSummary']:
                                summary = node_data['RetrieveSummary']['summary_result']
                            elif 'summary' in node_data and 'summary_result' in node_data['summary']:
                                summary = node_data['summary']['summary_result']
                            elif 'SummaryFails' in node_data and 'summary_result' in node_data['SummaryFails']:
                                summary = node_data['SummaryFails']['summary_result']

                        spit_data = node_data.get('spit_data', {}).get('_intermediate', None)
                        if spit_data and spit_data != [] and spit_data != {}:
                            _intermediate_outputs.append(spit_data)

                        # Problem_Extension 节点
                        problem_extension = node_data.get('problem_extension', {}).get('_intermediate', None)
                        if problem_extension and problem_extension != [] and problem_extension != {}:
                            _intermediate_outputs.append(problem_extension)

                        # Retrieve 节点
                        retrieve_node = node_data.get('retrieve', {}).get('_intermediate_outputs', None)
                        if retrieve_node and retrieve_node != [] and retrieve_node != {}:
                            _intermediate_outputs.extend(retrieve_node)

                        # Verify 节点
                        verify_n = node_data.get('verify', {}).get('_intermediate', None)
                        if verify_n and verify_n != [] and verify_n != {}:
                            _intermediate_outputs.append(verify_n)

                        # Summary 节点
                        summary_n = node_data.get('summary', {}).get('_intermediate', None)
                        if summary_n and summary_n != [] and summary_n != {}:
                            _intermediate_outputs.append(summary_n)

                graph_exec_time = time.time() - graph_exec_start
                logger.info(f"[PERF] Graph execution completed in {graph_exec_time:.4f}s")

                _intermediate_outputs = [item for item in _intermediate_outputs if item and item != [] and item != {}]

                optimized_outputs = merge_multiple_search_results(_intermediate_outputs)
                result = reorder_output_results(optimized_outputs)

                # 保存短期记忆到数据库
                # 只有 search_switch 不为 "2"（快速检索）时才保存
                try:
                    from app.repositories.memory_short_repository import ShortTermMemoryRepository
                    
                    retrieved_content = []
                    repo = ShortTermMemoryRepository(db)
                    
                    if str(search_switch) != "2":
                        for intermediate in _intermediate_outputs:
                            logger.debug(f"处理中间结果: {intermediate}")
                            intermediate_type = intermediate.get('type', '')
                            
                            if intermediate_type == "search_result":
                                query = intermediate.get('query', '')
                                raw_results = intermediate.get('raw_results', {})
                                reranked_results = raw_results.get('reranked_results', [])
                                
                                try:
                                    statements = [statement['statement'] for statement in reranked_results.get('statements', [])]
                                except Exception:
                                    statements = []
                                
                                # 去重
                                statements = list(set(statements))
                                
                                if query and statements:
                                    retrieved_content.append({query: statements})
                    
                    # 如果 retrieved_content 为空，设置为空字符串
                    if retrieved_content == []:
                        retrieved_content = ''
                    
                    # 只有当回答不是"信息不足"且不是快速检索时才保存
                    if '信息不足，无法回答。' != str(summary) and str(search_switch).strip() != "2":
                        # 使用 upsert 方法
                        repo.upsert(
                            end_user_id=end_user_id,
                            messages=message,
                            aimessages=summary,
                            retrieved_content=retrieved_content,
                            search_switch=str(search_switch)
                        )
                        logger.info(f"成功保存短期记忆: end_user_id={end_user_id}, search_switch={search_switch}")
                    else:
                        logger.debug(f"跳过保存短期记忆: summary={summary[:50] if summary else 'None'}, search_switch={search_switch}")
                        
                except Exception as save_error:
                    # 保存失败不应该影响主流程，只记录错误
                    logger.error(f"保存短期记忆失败: {str(save_error)}", exc_info=True)

                # Log successful operation
                if audit_logger:
                    duration = time.time() - start_time
                    audit_logger.log_operation(
                        operation="READ",
                        config_id=config_id,
                        end_user_id=end_user_id,
                        success=True,
                        duration=duration
                    )

                return {
                    "answer": summary,
                    "intermediate_outputs": result
                }
        except Exception as e:
            # Ensure proper error handling and logging
            error_msg = f"Read operation failed: {str(e)}"
            logger.error(error_msg)
            if audit_logger:
                duration = time.time() - start_time
                audit_logger.log_operation(
                    operation="READ",
                    config_id=config_id,
                    end_user_id=end_user_id,
                    success=False,
                    duration=duration,
                    error=error_msg
                )
            raise ValueError(error_msg)


    def get_messages_list(self, user_input: Write_UserInput) -> list[dict]:
        """
        Get standardized message list from user input.
        
        Args:
            user_input: Write_UserInput object
        
        Returns:
            list[dict]: Message list, each message contains role and content
            
        Raises:
            ValueError: If messages is empty or format is incorrect
        """
        from app.core.logging_config import get_api_logger
        logger = get_api_logger()
        
        if len(user_input.messages) == 0:
            logger.error("Validation failed: Message list cannot be empty")
            raise ValueError("Message list cannot be empty")
        
        for idx, msg in enumerate(user_input.messages):
            if not isinstance(msg, dict):
                logger.error(f"Validation failed: Message {idx} is not a dict: {type(msg)}")
                raise ValueError(f"Message format error: Message must be a dictionary. Error message index: {idx}, type: {type(msg)}")
            
            if 'role' not in msg:
                logger.error(f"Validation failed: Message {idx} missing 'role' field: {msg}")
                raise ValueError(f"Message format error: Message must contain 'role' field. Error message index: {idx}")
            
            if 'content' not in msg:
                logger.error(f"Validation failed: Message {idx} missing 'content' field: {msg}")
                raise ValueError(f"Message format error: Message must contain 'content' field. Error message index: {idx}")
            
            if msg['role'] not in ['user', 'assistant']:
                logger.error(f"Validation failed: Message {idx} invalid role: {msg['role']}")
                raise ValueError(f"Role must be 'user' or 'assistant', got: {msg['role']}. Message index: {idx}")
            
            if not msg['content'] or not msg['content'].strip():
                logger.error(f"Validation failed: Message {idx} content is empty")
                raise ValueError(f"Message content cannot be empty. Message index: {idx}, role: {msg['role']}")
        
        logger.info(f"Validation successful: Structured message list, count: {len(user_input.messages)}")
        return user_input.messages

    async def classify_message_type(self, message: str, config_id: UUID, db: Session) -> Dict:
        """
        Determine the type of user message (read or write)
        Updated to eliminate global variables in favor of explicit parameters.

        Args:
            message: User message to classify
            config_id: Configuration ID to load LLM model from database
            db: Database session

        Returns:
            Type classification result
        """
        logger.info("Classifying message type")

        # Load configuration to get LLM model ID
        config_service = MemoryConfigService(db)
        memory_config = config_service.load_memory_config(
            config_id=config_id,
            service_name="MemoryAgentService"
        )

        status = await status_typle(message, memory_config.llm_model_id)
        logger.debug(f"Message type: {status}")
        return status

    async def generate_summary_from_retrieve(
        self,
        retrieve_info: str,
        history: List[Dict],
        query: str,
        config_id: UUID,
        db: Session
    ) -> str:
        """
        基于检索信息、历史对话和查询生成最终答案
        
        使用 Retrieve_Summary_prompt.jinja2 模板调用大模型生成答案
        
        Args:
            retrieve_info: 检索到的信息
            history: 历史对话记录
            query: 用户查询
            config_id: 配置ID
            db: 数据库会话
            
        Returns:
            生成的答案文本
        """
        logger.info(f"Generating summary from retrieve info for query: {query[:50]}...")
        
        try:
            # 加载配置
            config_service = MemoryConfigService(db)
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                service_name="MemoryAgentService"
            )
            
            # 导入必要的模块
            from app.core.memory.agent.langgraph_graph.nodes.summary_nodes import summary_llm
            from app.core.memory.agent.models.summary_models import RetrieveSummaryResponse
            
            # 构建状态对象
            state = {
                "data": query,
                "memory_config": memory_config
            }
            
            # 直接调用 summary_llm 函数
            answer = await summary_llm(
                state=state,
                history=history,
                retrieve_info=retrieve_info,
                template_name='Retrieve_Summary_prompt.jinja2',
                operation_name='retrieve_summary',
                response_model=RetrieveSummaryResponse,
                search_mode="1"
            )
            
            logger.info(f"Successfully generated summary: {answer[:100] if answer else 'None'}...")
            return answer if answer else "信息不足，无法回答。"
            
        except Exception as e:
            logger.error(f"生成摘要失败: {str(e)}", exc_info=True)
            return "信息不足，无法回答。"


    async def get_knowledge_type_stats(
        self,
        end_user_id: Optional[str] = None,
        only_active: bool = True,
        current_workspace_id: Optional[uuid.UUID] = None,
        db: Session = None
    ) -> Dict[str, Any]:
        """
        统计知识库类型分布，包含：
        1. PostgreSQL 中的知识库类型：General, Web, Third-party, Folder（根据 workspace_id 过滤）
        2. Neo4j 中的 memory 类型（仅统计 Chunk 数量，根据 end_user_id/end_user_id 过滤）
        3. total: 所有类型的总和

        参数：
        - end_user_id: 用户组ID（可选，未提供时 memory 统计为 0）
        - only_active: 是否仅统计有效记录
        - current_workspace_id: 当前工作空间ID（可选，未提供时知识库统计为 0）
        - db: 数据库会话

        返回格式：
        {
            "General": count,
            "Web": count,
            "Third-party": count,
            "Folder": count,
            "memory": chunk_count,
            "total": sum_of_all
        }
        """
        result = {}

        # 1. 统计 PostgreSQL 中的知识库类型
        try:
            if db is None:
                from app.db import get_db
                db_gen = get_db()
                db = next(db_gen)

            # 初始化所有标准类型为 0
            for kb_type in KnowledgeType:
                result[kb_type.value] = 0

            # 如果提供了 workspace_id，则按 workspace_id 过滤
            if current_workspace_id:
                # 构建查询条件
                query = db.query(
                    Knowledge.type,
                    func.count(Knowledge.id).label('count')
                ).filter(Knowledge.workspace_id == current_workspace_id)

                # 检查 Knowledge 模型是否有 status 字段
                if only_active and hasattr(Knowledge, 'status'):
                    query = query.filter(Knowledge.status == 1)

                # 按类型分组
                type_counts = query.group_by(Knowledge.type).all()

                # 只填充标准类型的统计值，忽略其他类型
                valid_types = {kb_type.value for kb_type in KnowledgeType}
                for type_name, count in type_counts:
                    if type_name in valid_types:
                        result[type_name] = count

                logger.info(f"知识库类型统计成功 (workspace_id={current_workspace_id}): {result}")
            else:
                # 没有提供 workspace_id，所有知识库类型返回 0
                logger.info("未提供 workspace_id，知识库类型统计全部为 0")

        except Exception as e:
            logger.error(f"知识库类型统计失败: {e}")
            raise Exception(f"知识库类型统计失败: {e}")

        # 2. 统计 Neo4j 中的 memory 总量（统计当前空间下所有宿主的 Chunk 总数）
        try:
            if current_workspace_id:
                # 获取当前空间下的所有宿主
                from app.repositories import app_repository, end_user_repository
                from app.schemas.app_schema import App as AppSchema
                from app.schemas.end_user_schema import EndUser as EndUserSchema

                # 查询应用并转换为 Pydantic 模型
                apps_orm = app_repository.get_apps_by_workspace_id(db, current_workspace_id)
                apps = [AppSchema.model_validate(h) for h in apps_orm]
                app_ids = [app.id for app in apps]

                # 获取所有宿主
                end_users = []
                for app_id in app_ids:
                    end_user_orm_list = end_user_repository.get_end_users_by_app_id(db, app_id)
                    end_users.extend(h for h in end_user_orm_list)

                # 统计所有宿主的 Chunk 总数
                total_chunks = 0
                for end_user in end_users:
                    end_user_id_str = str(end_user.id)
                    memory_query = """
                    MATCH (n:Chunk) WHERE n.end_user_id = $end_user_id RETURN count(n) AS Count
                    """
                    neo4j_result = await _neo4j_connector.execute_query(
                        memory_query,
                        end_user_id=end_user_id_str,
                    )
                    chunk_count = neo4j_result[0]["Count"] if neo4j_result else 0
                    total_chunks += chunk_count
                    logger.debug(f"EndUser {end_user_id_str} Chunk数量: {chunk_count}")

                result["memory"] = total_chunks
                logger.info(f"Neo4j memory统计成功: 总Chunk数={total_chunks}, 宿主数={len(end_users)}")
            else:
                # 没有 workspace_id 时，返回 0
                result["memory"] = 0
                logger.info("未提供 workspace_id，memory 统计为 0")

        except Exception as e:
            logger.error(f"Neo4j memory统计失败: {e}", exc_info=True)
            # 如果 Neo4j 查询失败，memory 设为 0
            result["memory"] = 0

        # 3. 计算知识库类型总和（不包括 memory）
        result["total"] = (
            result.get("General", 0) +
            result.get("Web", 0) +
            result.get("Third-party", 0) +
            result.get("Folder", 0)
        )

        return result


    async def get_hot_memory_tags_by_user(
        self,
        end_user_id: Optional[str] = None,
        limit: int = 20,
        model_id: Optional[str] = None,
        language_type: Optional[str] = "zh"
    ) -> List[Dict[str, Any]]:
        """
        获取指定用户的热门记忆标签

        参数：
        - end_user_id: 用户ID（可选），对应Neo4j中的end_user_id字段
        - limit: 返回标签数量限制

        返回格式：
        [
            {"name": "标签名", "frequency": 频次},
            ...
        ]
        """
        try:
            # by_user=False 表示按 end_user_id 查询（在Neo4j中，end_user_id就是用户维度）
            tags = await get_hot_memory_tags(end_user_id, limit=limit, by_user=False)
            payload=[]
            for tag, freq in tags:
                if language_type!="zh":
                    tag=await Translation_English(model_id, tag)
                    payload.append({"name": tag, "frequency": freq})
                else:
                    payload.append({"name": tag, "frequency": freq})
            return payload
        except Exception as e:
            logger.error(f"热门记忆标签查询失败: {e}")
            raise Exception(f"热门记忆标签查询失败: {e}")


    async def get_user_profile(
        self,
        end_user_id: Optional[str] = None,
        current_user_id: Optional[str] = None,
        llm_id: Optional[str] = None,
        db: Session = None
    ) -> Dict[str, Any]:
        """
        获取用户详情，包含：
        1. 用户名字（直接使用 end_user_name)
        2. 用户标签（从摘要中用LLM总结3个标签）
        3. 热门记忆标签（从hot_memory_tags获取前4个）

        参数：
        - end_user_id: 用户ID（可选）
        - current_user_id: 当前登录用户的ID（保留参数）
        - llm_id: LLM模型ID（用于生成标签，可选，如果不提供则跳过标签生成）
        - db: 数据库会话（可选）

        返回格式：
        {
            "name": "用户名",
            "tags": ["产品设计师", "旅行爱好者", "摄影发烧友"],
            "hot_tags": [
                {"name": "标签1", "frequency": 10},
                {"name": "标签2", "frequency": 8},
                ...
            ]
        }
        """
        result = {}

        # 1. 根据 end_user_id 获取 end_user_name
        try:
            if end_user_id and db:
                from app.repositories import end_user_repository
                from app.schemas.end_user_schema import EndUser as EndUserSchema

                end_user_orm = end_user_repository.get_end_user_by_id(db, end_user_id)
                if end_user_orm:
                    end_user = EndUserSchema.model_validate(end_user_orm)
                    end_user_name = end_user.other_name
                else:
                    end_user_name = "默认用户"
            else:
                end_user_name = "默认用户"
        except Exception as e:
            logger.error(f"Failed to get end_user_name: {e}")
            end_user_name = "默认用户"

        result["name"] = end_user_name
        logger.debug(f"The end_user is: {end_user_name}")

        # 2. 使用LLM从语句和实体中提取标签
        try:
            connector = Neo4jConnector()

            # 查询该用户的语句
            query = (
                "MATCH (s:Statement) "
                "WHERE ($end_user_id IS NULL OR s.end_user_id = $end_user_id) AND s.statement IS NOT NULL "
                "RETURN s.statement AS statement "
                "ORDER BY s.created_at DESC LIMIT 100"
            )
            rows = await connector.execute_query(query, end_user_id=end_user_id)
            statements = [r.get("statement", "") for r in rows if r.get("statement")]

            # 查询该用户的热门实体
            entity_query = (
                "MATCH (e:ExtractedEntity) "
                "WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id) AND e.entity_type <> '人物' AND e.name IS NOT NULL "
                "RETURN e.name AS name, count(e) AS frequency "
                "ORDER BY frequency DESC LIMIT 20"
            )
            entity_rows = await connector.execute_query(entity_query, end_user_id=end_user_id)
            entities = [f"{r['name']} ({r['frequency']})" for r in entity_rows]

            await connector.close()

            if not statements or not llm_id:
                result["tags"] = []
                if not llm_id and statements:
                    logger.warning("llm_id not provided, skipping tag generation")
            else:
                # 构建摘要文本
                summary_text = f"用户语句样本：{' | '.join(statements[:20])}\n核心实体：{', '.join(entities)}"
                logger.debug(f"User data found: {len(statements)} statements, {len(entities)} entities")

                # 使用LLM提取标签
                with get_db_context() as db:
                    factory = MemoryClientFactory(db)
                    llm_client = factory.get_llm_client(llm_id)

                # 定义标签提取的结构
                class UserTags(BaseModel):
                    tags: list[str] = Field(..., description="3个描述用户特征的标签，如：产品设计师、旅行爱好者、摄影发烧友")

                messages = [
                    {
                        "role": "system",
                        "content": "你是一个信息提取助手。从用户的语句和实体中提取3个最能代表用户特征的标签。标签应该简洁（2-6个字），描述用户的职业、兴趣或特点。"
                    },
                    {
                        "role": "user",
                        "content": f"请从以下用户信息中提取3个标签：\n\n{summary_text}"
                    }
                ]

                user_tags = await llm_client.response_structured(
                    messages=messages,
                    response_model=UserTags
                )

                result["tags"] = user_tags.tags
                logger.debug(f"Extracted tags: {user_tags.tags}")

        except Exception as e:
            # 如果提取失败，使用默认值
            logger.error(f"Failed to extract user tags: {e}")
            result["tags"] = []

        try:
            # 3. 获取热门记忆标签（前4个）
            connector = Neo4jConnector()
            names_to_exclude = ['AI', 'Caroline', 'Melanie', 'Jon', 'Gina', '用户', 'AI助手', 'John', 'Maria']
            hot_tag_query = (
                "MATCH (e:ExtractedEntity) "
                "WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id) AND e.entity_type <> '人物' "
                "AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
                "RETURN e.name AS name, count(e) AS frequency "
                "ORDER BY frequency DESC LIMIT 4"
            )
            hot_tag_rows = await connector.execute_query(
                hot_tag_query,
                end_user_id=end_user_id,
                names_to_exclude=names_to_exclude
            )
            await connector.close()

            result["hot_tags"] = [{"name": r["name"], "frequency": r["frequency"]} for r in hot_tag_rows]
            logger.debug(f"Hot tags found: {len(result['hot_tags'])} tags")
        except Exception as e:
            logger.error(f"Failed to get hot tags: {e}")
            result["hot_tags"] = []

        return result

    async def stream_log_content(self) -> AsyncGenerator[str, None]:
        """
        Stream log content in real-time using Server-Sent Events (SSE)

        This method establishes a streaming connection and transmits log entries
        as they are written to the log file. It uses the LogStreamer to watch
        the file and yields SSE-formatted messages.

        Yields:
            SSE-formatted strings with the following event types:
            - log: Contains log content and timestamp
            - keepalive: Periodic keepalive messages to maintain connection
            - error: Error information if streaming fails
            - done: Indicates streaming has completed

        Raises:
            FileNotFoundError: If log file doesn't exist at stream start
            Exception: For other unexpected errors during streaming
        """
        logger.info("Starting log content streaming")

        # Get log file path - use project root directory
        current_file = os.path.abspath(__file__)  # app/services/memory_agent_service.py
        app_dir = os.path.dirname(os.path.dirname(current_file))  # app directory
        project_root = os.path.dirname(app_dir)  # redbear-mem directory
        log_path = os.path.join(project_root, "logs", "agent_service.log")

        # Check if file exists before starting stream
        if not os.path.exists(log_path):
            logger.error(f"Log file not found: {log_path}")
            # Send error event in SSE format
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件不存在', 'error': f'File not found: {log_path}'})}\n\n"
            return

        streamer = None
        try:
            # Initialize LogStreamer with keepalive interval from settings (default 300 seconds)
            keepalive_interval = getattr(settings, 'LOG_STREAM_KEEPALIVE_INTERVAL', 300)
            streamer = LogStreamer(log_path, keepalive_interval=keepalive_interval)

            logger.info(f"LogStreamer initialized for {log_path}")

            # Stream log content using read_existing_and_stream to get all existing content first
            async for message in streamer.read_existing_and_stream():
                event_type = message.get("event")
                data = message.get("data")

                # Format as SSE message
                # SSE format: "event: <type>\ndata: <json_data>\n\n"
                sse_message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

                logger.debug(f"Streaming event: {event_type}")
                yield sse_message

                # If error or done event, stop streaming
                if event_type in ["error", "done"]:
                    logger.info(f"Stream ended with event: {event_type}")
                    break

        except FileNotFoundError as e:
            logger.error(f"Log file not found during streaming: {e}")
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件在流式传输期间变得不可用', 'error': str(e)})}\n\n"

        except Exception as e:
            logger.error(f"Unexpected error during log streaming: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'code': 8001, 'message': '流式传输期间发生错误', 'error': str(e)})}\n\n"

        finally:
            # Resource cleanup
            logger.info("Log streaming completed, cleaning up resources")
            # LogStreamer uses context manager for file handling, so cleanup is automatic

def get_end_user_connected_config(end_user_id: str, db: Session) -> Dict[str, Any]:
    """
    获取终端用户关联的记忆配置

    通过以下流程获取配置：
    1. 根据 end_user_id 获取用户的 app_id
    2. 获取该应用的最新发布版本
    3. 从发布版本的 config 字段中提取 memory_config_id

    Args:
        end_user_id: 终端用户ID
        db: 数据库会话

    Returns:
        包含 memory_config_id 和相关信息的字典

    Raises:
        ValueError: 当终端用户不存在或应用未发布时
    """
    from app.models.app_release_model import AppRelease
    from app.models.end_user_model import EndUser
    from sqlalchemy import select

    logger.info(f"Getting connected config for end_user: {end_user_id}")

    # 1. 获取 end_user 及其 app_id
    end_user = db.query(EndUser).filter(EndUser.id == end_user_id).first()
    if not end_user:
        logger.warning(f"End user not found: {end_user_id}")
        raise ValueError(f"终端用户不存在: {end_user_id}")

    app_id = end_user.app_id
    logger.debug(f"Found end_user app_id: {app_id}")

    # 2. 获取该应用的最新发布版本
    stmt = (
        select(AppRelease)
        .where(AppRelease.app_id == app_id, AppRelease.is_active.is_(True))
        .order_by(AppRelease.version.desc())
    )
    latest_release = db.scalars(stmt).first()

    if not latest_release:
        logger.warning(f"No active release found for app: {app_id}")
        raise ValueError(f"应用未发布: {app_id}")

    logger.debug(f"Found latest release: version={latest_release.version}, id={latest_release.id}")

    # 3. 从 config 中提取 memory_config_id
    config = latest_release.config or {}
    
    # 如果 config 是字符串，解析为字典
    if isinstance(config, str):
        import json
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse config JSON for release {latest_release.id}")
            config = {}
    
    memory_obj = config.get('memory', {})
    memory_config_id = memory_obj.get('memory_content') if isinstance(memory_obj, dict) else None

    result = {
        "end_user_id": str(end_user_id),
        "app_id": str(app_id),
        "release_id": str(latest_release.id),
        "release_version": latest_release.version,
        "memory_config_id": memory_config_id
    }

    print(188*'*')
    print(result)
    print(188 * '*')

    logger.info(f"Successfully retrieved connected config: memory_config_id={memory_config_id}")
    return result


def get_end_users_connected_configs_batch(end_user_ids: List[str], db: Session) -> Dict[str, Dict[str, Any]]:
    """
    批量获取多个终端用户关联的记忆配置（优化版本，减少数据库查询次数）

    通过以下流程获取配置：
    1. 批量查询所有 end_user_id 对应的 app_id
    2. 批量获取这些应用的最新发布版本
    3. 从发布版本的 config 字段中提取 memory_config_id

    Args:
        end_user_ids: 终端用户ID列表
        db: 数据库会话

    Returns:
        字典，key 为 end_user_id，value 为包含 memory_config_id 和 memory_config_name 的字典
        格式: {
            "user_id_1": {"memory_config_id": "xxx", "memory_config_name": "xxx"},
            "user_id_2": {"memory_config_id": None, "memory_config_name": None},
            ...
        }
    """
    from app.models.app_release_model import AppRelease
    from app.models.end_user_model import EndUser
    from app.models.memory_config_model import MemoryConfig
    from sqlalchemy import select

    logger.info(f"Batch getting connected configs for {len(end_user_ids)} end_users")

    result = {}

    # 如果列表为空，直接返回空字典
    if not end_user_ids:
        return result

    # 1. 批量查询所有 end_user 及其 app_id
    end_users = db.query(EndUser).filter(EndUser.id.in_(end_user_ids)).all()

    # 创建 end_user_id -> app_id 的映射
    user_to_app = {str(eu.id): eu.app_id for eu in end_users}

    # 记录未找到的用户
    found_user_ids = set(user_to_app.keys())
    missing_user_ids = set(end_user_ids) - found_user_ids
    if missing_user_ids:
        logger.warning(f"End users not found: {missing_user_ids}")
        for user_id in missing_user_ids:
            result[user_id] = {"memory_config_id": None, "memory_config_name": None}

    # 2. 批量获取所有相关应用的最新发布版本
    app_ids = list(user_to_app.values())
    if not app_ids:
        return result

    # 查询所有活跃的发布版本
    stmt = (
        select(AppRelease)
        .where(AppRelease.app_id.in_(app_ids), AppRelease.is_active.is_(True))
        .order_by(AppRelease.app_id, AppRelease.version.desc())
    )
    releases = db.scalars(stmt).all()

    # 创建 app_id -> latest_release 的映射（每个 app 只保留最新版本）
    app_to_release = {}
    for release in releases:
        if release.app_id not in app_to_release:
            app_to_release[release.app_id] = release

    # 3. 收集所有 memory_config_id 并批量查询配置名称
    memory_config_ids = []
    for end_user_id, app_id in user_to_app.items():
        release = app_to_release.get(app_id)
        if release:
            config = release.config or {}
            memory_obj = config.get('memory', {})
            memory_config_id = memory_obj.get('memory_content') if isinstance(memory_obj, dict) else None
            if memory_config_id:
                memory_config_ids.append(memory_config_id)

    # 批量查询 memory_config_name
    config_id_to_name = {}
    if memory_config_ids:
        memory_configs = db.query(MemoryConfig).filter(MemoryConfig.id.in_(memory_config_ids)).all()
        config_id_to_name = {str(mc.id): mc.config_name for mc in memory_configs}

    # 4. 构建最终结果
    for end_user_id, app_id in user_to_app.items():
        release = app_to_release.get(app_id)

        if not release:
            logger.warning(f"No active release found for app: {app_id} (end_user: {end_user_id})")
            result[end_user_id] = {"memory_config_id": None, "memory_config_name": None}
            continue

        # 从 config 中提取 memory_config_id
        config = release.config or {}
        memory_obj = config.get('memory', {})
        memory_config_id = memory_obj.get('memory_content') if isinstance(memory_obj, dict) else None
        
        # 获取配置名称
        memory_config_name = config_id_to_name.get(memory_config_id) if memory_config_id else None

        result[end_user_id] = {
            "memory_config_id": memory_config_id,
            "memory_config_name": memory_config_name
        }

    logger.info(f"Successfully retrieved {len(result)} connected configs")
    return result